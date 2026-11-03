"""SRT Generator — macOS app shell (Contents/Resources/launcher.py).

Started by Contents/MacOS/launcher with SRT_APP_DIR (the read-only app code inside
the bundle) and SRT_DATA_DIR (~/Library/Application Support/SRT Generator).

1. One instance only: a second launch brings the running window forward and exits.
2. First launch, or the first launch after an update: install the pinned packages
   and run warmup.py to PASS, with progress shown in the window. Models live in the
   data folder, so updates never download them again. Then, once, offer the optional
   Hinglish model download (V4.2); otherwise it downloads the first time Hinglish is used.
3. Run the FastAPI server on a thread and load the UI into the same native window.
   Closing the window quits the app, which stops the server.
4. Start Sparkle (updater.py): a daily update check and "Check for Updates…" in the app menu;
   the app's Updates page drives it (V4.5).
5. After an update (VERSION differs from the last run), clear the web view's disk and memory
   cache before loading the page, so the new version's UI shows at once (V4.5). localStorage
   (theme, last page, settings) is kept. Then, once, offer to trash an old "Song SRT Generator.app".
"""

import fcntl
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import webview

APP_DIR = Path(os.environ["SRT_APP_DIR"])
SUPPORT = Path(os.environ["SRT_DATA_DIR"])
RESOURCES = Path(__file__).parent
APP_BUNDLE = RESOURCES.parent.parent  # …/SRT Generator.app
STAMP = SUPPORT / "installed.json"
LOG = SUPPORT / "install.log"
UV = str(SUPPORT / "bin" / "uv")

sys.path.insert(0, str(APP_DIR))
import config  # noqa: E402
from pipeline import ACCEPTED_EXTENSIONS  # noqa: E402

VERSION = (APP_DIR / "VERSION").read_text(encoding="utf-8").strip()
LOCAL_HOST = "127.0.0.1" if config.HOST in ("0.0.0.0", "") else config.HOST
port = config.PORT  # moves to a free port if something else holds config.PORT (start_server)
LAST_RUN = SUPPORT / "last-run-version"
OLD_APP_OFFERED = SUPPORT / "old-app-offered"
_updates = {}  # the Sparkle backend, once started


def base_url(at: int | None = None) -> str:
    return f"http://{LOCAL_HOST}:{at or port}"


def page_url() -> str:
    return f"{base_url()}/?v={VERSION}"  # a new version never matches a cached page


def server_version(at: int) -> str | None:
    """The version a server on this port reports in /api/meta: None if nothing answers like this
    app does ("?" if it answers without a version)."""
    try:
        with urllib.request.urlopen(base_url(at) + "/api/meta", timeout=2) as r:
            return str(json.loads(r.read()).get("version") or "?")
    except Exception:
        return None


def listener_pids(at: int) -> list[int]:
    out = subprocess.run(["/usr/sbin/lsof", "-nP", f"-iTCP:{at}", "-sTCP:LISTEN", "-t"],
                         capture_output=True, text=True).stdout
    return [int(x) for x in out.split() if x.isdigit() and int(x) != os.getpid()]


def is_ours(pid: int) -> bool:
    """A copy of this app (any version): the .app's launcher or a checkout's app.py."""
    cmd = subprocess.run(["/bin/ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    return "launcher.py" in cmd or "app.py" in cmd or "SRT Generator" in cmd


def stop(pid: int) -> bool:
    for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 3)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        for _ in range(wait * 10):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.1)
    return False


def claim_single_instance():
    """Hold an exclusive lock for the app's lifetime; if another copy holds it, focus that one —
    unless it is another version (an old copy still running after an update): stop it, take over."""
    lock = open(SUPPORT / "app.lock", "a+")
    for attempt in range(2):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            lock.seek(0)
            pid, _, other_port = lock.read().strip().partition(" ")  # "pid port" (before 4.5.1: "pid")
            other_port = int(other_port) if other_port.isdigit() else config.PORT
            running = server_version(other_port)
            if attempt == 0 and running not in (None, VERSION) and pid.isdigit() and is_ours(int(pid)):
                log_line(f"version {running} is still running (pid {pid}); stopping it")
                if stop(int(pid)):
                    continue
            try:
                from AppKit import NSApplicationActivateAllWindows, NSRunningApplication
                other = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
                if other:
                    other.activateWithOptions_(NSApplicationActivateAllWindows)
            except Exception:
                pass
            try:  # also un-minimizes the window once the UI is running
                urllib.request.urlopen(urllib.request.Request(base_url(other_port) + "/api/focus", method="POST"),
                                       timeout=2)
            except Exception:
                pass
            sys.exit(0)
    write_lock(lock)
    return lock


def write_lock(lock):
    lock.seek(0)
    lock.truncate()
    lock.write(f"{os.getpid()} {port}")
    lock.flush()


def log_line(message: str):
    with LOG.open("a", encoding="utf-8") as log:
        log.write(f"=== {time.ctime()}  {message}\n")


def trash_old_app():
    """After the 3.x data folder was moved (see Contents/MacOS/launcher), move the old
    "Song SRT Generator.app" to the Trash: opening it would set everything up again
    in its old folder and download the models a second time."""
    marker = SUPPORT / "migrated-from-3x"
    if not marker.exists():
        return
    from Foundation import NSFileManager, NSURL

    for folder in (Path("/Applications"), Path.home() / "Applications"):
        old = folder / "Song SRT Generator.app"
        if old.exists():
            ok, _, error = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
                NSURL.fileURLWithPath_(str(old)), None, None)
            log_line(f"moved {old} to the Trash" if ok else f"couldn't trash {old}: {error}")
    marker.unlink()


def picker_filter(mime_types: list[str]):
    """The web page's file-input MIME types → a pywebview file filter, [(description, [extensions])].

    pywebview turns WebKit's MIME types into UTTypes and drops the ones macOS doesn't know,
    so MKV (and M4V) files couldn't be picked; matching by extension accepts every format
    the pipeline reads. Returns None for anything else (pywebview's own handling)."""
    if any(m.startswith(("audio/", "video/")) for m in mime_types):
        return [("Audio or video", sorted(ext.lstrip(".") for ext in ACCEPTED_EXTENSIONS))]
    if mime_types and all(m.startswith("text/") for m in mime_types):
        return [("Text", ["txt"])]
    return None


def patch_file_picker():
    from webview.platforms import cocoa

    original = cocoa.BrowserView.create_file_dialog

    def create_file_dialog(self, dialog_type, directory, allow_multiple, save_filename,
                           file_filter, main_thread=False):
        if isinstance(file_filter, cocoa.Foundation.NSArray):
            file_filter = picker_filter([str(m) for m in file_filter]) or file_filter
        return original(self, dialog_type, directory, allow_multiple, save_filename,
                        file_filter, main_thread)

    cocoa.BrowserView.create_file_dialog = create_file_dialog


def start_updater(open_updates_page):
    """Sparkle must start on the main thread, once the native menu bar exists."""
    from PyObjCTools import AppHelper

    import updater

    def run():
        try:
            backend = updater.start(APP_BUNDLE, log_line, on_check=open_updates_page)
        except Exception as e:  # never let the updater stop the app
            log_line(f"Sparkle: failed to start: {e!r}")
            return
        _updates["backend"] = backend
        if "app" in sys.modules:  # the server is already running
            sys.modules["app"].update_backend = backend

    AppHelper.callAfter(run)


def clear_web_cache_if_updated():
    """A new version: drop WebKit's cached pages and files (disk, memory, fetch cache) so the old
    UI can't come back. Keeps localStorage and everything else. Runs before the page loads."""
    last = LAST_RUN.read_text(encoding="utf-8").strip() if LAST_RUN.exists() else ""
    if last == VERSION:
        return
    from Foundation import NSDate, NSSet
    from PyObjCTools import AppHelper
    import WebKit

    types = [t for t in ("WKWebsiteDataTypeDiskCache", "WKWebsiteDataTypeMemoryCache",
                         "WKWebsiteDataTypeFetchCache", "WKWebsiteDataTypeOfflineWebApplicationCache")
             if hasattr(WebKit, t)]
    done = threading.Event()

    def run():
        WebKit.WKWebsiteDataStore.defaultDataStore().removeDataOfTypes_modifiedSince_completionHandler_(
            NSSet.setWithArray_([getattr(WebKit, t) for t in types]), NSDate.distantPast(), lambda: done.set())
    AppHelper.callAfter(run)
    cleared = done.wait(10)
    log_line(f"web cache {'cleared' if cleared else 'NOT cleared (timeout)'} after update "
             f"{last or '(first run)'} -> {VERSION}: {', '.join(types)}")
    LAST_RUN.write_text(VERSION, encoding="utf-8")


def old_apps() -> list[Path]:
    """Copies of the 3.x "Song SRT Generator.app" left in Applications or Downloads."""
    found = []
    for folder in (Path("/Applications"), Path.home() / "Applications", Path.home() / "Downloads"):
        found += [p for p in (folder / "Song SRT Generator.app",) if p.exists()]
        if folder.name == "Downloads" and folder.is_dir():
            found += [p for p in folder.glob("*/Song SRT Generator.app")]
    return found


def requirements_hash() -> str:
    return hashlib.sha256((APP_DIR / "requirements.txt").read_bytes()).hexdigest()


def needs_setup() -> bool:
    try:
        stamp = json.loads(STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return stamp.get("version") != VERSION or stamp.get("requirements") != requirements_hash()


def port_in_use() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((LOCAL_HOST, port)) == 0


class Api:
    """Called from the progress page's buttons."""

    def __init__(self, shell):
        self._shell = shell

    def hinglish_choice(self, now):
        self._shell.hinglish_now = bool(now)
        self._shell.hinglish_answer.set()

    def retry(self):
        threading.Thread(target=self._shell.run, daemon=True).start()

    def show_log(self):
        subprocess.run(["open", "-R", str(LOG)])


class Shell:
    def __init__(self):
        self.window = None
        self.proc = None
        self.server = None
        self.updater_started = False
        self.lock = None  # app.lock, rewritten with the port once the server runs
        self.hinglish_answer = threading.Event()
        self.hinglish_now = False

    def ui(self, fn, *args):
        self.window.evaluate_js(f"{fn}({', '.join(json.dumps(a) for a in args)})")

    def stream(self, cmd) -> bool:
        """Run a setup command, showing its output in the window and appending it to install.log."""
        with LOG.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {time.ctime()}  {' '.join(cmd)}\n")
            log.flush()
            self.proc = subprocess.Popen(
                cmd, cwd=SUPPORT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", env=os.environ | {"PYTHONUNBUFFERED": "1"},
            )
            last = 0.0
            for line in self.proc.stdout:  # text mode also splits tqdm's \r updates
                log.write(line)
                line = line.strip()
                if line and time.monotonic() - last > 0.1:
                    last = time.monotonic()
                    self.ui("logLine", line)
            return self.proc.wait() == 0

    def fail(self, message):
        self.ui("fail", message)
        return False

    def setup(self) -> bool:
        first = not STAMP.exists()
        self.ui("begin", "Setting up SRT Generator" if first else f"Updating to version {VERSION}", first)

        bundled_models = RESOURCES / "models"  # only in --full builds
        if bundled_models.is_dir() and not (SUPPORT / "models").exists():
            shutil.copytree(bundled_models, SUPPORT / "models")

        self.ui("step", 1, "Installing the app's Python packages",
                "About 1 GB the first time. Updates only fetch what changed.")
        if not self.stream([UV, "pip", "install", "--python", sys.executable,
                            "-r", str(APP_DIR / "requirements.txt")]):
            return self.fail("Couldn't install the Python packages. Check your internet connection and try again.")

        if first:
            self.ui("step", 2, "Downloading the AI models and doing a test run",
                    "About 1.5 GB (5–20 minutes). The models are kept, so updates never download them again.")
        else:
            self.ui("step", 2, "Doing a test run", "Using the AI models already on this Mac.")
        if not self.stream([sys.executable, str(APP_DIR / "warmup.py")]):
            return self.fail("The test run didn't pass. Check your internet connection and try again.")

        try:
            offered = json.loads(STAMP.read_text(encoding="utf-8")).get("hinglish_offered", False)
        except (OSError, ValueError):
            offered = False
        if not offered:
            self.offer_hinglish_model()
        STAMP.write_text(json.dumps({"version": VERSION, "requirements": requirements_hash(),
                                     "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
                                     "hinglish_offered": True}), encoding="utf-8")
        self.ui("step", 4, "PASS — all set", "Opening the app…")
        return True

    def offer_hinglish_model(self):
        """Optional: download the Hinglish model now instead of on first use. Never fails setup."""
        import hinglish_model
        if hinglish_model.installed():
            return
        self.hinglish_answer.clear()
        self.ui("offerHinglish", config.HINGLISH_MODEL_MB)
        self.hinglish_answer.wait()
        if not self.hinglish_now:
            return
        self.ui("step", 3, "Downloading the Hinglish model",
                f"About {config.HINGLISH_MODEL_MB} MB, once. Hinglish then works offline.")
        if not self.stream([sys.executable, str(APP_DIR / "hinglish_model.py"), "download"]):
            self.ui("logLine", "Couldn't download it now — it downloads the first time you use Hinglish.")
            time.sleep(3)

    def start_server(self) -> bool:
        if not self.free_the_port():
            return False
        import uvicorn
        import app as server

        server.on_focus_request = self.focus
        server.update_backend = _updates.get("backend")
        self.server = uvicorn.Server(uvicorn.Config(server.app, host=config.HOST, port=port,
                                                    log_level="warning"))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(300):
            if self.server.started:
                write_lock(self.lock)
                log_line(f"server {VERSION} on port {port}")
                return True
            time.sleep(0.1)
        return self.fail("The app's local server didn't start. Click Try again.")

    def free_the_port(self) -> bool:
        """Something already answers on the port: another version of this app's server (say, left
        running by an earlier version) is stopped; anything else, or one that won't stop, keeps
        the port and this version moves to a free one. Either way the window only ever talks to
        this version's own server."""
        global port
        if not port_in_use():
            return True
        running = server_version(port)
        log_line(f"port {port} is in use (answers as version {running})")
        for pid in listener_pids(port):
            if running not in (None, VERSION) and is_ours(pid):
                log_line(f"stopping the server of version {running} (pid {pid})"
                         f": {'stopped' if stop(pid) else 'would not stop'}")
        for _ in range(30):
            if not port_in_use():
                return True
            time.sleep(0.1)
        with socket.socket() as s:
            s.bind((LOCAL_HOST, 0))
            port = s.getsockname()[1]
        log_line(f"using port {port} instead")
        return True

    def run(self):
        if not self.updater_started:
            self.updater_started = True
            try:
                start_updater(self.open_updates_page)
            except Exception as e:  # updates must never stop the app from opening
                log_line(f"Sparkle: couldn't start: {e!r}")
        self.ui("reset")
        if needs_setup() and not self.setup():
            return
        if self.server is None and not self.start_server():
            return
        clear_web_cache_if_updated()
        self.window.load_url(page_url())
        self.offer_to_trash_old_app()

    def open_updates_page(self):
        threading.Thread(target=lambda: self.window.evaluate_js(
            "window.__openPage && window.__openPage('updates')"), daemon=True).start()

    def offer_to_trash_old_app(self):
        """Once: an old "Song SRT Generator.app" is still around — offer to move it to the Trash."""
        found = old_apps()
        if OLD_APP_OFFERED.exists() or not found:
            return
        OLD_APP_OFFERED.write_text(VERSION, encoding="utf-8")
        where = "\n".join(str(p) for p in found)
        if not self.window.create_confirmation_dialog(
                "Old app found",
                f"The old “Song SRT Generator” app is still on this Mac:\n\n{where}\n\n"
                "Opening it would set up a second copy and download its models again. "
                "Move it to the Trash?"):
            log_line("old app kept: " + where.replace("\n", ", "))
            return
        from Foundation import NSFileManager, NSURL
        for old in found:
            ok, _, error = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
                NSURL.fileURLWithPath_(str(old)), None, None)
            log_line(f"moved {old} to the Trash" if ok else f"couldn't trash {old}: {error}")

    def focus(self):
        from AppKit import NSApp
        from PyObjCTools import AppHelper

        self.window.restore()
        self.window.show()
        AppHelper.callAfter(lambda: NSApp.activateIgnoringOtherApps_(True))


def main():
    SUPPORT.mkdir(parents=True, exist_ok=True)
    lock = claim_single_instance()
    trash_old_app()
    patch_file_picker()
    shell = Shell()
    shell.lock = lock
    webview.settings["ALLOW_DOWNLOADS"] = True
    shell.window = webview.create_window(
        "SRT Generator", html=(RESOURCES / "progress.html").read_text(encoding="utf-8"),
        js_api=Api(shell), width=1280, height=880, min_size=(920, 640), background_color="#f5f6f9",
    )
    # Persistent storage keeps the theme and language picks between launches.
    webview.start(shell.run, private_mode=False, storage_path=str(SUPPORT / "webview"),
                  icon=str(RESOURCES / "AppIcon.icns"))
    if shell.proc and shell.proc.poll() is None:
        shell.proc.kill()
    os._exit(0)  # window closed: stop the server and the job worker with it


if __name__ == "__main__":
    main()

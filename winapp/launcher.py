"""SRT Generator — Windows app shell (DECISIONS.md V4.7), the counterpart of macapp/launcher.py.

Started by "SRT Generator.exe" (winapp/launcher.cs) with pythonw.exe from the data folder's venv and
SRT_APP_DIR (the installed app: %LOCALAPPDATA%\\Programs\\SRT Generator) and SRT_DATA_DIR (all
writable state: %LOCALAPPDATA%\\SRT Generator — python, venv, models, jobs, corrections, settings).

1. One instance only (a named mutex): a second launch brings the running window forward and exits.
   Another version still running (an update was installed under it) is stopped instead.
2. First launch, or the first launch after an update: install the pinned packages and run
   warmup.py to PASS, with progress in the window. Models live in the data folder, so updates
   never download them again. Before the first setup, an old zip install (C:\\srt, Desktop…) is
   offered: its models and jobs move here, so nothing is downloaded twice. Then, once, the
   optional Hinglish model is offered.
3. The FastAPI server runs on a thread; the UI loads in the same native window (Edge WebView2).
   Closing the window quits the app, which stops the server.
4. WinSparkle (winupdater.py): a daily update check, "Check for updates…" in the app menu, and the
   Updates page drives it. An update quits the app, installs silently and reopens it.
"""

import ctypes
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

APP_DIR = Path(os.environ["SRT_APP_DIR"])
SUPPORT = Path(os.environ["SRT_DATA_DIR"])
HERE = Path(__file__).parent
STAMP = SUPPORT / "installed.json"
LOG = SUPPORT / "install.log"
UV = str(APP_DIR / "bin" / "uv.exe")
ICON = HERE / "app.ico"
MUTEX_NAME = "SRTGeneratorApp"  # the installer waits for it to disappear before replacing files
APP_ID = "BrightLifeCreations.SRTGenerator"  # taskbar grouping; the shortcuts carry the same id
CREATE_NO_WINDOW = 0x08000000

sys.path.insert(0, str(APP_DIR))
os.chdir(SUPPORT)
import config  # noqa: E402

VERSION = (APP_DIR / "VERSION").read_text(encoding="utf-8").strip()
LOCAL_HOST = "127.0.0.1" if config.HOST in ("0.0.0.0", "") else config.HOST
port = config.PORT
_updates = {}

# Old zip installs whose models and jobs can move here (the first setup offers it once).
OLD_INSTALL_PLACES = [Path("C:/srt"), Path("C:/SRT-Generator"), Path("C:/SRT Generator")] + [
    Path.home() / folder / name for folder in ("Desktop", "Documents", "Downloads")
    for name in ("SRT-Generator", "SRT Generator")]
MIGRATION_OFFERED = SUPPORT / "migration-offered"
SETTINGS_FILES = ("corrections.md", "ai-settings.json", "hinglish-spellings.md", "number-exceptions.txt")


def log_line(message: str):
    with LOG.open("a", encoding="utf-8") as log:
        log.write(f"=== {time.ctime()}  {message}\n")


def base_url(at: int | None = None) -> str:
    return f"http://{LOCAL_HOST}:{at or port}"


def page_url() -> str:
    return f"{base_url()}/?v={VERSION}"


def server_version(at: int) -> str | None:
    try:
        with urllib.request.urlopen(base_url(at) + "/api/meta", timeout=2) as r:
            return str(json.loads(r.read()).get("version") or "?")
    except Exception:
        return None


def listener_pids(at: int) -> list[int]:
    out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW).stdout
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{at}") and parts[4].isdigit():
            pids.add(int(parts[4]))
    return [p for p in pids if p != os.getpid()]


def stop(pid: int) -> bool:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=CREATE_NO_WINDOW)
    for _ in range(50):
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


def _alive(pid: int) -> bool:
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def claim_single_instance():
    """A named mutex for the app's lifetime. If another copy holds it: the same version is focused
    and this one exits; another version (still running after an update) is stopped."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    lock_file = SUPPORT / "app.lock"
    for attempt in range(2):
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            return handle
        kernel32.CloseHandle(handle)
        try:
            pid, _, other_port = lock_file.read_text(encoding="utf-8").strip().partition(" ")
        except OSError:
            pid, other_port = "", ""
        other_port = int(other_port) if other_port.isdigit() else config.PORT
        running = server_version(other_port)
        if attempt == 0 and running not in (None, VERSION) and pid.isdigit():
            log_line(f"version {running} is still running (pid {pid}); stopping it")
            if stop(int(pid)):
                time.sleep(0.5)
                continue
        try:
            urllib.request.urlopen(urllib.request.Request(base_url(other_port) + "/api/focus", method="POST"),
                                   timeout=2)
        except Exception:
            pass
        sys.exit(0)
    sys.exit(0)


def write_lock():
    (SUPPORT / "app.lock").write_text(f"{os.getpid()} {port}", encoding="utf-8")


def requirements_hash() -> str:
    return hashlib.sha256((APP_DIR / "requirements-windows.txt").read_bytes()).hexdigest()


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


def old_installs() -> list[Path]:
    """Zip installs (Install.bat / a checkout) with models or jobs worth keeping."""
    found = []
    for place in OLD_INSTALL_PLACES:
        try:
            if (place / "app.py").is_file() and ((place / "models").is_dir() or (place / "jobs").is_dir()):
                if place.resolve() != SUPPORT.resolve() and place not in found:
                    found.append(place)
        except OSError:
            continue
    return found


def move_tree(src: Path, dst: Path) -> int:
    """Move src's contents into dst (a rename on the same drive; copy + delete otherwise). Items
    already in dst are kept and the old copy stays where it was. Returns the number moved."""
    moved = 0
    dst.mkdir(parents=True, exist_ok=True)
    for item in list(src.iterdir()):
        target = dst / item.name
        if target.exists():
            if item.is_dir() and target.is_dir():
                moved += move_tree(item, target)
            continue
        shutil.move(str(item), str(target))
        moved += 1
    try:
        src.rmdir()
    except OSError:
        pass
    return moved


def set_app_id():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


class Api:
    """Called from the progress page's buttons."""

    def __init__(self, shell):
        self._shell = shell

    def hinglish_choice(self, now):
        self._shell.hinglish_now = bool(now)
        self._shell.hinglish_answer.set()

    def migrate_choice(self, yes):
        self._shell.migrate_yes = bool(yes)
        self._shell.migrate_answer.set()

    def retry(self):
        threading.Thread(target=self._shell.run, daemon=True).start()

    def show_log(self):
        subprocess.Popen(["explorer", "/select,", str(LOG)])


class Shell:
    def __init__(self):
        self.window = None
        self.proc = None
        self.server = None
        self.hinglish_answer = threading.Event()
        self.hinglish_now = False
        self.migrate_answer = threading.Event()
        self.migrate_yes = False
        self.quitting = False

    def ui(self, fn, *args):
        self.window.evaluate_js(f"{fn}({', '.join(json.dumps(a) for a in args)})")

    def stream(self, cmd) -> bool:
        """Run a setup command, showing its output in the window and appending it to install.log."""
        env = os.environ | {"PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
        with LOG.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {time.ctime()}  {' '.join(cmd)}\n")
            log.flush()
            self.proc = subprocess.Popen(cmd, cwd=SUPPORT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace", env=env,
                                         creationflags=CREATE_NO_WINDOW)
            last = 0.0
            for line in self.proc.stdout:
                log.write(line)
                line = line.strip()
                if line and time.monotonic() - last > 0.1:
                    last = time.monotonic()
                    self.ui("logLine", line)
            return self.proc.wait() == 0

    def fail(self, message):
        self.ui("fail", message)
        return False

    def offer_migration(self):
        """Before the first setup: an old zip install's models and jobs can move here."""
        if MIGRATION_OFFERED.exists() or (SUPPORT / "models").is_dir():
            return
        found = old_installs()
        if not found:
            return
        old = found[0]
        sizes = {}
        for name in ("models", "jobs"):
            folder = old / name
            if folder.is_dir():
                sizes[name] = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1e9
        self.migrate_answer.clear()
        self.ui("offerMigration", str(old), round(sizes.get("models", 0), 1),
                sum(1 for p in (old / "jobs").iterdir() if p.is_dir()) if (old / "jobs").is_dir() else 0)
        self.migrate_answer.wait()
        MIGRATION_OFFERED.write_text(f"{old}\n{'moved' if self.migrate_yes else 'declined'}\n", encoding="utf-8")
        if not self.migrate_yes:
            log_line(f"old install at {old}: not moved")
            return
        self.ui("step", 0, "Moving your models and jobs", f"From {old}. On the same drive this takes seconds.")
        for name in ("models", "jobs"):
            if (old / name).is_dir():
                n = move_tree(old / name, SUPPORT / name)
                log_line(f"moved {n} items from {old / name} to {SUPPORT / name}")
        for name in SETTINGS_FILES:
            if (old / name).is_file() and not (SUPPORT / name).exists():
                shutil.copy2(old / name, SUPPORT / name)
                log_line(f"copied {old / name}")

    def setup(self) -> bool:
        first = not STAMP.exists()
        self.ui("begin", "Setting up SRT Generator" if first else f"Updating to version {VERSION}", first)
        if first:
            self.offer_migration()
        self.ui("step", 1, "Installing the app's Python packages",
                "About 1 GB the first time. Updates only fetch what changed.")
        wheels = APP_DIR / "wheels"  # prebuilt wheels for the source-only packages (build_wheels.py)
        if not self.stream([UV, "pip", "install", "--python", sys.executable.replace("pythonw.exe", "python.exe"),
                            "-r", str(APP_DIR / "requirements-windows.txt"),
                            *(["--find-links", str(wheels)] if wheels.is_dir() else [])]):
            return self.fail("Couldn't install the Python packages. Check your internet connection and try again.")
        python = sys.executable.replace("pythonw.exe", "python.exe")
        have_models = (SUPPORT / "models" / "hf").is_dir()
        if have_models:
            self.ui("step", 2, "Doing a test run", "Using the AI models already on this PC.")
        else:
            self.ui("step", 2, "Downloading the AI models and doing a test run",
                    "About 1.5 GB (5–20 minutes). The models are kept, so updates never download them again.")
        if not self.stream([python, str(APP_DIR / "warmup.py")]):
            return self.fail("The test run didn't pass. Check your internet connection and try again.")
        try:
            offered = json.loads(STAMP.read_text(encoding="utf-8")).get("hinglish_offered", False)
        except (OSError, ValueError):
            offered = False
        if not offered:
            self.offer_hinglish_model(python)
        STAMP.write_text(json.dumps({"version": VERSION, "requirements": requirements_hash(),
                                     "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
                                     "hinglish_offered": True}), encoding="utf-8")
        log_line(f"setup PASS for {VERSION}")
        self.ui("step", 4, "PASS — all set", "Opening the app…")
        return True

    def offer_hinglish_model(self, python):
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
        if not self.stream([python, str(APP_DIR / "hinglish_model.py"), "download"]):
            self.ui("logLine", "Couldn't download it now — it downloads the first time you use Hinglish.")
            time.sleep(3)

    def start_server(self) -> bool:
        if not self.free_the_port():
            return False
        import uvicorn
        import app as server

        server.on_focus_request = self.focus
        server.update_backend = _updates.get("backend")
        self.server = uvicorn.Server(uvicorn.Config(server.app, host=config.HOST, port=port, log_level="warning"))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(300):
            if self.server.started:
                write_lock()
                log_line(f"server {VERSION} on port {port}")
                return True
            time.sleep(0.1)
        return self.fail("The app's local server didn't start. Click Try again.")

    def free_the_port(self) -> bool:
        """Another version of this app's server on the port is stopped; anything else keeps the
        port and this version moves to a free one."""
        global port
        if not port_in_use():
            return True
        running = server_version(port)
        log_line(f"port {port} is in use (answers as version {running})")
        if running not in (None, VERSION):
            for pid in listener_pids(port):
                log_line(f"stopping the server of version {running} (pid {pid}): "
                         f"{'stopped' if stop(pid) else 'would not stop'}")
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
        self.ui("reset")
        try:
            if needs_setup() and not self.setup():
                return
            if self.server is None and not self.start_server():
                return
        except Exception as e:  # show it instead of a frozen window
            log_line(f"setup raised {e!r}")
            self.fail(f"Something went wrong ({type(e).__name__}).")
            return
        self.window.load_url(page_url())

    def set_icon(self):
        """pywebview's Windows form shows Python's icon; use the app's (window and taskbar)."""
        try:
            import clr  # noqa: F401  (pythonnet, loaded by pywebview)
            from System.Drawing import Icon
            form = self.window.native
            form.Invoke(__import__("System").Action(lambda: setattr(form, "Icon", Icon(str(ICON)))))
        except Exception as e:
            log_line(f"couldn't set the window icon: {e!r}")

    def focus(self):
        try:
            self.window.restore()
            self.window.show()
            form = self.window.native
            import System
            form.Invoke(System.Action(lambda: (form.Activate(), setattr(form, "TopMost", True),
                                               setattr(form, "TopMost", False))))
        except Exception as e:
            log_line(f"focus failed: {e!r}")

    def check_for_updates(self):
        backend = _updates.get("backend")
        if backend:
            backend.check()
        threading.Thread(target=lambda: self.window.evaluate_js(
            "window.__openPage && window.__openPage('updates')"), daemon=True).start()

    def quit_for_update(self):
        """WinSparkle started the installer: close the window, which stops the app."""
        self.quitting = True
        try:
            self.window.destroy()
        finally:
            threading.Timer(5.0, lambda: os._exit(0)).start()


def start_updates(shell):
    try:
        import winupdater
        dll = SUPPORT / "bin" / "WinSparkle.dll"
        # A copy in the data folder, so the running app never locks a file the installer replaces.
        source = APP_DIR / "WinSparkle.dll"
        if source.is_file() and (not dll.is_file() or dll.stat().st_size != source.stat().st_size
                                 or dll.stat().st_mtime < source.stat().st_mtime):
            dll.parent.mkdir(exist_ok=True)
            shutil.copy2(source, dll)
        backend = winupdater.start(dll, VERSION, SUPPORT, log_line, on_shutdown=shell.quit_for_update)
    except Exception as e:  # updates must never stop the app from opening
        log_line(f"WinSparkle: couldn't start: {e!r}")
        return
    _updates["backend"] = backend
    if "app" in sys.modules:
        sys.modules["app"].update_backend = backend


def main():
    SUPPORT.mkdir(parents=True, exist_ok=True)
    mutex = claim_single_instance()  # noqa: F841  (held until the process ends)
    set_app_id()
    import webview
    from webview.menu import Menu, MenuAction

    shell = Shell()
    webview.settings["ALLOW_DOWNLOADS"] = True
    shell.window = webview.create_window(
        "SRT Generator", html=(HERE / "progress.html").read_text(encoding="utf-8"),
        js_api=Api(shell), width=1280, height=880, min_size=(920, 640), background_color="#f5f6f9")
    shell.window.events.shown += shell.set_icon
    start_updates(shell)
    menu = [Menu("SRT Generator", [MenuAction("Check for updates…", shell.check_for_updates),
                                    MenuAction("Quit", lambda: shell.window.destroy())])]
    webview.start(shell.run, private_mode=False, storage_path=str(SUPPORT / "webview"), menu=menu)
    if shell.proc and shell.proc.poll() is None:
        shell.proc.kill()
    if not shell.quitting:
        try:
            import winupdater
            winupdater.cleanup()
        except Exception:
            pass
    os._exit(0)  # window closed: stop the server and the job worker with it


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        SUPPORT.mkdir(parents=True, exist_ok=True)
        log_line(f"launcher crashed: {e!r}")
        raise

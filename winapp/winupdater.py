"""WinSparkle auto-updates for the Windows app (DECISIONS.md V4.7), the counterpart of macapp/updater.py.

WinSparkle.dll (pinned, MIT licence in COPYING.WinSparkle.txt next to it) is loaded with ctypes.
It reads the appcast (appcast-windows.xml on the latest GitHub release), verifies the EdDSA
signature of the new Setup.exe with the public key below, shows its own update window, runs the
installer silently and asks the app to quit (shutdown callback); the installer reopens the app.

Backend is the server's handle (app.update_backend), the same shape as the Mac's: state(), check(),
install(), set_automatic(). The Updates page drives it; "Check for updates…" in the app menu too.
Settings: "Check automatically" is WinSparkle's own (HKCU\\Software\\BrightLife Creations\\
SRT Generator\\WinSparkle); "Download and install automatically" is ours (update-settings.json in
the data folder): when a scheduled check finds an update, it installs it straight away.
"""

import ctypes
import json
import re
import threading
import time
import urllib.request
import winreg
from ctypes import CFUNCTYPE, c_char_p, c_int, c_longlong, c_wchar_p
from datetime import datetime, timezone
from pathlib import Path

FEED_URL = ("https://github.com/brightlifecreations-projects/song-srt-generator/releases/latest/download/"
            "appcast-windows.xml")
# The public half of the Windows release key (DECISIONS.md V4.7). The private half is only in the
# release laptop's Windows Credential Manager ("SRT Generator release key (Windows)").
EDDSA_PUBLIC_KEY = "Q38yXnQGJ/pyvtmN3QxpC1oIyvOYTBITuPvB3ef76v4="
COMPANY, APP = "BrightLife Creations", "SRT Generator"
REGISTRY = rf"Software\{COMPANY}\{APP}\WinSparkle"
CHECK_INTERVAL = 24 * 3600

VOID_CB = CFUNCTYPE(None)
INT_CB = CFUNCTYPE(c_int)
_keep = []  # ctypes callbacks must outlive the DLL's use of them


class Status:
    def __init__(self):
        self.status, self.error, self.available = "idle", None, None
        self.manual = False  # the current check was started by the user (WinSparkle shows its window)


class Backend:
    mode = "winsparkle"  # what /api/updates reports (the Mac's is "sparkle")

    def __init__(self, dll, version: str, feed: str, settings_file: Path, log):
        self.dll, self.version, self.feed, self.settings_file, self.log = dll, version, feed, settings_file, log
        self.s = Status()

    # --- the server's interface -----------------------------------------------------------------
    def state(self) -> dict:
        last = self.dll.win_sparkle_get_last_check_time()
        return {"auto_check": bool(self.dll.win_sparkle_get_automatic_check_for_updates()),
                "auto_install": self._auto_install(),
                "last_checked": datetime.fromtimestamp(last, timezone.utc).isoformat() if last > 0 else None,
                "status": self.s.status, "error": self.s.error, "available": self.s.available,
                "progress": None}

    def check(self):
        """A check with WinSparkle's window (it ignores "Skip this version", like the Mac)."""
        self.s.status, self.s.error, self.s.manual = "checking", None, True
        self.dll.win_sparkle_check_update_with_ui()

    def install(self):
        """Download, verify, install silently and reopen: WinSparkle's check-and-install."""
        self.s.status, self.s.error, self.s.manual = "downloading", None, True
        self.dll.win_sparkle_check_update_with_ui_and_install()

    def set_automatic(self, auto_check=None, auto_install=None):
        if auto_check is not None:
            self.dll.win_sparkle_set_automatic_check_for_updates(1 if auto_check else 0)
        if auto_install is not None:
            saved = self._settings()
            saved["auto_install"] = bool(auto_install)
            self.settings_file.write_text(json.dumps(saved), encoding="utf-8")

    # --- callbacks from WinSparkle's thread -----------------------------------------------------
    def found(self):
        self.s.available = self._latest_item()
        self.s.status = "available"
        self.log(f"WinSparkle: update found ({(self.s.available or {}).get('version')})")
        if not self.s.manual and self._auto_install():
            self.log("WinSparkle: installing it automatically (Download and install automatically is on)")
            threading.Timer(2.0, self.install).start()
        self.s.manual = False

    def not_found(self):
        self.s.status, self.s.available, self.s.manual = "uptodate", None, False

    def error(self):
        # WinSparkle's callback carries no detail; its own window says what went wrong (no internet,
        # or a download that failed its signature check: "The update is improperly signed").
        self.s.status, self.s.manual = "error", False
        self.s.error = ("The update couldn't be checked, downloaded or verified — see the Software Update "
                        "window for why. Nothing was installed.")
        self.log("WinSparkle: error during the update check")

    def cancelled(self):
        if self.s.status in ("checking", "downloading"):
            self.s.status = "available" if self.s.available else "idle"
        self.s.manual = False

    # --- helpers ---------------------------------------------------------------------------------
    def _settings(self) -> dict:
        try:
            return json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _auto_install(self) -> bool:
        return self._settings().get("auto_install", False) is True

    def _latest_item(self) -> dict | None:
        """The newest item of the appcast, for the Updates page (version and notes)."""
        try:
            with urllib.request.urlopen(urllib.request.Request(self.feed, headers={"User-Agent": "SRT Generator"}),
                                        timeout=15) as r:
                xml = r.read().decode("utf-8", "replace")
        except Exception:
            return None
        item = re.search(r"<item>(.*?)</item>", xml, re.S)
        if not item:
            return None
        body = item.group(1)
        version = re.search(r"<sparkle:shortVersionString>(.*?)<", body) or re.search(r"<sparkle:version>(.*?)<", body)
        notes = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>", body, re.S)
        return {"version": version.group(1) if version else "?", "notes_html": notes.group(1) if notes else ""}


def _first_run_defaults():
    """Daily checks on by default, without WinSparkle's "check automatically?" question. A value
    already there (the user's switch) is left alone."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY) as key:
            winreg.QueryValueEx(key, "CheckForUpdates")
            return False
    except OSError:
        return True


def start(dll_path: Path, version: str, data_dir: Path, log=print, on_shutdown=None) -> Backend | None:
    """Load WinSparkle, configure it and start it (a daily check). Returns the Backend, or None
    (logged) when WinSparkle isn't there or can't start. Call from the main thread."""
    if not dll_path.is_file():
        log(f"WinSparkle: not found at {dll_path}, updates are off")
        return None
    dll = ctypes.CDLL(str(dll_path))
    dll.win_sparkle_set_appcast_url.argtypes = [c_char_p]
    dll.win_sparkle_set_eddsa_public_key.argtypes = [c_char_p]
    dll.win_sparkle_set_eddsa_public_key.restype = c_int
    dll.win_sparkle_set_app_details.argtypes = [c_wchar_p, c_wchar_p, c_wchar_p]
    dll.win_sparkle_set_automatic_check_for_updates.argtypes = [c_int]
    dll.win_sparkle_get_automatic_check_for_updates.restype = c_int
    dll.win_sparkle_set_update_check_interval.argtypes = [c_int]
    dll.win_sparkle_get_last_check_time.restype = c_longlong
    for name in ("error", "did_find_update", "did_not_find_update", "update_cancelled",
                 "update_dismissed", "shutdown_request"):
        getattr(dll, f"win_sparkle_set_{name}_callback").argtypes = [VOID_CB]
    dll.win_sparkle_set_can_shutdown_callback.argtypes = [INT_CB]

    # A test feed (DECISIONS.md V4.7, "a real update"): appcast-url.txt in the data folder.
    override = data_dir / "appcast-url.txt"
    feed = override.read_text(encoding="utf-8").strip() if override.is_file() else FEED_URL
    if override.is_file():
        log(f"WinSparkle: TEST FEED from {override}: {feed}")
    backend = Backend(dll, version, feed, data_dir / "update-settings.json", log)

    dll.win_sparkle_set_app_details(COMPANY, APP, version)
    dll.win_sparkle_set_appcast_url(feed.encode())
    if not dll.win_sparkle_set_eddsa_public_key(EDDSA_PUBLIC_KEY.encode()):
        log("WinSparkle: the public key is invalid, updates are off")
        return None
    dll.win_sparkle_set_update_check_interval(CHECK_INTERVAL)
    if _first_run_defaults():
        dll.win_sparkle_set_automatic_check_for_updates(1)

    def shutdown():
        log("WinSparkle: the installer is running; quitting so it can replace the app")
        if on_shutdown:
            on_shutdown()

    callbacks = {
        "error": VOID_CB(backend.error), "did_find_update": VOID_CB(backend.found),
        "did_not_find_update": VOID_CB(backend.not_found), "update_cancelled": VOID_CB(backend.cancelled),
        "update_dismissed": VOID_CB(backend.cancelled), "shutdown_request": VOID_CB(shutdown),
    }
    for name, cb in callbacks.items():
        getattr(dll, f"win_sparkle_set_{name}_callback")(cb)
    can = INT_CB(lambda: 1)  # nothing to save: jobs are on disk
    dll.win_sparkle_set_can_shutdown_callback(can)
    _keep.extend([*callbacks.values(), can, dll])
    dll.win_sparkle_init()
    log(f"WinSparkle: started, feed {feed}, automatic checks "
        f"{bool(dll.win_sparkle_get_automatic_check_for_updates())} every {CHECK_INTERVAL} s")
    return backend


def cleanup():
    for obj in _keep:
        if isinstance(obj, ctypes.CDLL):
            try:
                obj.win_sparkle_cleanup()
            except Exception:
                pass

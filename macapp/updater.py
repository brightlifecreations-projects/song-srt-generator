"""Sparkle 2 auto-updates for the macOS app (Contents/Resources/updater.py, DECISIONS V4.0, V4.5).

Sparkle.framework ships in Contents/Frameworks and is loaded through pyobjc. The app's process
is the venv's Python, so NSBundle.mainBundle is not the .app; SPUUpdater is built directly with
the .app as its host bundle.

V4.5: the app's own Updates page is the UI. Instead of Sparkle's standard windows, AppDriver
(an SPUUserDriver written in Python — pyobjc calls Sparkle's reply blocks fine) records what
Sparkle reports, and Backend lets the server read that state and act: check, install (download,
install and relaunch, all through Sparkle), and the automatic check / download switches, which
Sparkle itself stores. Updates found by a scheduled check light the Updates item's dot instead
of popping a window. Every Sparkle call runs on the main thread.

Feed URL, public EdDSA key and the daily check interval are in Info.plist.
"""

import threading
from datetime import datetime, timezone
from pathlib import Path

import objc
from AppKit import NSApp, NSMenuItem
from Foundation import NSBundle, NSObject

CHOICE_INSTALL, CHOICE_DISMISS = 1, 2  # SPUUserUpdateChoice
_state = {}  # keeps the updater, driver and menu target alive for the app's lifetime


def _on_main(fn):
    """Run fn on the main thread and wait for it (Sparkle and AppKit want the main thread)."""
    from PyObjCTools import AppHelper

    if threading.current_thread() is threading.main_thread():
        return fn()
    done, out = threading.Event(), {}

    def run():
        try:
            out["value"] = fn()
        finally:
            done.set()
    AppHelper.callAfter(run)
    done.wait(10)
    return out.get("value")


class Status:
    """What the Updates page shows. Written from the main thread, read by the server."""

    def __init__(self):
        self.status, self.error, self.available = "idle", None, None
        self.progress = None          # (received bytes, expected bytes) while downloading
        self.reply = None             # Sparkle's pending "update found" reply block
        self.install_now = None       # Sparkle's "install the downloaded update now" block
        self.ready_reply = None       # Sparkle's pending "ready to install" reply block
        self.install_requested = False


def _item_info(item) -> dict:
    return {"version": str(item.displayVersionString() or item.versionString()),
            "notes_html": str(item.itemDescription() or "")}


class AppDriver(NSObject, protocols=[objc.protocolNamed("SPUUserDriver")]):
    """Sparkle's user driver, reporting to Status instead of showing windows."""

    def initWithStatus_(self, status):
        self = objc.super(AppDriver, self).init()
        self.s = status
        return self

    def showUpdatePermissionRequest_reply_(self, request, reply):
        reply(None)  # Info.plist already enables the daily check

    def showUserInitiatedUpdateCheckWithCancellation_(self, cancel):
        self.s.status, self.s.error = "checking", None

    def showUpdateFoundWithAppcastItem_state_reply_(self, item, state, reply):
        self.s.available, self.s.status, self.s.reply = _item_info(item), "available", reply
        if self.s.install_requested:
            self._install()

    def showUpdateReleaseNotesWithDownloadData_(self, data):
        pass

    def showUpdateReleaseNotesFailedToDownloadWithError_(self, error):
        pass

    def showUpdateNotFoundWithError_acknowledgement_(self, error, ack):
        self.s.status, self.s.available = "uptodate", None
        ack()

    def showUpdaterError_acknowledgement_(self, error, ack):
        self.s.status, self.s.error = "error", str(error.localizedDescription())
        self.s.install_requested = False
        ack()

    def showDownloadInitiatedWithCancellation_(self, cancel):
        self.s.status, self.s.progress = "downloading", (0, 0)

    def showDownloadDidReceiveExpectedContentLength_(self, length):
        self.s.progress = (0, int(length))

    def showDownloadDidReceiveDataOfLength_(self, length):
        got, total = self.s.progress or (0, 0)
        self.s.progress = (got + int(length), total)

    def showDownloadDidStartExtractingUpdate(self):
        self.s.status = "extracting"

    def showExtractionReceivedProgress_(self, progress):
        pass

    def showReadyToInstallAndRelaunch_(self, reply):
        if self.s.install_requested:
            self.s.status = "installing"
            reply(CHOICE_INSTALL)
        else:
            self.s.status, self.s.ready_reply = "ready", reply

    def showInstallingUpdateWithApplicationTerminated_retryTerminatingApplication_(self, terminated, retry):
        self.s.status = "installing"

    def showUpdateInstalledAndRelaunched_acknowledgement_(self, relaunched, ack):
        ack()

    def showUpdateInFocus(self):
        pass

    def dismissUpdateInstallation(self):
        if self.s.status in ("checking", "downloading", "extracting"):
            self.s.status = "idle"
        self.s.reply = None

    def _install(self):
        """Answer whichever reply Sparkle is waiting for with "install"."""
        self.s.status = "downloading"
        if self.s.ready_reply is not None:
            reply, self.s.ready_reply = self.s.ready_reply, None
            self.s.status = "installing"
            reply(CHOICE_INSTALL)
        elif self.s.reply is not None:
            reply, self.s.reply = self.s.reply, None
            reply(CHOICE_INSTALL)


class UpdaterDelegate(NSObject):
    """Updates Sparkle downloaded silently (automatic download on) install on quit; this offers
    "Update now" for them too."""

    def initWithStatus_(self, status):
        self = objc.super(UpdaterDelegate, self).init()
        self.s = status
        return self

    def updater_didFindValidUpdate_(self, updater, item):
        if self.s.available is None:
            self.s.available = _item_info(item)
            self.s.status = "available"

    def updater_willInstallUpdateOnQuit_immediateInstallationBlock_(self, updater, item, install_now):
        self.s.available, self.s.status, self.s.install_now = _item_info(item), "available", install_now
        return True


class Backend:
    """The server's handle on Sparkle (app.update_backend)."""

    def __init__(self, updater, status, driver, on_check=None):
        self.updater, self.s, self.driver, self.on_check = updater, status, driver, on_check

    def state(self) -> dict:
        def read():
            last = self.updater.lastUpdateCheckDate()
            return {"auto_check": bool(self.updater.automaticallyChecksForUpdates()),
                    "auto_install": bool(self.updater.automaticallyDownloadsUpdates()),
                    "last_checked": datetime.fromtimestamp(last.timeIntervalSince1970(), timezone.utc).isoformat()
                    if last else None}
        out = _on_main(read) or {}
        got, total = self.s.progress or (0, 0)
        return {**out, "status": self.s.status, "error": self.s.error, "available": self.s.available,
                "progress": got / total if total else None}

    def check(self):
        def run():
            if self.s.status == "available":
                return  # Sparkle is still holding the update found earlier
            if self.updater.canCheckForUpdates():
                self.s.status, self.s.error = "checking", None
                self.updater.checkForUpdates()
        _on_main(run)

    def install(self):
        def run():
            self.s.install_requested = True
            if self.s.install_now is not None:  # downloaded in the background: install it now
                block, self.s.install_now = self.s.install_now, None
                self.s.status = "installing"
                block()
            elif self.s.reply is not None or self.s.ready_reply is not None:
                self.driver._install()
            elif self.updater.canCheckForUpdates():  # nothing pending: check, then install
                self.s.status = "checking"
                self.updater.checkForUpdates()
        _on_main(run)

    def set_automatic(self, auto_check=None, auto_install=None):
        def run():
            if auto_check is not None:
                self.updater.setAutomaticallyChecksForUpdates_(bool(auto_check))
            if auto_install is not None:
                self.updater.setAutomaticallyDownloadsUpdates_(bool(auto_install))
        _on_main(run)


class SRTUpdateMenuTarget(NSObject):
    """Target of the "Check for Updates…" menu item: check, and show the Updates page."""

    def checkForUpdates_(self, sender):
        backend = _state["backend"]
        backend.check()
        if backend.on_check:
            backend.on_check()

    def validateMenuItem_(self, item):
        return True


def start(app_path: Path, log=print, on_check=None):
    """Start Sparkle for the .app at app_path and add "Check for Updates…" to the app menu.
    Returns the Backend, or None (and logs why) if Sparkle isn't bundled or can't start."""
    framework = app_path / "Contents" / "Frameworks" / "Sparkle.framework"
    if not framework.is_dir():
        log(f"Sparkle: not bundled at {framework}, updates are off")
        return None
    sparkle = {}
    objc.loadBundle("Sparkle", sparkle, bundle_path=str(framework))
    host = NSBundle.bundleWithPath_(str(app_path))
    status = Status()
    driver = AppDriver.alloc().initWithStatus_(status)
    delegate = UpdaterDelegate.alloc().initWithStatus_(status)
    updater = sparkle["SPUUpdater"].alloc().initWithHostBundle_applicationBundle_userDriver_delegate_(
        host, host, driver, delegate)
    # pyobjc has no metadata for Sparkle's NSError** argument here, so only the BOOL comes back.
    started = updater.startUpdater_(None)
    if not (started[0] if isinstance(started, tuple) else started):
        log(f"Sparkle: couldn't start: {started}")
        return None
    backend = Backend(updater, status, driver, on_check)
    _state.update(updater=updater, driver=driver, delegate=delegate, backend=backend,
                  target=SRTUpdateMenuTarget.alloc().init())

    app_menu = NSApp.mainMenu().itemAtIndex_(0).submenu() if NSApp.mainMenu() else None
    if app_menu is not None:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Check for Updates…", "checkForUpdates:", "")
        item.setTarget_(_state["target"])
        app_menu.insertItem_atIndex_(item, 1)  # right after "About"
    log(f"Sparkle: started, feed {updater.feedURL()}, automatic checks "
        f"{bool(updater.automaticallyChecksForUpdates())} every {updater.updateCheckInterval():.0f} s")
    return backend

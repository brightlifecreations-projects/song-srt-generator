"""Sparkle 2 auto-updates for the macOS app (Contents/Resources/updater.py, DECISIONS V4.0).

Sparkle.framework ships in Contents/Frameworks and is loaded through pyobjc. The app's
process is the venv's Python, so NSBundle.mainBundle is not the .app and
SPUStandardUpdaterController (which always uses the main bundle) can't find SUFeedURL.
This builds the same two objects it wraps, with the .app as the host bundle:
SPUStandardUserDriver (Sparkle's own update windows) and SPUUpdater.

Feed URL, public EdDSA key and the daily check interval are in Info.plist. Must run on
the main thread.
"""

from pathlib import Path

import objc
from AppKit import NSApp, NSMenuItem
from Foundation import NSBundle, NSObject

_state = {}  # keeps the updater and the menu target alive for the app's lifetime


class SRTUpdateMenuTarget(NSObject):
    """Target of the "Check for Updates…" menu item."""

    def checkForUpdates_(self, sender):
        _state["updater"].checkForUpdates()

    def validateMenuItem_(self, item):
        return bool(_state["updater"].canCheckForUpdates())


def start(app_path: Path, log=print) -> bool:
    """Start Sparkle for the .app at app_path and add "Check for Updates…" to the app menu.
    Returns False (and logs why) if Sparkle isn't bundled or can't start; the app runs on."""
    framework = app_path / "Contents" / "Frameworks" / "Sparkle.framework"
    if not framework.is_dir():
        log(f"Sparkle: not bundled at {framework}, updates are off")
        return False
    sparkle = {}
    objc.loadBundle("Sparkle", sparkle, bundle_path=str(framework))
    host = NSBundle.bundleWithPath_(str(app_path))
    driver = sparkle["SPUStandardUserDriver"].alloc().initWithHostBundle_delegate_(host, None)
    updater = sparkle["SPUUpdater"].alloc().initWithHostBundle_applicationBundle_userDriver_delegate_(
        host, host, driver, None)
    # pyobjc has no metadata for Sparkle's NSError** argument here, so only the BOOL comes back.
    started = updater.startUpdater_(None)
    if not (started[0] if isinstance(started, tuple) else started):
        log(f"Sparkle: couldn't start: {started}")
        return False
    _state.update(updater=updater, driver=driver, target=SRTUpdateMenuTarget.alloc().init())

    app_menu = NSApp.mainMenu().itemAtIndex_(0).submenu() if NSApp.mainMenu() else None
    if app_menu is not None:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Check for Updates…", "checkForUpdates:", "")
        item.setTarget_(_state["target"])
        app_menu.insertItem_atIndex_(item, 1)  # right after "About"
    log(f"Sparkle: started, feed {updater.feedURL()}, automatic checks "
        f"{bool(updater.automaticallyChecksForUpdates())} every {updater.updateCheckInterval():.0f} s")
    return True

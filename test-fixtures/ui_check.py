"""Script-driven UI check in a WKWebView window (the Mac app's engine). Test fixture, not shipped.

    python test-fixtures/ui_check.py <base-url> <file-to-drop[,more files]> <steps-module.js> [screenshot-dir]

Opens the app, runs the JavaScript checks in <steps-module.js> (an async function body that gets
`file` — the dropped File — and helpers `sleep`, `until`, `log`), prints each log line and the
final result, then closes. Exit 1 if the script throws.

The script can also call `await snap(name)` (a PNG of the window's web view, saved in the
screenshot dir) and `await resize(width, height)` (the window's size in points).
"""

import base64
import json
import sys
import threading
from pathlib import Path

import webview

SHOTS = Path(sys.argv[4]) if len(sys.argv) > 4 else Path(".")


class Api:
    """Called from the steps script: screenshots and window size."""

    def __init__(self):
        self.window = None

    def snap(self, name):
        from AppKit import NSBitmapImageRep, NSPNGFileType
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView

        done, result = threading.Event(), {}

        def handler(image, error):
            if image is not None:
                rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                png = rep.representationUsingType_properties_(NSPNGFileType, None)
                SHOTS.mkdir(parents=True, exist_ok=True)
                png.writeToFile_atomically_(str(SHOTS / f"{name}.png"), True)
                result["ok"] = True
            done.set()

        def take():
            view = BrowserView.instances[self.window.uid].webview
            view.takeSnapshotWithConfiguration_completionHandler_(None, handler)

        AppHelper.callAfter(take)
        done.wait(15)
        return bool(result.get("ok"))

    def resize(self, width, height):
        self.window.resize(int(width), int(height))
        return True

HELPERS = """
window.__log = [];
const snap = name => window.pywebview.api.snap(name);
const resize = async (w, h) => { await window.pywebview.api.resize(w, h); await new Promise(r => setTimeout(r, 700)); };
const log = (...a) => window.__log.push(a.map(x => typeof x === "string" ? x : JSON.stringify(x)).join(" "));
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 600000, what = "condition") {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { const v = fn(); if (v) return v; await sleep(250); }
  throw new Error("timed out waiting for " + what);
}
"""


def main(argv):
    base, media, steps = argv[:3]
    media_files = [Path(m) for m in media.split(",")]  # several files: `files`; the first is `file`
    data = [{"name": m.name, "b64": base64.b64encode(m.read_bytes()).decode()} for m in media_files]
    body = Path(steps).read_text(encoding="utf-8")
    api = Api()
    window = webview.create_window("UI check", base, width=1440, height=960, js_api=api)
    api.window = window
    outcome = {}

    def run():
        import time
        time.sleep(3)
        window.evaluate_js(HELPERS + f"""
          window.__done = null;
          (async () => {{
            const files = {json.dumps(data)}.map(f =>
              new File([Uint8Array.from(atob(f.b64), c => c.charCodeAt(0))], f.name));
            const file = files[0];
            {body}
          }})().then(r => window.__done = {{ok: true, result: r}},
                     e => window.__done = {{ok: false, error: String(e && (e.message + ' | ' + e.stack) || e)}});
        """)
        while True:
            time.sleep(1)
            done = window.evaluate_js("window.__done")
            for line in window.evaluate_js("window.__log.splice(0)") or []:
                print("  ", line, flush=True)
            if done:
                outcome.update(done)
                break
        window.destroy()

    threading.Thread(target=run, daemon=True).start()
    webview.start()
    print("RESULT", json.dumps(outcome, ensure_ascii=False))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

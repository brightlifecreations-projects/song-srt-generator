"""Script-driven UI check in a WKWebView window (the Mac app's engine). Test fixture, not shipped.

    python test-fixtures/ui_check.py <base-url> <file-to-drop> <steps-module.js>

Opens the app, runs the JavaScript checks in <steps-module.js> (an async function body that gets
`file` — the dropped File — and helpers `sleep`, `until`, `log`), prints each log line and the
final result, then closes. Exit 1 if the script throws.
"""

import base64
import json
import sys
import threading
from pathlib import Path

import webview

HELPERS = """
window.__log = [];
const log = (...a) => window.__log.push(a.map(x => typeof x === "string" ? x : JSON.stringify(x)).join(" "));
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 600000, what = "condition") {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { const v = fn(); if (v) return v; await sleep(250); }
  throw new Error("timed out waiting for " + what);
}
"""


def main(argv):
    base, media, steps = argv
    data = base64.b64encode(Path(media).read_bytes()).decode()
    body = Path(steps).read_text(encoding="utf-8")
    window = webview.create_window("UI check", base, width=1400, height=1000)
    outcome = {}

    def run():
        import time
        time.sleep(3)
        window.evaluate_js(HELPERS + f"""
          window.__done = null;
          (async () => {{
            const bytes = Uint8Array.from(atob({json.dumps(data)}), c => c.charCodeAt(0));
            const file = new File([bytes], {json.dumps(Path(media).name)});
            {body}
          }})().then(r => window.__done = {{ok: true, result: r}},
                     e => window.__done = {{ok: false, error: String(e && e.stack || e)}});
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

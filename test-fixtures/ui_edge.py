"""Script-driven UI check in Microsoft Edge (Windows), through the DevTools protocol. Test fixture,
not shipped. The Windows counterpart of ui_check.py (WKWebView, Mac).

    .venv\\Scripts\\python test-fixtures\\ui_edge.py <base-url> <file[,file…]> <steps.js> [out-dir]

Opens the app in a headless Edge (the browser Start.bat opens on most Windows PCs), puts the files
in the page as File objects (`files`), and runs <steps.js> as an async function body with helpers:

    sleep(ms), until(fn, ms, what), log(...)       as in ui_check.py
    await snap(name)                               PNG of the page in out-dir
    await resize(width, height)                    the window size
    await download(clickFn)                        clicks a download link and returns the file
                                                   the browser saved, as text (decoded UTF-8)
                                                   plus its raw bytes as hex in .hex

window.confirm() is answered OK. Prints every log line; exit 1 if the script throws.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)), None)


def free_port() -> int:
    """A fresh DevTools port per run: a leftover Edge on a fixed port would be attached to instead."""
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


PORT = free_port()

HELPERS = r"""
const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = (...a) => console.log(a.map(x => typeof x === "string" ? x : JSON.stringify(x)).join(" "));
const until = async (fn, ms = 10000, what = "condition") => {
  const t0 = Date.now();
  for (;;) { const v = await fn(); if (v) return v; if (Date.now() - t0 > ms) throw new Error("timed out waiting for " + what); await sleep(100); }
};
window.__cb = window.__cb || {}; let __n = 0;
const py = (op, extra = {}) => new Promise(r => { const id = ++__n; window.__cb[id] = r; window.__py(JSON.stringify({ id, op, ...extra })); });
const snap = name => py("snap", { name });
const resize = (width, height) => py("resize", { width, height });
const download = async click => { await py("arm"); click(); return py("download"); };
"""


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    def send(self, method, params=None):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        return self.n

    def call(self, method, params=None, handle=None):
        mid = self.send(method, params)
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if handle:
                handle(msg)


def main():
    base, files, steps = sys.argv[1], [f for f in sys.argv[2].split(",") if f], Path(sys.argv[3])
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else Path(tempfile.mkdtemp(prefix="ui-edge-"))
    out.mkdir(parents=True, exist_ok=True)
    downloads = out / "downloads"
    shutil.rmtree(downloads, ignore_errors=True)
    downloads.mkdir()
    # UI_ATTACH_PORT: drive the Windows app's own window (Edge WebView2 started with
    # WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>) instead of a headless Edge.
    attach = os.environ.get("UI_ATTACH_PORT")
    port = int(attach) if attach else PORT
    profile = tempfile.mkdtemp(prefix="edge-profile-")
    proc = None if attach else subprocess.Popen(
        [EDGE, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", "--disable-extensions",
         "--disable-component-extensions-with-background-pages", "--disable-sync",
         "--window-size=1400,1000", "--autoplay-policy=no-user-gesture-required",
         "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        if attach:  # the app's one window
            page = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5))
                        if t["type"] == "page")
        else:  # a tab of our own: extensions or sync can open tabs of theirs in a fresh profile
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
            page = json.load(urllib.request.urlopen(req, timeout=10))
        browser_ws = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version"))["webSocketDebuggerUrl"]
        # The download setting lasts only while the session that made it stays connected (headless
        # Edge refuses downloads otherwise), so this connection stays open for the whole run.
        bws = connect(browser_ws, max_size=None)
        Page(bws).call("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(downloads),
                                                       "eventsEnabled": False})
        with bws, connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            p = Page(ws)
            for domain in ("Page", "Runtime"):
                p.call(f"{domain}.enable")
            p.call("Runtime.addBinding", {"name": "__py"})
            p.call("Page.navigate", {"url": base})
            time.sleep(2.5)
            p.call("Runtime.addBinding", {"name": "__py"})
            # The files, as File objects in the page (chunks, so large videos fit in one message each).
            p.call("Runtime.evaluate", {"expression": "window.__files = []; window.__parts = [];"})
            # UI_GLOBALS='{"__mock": "http://…"}' puts values on window for the steps script.
            p.call("Runtime.evaluate", {"expression": f"Object.assign(window, {os.environ.get('UI_GLOBALS') or '{}'})"})
            for f in files:
                data = Path(f).read_bytes()
                p.call("Runtime.evaluate", {"expression": "window.__parts = [];"})
                for i in range(0, len(data), 4_000_000):
                    chunk = base64.b64encode(data[i:i + 4_000_000]).decode()
                    p.call("Runtime.evaluate", {"expression":
                           f"window.__parts.push(Uint8Array.from(atob('{chunk}'), c => c.charCodeAt(0)));"})
                mime = {".mp4": "video/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".txt": "text/plain"}.get(
                    Path(f).suffix.lower(), "application/octet-stream")
                p.call("Runtime.evaluate", {"expression":
                       f"window.__files.push(new File(window.__parts, {json.dumps(Path(f).name)}, {{type: '{mime}'}}));"})
            state = {"armed": set(), "failed": False}

            def reply(cb_id, value):
                p.send("Runtime.evaluate", {"expression": f"window.__cb[{cb_id}]({json.dumps(value)})"})

            def handle(msg):
                method, params = msg.get("method"), msg.get("params", {})
                if method == "Runtime.consoleAPICalled":
                    text = " ".join(str(a.get("value", a.get("description", ""))) for a in params.get("args", []))
                    print(f"[{params.get('type')}] {text}", flush=True)
                elif method == "Runtime.exceptionThrown":
                    print("[exception]", params["exceptionDetails"].get("exception", {}).get("description"), flush=True)
                elif method == "Page.javascriptDialogOpening":
                    print(f"[dialog] {params.get('message')}", flush=True)
                    p.send("Page.handleJavaScriptDialog", {"accept": True})
                elif method == "Runtime.bindingCalled" and params.get("name") == "__py":
                    req = json.loads(params["payload"])
                    op = req["op"]
                    if op == "snap":
                        shot = p.call("Page.captureScreenshot", {"format": "png"}, handle=handle)
                        (out / f"{req['name']}.png").write_bytes(base64.b64decode(shot["data"]))
                        reply(req["id"], True)
                    elif op == "resize":
                        p.call("Emulation.setDeviceMetricsOverride", {"width": req["width"], "height": req["height"],
                                                                      "deviceScaleFactor": 1, "mobile": False},
                               handle=handle)
                        reply(req["id"], True)
                    elif op == "arm":
                        # Edge overwrites a file of the same name, so earlier downloads move aside first.
                        keep = downloads / f"earlier-{time.time_ns()}"
                        for f in os.listdir(downloads):
                            if (downloads / f).is_file():
                                keep.mkdir(exist_ok=True)
                                os.replace(downloads / f, keep / f)
                        state["armed"] = set(os.listdir(downloads))
                        reply(req["id"], True)
                    elif op == "download":
                        t0 = time.time()
                        while time.time() - t0 < 15:
                            new = [f for f in os.listdir(downloads) if f not in state["armed"]
                                   and not f.endswith((".crdownload", ".tmp"))]
                            if new:
                                time.sleep(0.3)
                                data = (downloads / new[0]).read_bytes()
                                reply(req["id"], {"name": new[0], "text": data.decode("utf-8", "replace"),
                                                  "hex": data[:8].hex(), "crlf": data.count(b"\r\n"),
                                                  "lf": data.count(b"\n"), "bom": data.startswith(b"\xef\xbb\xbf")})
                                return
                            time.sleep(0.1)
                        reply(req["id"], None)

            body = steps.read_text(encoding="utf-8")
            expr = ("(async () => { const files = window.__files; " + HELPERS + "\n" + body +
                    "\n})().then(r => 'OK ' + JSON.stringify(r ?? null), e => 'FAIL ' + (e && e.stack || e))")
            result = p.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "timeout": 3_600_000},
                            handle=handle)
            value = result.get("result", {}).get("value", str(result))
            print(value, flush=True)
            return 0 if str(value).startswith("OK") else 1
    finally:
        # Edge starts a tree of helper processes; terminate() would only stop the first one.
        if proc:  # never the app's own window
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

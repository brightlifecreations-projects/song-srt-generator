"""Look into the Windows app's own window (Edge WebView2) from a test. Test fixture, not shipped.

    python test-fixtures/webview_peek.py <port> [js-expression] [screenshot.png]

The app must be started with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>.
Prints the page URL and the expression's value (default: the page title and visible text).
"""

import base64
import json
import sys
import urllib.request

from websockets.sync.client import connect

port = int(sys.argv[1])
expr = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else \
    "JSON.stringify({title: document.title, text: document.body.innerText.slice(0, 600)})"
shot = sys.argv[3] if len(sys.argv) > 3 else None
page = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5))
            if t["type"] == "page")
print("url:", page["url"][:120])
with connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            print(m["result"]["result"].get("value", m["result"]))
            break
    if shot:
        ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 2:
                open(shot, "wb").write(base64.b64decode(m["result"]["data"]))
                break

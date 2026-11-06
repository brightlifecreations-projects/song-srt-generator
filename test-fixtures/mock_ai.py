"""A stand-in AI provider for UI tests (OpenAI-compatible Chat Completions). Test fixture, not shipped.

    python test-fixtures/mock_ai.py <port>

Point the app's Custom provider at http://127.0.0.1:<port>/<mode>/v1 (a local Custom server needs
no key). Modes, chosen by the URL:

    ok         final check: fixes two words (a capital and a one-letter spelling change) and keeps
               the cuts; AI polish: returns the draft unchanged
    badjson    answers with text that isn't JSON
    wordcount  final check: turns one word into two (must be refused: word count changed)

Every request is appended to requests.jsonl next to this file's working directory.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def final_check_answer(payload: dict, mode: str) -> dict:
    tokens = payload["tokens"]
    changes = []
    words = [t for t in tokens if t["t"].isalpha() and t["t"].islower() and len(t["t"]) >= 5]
    if mode == "wordcount" and words:
        changes.append({"i": words[0]["i"], "t": words[0]["t"] + " extra"})
    elif words:
        first = words[0]
        changes.append({"i": first["i"], "t": first["t"][:-2] + first["t"][-1] + first["t"][-2]})  # a typo-style fix
        if len(words) > 1:
            changes.append({"i": words[1]["i"], "t": words[1]["t"].capitalize()})
    return {"tokens": changes, "cue_starts": payload["cue_starts"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # model list
        self.answer({"data": [{"id": "mock-model"}]})

    def do_POST(self):
        mode = self.path.strip("/").split("/")[0]
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        with open("requests.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"mode": mode, "system": system[:80], "user": json.loads(user)}, ensure_ascii=False) + "\n")
        if mode == "badjson":
            content = "Sure! Here are the captions you asked for."
        elif "final check" in system:
            content = json.dumps(final_check_answer(json.loads(user), mode), ensure_ascii=False)
        else:  # AI polish of a draft: same lines back
            content = json.dumps({"lines": [line["text"] for line in json.loads(user)["lines"]]}, ensure_ascii=False)
        self.answer({"choices": [{"message": {"content": content}}]})

    def answer(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()

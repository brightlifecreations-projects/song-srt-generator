"""AI polish adapters against a mocked provider (no network, no real keys).

    .venv/bin/python -m unittest discover -s tests -v

One local HTTP server speaks all three API formats (Anthropic Messages via the official SDK,
OpenAI Chat Completions, Gemini generateContent). Each test sets what it answers: a good reply,
the wrong number of lines, invalid JSON, a refused key, a rate limit, or a reply too slow for the
timeout; "offline" points the adapter at a port nothing listens on.
"""

import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_polish  # noqa: E402

DRAFT = ["Kal ke bord exam ke liye agar bleaching powder ka", "Yahi likh ke aana hai kal paper mein."]
FIXED = ["Kal ke board exam ke liye agar bleaching powder ka", "Yahi likh ke aana hai kal [paper?] mein."]


class Mock:
    scenario = "ok"
    requests: list = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Mock.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        s = Mock.scenario
        if s == "slow":
            time.sleep(3)
        if s == "badkey":
            return self._send(401, {"type": "error", "error": {"type": "authentication_error",
                                                               "message": "invalid x-api-key"}})
        if s == "ratelimit":
            return self._send(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
        text = {"ok": json.dumps({"lines": FIXED}),
                "slow": json.dumps({"lines": FIXED}),
                "mismatch": json.dumps({"lines": FIXED[:1]}),
                "badjson": "Sure! Here are the lines: Kal ke board exam…",
                "rewrite": json.dumps({"lines": ["Tomorrow's board exam needs this new bleaching powder reaction.",
                                                 FIXED[1]]})}[s]
        if self.path.startswith("/v1/messages"):
            return self._send(200, {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
                                    "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                    "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10}})
        if self.path.endswith("/chat/completions"):
            return self._send(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})
        if ":generateContent" in self.path:
            return self._send(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})
        self._send(404, {"error": "unknown path"})

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls._get_key = ai_polish.get_key
        ai_polish.get_key = lambda provider: "sk-test-not-a-real-key"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        ai_polish.get_key = cls._get_key

    def setUp(self):
        Mock.scenario = "ok"
        Mock.requests = []
        self._timeout = ai_polish.config.AI_TIMEOUT_SECONDS
        ai_polish.config.AI_TIMEOUT_SECONDS = 1.5

    def tearDown(self):
        ai_polish.config.AI_TIMEOUT_SECONDS = self._timeout

    # Each provider format, pointed at the mock (Custom = any OpenAI-compatible server).
    def settings(self, fmt: str, offline: bool = False) -> dict:
        base = "http://127.0.0.1:9" if offline else self.url  # port 9: nothing listens
        provider = {"anthropic": "anthropic", "openai": "custom", "gemini": "gemini"}[fmt]
        ai_polish.PROVIDERS["anthropic"]["base_url"] = base
        ai_polish.PROVIDERS["gemini"]["base_url"] = base
        models = {code: p["model"] or "test-model" for code, p in ai_polish.PROVIDERS.items()}
        return {"enabled": True, "provider": provider, "models": models, "custom_base_url": base}

    def polish(self, fmt, **kw):
        return ai_polish.polish(DRAFT, self.settings(fmt, **kw), section="reels", script="hinglish",
                                language="Hindi", topic_words=["NCERT"], correction_rows={},
                                spellings={}, doubtful=[{"line": 1, "word": "bord", "why": "low confidence"}])

    def check_all(self, scenario, kind, offline=False):
        for fmt in ("anthropic", "openai", "gemini"):
            with self.subTest(fmt=fmt):
                Mock.scenario = scenario
                with self.assertRaises(ai_polish.PolishError) as e:
                    self.polish(fmt, offline=offline)
                self.assertEqual(e.exception.kind, kind, str(e.exception))

    def test_good_reply(self):
        for fmt in ("anthropic", "openai", "gemini"):
            with self.subTest(fmt=fmt):
                result = self.polish(fmt)
                self.assertEqual(result["lines"], FIXED)
                kinds = [(m["original"], m["replacement"], m["kind"]) for m in result["marks"]]
                self.assertEqual(kinds, [("bord", "board", "ai"), ("paper", "[paper?]", "ai-unsure")])
                m = result["marks"][0]
                self.assertEqual(FIXED[0][m["start"]:m["end"]], "board")

    def test_line_count_mismatch(self):
        self.check_all("mismatch", "mismatch")

    def test_invalid_json(self):
        self.check_all("badjson", "invalid")

    def test_offline(self):
        self.check_all("ok", "offline", offline=True)

    def test_bad_key(self):
        self.check_all("badkey", "auth")

    def test_rate_limit(self):
        self.check_all("ratelimit", "rate_limit")

    def test_timeout(self):
        self.check_all("slow", "timeout")

    def test_rewritten_line_is_refused(self):
        """A line the AI rephrased instead of correcting stays as it was."""
        Mock.scenario = "rewrite"
        result = self.polish("openai")
        self.assertEqual(result["lines"][0], DRAFT[0])
        self.assertEqual(result["skipped_lines"], [0])

    def test_request_contents_and_key_placement(self):
        """What's sent: the lines and context, never audio; the key only in an auth header."""
        for fmt in ("anthropic", "openai", "gemini"):
            Mock.requests = []
            self.polish(fmt)
            req = Mock.requests[-1]
            self.assertNotIn("sk-test-not-a-real-key", req["path"])
            sent = json.dumps(req["body"])
            self.assertIn("bleaching powder", sent)
            self.assertIn("NCERT", sent)
            headers = {k.lower(): v for k, v in req["headers"].items()}
            # anthropic SDK 2.x sends an API key as "Authorization: Bearer …" (API version 2026-10-21)
            key_header = {"anthropic": "authorization", "openai": "authorization", "gemini": "x-goog-api-key"}[fmt]
            self.assertIn("sk-test-not-a-real-key", headers[key_header])

    def test_anthropic_fallback_only_for_supported_models(self):
        s = self.settings("anthropic")
        ai_polish.polish(DRAFT, s, section="reels", script="hinglish", language=None, topic_words=[],
                         correction_rows={}, spellings={}, doubtful=[])
        self.assertEqual(Mock.requests[-1]["body"].get("fallbacks"), "default")  # claude-opus-5-5
        s["models"]["anthropic"] = "claude-haiku-5-5"
        ai_polish.polish(DRAFT, s, section="reels", script="hinglish", language=None, topic_words=[],
                         correction_rows={}, spellings={}, doubtful=[])
        self.assertNotIn("fallbacks", Mock.requests[-1]["body"])


if __name__ == "__main__":
    unittest.main()

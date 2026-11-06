"""Preview = export (V4.6): what the Review panel shows is exactly what is downloaded.

    .venv/Scripts/python -m unittest tests.test_export -v      (Windows)
    .venv/bin/python -m unittest tests.test_export -v          (Mac)

A finished job is built from synthetic aligned words (no models), registered with the real server,
and the server is run on a spare port. For each caption mode, with and without Review edits, the
cues the panel gets (GET /api/jobs/<id>, the same list it renders) are compared with the
downloaded SRT, original.srt / edited.srt and cues.json / edited-cues.json: count, text, start and
end to the millisecond. The bytes are checked too: UTF-8 without BOM, CRLF line endings only.
"""

import json
import logging
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = tempfile.mkdtemp(prefix="srt-export-test-")
import os  # noqa: E402

os.environ["SRT_DATA_DIR"] = TMP  # corrections.md etc. go to a throwaway folder
import app  # noqa: E402
import captions  # noqa: E402
import pipeline  # noqa: E402

SRT_TIME = re.compile(r"^(\d\d):(\d\d):(\d\d),(\d{3}) --> (\d\d):(\d\d):(\d\d),(\d{3})$")


def parse_srt(data: bytes) -> list[dict]:
    """A strict parser: numbered from 1, one text line, CRLF only."""
    text = data.decode("utf-8")
    blocks = [b for b in text.split("\r\n\r\n") if b.strip()]
    cues = []
    for n, block in enumerate(blocks, start=1):
        lines = block.strip("\r\n").split("\r\n")
        assert len(lines) == 3, f"cue {n} isn't 3 lines: {lines!r}"
        assert lines[0] == str(n), f"cue {n} is numbered {lines[0]}"
        m = SRT_TIME.match(lines[1])
        assert m, lines[1]
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": lines[2]})
    return cues


def ms(cues):
    return [(round(c["start"] * 1000), round(c["end"] * 1000), c["text"]) for c in cues]


def words_of(text: str) -> list[dict]:
    out, t = [], 0.137
    for li, line in enumerate(text.split("\n")):
        for w in line.split():
            # odd times on purpose, so rounding problems would show
            out.append({"word": w, "start": round(t, 3), "end": round(t + 0.2617, 3), "line": li,
                        "speaker": None, "probability": 0.9})
            t += 0.3123
        t += 0.8
    return out


class ExportMatchesPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        import uvicorn
        cls.server = uvicorn.Server(uvicorn.Config(app.app, host="127.0.0.1", port=cls.port, log_level="error"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/api/meta", timeout=1)
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path) -> bytes:
        with urllib.request.urlopen(self.url(path), timeout=10) as r:
            return r.read()

    def put(self, path, body) -> dict:
        req = urllib.request.Request(self.url(path), data=json.dumps(body).encode(), method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def make_job(self, mode: str) -> tuple[str, dict]:
        job_dir = Path(tempfile.mkdtemp(prefix="job-", dir=TMP))
        log = logging.getLogger(f"test.{job_dir.name}")
        words = words_of("Sarah said “twenty five” times, she’d had ENOUGH of it\n"
                         "आज मैं बहुत खुश हूँ और कल भी रहूँगा\n"
                         "seventy percent of the time it works every time")
        tokens = captions.offline_cleanup(captions.make_tokens(words, "en"), words)
        ctx = {"job_dir": job_dir, "log": log, "stem": "गाना – test song", "source_file": "गाना.mp3",
               "section": "reels", "isolate": "off", "music_detected": None, "duration": 30.0,
               "timings": {}, "source_path": job_dir / "input.wav", "vocals_path": None,
               "input_audio": job_dir / "input.wav", "video_format": "vertical", "warnings": [],
               "timeline": {"words": words, "tokens": tokens, "lines": [], "speakers": [], "dropped_lines": [],
                            "numbers_language": "en", "script_text": None, "ai": {}}}
        rules = pipeline.caption_rules("reels", "vertical", mode, language="en")
        out = pipeline.write_outputs(ctx, pipeline.build_layout(ctx, rules, None), rules)
        job_id = f"test{mode}{int(time.time() * 1000) % 100000}"
        app.JOBS[job_id] = {"status": "done", "mode": "lyrics", "section": "reels", "isolate": "off",
                            "ctx": ctx, "cues": out["cues"], "srt_path": str(out["srt_path"]),
                            "original_srt_path": str(out["original_srt_path"]), "srt_name": out["srt_path"].name,
                            "max_chars": out["max_chars"], "caption_mode": mode, "warnings": [],
                            "upload_dir": str(job_dir / "none")}
        return job_id, ctx

    def check_bytes(self, data: bytes):
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "BOM")
        self.assertNotIn(b"\r\r", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "a bare LF")
        data.decode("utf-8")

    def assert_same(self, preview, *others, label=""):
        for name, other in others:
            self.assertEqual(len(preview), len(other), f"{label} count vs {name}")
            self.assertEqual(ms(preview), ms(other), f"{label} cues vs {name}")

    def run_mode(self, mode):
        job_id, ctx = self.make_job(mode)
        job_dir = ctx["job_dir"]
        state = json.loads(self.get(f"/api/jobs/{job_id}"))
        preview = state["cues"]  # exactly what Workspace.jsx hands the Review panel
        download = self.get(f"/api/jobs/{job_id}/srt")
        self.check_bytes(download)
        cues_json = json.loads((job_dir / "cues.json").read_text(encoding="utf-8"))["cues"]
        self.assert_same(preview, ("download", parse_srt(download)),
                         ("original.srt", parse_srt((job_dir / "original.srt").read_bytes())),
                         ("<name>.srt", parse_srt((job_dir / f"{ctx['stem']}.srt").read_bytes())),
                         ("cues.json", cues_json), label=f"{mode} generated:")
        # Times are already whole milliseconds, so the panel's r3() rounding changes nothing.
        for c in preview:
            self.assertEqual(c["start"], round(c["start"], 3))
            self.assertEqual(c["end"], round(c["end"], 3))
        for a, b in zip(preview, preview[1:]):
            self.assertLessEqual(a["end"], b["start"], "overlap")

        # Review edits, the way the panel sends them (sorted, single-spaced, rounded to ms).
        edited = [{"start": c["start"], "end": c["end"], "text": c["text"], **({"ai": c["ai"]} if c.get("ai") else {})}
                  for c in preview]
        edited[0]["text"] = "Sara said"
        edited[1]["end"] = round(edited[1]["end"] - 0.1, 3)
        edited.insert(2, {"start": edited[1]["end"], "end": edited[2]["start"], "text": "नया “cue”"})
        if edited[2]["end"] <= edited[2]["start"]:
            edited[2]["end"] = round(edited[2]["start"] + 0.001, 3)
        del edited[-1]
        self.put(f"/api/jobs/{job_id}/cues", {"cues": edited})
        state = json.loads(self.get(f"/api/jobs/{job_id}"))
        self.assertTrue(state["edited"])
        download = self.get(f"/api/jobs/{job_id}/srt")
        self.check_bytes(download)
        self.assert_same(edited, ("download", parse_srt(download)),
                         ("edited.srt", parse_srt((job_dir / "edited.srt").read_bytes())),
                         ("edited-cues.json", json.loads((job_dir / "edited-cues.json").read_text(encoding="utf-8"))),
                         ("job state", state["edited_cues"]), label=f"{mode} edited:")
        original = self.get(f"/api/jobs/{job_id}/srt?original=1")
        self.assert_same(preview, ("original download", parse_srt(original)), label=f"{mode} original:")

        # An empty cue is refused, not silently dropped (that made the count differ before 4.6).
        req = urllib.request.Request(self.url(f"/api/jobs/{job_id}/cues"), method="PUT",
                                     data=json.dumps({"cues": [*edited, {"start": 40, "end": 41, "text": "  "}]}).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(e.exception.code, 400)
        self.assert_same(edited, ("download after a refused save", parse_srt(self.get(f"/api/jobs/{job_id}/srt"))))
        return preview

    def test_line_by_line(self):
        cues = self.run_mode("line")
        self.assertTrue(any("25" in c["text"] for c in cues))

    def test_word_by_word(self):
        cues = self.run_mode("word")
        self.assertTrue(all(len(c["text"].split()) == 1 for c in cues))

    def test_switching_mode_rebuilds_the_files(self):
        job_id, ctx = self.make_job("line")
        out = pipeline.relayout(ctx, "word")
        app.JOBS[job_id]["cues"] = out["cues"]
        self.assert_same(out["cues"], ("download", parse_srt(self.get(f"/api/jobs/{job_id}/srt"))),
                         ("original.srt", parse_srt((ctx["job_dir"] / "original.srt").read_bytes())),
                         label="relayout:")
        self.assertFalse((ctx["job_dir"] / "edited.srt").exists())

    def test_unicode_filename_header(self):
        job_id, _ = self.make_job("line")
        with urllib.request.urlopen(self.url(f"/api/jobs/{job_id}/srt"), timeout=10) as r:
            disposition = r.headers["content-disposition"]
        self.assertIn("filename*=utf-8''", disposition.lower())


if __name__ == "__main__":
    unittest.main()

"""Real jobs, fully logged (V4.9): job.json and log.txt list every pipeline step and external command;
no key, key-store value or home path appears in any log or the report zip.

Runs the real models on the sample clip (about 40 s); skipped when the models aren't installed."""

import importlib.util
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_polish  # noqa: E402
import joblog  # noqa: E402
import pipeline  # noqa: E402
import report  # noqa: E402
import review  # noqa: E402

SECRET = "sk-proj-TESTKEYSTOREVALUE0123456789abcdef"
MODELS = (pipeline.MODELS_DIR / "htdemucs.yaml").exists() and (pipeline.MODELS_DIR / "hf").is_dir()


class FakeKeyring:
    def get_password(self, service, provider):
        return SECRET


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(MODELS and os.environ.get("SRT_SKIP_SLOW") != "1", "models not installed (or SRT_SKIP_SLOW=1)")
class JobLogging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="srt-joblogging-"))
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp)  # the mock AI writes requests.jsonl here
        spec = importlib.util.spec_from_file_location("mock_ai", ROOT / "test-fixtures" / "mock_ai.py")
        mock_ai = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mock_ai)
        port = free_port()
        cls.server = ThreadingHTTPServer(("127.0.0.1", port), mock_ai.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.patches = [mock.patch.object(pipeline, "JOBS_DIR", cls.tmp / "jobs"),
                       mock.patch.object(joblog, "SETTINGS_FILE", cls.tmp / "app-settings.json"),
                       mock.patch.object(ai_polish, "_keyring", lambda: FakeKeyring())]
        for p in cls.patches:
            p.start()
        cls.ai = {"provider": "custom", "models": {"custom": "mock-model"},
                  "custom_base_url": f"http://127.0.0.1:{port}/ok/v1", "anthropic_workspace_id": ""}
        # A video with the sample clip's sound: extraction, thumbnail, the music check, isolation.
        cls.video = cls.tmp / "Sample Video.mp4"
        joblog.run([pipeline.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=navy:s=320x240:r=25",
                    "-i", str(ROOT / "sample" / "clip.wav"), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(cls.video)], check=True)
        lyrics = (ROOT / "sample" / "lyrics.txt").read_text(encoding="utf-8")
        cls.ctx = pipeline.start_job(cls.video, "reels", isolate="auto", settings={"mode": "lyrics"})
        cls.result = pipeline.align_and_write(cls.ctx, lyrics, language="en", ai=cls.ai, video_format="vertical")
        review.log_edits(cls.ctx, cls.result["cues"], cls.result["cues"][1:])  # a deleted caption
        cls.job = cls.result["job_dir"]
        joblog.close_logger(cls.ctx["log"])
        cls.tctx = pipeline.start_job(ROOT / "sample" / "clip.wav", "drama", isolate="off")
        cls.draft = pipeline.run_transcription(cls.tctx, "en", script="same")
        joblog.close_logger(cls.tctx["log"])
        joblog.bind(None, None)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for p in cls.patches:
            p.stop()
        os.chdir(cls.cwd)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def data(self, job=None):
        return json.loads(((job or self.job) / "job.json").read_text(encoding="utf-8"))

    def text(self, job=None):
        return ((job or self.job) / "log.txt").read_text(encoding="utf-8")

    def test_every_step_is_in_job_json(self):
        steps = [s["name"] for s in self.data()["steps"]]
        for name in ("validate", "extract audio", "music detection", "alignment", "script lines", "voice activity",
                     "numbers and symbols", "final AI check", "line-breaking", "listening check",
                     "possible missing captions", "write SRT"):
            self.assertIn(name, steps)
        for s in self.data()["steps"]:
            self.assertIn("seconds", s, s["name"])
            self.assertIn("start", s)
            self.assertIn("end", s)
        self.assertIn("transcription", [s["name"] for s in self.data(self.tctx["job_dir"])["steps"]])

    def test_every_external_command_is_in_job_json(self):
        cmds = self.data()["commands"]
        whats = {c["what"] for c in cmds}
        for what in ("duration", "audio stream check", "input details", "extract audio", "thumbnail",
                     "music check excerpt", "loudness", "Voice separation"):
            self.assertIn(what, whats)
        for c in cmds:
            self.assertIn("cmd", c)
            self.assertIn("exit", c)
            self.assertIn("seconds", c)
        self.assertTrue(all(c["exit"] == 0 for c in cmds), [c for c in cmds if c["exit"]])
        self.assertIn("silence check", {c["what"] for c in self.data(self.tctx["job_dir"])["commands"]})

    def test_settings_input_environment_summary(self):
        d = self.data()
        self.assertEqual(d["settings"]["section"], "reels")
        self.assertEqual(d["settings"]["final_check"], {"provider": "custom", "model": "mock-model"})
        self.assertEqual(d["input"]["name"], "Sample Video.mp4")
        self.assertTrue(d["input"]["has_video"])
        self.assertEqual({s["codec_type"] for s in d["input"]["streams"]}, {"audio", "video"})
        self.assertEqual(d["status"], "done")
        self.assertEqual(d["summary"]["captions"], len(self.result["cues"]))
        self.assertTrue(d["script_lines"])
        self.assertIn("missing_captions", d)
        self.assertTrue(d["models"])
        self.assertTrue(d["edits"])
        self.assertEqual(d["ai"][0]["kind"], "final check")
        self.assertTrue(d["ai"][0]["request"])
        self.assertEqual(set(d["ai"][0]["usage"]), {"input_tokens", "output_tokens"})
        self.assertTrue((self.job / "video.mp4").exists())
        self.assertTrue(self.video.exists())  # the user's own file is copied, never moved
        self.assertTrue((self.job / "thumb.jpg").exists())

    def test_log_txt_has_the_details(self):
        text = self.text()
        self.assertTrue(all(re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} ", line)
                            for line in text.splitlines() if line and not line.startswith((" ", "{", "}", "[", "]", '"'))
                            and re.match(r"\d{4}-", line)))
        for needle in ("SRT Generator", "Free disk", "Packages:", "Settings:", "Input: Sample Video.mp4",
                       "Command (extract audio)", "Command exit 0", "Music check", "Aligned words:", "Line 1 confidence",
                       "Script line 1 confidence", "Caption text:", "Final AI check request:", "Final AI check response:",
                       "Final AI check (", "tokens {", "Captions built:", "Listening check: caption 1 ", "SRT written:",
                       "Summary:", "Review edit: deleted", "Model load", "Step start: alignment", "Step end: alignment"):
            self.assertIn(needle, text)
        ttext = self.text(self.tctx["job_dir"])
        for needle in ("Whisper detected language: en", "Whisper segment ", "corrections.md (", "Draft:"):
            self.assertIn(needle, ttext)

    def test_no_key_or_home_path_in_any_log_or_report(self):
        out = report.build(category="other", description="test", at=None, job_dir=self.job,
                           include={"logs": True, "text": True, "app_log": True, "audio": True}, version="test",
                           out_dir=self.tmp)
        self.assertEqual(report.secrets_found(Path(out["path"]), [SECRET]), [])
        files = [p for p in self.tmp.rglob("*") if p.is_file() and p.suffix in (".txt", ".json", ".log", ".srt", ".jsonl")
                 and p.name != "requests.jsonl"]
        self.assertTrue(files)
        for p in files:
            data = p.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(SECRET, data, p)
            self.assertNotIn(SECRET[8:20], data, p)
            if p.name in ("log.txt", "job.json"):
                self.assertNotIn(joblog.HOME, data, p)
        # Paths under the home folder are shown with ~ ("~\AppData\..." on Windows).
        self.assertIn("~" + os.sep, self.text())
        with zipfile.ZipFile(out["path"]) as z:
            self.assertIn("job/input.wav", z.namelist())


if __name__ == "__main__":
    unittest.main()

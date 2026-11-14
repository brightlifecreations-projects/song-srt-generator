"""Backend logging (V4.9): redaction, log.txt, job.json, commands, app.log rotation, API requests."""

import json
import logging
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import joblog  # noqa: E402

MS_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")


class Redaction(unittest.TestCase):
    def test_home_folder_becomes_tilde(self):
        self.assertEqual(joblog.redact(f"{joblog.HOME}/Music/song.mp3"), "~/Music/song.mp3")
        self.assertEqual(joblog.redact(f"open {joblog.HOME}"), "open ~")

    def test_registered_secret_is_removed(self):
        joblog.add_secret("my-saved-value-12345")
        self.assertEqual(joblog.redact("x my-saved-value-12345 y"), "x [key removed] y")

    def test_key_shapes_are_removed(self):
        for text, secret in (("sk-ant-api03-abcdefghijklmnop", "abcdefghijklmnop"),
                             ("sk-proj-abcdefghijklmnopqrstuv", "abcdefghijklmnopqrstuv"),
                             ("AIzaSyA1234567890abcdefghijk", "1234567890abcdefghijk"),
                             ("gsk_abcdefghijklmnopqrst", "abcdefghijklmnopqrst"),
                             ("Bearer abcdefghijklmnopqrstu", "abcdefghijklmnopqrstu"),
                             ("x-api-key: abcdefghijklmnopq", "abcdefghijklmnopq"),
                             ('api_key="abcdefghijklmnopq"', "abcdefghijklmnopq")):
            out = joblog.redact(f"before {text} after")
            self.assertIn("[key removed]", out, text)
            self.assertNotIn(secret, out, text)
            self.assertTrue(out.startswith("before ") and out.endswith(" after"), out)

    def test_ordinary_words_are_kept(self):
        for text in ("task-transcribe-something-long", '"tokens": 12, "max_tokens": 16000', "desk-lamp-shopping-list",
                     "whisper-hinglish-apex-int8"):
            self.assertEqual(joblog.redact(text), text)

    def test_nested(self):
        joblog.add_secret("another-secret-999")
        out = joblog.redact_obj({"a": [f"{joblog.HOME}/x", {"b": "another-secret-999"}], "n": 3})
        self.assertEqual(out, {"a": ["~/x", {"b": "[key removed]"}], "n": 3})


class JobLog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="srt-joblog-"))
        self.job = self.tmp / "20261114-120000-test"
        self.job.mkdir()
        self.settings = mock.patch.object(joblog, "SETTINGS_FILE", self.tmp / "app-settings.json")
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        joblog.bind(None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_log_txt_has_millisecond_timestamps_and_debug_when_detailed(self):
        log = joblog.job_logger(self.job)
        log.debug("detail")
        log.info("step")
        joblog.close_logger(log)
        lines = (self.job / "log.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(MS_LINE.match(line) for line in lines), lines)

    def test_normal_level_drops_debug(self):
        joblog.save_settings({"log_level": "normal"})
        log = joblog.job_logger(self.job)
        log.debug("detail")
        log.info("step")
        joblog.close_logger(log)
        text = (self.job / "log.txt").read_text(encoding="utf-8")
        self.assertNotIn("detail", text)
        self.assertIn("step", text)
        joblog.save_settings({"log_level": "detailed"})

    def test_settings_default_and_save(self):
        self.assertEqual(joblog.settings(), {"log_level": "detailed", "job_keep_days": 0})
        joblog.save_settings({"job_keep_days": "30", "log_level": "nonsense"})
        self.assertEqual(joblog.settings(), {"log_level": "detailed", "job_keep_days": 30})

    def test_record_steps_commands_and_errors(self):
        log = joblog.job_logger(self.job)
        record = joblog.Record(self.job, log)
        joblog.bind(log, record)
        with record.step("first", n=1) as step:
            joblog.run([sys.executable, "-c", "print('hello'); import sys; print('oops', file=sys.stderr)"], what="hello")
            step["outputs"] = {"done": True}
        with self.assertRaises(ValueError):
            with record.step("second"):
                raise ValueError("broken")
        proc = joblog.run([sys.executable, "-c", "import sys; sys.exit(3)"], what="fails")
        self.assertEqual(proc.returncode, 3)
        with self.assertRaises(FileNotFoundError):
            joblog.run(["/no/such/program-xyz"], what="missing")
        joblog.external("Voice separation", "mlx separator a.wav -> vocals.wav", 1.5, True, "vocals.wav 10 KB")
        joblog.model_event("load", "faster-whisper-medium", seconds=1.2)
        record.finish("done")
        joblog.close_logger(log)
        data = json.loads((self.job / "job.json").read_text(encoding="utf-8"))
        self.assertEqual([s["name"] for s in data["steps"]], ["first", "second"])
        self.assertEqual(data["steps"][0]["outputs"], {"done": True})
        self.assertGreaterEqual(data["steps"][0]["seconds"], 0)
        self.assertIn("ValueError: broken", data["steps"][1]["error"])
        self.assertIn("Traceback", data["steps"][1]["traceback"])
        cmds = {c["what"]: c for c in data["commands"]}
        self.assertEqual(cmds["hello"]["exit"], 0)
        self.assertIn("hello", cmds["hello"]["stdout"])
        self.assertIn("oops", cmds["hello"]["stderr"])
        self.assertEqual(cmds["fails"]["exit"], 3)
        self.assertIn("FileNotFoundError", cmds["missing"]["error"])
        self.assertEqual(cmds["Voice separation"]["exit"], 0)
        self.assertEqual(data["models"][0]["event"], "load")
        self.assertEqual(data["status"], "done")
        for key in ("app_version", "os", "cpu", "gpu", "python", "packages", "models", "disk_free_mb", "memory"):
            self.assertIn(key, data["environment"])
        text = (self.job / "log.txt").read_text(encoding="utf-8")
        for needle in ("Step start: first", "Step end: first", "Command (hello)", "Command exit 0", "Command exit 3",
                       "stderr: oops", "Command failed to run", "Voice separation", "Model load: faster-whisper-medium",
                       "FAILED: ValueError: broken"):
            self.assertIn(needle, text)

    def test_huge_output_is_trimmed(self):
        log = joblog.job_logger(self.job)
        record = joblog.Record(self.job, log)
        joblog.bind(log, record)
        joblog.run([sys.executable, "-c", "print('x' * 20000)"], what="big")
        record.save()
        joblog.close_logger(log)
        out = json.loads((self.job / "job.json").read_text(encoding="utf-8"))["commands"][0]["stdout"]
        self.assertLess(len(out), joblog.OUTPUT_LIMIT + 200)
        self.assertIn("characters trimmed", out)


class AppLog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="srt-applog-"))
        self.patches = [mock.patch.object(joblog, "LOGS_DIR", self.tmp), mock.patch.object(joblog, "APP_LOG_BYTES", 2000),
                        mock.patch.object(joblog, "SETTINGS_FILE", self.tmp / "app-settings.json")]
        for p in self.patches:
            p.start()
        self.saved = (joblog._app_handler, list(joblog.app_log.handlers), sys.excepthook)
        joblog._app_handler = None
        joblog.app_log.handlers.clear()

    def tearDown(self):
        joblog.app_log.removeHandler(joblog._app_handler)
        joblog._app_handler.close()
        joblog._app_handler, handlers, sys.excepthook = self.saved
        joblog.app_log.handlers[:] = handlers
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rotation_keeps_five_files(self):
        log = joblog.setup_app_log()
        for n in range(400):
            log.info("line %d %s", n, "x" * 60)
        files = sorted(p.name for p in self.tmp.iterdir() if p.name.startswith("app.log"))
        self.assertEqual(files, ["app.log", "app.log.1", "app.log.2", "app.log.3", "app.log.4"])
        self.assertLessEqual((self.tmp / "app.log").stat().st_size, 2200)
        self.assertIn("line 399", joblog.app_log_since(1))
        self.assertEqual(joblog.clear_logs(), 5)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.name.startswith("app.log")), ["app.log"])

    def test_uncaught_errors_are_logged(self):
        joblog.setup_app_log()
        with mock.patch("sys.stderr"):
            try:
                raise RuntimeError("kaboom")
            except RuntimeError:
                sys.excepthook(*sys.exc_info())
        self.assertIn("RuntimeError: kaboom", (self.tmp / "app.log").read_text(encoding="utf-8"))


class ApiRequests(unittest.TestCase):
    def test_every_api_request_is_logged(self):
        from fastapi.testclient import TestClient
        import app
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())
        joblog.app_log.addHandler(handler)
        level = joblog.app_log.level
        joblog.app_log.setLevel(logging.DEBUG)  # "Detailed": polling requests are DEBUG lines
        try:
            client = TestClient(app.app)
            self.assertEqual(client.get("/api/meta").status_code, 200)
            self.assertEqual(client.get("/api/jobs/nope").status_code, 404)
        finally:
            joblog.app_log.removeHandler(handler)
            joblog.app_log.setLevel(level)
        self.assertTrue(any(re.match(r"API GET /api/meta -> 200 in \d+ ms", m) for m in records), records)
        self.assertTrue(any(m.startswith("API GET /api/jobs/nope -> 404") for m in records), records)


if __name__ == "__main__":
    unittest.main()

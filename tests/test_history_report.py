"""History page and Report a problem (V4.9): listing and filtering jobs, clean-up, opening a job again
after a restart, deleting, the settings, and the report zip (contents follow the checkboxes)."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import history  # noqa: E402
import joblog  # noqa: E402
import report  # noqa: E402

CUES = [{"start": 0.0, "end": 1.0, "text": "My ex wife left me", "listen": {"status": "ok", "reasons": [], "heard": ""}},
        {"start": 1.2, "end": 2.0, "text": "for a guy 22 years younger",
         "listen": {"status": "flag", "reasons": ["heard ‘older’"], "heard": "older"}}]


def make_job(jobs: Path, name: str, *, file="clip.wav", section="song", status="done", cues=CUES, edited=None,
             error=None, audio=True, text="Walmart lyrics"):
    d = jobs / name
    d.mkdir(parents=True)
    record = {"job": name, "created": f"{name[:4]}-{name[4:6]}-{name[6:8]}T{name[9:11]}:{name[11:13]}:{name[13:15]}",
              "status": status, "app_version": "4.9", "settings": {"section": section, "mode": "lyrics", "language": "en"},
              "input": {"name": file, "duration": 17.2, "has_video": file.endswith(".mp4")},
              "errors": [{"message": error}] if error else []}
    (d / "job.json").write_text(json.dumps(record), encoding="utf-8")
    if cues is not None:
        (d / "cues.json").write_text(json.dumps({"source_file": file, "section": section, "cues": cues,
                                                 "listen_check": {"checked": len(cues)}, "caption_mode": "line",
                                                 "max_chars_per_line": 30, "duration_seconds": 17.2}), encoding="utf-8")
        # Bytes, not write_text: on Windows, text mode would turn each \r\n into \r\r\n.
        (d / "original.srt").write_bytes(b"1\r\n00:00:00,000 --> 00:00:01,000\r\nx\r\n")
    if edited is not None:
        (d / "edited-cues.json").write_text(json.dumps(edited), encoding="utf-8")
        (d / "edited.srt").write_bytes(b"1\r\n00:00:00,000 --> 00:00:01,000\r\nedited\r\n")
    if audio:
        (d / "input.wav").write_bytes(b"RIFF....WAVEfmt fake audio")
    (d / "lyrics.txt").write_text(text, encoding="utf-8")
    (d / "log.txt").write_text(f"2026-11-14 12:00:00.000 INFO Command: {joblog.HOME}/bin/ffmpeg -i x\n", encoding="utf-8")
    return d


class History(unittest.TestCase):
    def setUp(self):
        self.jobs = Path(tempfile.mkdtemp(prefix="srt-hist-"))
        make_job(self.jobs, "20261101-100000-clip")
        make_job(self.jobs, "20261105-100000-reel", file="Reel 1.mp4", section="reels",
                 cues=[{"start": 0, "end": 1, "text": "Board exam kal hai", "listen": {"status": "ok"}}],
                 text="Board exam kal hai")
        make_job(self.jobs, "20261110-100000-drama", file="drama.mp4", section="drama", status="error", cues=None,
                 error="Couldn't read this file.")
        make_job(self.jobs, "20261112-100000-clip", edited=[{"start": 0, "end": 1, "text": "fixed by hand"}])

    def tearDown(self):
        shutil.rmtree(self.jobs, ignore_errors=True)

    def test_newest_first_with_status_and_edited(self):
        jobs = history.list_jobs(self.jobs)
        self.assertEqual([j["name"] for j in jobs], ["20261112-100000-clip", "20261110-100000-drama",
                                                     "20261105-100000-reel", "20261101-100000-clip"])
        by = {j["name"]: j for j in jobs}
        self.assertEqual(by["20261101-100000-clip"]["status"], "check")
        self.assertEqual(by["20261101-100000-clip"]["to_check"], 1)
        self.assertEqual(by["20261105-100000-reel"]["status"], "verified")
        self.assertTrue(by["20261105-100000-reel"]["has_video"])
        self.assertEqual(by["20261110-100000-drama"]["status"], "error")
        self.assertEqual(by["20261110-100000-drama"]["error"], "Couldn't read this file.")
        self.assertFalse(by["20261110-100000-drama"]["can_open"])
        self.assertTrue(by["20261112-100000-clip"]["edited"])
        self.assertEqual(by["20261112-100000-clip"]["captions"], 1)  # the edited captions count

    def test_filters(self):
        jobs = history.list_jobs(self.jobs)
        names = lambda **kw: [j["name"] for j in history.filter_jobs(jobs, self.jobs, **kw)]  # noqa: E731
        self.assertEqual(names(q="reel"), ["20261105-100000-reel"])               # file name
        self.assertEqual(names(q="board EXAM"), ["20261105-100000-reel"])          # caption text, any case
        self.assertEqual(names(q="fixed by hand"), ["20261112-100000-clip"])      # the edited text
        self.assertEqual(names(q="walmart"), ["20261112-100000-clip", "20261110-100000-drama", "20261101-100000-clip"])
        self.assertEqual(names(section="drama"), ["20261110-100000-drama"])
        self.assertEqual(names(status="error"), ["20261110-100000-drama"])
        self.assertEqual(names(status="check"), ["20261101-100000-clip"])
        self.assertEqual(names(date_from="2026-11-05", date_to="2026-11-10"),
                         ["20261110-100000-drama", "20261105-100000-reel"])
        self.assertEqual(names(q="nothing like this"), [])

    def test_job_names_cannot_escape_the_jobs_folder(self):
        for bad in ("../x", "20261101-100000-clip/../../etc", "/etc", "", "20261101-100000-CLIP", None):
            self.assertIsNone(history.job_dir(self.jobs, bad))
        self.assertEqual(history.job_dir(self.jobs, "20261101-100000-clip"), self.jobs / "20261101-100000-clip")

    def test_cleanup(self):
        now = time.mktime(time.strptime("20261112-120000", "%Y%m%d-%H%M%S"))
        self.assertEqual(history.cleanup(self.jobs, 0, now), [])
        gone = history.cleanup(self.jobs, 5, now)
        self.assertEqual(sorted(gone), ["20261101-100000-clip", "20261105-100000-reel"])
        self.assertEqual(sorted(p.name for p in self.jobs.iterdir()), ["20261110-100000-drama", "20261112-100000-clip"])


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="srt-report-"))
        self.job = make_job(self.tmp / "jobs", "20261112-100000-clip")
        self.secret = "sk-ant-api03-REALLYSECRETVALUE1234567890"
        joblog.add_secret(self.secret)
        (self.job / "log.txt").write_text(f"x {self.secret} {joblog.HOME}/Music\n", encoding="utf-8")
        (self.job / "ai-final-check.json").write_text(json.dumps({"key": self.secret}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **include):
        with mock.patch.object(joblog, "app_log_since", return_value=f"app {self.secret} {joblog.HOME}\n"):
            return report.build(category="missing", description="Line 3 is missing", at=83.4, job_dir=self.job,
                                include=include, version="4.9", out_dir=self.tmp)

    def names(self, out):
        with zipfile.ZipFile(out["path"]) as z:
            return set(z.namelist())

    def test_default_contents_and_no_audio(self):
        out = self.build()
        names = self.names(out)
        self.assertTrue(out["name"].startswith("SRT-Generator-report-") and out["name"].endswith(".zip"))
        self.assertIn("report.json", names)
        self.assertIn("app.log", names)
        for n in ("job/job.json", "job/log.txt", "job/cues.json", "job/lyrics.txt", "job/original.srt"):
            self.assertIn(n, names)
        self.assertFalse([n for n in names if "input" in n])
        self.assertEqual(out["files"][0]["name"], "report.json")
        self.assertEqual({f["name"] for f in out["files"]}, names)  # the preview lists exactly what's inside

    def test_checkboxes(self):
        names = self.names(self.build(audio=True))
        self.assertIn("job/input.wav", names)
        names = self.names(self.build(logs=False, text=False, app_log=False))
        self.assertEqual(names, {"report.json"})
        names = self.names(self.build(logs=True, text=False, app_log=False))
        self.assertIn("job/log.txt", names)
        self.assertNotIn("job/cues.json", names)
        names = self.names(self.build(logs=False, text=True, app_log=False))
        self.assertIn("job/cues.json", names)
        self.assertNotIn("job/log.txt", names)

    def test_a_source_named_input_is_not_listed_twice(self):
        (self.job / "input.srt").write_text("1\r\n00:00:00,000 --> 00:00:01,000\r\nx\r\n", encoding="utf-8")
        out = self.build(audio=True)
        names = [f["name"] for f in out["files"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("job/input.srt", names)
        self.assertIn("job/input.wav", names)

    def test_no_keys_or_home_path_anywhere(self):
        out = self.build(audio=True)
        self.assertEqual(report.secrets_found(Path(out["path"]), [self.secret]), [])
        with zipfile.ZipFile(out["path"]) as z:
            self.assertIn("~/Music", z.read("job/log.txt").decode())
            info = json.loads(z.read("report.json"))
        self.assertEqual(info["category"], "Missing captions")
        self.assertEqual(info["time_in_video"], 83.4)
        self.assertEqual(info["job"], "20261112-100000-clip")
        self.assertNotIn("ai-settings.json", str(info))

    def test_email(self):
        out = self.build()
        self.assertEqual(out["email"]["to"], "ramanworks06@gmail.com")
        self.assertEqual(out["email"]["subject"], "SRT Generator problem: Missing captions")
        self.assertIn("Line 3 is missing", out["email"]["body"])
        self.assertIn("App version: 4.9", out["email"]["body"])
        self.assertIn("OS: ", out["email"]["body"])
        self.assertIn(out["name"], out["email"]["body"])
        self.assertTrue(out["mailto"].startswith("mailto:ramanworks06@gmail.com?subject=SRT%20Generator%20problem"))

    def test_general_report_without_a_job(self):
        with mock.patch.object(joblog, "app_log_since", return_value="app\n"):
            out = report.build(category="startup", description="It won't open", at=None, job_dir=None,
                               include={"logs": True, "text": True}, version="4.9", out_dir=self.tmp)
        self.assertEqual(self.names(out), {"report.json", "app.log"})

    def test_explorer_select_quotes_only_the_path(self):
        p = Path(r"C:\Users\x\AppData\Local\SRT Generator\jobs\20261112-100000-clip")
        self.assertEqual(report.explorer_select_command(p),
                         r'explorer /select,"C:\Users\x\AppData\Local\SRT Generator\jobs\20261112-100000-clip"')

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_windows_show_in_explorer_uses_that_command(self):
        folder = Path(self.tmp) / "SRT Generator" / "jobs" / "a job"
        folder.mkdir(parents=True)
        with mock.patch("subprocess.Popen") as popen, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SRT_NO_OPEN", None)
            report.safe_open(str(folder))
        popen.assert_called_once_with(f'explorer /select,"{folder}"')

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_windows_downloads_is_the_known_folder(self):
        """The zip goes where the user's Downloads folder really is, even when it has been moved."""
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            where = os.path.expandvars(winreg.QueryValueEx(k, "{" + report.FOLDERID_DOWNLOADS + "}")[0])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SRT_REPORT_DIR", None)
            self.assertEqual(os.path.normcase(str(report.downloads_dir())), os.path.normcase(where))
        moved = Path(self.tmp) / "Moved Downloads"
        moved.mkdir()
        with mock.patch.object(report, "_windows_downloads", return_value=moved), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SRT_REPORT_DIR", None)
            self.assertEqual(report.downloads_dir(), moved)


class HistoryApi(unittest.TestCase):
    """Open a job again after the app restarted (nothing in memory), edits included; delete; settings."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="srt-histapi-"))
        import app
        self.app = app
        self.jobs = self.tmp / "jobs"
        self.patches = [mock.patch.object(app, "JOBS_DIR", self.jobs),
                        mock.patch.object(joblog, "SETTINGS_FILE", self.tmp / "app-settings.json"),
                        mock.patch.dict(os.environ, {"SRT_REPORT_DIR": str(self.tmp), "SRT_NO_OPEN": "1"})]
        for p in self.patches:
            p.start()
        d = make_job(self.jobs, "20261112-100000-clip", edited=[{"start": 0.0, "end": 1.5, "text": "fixed by hand"}])
        (d / "state.json").write_text(json.dumps({"source_file": "clip.wav", "stem": "clip", "duration": 17.2,
                                                  "section": "song", "isolate": "on", "input_audio": "input.wav",
                                                  "source_path": "input.wav", "rules": {"mode": "line", "max_chars": 30},
                                                  "timings": {}, "warnings": []}), encoding="utf-8")
        from fastapi.testclient import TestClient
        self.client = TestClient(app.app)

    def tearDown(self):
        for job_id in [k for k, j in self.app.JOBS.items() if j.get("restored")]:
            joblog.close_logger(self.app.JOBS[job_id]["ctx"].get("log"))
            self.app.JOBS.pop(job_id)
        for p in self.patches:
            p.stop()
        joblog.app_log.setLevel(joblog.level())
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_open_restores_the_review_panel_with_edits(self):
        r = self.client.post("/api/history/20261112-100000-clip/open")
        self.assertEqual(r.status_code, 200, r.text)
        job_id = r.json()["job_id"]
        self.assertEqual(r.json()["section"], "song")
        s = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(s["status"], "done")
        self.assertEqual(s["edited_cues"], [{"start": 0.0, "end": 1.5, "text": "fixed by hand"}])
        self.assertEqual(len(s["cues"]), 2)
        self.assertEqual(s["cues"][1]["listen"]["status"], "flag")
        self.assertTrue(s["edited"])
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}/srt").text.split("\r\n")[2], "edited")
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}/srt?original=1").text.split("\r\n")[2], "x")
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}/audio").status_code, 200)
        # Editing again works on the restored job, and is logged.
        r = self.client.put(f"/api/jobs/{job_id}/cues", json={"cues": [{"start": 0.0, "end": 1.6, "text": "fixed again"}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Review edit: text and timing", (self.jobs / "20261112-100000-clip" / "log.txt").read_text(encoding="utf-8"))

    def test_open_refuses_a_job_without_captions(self):
        make_job(self.jobs, "20261113-100000-bad", cues=None)
        self.assertEqual(self.client.post("/api/history/20261113-100000-bad/open").status_code, 409)
        self.assertIn(self.client.post("/api/history/..%2F..%2Fetc/open").status_code, (404, 405))

    def test_list_log_settings_delete(self):
        body = self.client.get("/api/history?q=walmart").json()
        self.assertEqual([j["name"] for j in body["jobs"]], ["20261112-100000-clip"])
        log = self.client.get("/api/history/20261112-100000-clip/log").json()["log"]
        self.assertIn("~/bin/ffmpeg", log)
        self.assertNotIn(joblog.HOME, log)
        self.assertEqual(self.client.put("/api/settings", json={"job_keep_days": 30, "log_level": "normal"}).json()["job_keep_days"], 30)
        self.assertEqual(self.client.get("/api/settings").json()["log_level"], "normal")
        self.assertEqual(self.client.delete("/api/history/20261112-100000-clip").status_code, 200)
        self.assertFalse((self.jobs / "20261112-100000-clip").exists())
        self.assertEqual(self.client.delete("/api/history/20261112-100000-clip").status_code, 404)

    def test_report_endpoint_and_email(self):
        r = self.client.post("/api/report", json={"category": "timing", "description": "late", "time": "12.5",
                                                  "job": "20261112-100000-clip", "include": {"audio": False}})
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertTrue(out["path"].startswith("~") or not out["path"].startswith(joblog.HOME))
        self.assertTrue((self.tmp / out["name"]).exists())
        before = len(report.opened)
        self.assertEqual(self.client.post("/api/report/open", json={"name": out["name"], "open": "email",
                                                                    "mailto": out["mailto"]}).status_code, 200)
        self.assertEqual(report.opened[before], out["mailto"])
        self.assertEqual(self.client.post("/api/report/open", json={"name": out["name"], "open": "email",
                                                                    "mailto": "mailto:someone@else.com?x"}).status_code, 400)
        self.assertEqual(self.client.post("/api/report/open", json={"name": "nope.zip", "open": "zip"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()

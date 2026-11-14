"""tools/analyze_report.py (V4.9): validates a report zip and prints its summary."""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import analyze_report  # noqa: E402
import joblog  # noqa: E402
import report  # noqa: E402
from test_history_report import make_job  # noqa: E402


class Analyze(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="srt-analyze-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_tool(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = analyze_report.main([str(a) for a in args])
        return code, out.getvalue()

    def test_summary(self):
        job = make_job(self.tmp / "jobs", "20261112-100000-clip")
        with mock.patch.object(joblog, "app_log_since", return_value="2026-11-14 12:00:00.000 ERROR boom\n"):
            built = report.build(category="missing", description="Line 3 missing", at=12.0, job_dir=job,
                                 include={}, version="4.9", out_dir=self.tmp)
        code, text = self.run_tool(built["path"], "--no-rerun")
        self.assertEqual(code, 0, text)
        for needle in ("Category:     Missing captions", "Line 3 missing", "0:12.000", "== Steps", "== Script lines",
                       "Captions: 2 generated", "1 flagged", "heard ‘older’", "== Possible missing captions",
                       "ERROR boom"):
            self.assertIn(needle, text)

    def test_rejects_unsafe_and_foreign_zips(self):
        bad = self.tmp / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../escape.txt", "x")
        self.assertEqual(self.run_tool(bad)[0], 2)
        other = self.tmp / "other.zip"
        with zipfile.ZipFile(other, "w") as z:
            z.writestr("hello.txt", "x")
        code, text = self.run_tool(other)
        self.assertEqual(code, 2)
        self.assertIn("isn't an SRT Generator problem report", text)


if __name__ == "__main__":
    unittest.main()

"""corrections.md learns from AI polish and the user (Source column, V4.3).

    .venv/bin/python -m unittest discover -s tests -v

Runs the real approval path (pipeline.record_approval) in a scratch data folder: the offline
draft, the AI's changes as run_polish remembers them, and the text the user approved.
"""

import json
import logging
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import corrections  # noqa: E402
import pipeline  # noqa: E402


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        self.job = self.data / "job"
        self.job.mkdir()
        self._data_dir = pipeline.DATA_DIR
        pipeline.DATA_DIR = self.data

    def tearDown(self):
        pipeline.DATA_DIR = self._data_dir
        shutil.rmtree(self.data)

    def approve(self, draft, ai_changes, approved, auto_corrections=()):
        ctx = {"job_dir": self.job, "log": logging.getLogger("test"), "whisper_draft": draft,
               "corrections_table": "Hinglish", "recorded_pairs": {},
               "auto_corrections": list(auto_corrections), "ai_changes": ai_changes}
        pipeline.record_approval(ctx, approved)
        return corrections.load(self.data).get("Hinglish", {})

    def test_kept_ai_fix_is_recorded_as_ai(self):
        rows = self.approve("kal ke bord exam", [{"from": "bord", "to": "board"}], "kal ke board exam")
        self.assertEqual(rows["bord"]["correct"], "board")
        self.assertEqual(rows["bord"]["source"], "AI")

    def test_undone_ai_fix_is_not_recorded(self):
        rows = self.approve("kal ke board exam", [{"from": "exam", "to": "exams"}], "kal ke board exam")
        self.assertNotIn("exam", rows)
        rejected = json.loads((self.job / "ai-rejected.json").read_text())
        self.assertEqual(rejected, [{"from": "exam", "to": "exams"}])
        self.assertIn("AI fixes you rejected", (self.job / "corrections-diff.md").read_text())

    def test_edited_ai_fix_is_recorded_as_ai_plus_user(self):
        rows = self.approve("padhai karni hai", [{"from": "padhai", "to": "padai"}], "padhaai karni hai")
        self.assertEqual(rows["padhai"]["correct"], "padhaai")
        self.assertEqual(rows["padhai"]["source"], "AI+User")

    def test_user_only_fix_is_recorded_as_user(self):
        rows = self.approve("yeh zindgi hai", [{"from": "bord", "to": "board"}], "yeh zindagi hai")
        self.assertEqual(rows["zindgi"]["source"], "User")

    def test_ai_fix_on_an_auto_corrected_word_counts_from_the_original(self):
        """corrections.md turned "adti" into "aditi", the AI made it "Aditi": learnt from "adti"."""
        rows = self.approve("kabhi kabhi adti", [{"from": "aditi", "to": "Aditi"}], "kabhi kabhi Aditi",
                            auto_corrections=[{"original": "adti", "replacement": "aditi"}])
        self.assertEqual(rows["adti"]["source"], "AI")

    def test_mixed_sources_combine(self):
        self.approve("kal ke bord exam", [{"from": "bord", "to": "board"}], "kal ke board exam")
        rows = self.approve("bord pe likho", [], "board pe likho")
        self.assertEqual(rows["bord"]["count"], 2)
        self.assertEqual(rows["bord"]["source"], "AI+User")

    def test_old_format_file_imports_cleanly(self):
        old = ("# corrections\n\n## Hinglish\n| Wrong | Correct | Times seen | Last seen | Notes |\n"
               "|---|---|---|---|---|\n| adti | Aditi | 3 | 2026-11-02 | from a teammate |\n"
               "| ~~main~~ | mein | 2 | 2026-11-01 | |\n")
        theirs = corrections.parse(old, "teammate.md")
        self.assertEqual({k: r["source"] for k, r in theirs["Hinglish"].items()}, {"adti": "User", "main": "User"})
        self.assertEqual(theirs["Hinglish"]["adti"]["notes"], "from a teammate")
        mine = {"Hinglish": {"bord": {"wrong": "bord", "correct": "board", "count": 1, "last_seen": "2026-11-03",
                                      "source": "AI", "notes": "", "disabled": False}}}
        merged = corrections.merge(mine, [("teammate.md", theirs)])
        corrections.save(self.data, merged)
        again = corrections.load(self.data)["Hinglish"]  # saved in the new format, read back
        self.assertEqual({k: (r["source"], r["disabled"]) for k, r in again.items()},
                         {"bord": ("AI", False), "adti": ("User", False), "main": ("User", True)})
        self.assertIn("| Source |", corrections.path(self.data).read_text())

    def test_bad_source_is_reported(self):
        bad = ("## Hinglish\n| Wrong | Correct | Times seen | Last seen | Source | Notes |\n"
               "| adti | Aditi | 3 | 2026-11-02 | Robot | |\n")
        with self.assertRaises(corrections.CorrectionsError) as e:
            corrections.parse(bad, "x.md")
        self.assertIn("'Source' must be User, AI or AI+User", e.exception.errors[0])


if __name__ == "__main__":
    unittest.main()

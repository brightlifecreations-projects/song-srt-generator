"""Listening check (V4.8): timing rules, comparing what was heard, and the auto-fix decisions.

    .venv/bin/python -m unittest tests.test_listen_check -v

No models: Whisper is replaced by a fake "ear" that hears a given list of words and re-aligns to
given times.
"""

import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import captions  # noqa: E402
import listen_check as lc  # noqa: E402


def timed(text, start=1.0, step=0.4):
    out, t = [], start
    for w in text.split():
        out.append({"word": w, "start": round(t, 3), "end": round(t + step - 0.05, 3), "line": 0,
                    "speaker": None})
        t += step
    return out


def heard(text, start=1.0, step=0.4, prob=0.95):
    return [{"word": w["word"], "start": w["start"], "end": w["end"], "prob": prob} for w in timed(text, start, step)]


RULES = captions.rules_for({"max_cue_seconds": None, "pause_split_seconds": None, "max_words": None},
                           "vertical", language="en")


class FakeEar:
    """Hears `HEARD` in any window (only the words inside it), re-aligns to `REALIGN`."""
    HEARD: list = []
    CLIPS: dict = {}
    REALIGN = None

    def __init__(self, ctx, kind, language):
        self.duration, self.calls, self.realigned = 60.0, 0, []

    def hear(self, t0, t1):
        self.calls += 1
        source = next((v for (a, b), v in FakeEar.CLIPS.items() if abs(a - t0) < 0.01 and abs(b - t1) < 0.01),
                      FakeEar.HEARD)
        return [w for w in source if t0 <= (w["start"] + w["end"]) / 2 <= t1]

    def realign(self, t0, t1, text):
        self.calls += 1
        self.realigned.append(text)
        return FakeEar.REALIGN(text) if FakeEar.REALIGN else None


class Base(unittest.TestCase):
    def setUp(self):
        self.real_ear = lc.Ear
        lc.Ear = FakeEar
        FakeEar.HEARD, FakeEar.CLIPS, FakeEar.REALIGN = [], {}, None
        self.audio = Path(tempfile.mkdtemp()) / "vocals.wav"
        self.audio.write_bytes(b"")

    def tearDown(self):
        lc.Ear = self.real_ear

    def ctx(self, **kw):
        return {"source_path": self.audio, "log": logging.getLogger("test.listen"), "script": None,
                "whisper_language_used": "en", "timeline": {}, **kw}

    def check(self, text, rules=RULES, tokens=None, words=None, **ctx):
        words = words or timed(text)
        tokens = tokens or captions.make_tokens(words, "en")
        cues = captions.build_cues(tokens, words, rules)
        return lc.run(self.ctx(**ctx), tokens, words, cues, rules)


class Comparing(Base):
    def test_all_heard_is_ok(self):
        FakeEar.HEARD = heard("I love you so much")
        out = self.check("I love you so much")
        self.assertEqual(out["summary"]["flagged"], 0)
        self.assertTrue(all(c["listen"]["status"] == "ok" for c in out["cues"]))

    def test_numbers_and_symbols_dont_count(self):
        FakeEar.HEARD = heard("twenty two years, younger!")
        out = self.check("22 years younger")
        self.assertEqual(out["summary"]["flagged"], 0, out["cues"])
        FakeEar.HEARD = heard("at seven thirty")
        self.assertEqual(self.check("at 7:30")["summary"]["flagged"], 0)
        FakeEar.HEARD = heard("I'm four")
        self.assertEqual(self.check("Im 4")["summary"]["flagged"], 0)

    def test_different_word_is_flagged_with_reason(self):
        FakeEar.HEARD = heard("main road jaunga")
        out = self.check("main zaroor jaunga")
        cue = out["cues"][0]
        self.assertEqual(cue["listen"]["status"], "flag")
        self.assertIn("heard ‘road’, caption says ‘zaroor’", cue["listen"]["reasons"][0])

    def test_flag_needs_both_passes(self):
        """The caption's own clip heard it right: the window pass alone doesn't flag."""
        FakeEar.HEARD = heard("main road jaunga")
        words = timed("main zaroor jaunga")
        cues = captions.build_cues(captions.make_tokens(words), words, RULES)
        FakeEar.CLIPS = {(cues[0]["start"] - lc.PAD, cues[0]["end"] + lc.PAD): heard("main zaroor jaunga")}
        out = self.check("main zaroor jaunga")
        self.assertEqual(out["cues"][0]["listen"]["status"], "ok")

    def test_unsure_hearing_isnt_evidence(self):
        FakeEar.HEARD = heard("main road jaunga", prob=0.2)
        self.assertEqual(self.check("main zaroor jaunga")["summary"]["flagged"], 0)

    def test_function_words_swapped_dont_count(self):
        FakeEar.HEARD = heard("give me the book")
        self.assertEqual(self.check("give me a book")["summary"]["flagged"], 0)

    def test_hinglish_spellings_agree(self):
        self.assertGreaterEqual(lc.similarity("jaroor", "zaroor"), lc.AGREE)
        self.assertGreaterEqual(lc.similarity("kalke", "kalke"), lc.AGREE)
        self.assertEqual(lc.similarity("22", "23"), 0.0)

    def test_run_together_words_match(self):
        pairs = lc.match(["kal", "ke", "paper"], ["kalke", "paper"])
        self.assertEqual([p[0] for p in pairs], [[0], [0], [1]])

    def test_nothing_heard_at_all(self):
        FakeEar.HEARD = []
        cue = self.check("hello there friend")["cues"][0]
        self.assertEqual(cue["listen"]["status"], "flag")
        self.assertIn("nothing heard", cue["listen"]["reasons"][0])

    def test_translation_skipped(self):
        out = self.check("hello there", script="english")
        self.assertIn("translation", out["summary"]["skipped"])
        self.assertNotIn("listen", out["cues"][0])

    def test_no_audio_skipped_never_raises(self):
        self.audio.unlink()
        self.assertIn("audio", self.check("hello there")["summary"]["skipped"])


class TimingRules(unittest.TestCase):
    def cue(self, start, end, w0, w1):
        return {"start": start, "end": end, "words": [{"start": w0, "end": w0 + 0.1}, {"start": w1 - 0.1, "end": w1}]}

    def test_built_cues_keep_the_rules(self):
        for mode in ("line", "word"):
            words = timed("one two three four five six seven eight nine ten eleven twelve", step=0.25)
            rules = {**RULES, "mode": mode}
            cues = captions.build_cues(captions.make_tokens(words), words, rules)
            self.assertEqual([r for r in lc.timing_rules(cues, rules) if r], [], mode)

    def test_early_start_late_end_overlap_short(self):
        cues = [self.cue(0.5, 2.6, 1.0, 2.0), self.cue(2.5, 2.52, 2.5, 3.0)]
        reasons = lc.timing_rules(cues, RULES)
        self.assertTrue(any("before its first word" in r for r in reasons[0]))
        self.assertTrue(any("after its last word" in r for r in reasons[0]))
        self.assertIn("overlaps the next caption", reasons[0])
        self.assertIn("too short to read", reasons[1])

    def test_limits_are_inclusive(self):
        cues = [self.cue(1.0 - lc.EARLY, 2.0 + lc.LATE, 1.0, 2.0)]
        self.assertEqual(lc.timing_rules(cues, RULES), [[]])

    def test_word_mode_may_bridge_to_the_next_word(self):
        rules = {**RULES, "mode": "word"}
        cues = [self.cue(1.0, 2.8, 1.0, 2.0), self.cue(2.8, 3.2, 2.8, 3.1)]
        self.assertEqual(lc.timing_rules(cues, rules)[0], [])
        self.assertTrue(lc.timing_rules(cues, RULES)[0])  # line by line: too long after the word

    def test_fix_brings_cues_back_within(self):
        cues = [self.cue(0.5, 2.9, 1.0, 2.0), self.cue(2.5, 3.0, 2.5, 3.0)]
        self.assertEqual(lc.fix_timing_rules(cues, RULES), 1)
        self.assertEqual((cues[0]["start"], cues[0]["end"]), (0.8, 2.3))
        self.assertEqual([r for r in lc.timing_rules(cues, RULES) if r], [])

    def test_heard_timing_off(self):
        cue = {"start": 2.0, "end": 3.0}
        self.assertTrue(lc.timing_off(cue, 1.2, None))   # appears 0.8 s late
        self.assertTrue(lc.timing_off(cue, 2.9, None))   # appears long before the words
        self.assertTrue(lc.timing_off(cue, None, 3.8))   # gone before the last word ends
        self.assertEqual(lc.timing_off(cue, 1.7, 3.3), [])  # within tolerance


class AutoFix(Base):
    def shifted(self, delay=1.0):
        """The aligner put the caption `delay` s late; Whisper hears it where it is."""
        words = timed("hello there my friend", start=1.0 + delay)
        FakeEar.HEARD = heard("hello there my friend", start=1.0)
        return words

    def test_timing_realigned_when_both_sources_agree(self):
        words = self.shifted()
        FakeEar.REALIGN = lambda text: [(w["start"], w["end"]) for w in timed(text, start=1.02)]
        out = self.check("hello there my friend", words=words)
        cue = out["cues"][0]
        self.assertEqual(cue["listen"]["status"], "fixed")
        self.assertAlmostEqual(cue["start"], 1.02, places=3)
        self.assertEqual(out["words"][0]["start"], 1.02)  # the timeline is fixed (kept on a switch)
        self.assertIn("re-aligned", cue["listen"]["reasons"][0])

    def test_timing_flagged_when_realign_disagrees(self):
        words = self.shifted()
        FakeEar.REALIGN = lambda text: [(w["start"], w["end"]) for w in timed(text, start=5.0)]
        cue = self.check("hello there my friend", words=words)["cues"][0]
        self.assertEqual(cue["listen"]["status"], "flag")
        self.assertIn("timing:", cue["listen"]["reasons"][0])
        self.assertAlmostEqual(cue["start"], 2.0, places=3)  # unchanged

    def test_realign_confirms_current_timing(self):
        words = self.shifted()
        FakeEar.REALIGN = lambda text: [(w["start"], w["end"]) for w in timed(text, start=2.0)]
        cue = self.check("hello there my friend", words=words)["cues"][0]
        self.assertEqual(cue["listen"]["status"], "ok")

    def test_ai_change_put_back_when_original_and_audio_agree(self):
        words = timed("thank you bhai")
        tokens = captions.make_tokens(words)
        tokens[2] = {**tokens[2], "orig": "bhai", "text": "bye"}  # the AI's "fix" was wrong
        FakeEar.HEARD = heard("thank you bhai")
        out = self.check("", words=words, tokens=tokens)
        cue = out["cues"][0]
        self.assertEqual(cue["text"].split()[-1], "bhai")
        self.assertEqual(cue["listen"]["status"], "fixed")
        self.assertIn("put back", cue["listen"]["reasons"][0])
        self.assertNotIn("orig", out["tokens"][2])
        self.assertNotIn("ai", cue)

    def test_users_own_word_is_never_changed(self):
        FakeEar.HEARD = heard("I went to the store")
        out = self.check("I went to the shop")
        self.assertEqual(out["cues"][0]["text"], "I went to the shop")
        self.assertEqual(out["cues"][0]["listen"]["status"], "flag")

    def test_ai_unsure_cleared_when_heard(self):
        words = timed("meet Aditi today")
        tokens = captions.make_tokens(words)
        tokens[1] = {**tokens[1], "unsure": True}
        FakeEar.HEARD = heard("meet Aditi today")
        out = self.check("", words=words, tokens=tokens)
        self.assertNotIn("unsure", out["cues"][0])

    def test_window_heard_once_and_kept_for_a_switch(self):
        FakeEar.HEARD = heard("I love you so much")
        ctx = self.ctx()
        words = timed("I love you so much")
        tokens = captions.make_tokens(words)
        lc.run(ctx, tokens, words, captions.build_cues(tokens, words, RULES), RULES)
        self.assertIn("heard", ctx["timeline"])
        rules = {**RULES, "mode": "word"}
        out = lc.run(ctx, tokens, words, captions.build_cues(tokens, words, rules), rules)
        self.assertEqual(out["summary"]["calls"], 0)  # nothing re-heard
        self.assertEqual(out["summary"]["ok"], 5)


class Windows(unittest.TestCase):
    def test_grouped_and_padded(self):
        cues = [{"start": float(t), "end": t + 1.5} for t in range(0, 60, 2)]
        spans = lc.windows(cues)
        self.assertGreater(len(spans), 1)
        self.assertLess(len(spans), 5)
        for t0, t1, _ in spans:
            self.assertLessEqual(t1 - t0, lc.WINDOW_MAX + 0.001)
        self.assertEqual(spans[0][0], -lc.SEARCH)


if __name__ == "__main__":
    unittest.main()

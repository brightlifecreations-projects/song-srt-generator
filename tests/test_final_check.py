"""Final AI check (V4.6): the validator and the fallbacks, with a fake provider (no network).

    .venv/Scripts/python -m unittest tests.test_final_check -v
"""

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_polish  # noqa: E402
import captions  # noqa: E402
import final_check  # noqa: E402

SETTINGS = {"provider": "anthropic", "models": {"anthropic": "claude-opus-5-5"}}


def timed(text):
    out, t = [], 0.0
    for w in text.split():
        out.append({"word": w, "start": round(t, 3), "end": round(t + 0.3, 3), "line": 0, "speaker": None})
        t += 0.35
    return out


class Fake:
    """Stands in for a provider adapter: answers with `reply`, or raises / sleeps."""

    def __init__(self, reply=None, error=None, delay=0.0):
        self.reply, self.error, self.delay, self.timeout, self.sent = reply, error, delay, None, None

    def complete(self, system, user, schema=None):
        self.sent = (system, json.loads(user), schema)
        if self.delay > self.timeout:
            time.sleep(0.01)
            raise ai_polish.PolishError("timeout", f"The provider didn't answer within {self.timeout:.0f} seconds")
        if self.error:
            raise self.error
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


class FinalCheck(unittest.TestCase):
    def setUp(self):
        self.words = timed("i went to teh store with sarah and bought five apples")
        self.tokens = captions.make_tokens(self.words, "en")
        self.rules = captions.rules_for({"max_cue_seconds": None, "pause_split_seconds": None, "max_words": None},
                                        "vertical", language="en")
        self.chunks = captions.line_chunks(self.tokens, self.words, self.rules)

    def run_check(self, reply=None, **kw):
        fake = Fake(reply, **{k: v for k, v in kw.items() if k in ("error", "delay")})
        result = final_check.review(self.tokens, self.words, self.chunks, SETTINGS, section="reels",
                                    language="English", script=None, script_text=None, rules=self.rules,
                                    timeout=kw.get("timeout", 5), adapter=fake)
        return result, fake

    def texts(self, result):
        return [t["text"] for t in result["tokens"]]

    def test_good_answer_fixes_text_and_keeps_timing(self):
        idx = {t["text"]: k for k, t in enumerate(self.tokens)}
        reply = {"tokens": [{"i": idx["teh"], "t": "the"}, {"i": idx["sarah"], "t": "Sarah"}, {"i": 0, "t": "I"}],
                 "cue_starts": [c[0] for c in self.chunks]}
        result, fake = self.run_check(reply)
        self.assertIsNone(result["message"])  # its cuts no longer fit ("to the |"): recut quietly
        self.assertIsNone(result["chunks"])
        self.assertEqual(result["changes"], 3)
        self.assertIn("the", self.texts(result))
        self.assertIn("Sarah", self.texts(result))
        self.assertEqual(fake.sent[2], final_check.RESPONSE_SCHEMA)
        self.assertIn("5", [t["t"] for t in fake.sent[1]["tokens"]])  # numbers already digits
        cues = captions.build_cues(result["tokens"], self.words, self.rules)
        for c in cues[:-1]:
            self.assertNotIn(captions.core(c["text"].split()[-1]), captions.WEAK_END_ENGLISH)
        # Timing comes from the same words: the first and last word times are unchanged.
        self.assertEqual(cues[0]["start"], self.words[0]["start"])
        self.assertEqual([w["start"] for c in cues for w in c["words"]], [w["start"] for w in self.words])
        self.assertEqual(sum(len(c["ai"]) for c in cues if "ai" in c), 3)
        sarah = next(a for c in cues for a in c.get("ai", []) if a["to"] == "Sarah")
        self.assertEqual(sarah["from"], "sarah")

    def test_invalid_json(self):
        result, _ = self.run_check("this is not json {")
        self.assertIn("wasn't valid JSON", result["message"])
        self.assertEqual(self.texts(result), [t["text"] for t in self.tokens])
        self.assertIsNone(result["chunks"])

    def test_word_count_change(self):
        """V4.8: a change that adds or drops a word is refused on its own (with the reason for the
        job log); the AI's other changes are still used."""
        result, _ = self.run_check({"tokens": [{"i": 3, "t": "the big"}, {"i": 6, "t": "Sarah"}],
                                    "cue_starts": [0]})
        self.assertIn("refused", result["message"])
        self.assertIn("number of words", result["refused"][0]["why"])
        self.assertEqual(result["refused"][0]["to"], "the big")
        self.assertEqual(self.texts(result)[3], "teh")
        self.assertEqual(self.texts(result)[6], "Sarah")
        result, _ = self.run_check({"tokens": [{"i": 3, "t": ""}], "cue_starts": [0]})
        self.assertIn("number of words", result["refused"][0]["why"])
        self.assertEqual(self.texts(result), [t["text"] for t in self.tokens])
        result, _ = self.run_check({"tokens": [{"i": k, "t": "x"} for k in range(len(self.tokens) + 1)],
                                    "cue_starts": [0]})
        self.assertIn("more words", result["message"])

    def test_unknown_index(self):
        result, _ = self.run_check({"tokens": [{"i": 99, "t": "x"}], "cue_starts": [0]})
        self.assertIn("doesn't exist", result["message"])

    def test_timeout(self):
        result, _ = self.run_check({"tokens": [], "cue_starts": [0]}, delay=10, timeout=1)
        self.assertIn("didn't answer within 1 seconds", result["message"])
        self.assertIn("rule-based captions were used", result["message"])
        self.assertEqual(self.texts(result), [t["text"] for t in self.tokens])

    def test_provider_error(self):
        result, _ = self.run_check(error=ai_polish.PolishError("auth", "The API key was refused"))
        self.assertTrue(result["message"].startswith("Final AI check skipped — The API key was refused"))

    def test_bad_cuts_keep_rule_cuts_but_text_fixes(self):
        idx = {t["text"]: k for k, t in enumerate(self.tokens)}
        reply = {"tokens": [{"i": idx["teh"], "t": "the"}], "cue_starts": [0]}  # one 11-word caption
        result, _ = self.run_check(reply)
        self.assertIsNone(result["chunks"])
        self.assertIn("the", self.texts(result))
        self.assertIn("rule-based cuts were kept", result["message"])

    def test_good_cuts_are_used(self):
        n = len(self.tokens)
        # I went to the store | with Sarah and | bought 5 apples  -> "and" at the end breaks the rules
        bad = {"tokens": [], "cue_starts": [0, 5, 8]}
        self.assertIsNone(self.run_check(bad)[0]["chunks"])
        good = {"tokens": [], "cue_starts": [0, 2, 5, 7]}  # i went | to teh store | with sarah | and bought 5 apples
        result, _ = self.run_check(good)
        self.assertEqual(result["chunks"], [[0, 1], [2, 3, 4], [5, 6], [7, 8, 9, 10][:n - 7]])

    def test_different_word_is_ignored(self):
        idx = {t["text"]: k for k, t in enumerate(self.tokens)}
        reply = {"tokens": [{"i": idx["apples"], "t": "bananas"}, {"i": idx["teh"], "t": "the"}], "cue_starts": [0]}
        result, _ = self.run_check(reply)
        self.assertIn("apples", self.texts(result))
        self.assertIn("the", self.texts(result))
        self.assertIn("ignored", result["message"])

    def test_change_back_clears_the_highlight(self):
        idx = {t["text"]: k for k, t in enumerate(self.tokens)}
        first, _ = self.run_check({"tokens": [{"i": idx["teh"], "t": "the"}], "cue_starts": [0]})
        self.tokens = first["tokens"]
        second, _ = self.run_check({"tokens": [{"i": idx["teh"], "t": "teh"}], "cue_starts": [0]})
        self.assertEqual(second["tokens"][idx["teh"]]["text"], "teh")
        self.assertNotIn("orig", second["tokens"][idx["teh"]])

    def test_unsure_words_are_flagged(self):
        result, fake = self.run_check({"tokens": [], "cue_starts": [0], "unsure": [6, 99]})
        self.assertEqual(result["unsure"], [6])
        self.assertTrue(result["tokens"][6].get("unsure"))
        cues = captions.build_cues(result["tokens"], self.words, self.rules)
        cue = next(c for c in cues if 6 in c["tokens"])
        self.assertEqual(cue["unsure"], [cue["tokens"].index(6)])
        self.assertNotIn("[?]", " ".join(c["text"] for c in cues))  # never in the SRT
        self.assertIn("unsure", fake.sent[2]["required"])

    def test_editorial_prompt(self):
        _, fake = self.run_check({"tokens": [], "cue_starts": [0], "unsure": []})
        system, sent, _ = fake.sent
        for rule in ("Misheard", "proper nouns", "digits", "7:30", "symbol", "4-5 words", "one speaker",
                     "[?]", "NEVER add, remove"):
            self.assertIn(rule, system)
        self.assertIn("no one", sent["number_exceptions"])

    def test_sound_alike_fix_allowed(self):
        self.assertTrue(final_check.plausible("rasta", "raasta"))
        self.assertTrue(final_check.plausible("seven", "7:30"))
        self.assertFalse(final_check.plausible("road", "zaroor"))

    def test_plausible(self):
        self.assertTrue(final_check.plausible("teh", "the"))
        self.assertTrue(final_check.plausible("five", "5"))
        self.assertTrue(final_check.plausible("“hello", "hello"))
        self.assertTrue(final_check.plausible("sarah", "Sarah,"))
        self.assertFalse(final_check.plausible("apples", "bananas"))

    def test_cuts_judged_phrase_by_phrase(self):
        """V4.6.1: the hard breaks are sent as must_end_after, and one phrase with a bad cut keeps
        the rule-based cuts there without throwing away the AI's good cuts elsewhere."""
        self.words = timed("my old friend called me today. i went to the store with sarah and bought apples")
        self.tokens = captions.make_tokens(self.words, "en")
        self.chunks = captions.line_chunks(self.tokens, self.words, self.rules)
        # Rule-based: my old friend | called me today. || i went to the store | with sarah and bought apples
        # AI: my old friend called | me today. (fits) || … | with sarah and | bought apples ("and" ends one)
        result, fake = self.run_check({"tokens": [], "cue_starts": [0, 4, 6, 11, 14]})
        self.assertEqual(fake.sent[1]["must_end_after"], [5])
        rule_second = [c for c in captions.line_chunks(self.tokens, self.words, self.rules) if c[0] >= 6]
        self.assertEqual(result["chunks"], [[0, 1, 2, 3], [4, 5]] + rule_second)
        self.assertIn("in 1 phrase", result["message"])
        self.assertTrue(captions.check_chunks(self.tokens, self.words, result["chunks"], self.rules))

    def test_hinglish_is_named_for_the_ai(self):
        """Hinglish is timed with the English model, but the request names the text as Hinglish."""
        import pipeline
        seen = {}
        original, log_ai = final_check.review, pipeline._log_ai
        pipeline._log_ai = lambda *a: None
        final_check.review = lambda *a, **kw: seen.update(kw) or {
            "tokens": a[0], "chunks": None, "changes": 0, "message": None, "sent": "{}", "returned": None}
        try:
            for script, expected in (("hinglish", pipeline.HINGLISH_LABEL), ("same", "English")):
                ctx = {"timeline": {"words": self.words, "tokens": self.tokens, "ai": {}}, "section": "reels",
                       "whisper_language_used": "en", "script": script, "job_dir": Path("/nonexistent")}
                pipeline.build_layout(ctx, self.rules, SETTINGS)
                self.assertEqual(seen["language"], expected)
        finally:
            final_check.review, pipeline._log_ai = original, log_ai

    def test_active_needs_a_provider_and_the_switch(self):
        original = ai_polish.load_settings
        try:
            ai_polish.load_settings = lambda: {**SETTINGS, "final_check": True, "keys_saved": [],
                                               "custom_base_url": ""}
            self.assertIsNone(final_check.active())  # no key
            ai_polish.load_settings = lambda: {**SETTINGS, "final_check": True, "keys_saved": ["anthropic"],
                                               "custom_base_url": ""}
            self.assertIsNotNone(final_check.active())
            ai_polish.load_settings = lambda: {**SETTINGS, "final_check": False, "keys_saved": ["anthropic"],
                                               "custom_base_url": ""}
            self.assertIsNone(final_check.active())  # switched off
        finally:
            ai_polish.load_settings = original


if __name__ == "__main__":
    unittest.main()

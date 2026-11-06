"""Caption rules (V4.6): line breaking, speakers, numbers, word by word, timing.

    .venv/Scripts/python -m unittest discover -s tests -v      (Windows)
    .venv/bin/python -m unittest discover -s tests -v          (Mac)

Pure Python: no models, no audio.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import captions  # noqa: E402
from captions import core  # noqa: E402


def words_of(*lines, step=0.3, gap=0.05, pauses=None, speakers=None):
    """Timed words: each word `step` long, `gap` apart; pauses={word index: extra seconds}."""
    out, t, n = [], 0.0, 0
    pauses = pauses or {}
    for li, line in enumerate(lines):
        for w in line.split():
            t += pauses.get(n, 0.0)
            out.append({"word": w, "start": round(t, 3), "end": round(t + step, 3), "line": li,
                        "speaker": speakers[li] if speakers else None})
            t += step + gap
            n += 1
    return out


def line_rules(**kw):
    return captions.rules_for({"max_cue_seconds": None, "pause_split_seconds": None, "max_words": None},
                              kw.pop("fmt", "vertical"), **kw)


def build(*lines, rules=None, language="en", **kw):
    words = words_of(*lines, **kw)
    tokens = captions.offline_cleanup(captions.make_tokens(words, language), words)
    return captions.build_cues(tokens, words, rules or line_rules()), tokens, words


class LineBreaking(unittest.TestCase):
    TEXT = ("She walked into the store for her son and asked for the shoes in a size 4 "
            "because the old ones were too small for him now")

    def test_limits(self):
        for fmt, cap in (("vertical", 30), ("horizontal", 42)):
            cues, _, _ = build(self.TEXT, rules=line_rules(fmt=fmt))
            for c in cues:
                self.assertLessEqual(len(c["text"].split()), 5, c["text"])
                self.assertLessEqual(captions.display_len(c["text"]), cap, c["text"])

    def test_no_weak_word_endings(self):
        cues, _, _ = build(self.TEXT)
        for c in cues[:-1]:
            self.assertNotIn(core(c["text"].split()[-1]), captions.WEAK_END, [x["text"] for x in cues])

    def test_phrases_kept_whole(self):
        texts = [c["text"] for c in build(self.TEXT)[0]]
        joined = " | ".join(texts)
        self.assertNotIn("for | her", joined)
        self.assertNotIn("her | son", joined)
        self.assertNotIn("size | 4", joined)
        self.assertNotIn("a | size", joined)

    def test_punctuation_ends_a_caption(self):
        texts = [c["text"] for c in build("Wait, I think we should go home now.")[0]]
        self.assertEqual(texts[0], "Wait,")

    def test_subject_stays_with_verb(self):
        texts = [c["text"] for c in build("Yesterday after a long day at work I finally called my mother back")[0]]
        for t in texts:
            self.assertNotEqual(core(t.split()[-1]), "i", texts)

    def test_hinglish_weak_words(self):
        texts = [c["text"] for c in build("Mujhe lagta hai ki tum aur main kal subah Delhi ke liye niklenge",
                                          language="hinglish")[0]]
        for t in texts[:-1]:
            self.assertNotIn(core(t.split()[-1]), {"ki", "aur", "ke", "ka", "toh"}, texts)

    def test_never_crosses_script_lines(self):
        cues, _, _ = build("Hold me close", "never let me go")
        self.assertEqual([c["text"] for c in cues], ["Hold me close", "never let me go"])

    def test_merge_short_option(self):
        words = words_of("Go now please", step=0.12, gap=0.01)
        tokens = captions.make_tokens(words)
        rules = line_rules(merge_short=True, max_words=1)
        rules["max_words"] = 1
        self.assertEqual(len(captions.build_cues(tokens, words, rules)), 3)  # can't fit: kept
        rules = line_rules(merge_short=True)
        cues = captions.build_cues(tokens, words, rules)
        self.assertEqual(len(cues), 1)

    def test_short_captions_allowed(self):
        words = words_of("Yes.", "No.", step=0.15)
        cues = captions.build_cues(captions.make_tokens(words), words, line_rules())
        self.assertEqual(len(cues), 2)
        self.assertLess(cues[0]["end"] - cues[0]["start"], 0.5)


class Speakers(unittest.TestCase):
    def test_labels_removed_and_split(self):
        texts, speakers, labels = captions.split_speakers(
            ["Sarah: Where were you last night", "Tom: At work", "I told you that", "Sarah:", "Fine"])
        self.assertEqual(texts, ["Where were you last night", "At work", "I told you that", "Fine"])
        self.assertEqual(speakers, ["Sarah", "Tom", "Tom", "Sarah"])
        self.assertEqual(labels, ["Sarah", "Tom"])

    def test_single_colon_is_text(self):
        texts, speakers, _ = captions.split_speakers(["Listen: the rain is falling", "and we sing"])
        self.assertEqual(texts[0], "Listen: the rain is falling")
        self.assertEqual(speakers, [None, None])

    def test_new_caption_at_speaker_change(self):
        words = words_of("I said", "no way", speakers=["A", "B"])
        for w in words:
            w["line"] = 0  # same line, different speakers
        words[2]["speaker"] = words[3]["speaker"] = "B"
        cues = captions.build_cues(captions.make_tokens(words), words, line_rules())
        self.assertEqual([c["text"] for c in cues], ["I said", "no way"])

    def test_pause_split_in_transcribe_mode(self):
        rules = captions.rules_for({"max_cue_seconds": None, "pause_split_seconds": None, "max_words": None},
                                   "vertical", transcribe=True)
        words = words_of("we went home then we slept", pauses={3: 1.2})
        cues = captions.build_cues(captions.make_tokens(words), words, rules)
        self.assertEqual(cues[0]["text"], "we went home")


class Numbers(unittest.TestCase):
    def tokens(self, text, language="en", exceptions=None):
        words = words_of(text)
        return [t["text"] for t in captions.make_tokens(words, language, exceptions)], words

    def test_digits(self):
        cases = {
            "for twelve years": "for 12 years",
            "Week one": "Week 1",
            "at seven a.m.": "at 7 a.m.",
            "seventy percent": "70%",
            "in a size four": "in a size 4",
            "twenty five people": "25 people",
            "twenty-five people": "25 people",
            "one hundred and five days": "105 days",
            "three thousand two hundred": "3200",
        }
        for spoken, shown in cases.items():
            self.assertEqual(" ".join(self.tokens(spoken)[0]), shown, spoken)

    def test_expressions_stay_words(self):
        for text in ("not one inch", "one time only", "you two", "the one I love", "no one came"):
            self.assertEqual(" ".join(self.tokens(text)[0]), text, text)

    def test_editable_exceptions(self):
        self.assertEqual(" ".join(self.tokens("seven years")[0]), "7 years")
        self.assertEqual(" ".join(self.tokens("seven years", exceptions=["seven years"])[0]), "seven years")
        self.assertEqual(captions.load_exceptions("# comment\n  Seven   Years \n\n"), ["seven years"])

    def test_number_spans_both_words_times(self):
        words = words_of("twenty five")
        tokens = captions.make_tokens(words)
        self.assertEqual(len(tokens), 1)
        cues = captions.build_cues(tokens, words, line_rules())
        self.assertEqual(cues[0]["text"], "25")
        self.assertEqual(cues[0]["start"], words[0]["start"])
        self.assertAlmostEqual(cues[0]["end"], words[1]["end"] + captions.END_TAIL, places=3)

    def test_punctuation_kept(self):
        self.assertEqual(self.tokens("I waited twenty minutes,")[0][-2:], ["20", "minutes,"])
        self.assertEqual(self.tokens("it was twenty.")[0][-1], "20.")

    def test_hinglish_counts(self):
        self.assertEqual(" ".join(self.tokens("do saal pehle", "hinglish")[0]), "2 saal pehle")
        self.assertEqual(" ".join(self.tokens("teen din baad", "hinglish")[0]), "3 din baad")
        self.assertEqual(" ".join(self.tokens("mujhe do na", "hinglish")[0]), "mujhe do na")  # do = give
        self.assertEqual(" ".join(self.tokens("ek baar aur", "hinglish")[0]), "ek baar aur")  # expression
        self.assertEqual(" ".join(self.tokens("do saal", "en")[0]), "do saal")  # only for Hinglish


class Cleanup(unittest.TestCase):
    def clean(self, *lines):
        words = words_of(*lines)
        return " ".join(t["text"] for t in captions.offline_cleanup(captions.make_tokens(words), words))

    def test_stray_quotes(self):
        self.assertEqual(self.clean('He said "hello there'), "He said hello there")
        self.assertEqual(self.clean('He said "hello" there'), 'He said "hello" there')
        self.assertEqual(self.clean("don't stop the players' bus"), "Don't stop the players' bus")
        self.assertEqual(self.clean("‘hi there"), "Hi there")

    def test_sentence_case_and_i(self):
        self.assertEqual(self.clean("so i went. then i'm home"), "So I went. Then I'm home")

    def test_caps_for_emphasis_stay(self):
        self.assertEqual(self.clean("this is SO good"), "This is SO good")


class WordByWord(unittest.TestCase):
    def test_one_cue_per_word_no_gaps_or_overlaps(self):
        words = words_of("I waited twenty five minutes", "then left", pauses={5: 2.0})
        tokens = captions.make_tokens(words)
        rules = line_rules(mode="word")
        cues = captions.build_cues(tokens, words, rules)
        self.assertEqual([c["text"] for c in cues], ["I", "waited", "25", "minutes", "then", "left"])
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a["end"], b["start"])
        self.assertEqual(cues[0]["end"], cues[1]["start"])  # short gap bridged
        self.assertLess(cues[3]["end"], cues[4]["start"])   # a real pause stays a pause
        self.assertEqual(cues[2]["start"], words[2]["start"])
        for c in cues:
            self.assertGreater(c["end"], c["start"])

    def test_squeezed_word_still_visible(self):
        words = words_of("My ex-wife left")
        words[0]["end"] = words[0]["start"]          # the aligner gave "My" no time
        words[1]["start"] = words[0]["start"]        # and "ex-wife" starts at the same instant
        cues = captions.build_cues(captions.make_tokens(words), words, line_rules(mode="word"))
        for c in cues:
            self.assertGreaterEqual(round(c["end"] - c["start"], 3), captions.MIN_VISIBLE)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a["end"], b["start"])

    def test_switching_mode_keeps_word_times(self):
        words = words_of("one two three four five six seven eight")
        tokens = captions.make_tokens(words)
        line = captions.build_cues(tokens, words, line_rules())
        word = captions.build_cues(tokens, words, line_rules(mode="word"))
        self.assertEqual(line[0]["start"], word[0]["start"])
        self.assertEqual(" ".join(c["text"] for c in line), " ".join(c["text"] for c in word))


class Timing(unittest.TestCase):
    def test_starts_on_first_word_ends_just_after_last(self):
        cues, _, words = build("Hello there.", "How are you?", pauses={2: 1.0})
        self.assertEqual(cues[0]["start"], words[0]["start"])
        self.assertAlmostEqual(cues[0]["end"], words[1]["end"] + captions.END_TAIL, places=3)

    def test_no_overlaps_when_words_touch(self):
        cues, _, _ = build("a b c d e f g h i j k", step=0.2, gap=0.0)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a["end"], b["start"])

    def test_unmatched_lines(self):
        def seg(*ws):
            return SimpleNamespace(words=[SimpleNamespace(word=f" {w}", start=s, end=e, probability=p)
                                          for w, s, e, p in ws])
        segments = [seg(("hello", 0.0, 0.4, 0.9), ("world", 0.5, 0.9, 0.8)),
                    seg(("not", 0.9, 0.9, 0.01), ("sung", 0.9, 0.9, 0.02)), seg(("here", 0.9, 0.91, 0.01)),
                    seg(("bye", 1.0, 1.5, 0.7))]
        words = captions.words_from_segments(segments, ["hello world", "not sung here", "bye"])
        self.assertEqual([w["line"] for w in words], [0, 0, 1, 1, 1, 2])  # 4 segments, 3 lines
        self.assertEqual(captions.unmatched_lines(words), [1])


class ChunkCheck(unittest.TestCase):
    def test_rejects_bad_cuts(self):
        words = words_of("I love you so much, my dear friend")
        tokens = captions.make_tokens(words)
        rules = line_rules()
        good = captions.line_chunks(tokens, words, rules)
        self.assertTrue(captions.check_chunks(tokens, words, good, rules))
        self.assertFalse(captions.check_chunks(tokens, words, [list(range(len(tokens)))], rules))  # too long
        self.assertFalse(captions.check_chunks(tokens, words, [[0, 1, 2, 3, 4, 5], [6, 7]], rules))
        self.assertFalse(captions.check_chunks(tokens, words, [[0, 1, 2], [3, 4, 5, 6], [7]], rules) and
                         core(tokens[2]["text"]) in captions.WEAK_END)


if __name__ == "__main__":
    unittest.main()

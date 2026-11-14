"""Script lines the aligner wasn't sure of (V4.9): recovered, flagged, not found, added back, repeats
in order; possible missing captions."""

import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import captions  # noqa: E402
import pipeline  # noqa: E402
import review  # noqa: E402
import script_lines  # noqa: E402


def words_for(lines, spans, probs=None):
    """Aligned words: each line's words spread over its (start, end), with a probability per line."""
    out = []
    for li, (line, (a, b)) in enumerate(zip(lines, spans)):
        ws = line.split()
        step = (b - a) / len(ws)
        for k, w in enumerate(ws):
            out.append({"word": w, "start": round(a + k * step, 3), "end": round(a + (k + 1) * step - 0.02, 3),
                        "line": li, "speaker": None, "probability": (probs or [0.9] * len(lines))[li]})
    return out


def squeezed(lines, li, at, words):
    """Line li's words all parked at one instant (the aligner lost its place)."""
    for w in words:
        if w["line"] == li:
            w["start"] = w["end"] = at
            w["probability"] = 0.0
    return words


def heard(text, a, b, prob=0.9):
    ws = text.split()
    step = (b - a) / len(ws)
    return [{"word": w, "start": round(a + k * step, 3), "end": round(a + (k + 1) * step - 0.02, 3), "prob": prob}
            for k, w in enumerate(ws)]


class FakeEar:
    """Stands in for listen_check.Ear: hears `audio` (heard words) inside whatever spans it's asked."""

    def __init__(self, audio):
        self.audio_words, self.calls, self.asked = audio, 0, []

    def hear_many(self, spans):
        self.calls += 1
        self.asked += spans
        return [[w for w in self.audio_words if a <= (w["start"] + w["end"]) / 2 <= b] for a, b in spans]


def ctx():
    return {"log": logging.getLogger("test.script_lines"), "duration": 20.0, "whisper_language_used": "en"}


def resolve(words, lines, ear, speech, threshold=0.15):
    with mock.patch.object(script_lines, "_realign", return_value=None):
        return script_lines.resolve(ctx(), words, lines, threshold=threshold, hearing=("medium", "en"),
                                    language="en", speech=speech, ear=ear)


LINES = ["The morning train was late", "Nobody said a single word", "I counted every minute",
         "Then the doors opened"]
SPANS = [(0.0, 2.0), (2.5, 4.5), (5.0, 7.0), (7.5, 9.5)]


class Resolve(unittest.TestCase):
    def test_confident_lines_are_untouched(self):
        words = words_for(LINES, SPANS)
        ear = FakeEar([])
        r = resolve(words, LINES, ear, [(0, 10)])
        self.assertEqual([x["status"] for x in r["lines"]], ["kept"] * 4)
        self.assertEqual(r["words"], words)
        self.assertEqual(ear.calls, 0)

    def test_heard_in_the_gap_is_recovered(self):
        # Line 3 was squeezed to the end of the file; the audio has it between lines 2 and 4.
        words = squeezed(LINES, 2, 19.9, words_for(LINES, SPANS))
        ear = FakeEar(heard("I counted every minute", 5.0, 7.0))
        r = resolve(words, LINES, ear, [(0, 10)])
        self.assertEqual(r["lines"][2]["status"], "recovered")
        self.assertEqual(r["recovered"], [2])
        self.assertEqual(r["lines"][2]["searched"], [4.48, 7.5])
        line3 = [w for w in r["words"] if w["line"] == 2]
        self.assertEqual(len(line3), 4)
        self.assertGreaterEqual(line3[0]["start"], 4.48)
        self.assertLessEqual(line3[-1]["end"], 7.5)
        self.assertEqual(ear.calls, 1)

    def test_spelling_and_number_variants_still_count_as_heard(self):
        lines = ["Main tumse baat karna chahta hoon", "Lekin tum mera phone nahin uthate", "Chalo kal fir milte hain"]
        spans = [(0.0, 3.0), (3.5, 6.0), (6.5, 9.0)]
        words = words_for(lines, spans, [0.8, 0.05, 0.8])
        ear = FakeEar(heard("lekin tum mera fone nahi uthate", 3.5, 6.0))
        with mock.patch.object(script_lines, "_realign", return_value=None):
            r = script_lines.resolve(ctx(), words, lines, threshold=0.15, hearing=("apex", "en"), language="hinglish",
                                     speech=[(0, 10)], ear=ear)
        self.assertEqual(r["lines"][1]["status"], "recovered", r["lines"][1])
        words = words_for(["I waited twenty two minutes"], [(0.0, 3.0)], [0.05])
        r = resolve(words, ["I waited twenty two minutes"], FakeEar(heard("I waited 22 minutes", 0, 3)), [(0, 3)])
        self.assertEqual(r["lines"][0]["status"], "recovered", r["lines"][0])

    def test_speech_without_a_clear_match_is_flagged(self):
        words = words_for(LINES, SPANS, [0.9, 0.9, 0.05, 0.9])
        ear = FakeEar(heard("something else entirely here", 5.0, 7.0))
        r = resolve(words, LINES, ear, [(0, 10)])
        self.assertEqual(r["lines"][2]["status"], "flagged")
        self.assertEqual(r["flagged"], [2])
        self.assertEqual(len([w for w in r["words"] if w["line"] == 2]), 4)  # kept, with its timing

    def test_voice_but_nothing_written_is_flagged_not_dropped(self):
        words = words_for(LINES, SPANS, [0.9, 0.9, 0.05, 0.9])
        r = resolve(words, LINES, FakeEar([]), [(5.0, 7.0)])
        self.assertEqual(r["lines"][2]["status"], "flagged")
        self.assertEqual(r["not_found"], [])

    def test_no_speech_in_the_gap_is_not_found(self):
        words = words_for(LINES, SPANS, [0.9, 0.9, 0.05, 0.9])
        r = resolve(words, LINES, FakeEar([]), [(0, 2.0), (2.5, 4.5), (7.5, 9.5)])
        self.assertEqual(r["lines"][2]["status"], "not_found")
        self.assertEqual(r["not_found"], [{"line": 3, "text": LINES[2], "gap": [4.48, 7.5]}])
        self.assertFalse([w for w in r["words"] if w["line"] == 2])
        self.assertEqual(len(r["words"]), sum(len(line.split()) for line in LINES) - 4)

    def test_repeats_are_placed_in_order(self):
        # A chorus: lines 2-5 repeat "smile a little" / "just for me"; the aligner lost both repeats
        # in the middle (squeezed), and the audio has them in order.
        lines = ["Hold my hand", "Smile a little", "Just for me", "Smile a little", "Just for me", "Walk with me"]
        spans = [(0.0, 1.5), (2.0, 3.5), (4.0, 5.5), (6.0, 7.5), (8.0, 9.5), (10.0, 11.5)]
        words = words_for(lines, spans)
        for li in (2, 3, 4):
            squeezed(lines, li, 19.9, words)
        audio = heard("just for me", 4.0, 5.5) + heard("smile a little", 6.0, 7.5) + heard("just for me", 8.0, 9.5)
        r = resolve(words, lines, FakeEar(audio), [(0, 12)])
        self.assertEqual([x["status"] for x in r["lines"]], ["kept", "kept", "recovered", "recovered", "recovered", "kept"])
        starts = [min(w["start"] for w in r["words"] if w["line"] == li) for li in range(6)]
        self.assertEqual(starts, sorted(starts))
        for li, (a, b) in zip((2, 3, 4), spans[2:5]):
            first = min(w["start"] for w in r["words"] if w["line"] == li)
            self.assertLess(abs(first - a), 0.6, (li, first, a))

    def test_without_hearing_low_lines_are_kept_and_flagged(self):
        words = words_for(LINES, SPANS, [0.9, 0.05, 0.9, 0.9])
        with mock.patch.object(script_lines, "_realign", return_value=None):
            r = script_lines.resolve(ctx(), words, LINES, threshold=0.15, hearing=None, language="en",
                                     speech=None, ear=None)
        self.assertEqual(r["lines"][1]["status"], "flagged")
        self.assertEqual(len(r["words"]), len(words))

    def test_confidence(self):
        self.assertEqual(script_lines.confidence([])[1], script_lines.NO_PLACE)
        ws = words_for(["a b c d"], [(0, 2)], [0.6])
        self.assertEqual(script_lines.confidence(ws), (0.6, ""))
        squeezed(["a b c d"], 0, 1.0, ws)
        self.assertEqual(script_lines.confidence(ws)[0], 0.0)


class Reanchor(unittest.TestCase):
    """The aligner lost its place at line 3 and parked lines 3–8 at the end of the file (V4.9)."""

    LINES = ["The morning train was late", "Nobody said a single word", "I counted every minute",
             "Then the doors opened wide", "We stood there pressed together", "All the way home tonight",
             "The city lights were fading", "And nobody said goodbye"]
    TRUE = [(0.0, 2.0), (2.5, 4.5), (5.0, 7.0), (7.5, 9.5), (10.0, 12.0), (12.5, 14.5), (15.0, 17.0), (17.5, 19.5)]

    def lost(self):
        words = words_for(self.LINES, self.TRUE)
        for li in range(2, 8):
            squeezed(self.LINES, li, 19.9, words)
        return words

    def test_detects_where_it_got_lost(self):
        self.assertEqual(script_lines.lost_at(self.lost(), self.LINES, 0.15)[0], 2)
        self.assertIsNone(script_lines.lost_at(words_for(self.LINES, self.TRUE), self.LINES, 0.15))
        low = words_for(self.LINES, self.TRUE, [0.9, 0.9, 0.1, 0.1, 0.1, 0.9, 0.9, 0.9])
        self.assertEqual(script_lines.lost_at(low, self.LINES, 0.15)[0], 2)
        order = words_for(self.LINES, [(0, 2), (2.5, 4.5), (1.0, 1.5)] + self.TRUE[3:])
        self.assertIn("starts", script_lines.lost_at(order, self.LINES, 0.15)[1])

    def reanchor(self, audio):
        with mock.patch.object(script_lines, "_realign", return_value=None):
            return script_lines.reanchor(ctx(), self.lost(), self.LINES, threshold=0.15, ear=FakeEar(audio),
                                         language="en", numbers_language="en", differ=0.6)

    def test_lines_go_back_to_where_they_are_said(self):
        audio = [w for line, (a, b) in zip(self.LINES[2:], self.TRUE[2:]) for w in heard(line, a, b)]
        words, rounds = self.reanchor(audio)
        by = script_lines.line_words(words)
        for li in range(2, 8):
            self.assertLess(abs(by[li][0]["start"] - self.TRUE[li][0]), 0.4, (li, by[li][0]["start"]))
        self.assertEqual(rounds[0]["lost_at"], 3)
        self.assertEqual(rounds[0]["anchor"], 2)
        self.assertEqual(rounds[0]["anchored"], 6)
        self.assertEqual(rounds[0]["lines"][0]["before"], [19.9, 19.9])
        self.assertEqual(len(rounds), 1)  # nothing left lost: one round was enough

    def test_unheard_lines_are_placed_between_heard_ones_in_order(self):
        keep = (2, 5, 7)  # Whisper heard only three of the six lines
        audio = [w for li in keep for w in heard(self.LINES[li], *self.TRUE[li])]
        words, rounds = self.reanchor(audio)
        by = script_lines.line_words(words)
        starts = [by[li][0]["start"] for li in range(8)]
        self.assertEqual(starts, sorted(starts))
        for li in keep:
            self.assertLess(abs(by[li][0]["start"] - self.TRUE[li][0]), 0.4)
        self.assertTrue(all(by[li][0]["start"] < by[li][-1]["end"] for li in range(8)))

    def test_nothing_heard_does_not_loop(self):
        words, rounds = self.reanchor([])
        self.assertLessEqual(len(rounds), script_lines.REANCHOR_ROUNDS)


class MarksAndAddBack(unittest.TestCase):
    def test_flagged_line_marks_its_caption(self):
        words = words_for(LINES, SPANS)
        tokens = captions.make_tokens(words, "en")
        rules = captions.rules_for({"max_cue_seconds": None, "pause_split_seconds": None, "max_words": None}, "vertical")
        cues = captions.build_cues(tokens, words, rules)
        tl = {"script_flags": {"2": "flagged", "1": "recovered"}}
        pipeline._mark_script_lines(cues, tl, tokens, words)
        by_text = {c["text"]: c.get("script") for c in cues}
        flagged = [c for c in cues if (c.get("script") or {}).get("status") == "flag"]
        self.assertTrue(flagged)
        self.assertTrue(all(c["script"]["reason"] == pipeline.SCRIPT_FLAG for c in flagged))
        self.assertTrue(any((v or {}).get("status") == "recovered" for v in by_text.values()))
        self.assertEqual(pipeline.script_summary({**tl, "not_found": [{"line": 4}]}),
                         {"recovered": 1, "flagged": 1, "not_found": 1})

    def test_added_back_line_reaches_the_srt_only_when_added(self):
        cues = [{"start": 0.0, "end": 2.0, "text": "The morning train was late"},
                {"start": 2.5, "end": 4.5, "text": "Nobody said a single word"}]
        self.assertNotIn("I counted every minute", pipeline.srt_text(review.clean_cues(cues)))
        added = cues + [{"start": 5.0, "end": 7.0, "text": "I counted every minute", "added": 3}]
        clean = review.clean_cues(added)
        self.assertEqual(clean[-1]["added"], 3)
        self.assertIn("I counted every minute", pipeline.srt_text(clean))

    def test_script_flag_survives_saving(self):
        clean = review.clean_cues([{"start": 0, "end": 1, "text": "Hi", "script": {"status": "flag", "reason": "x",
                                                                                   "lines": [2], "resolved": True}}])
        self.assertEqual(clean[0]["script"], {"status": "flag", "reason": "x", "lines": [2], "resolved": True})


class MissingCaptions(unittest.TestCase):
    def test_speech_without_a_caption(self):
        speech = [(0.0, 3.0), (5.0, 9.0), (12.0, 12.8)]
        cues = [{"start": 0.0, "end": 3.0}, {"start": 5.0, "end": 6.0}]
        self.assertEqual(script_lines.missing_captions(speech, cues),
                         [{"start": 6.0, "end": 9.0, "seconds": 3.0}])

    def test_short_stretches_are_ignored(self):
        self.assertEqual(script_lines.missing_captions([(0.0, 1.4)], []), [])
        self.assertEqual(script_lines.missing_captions([(0.0, 1.6)], []), [{"start": 0.0, "end": 1.6, "seconds": 1.6}])

    def test_gap_inside_a_speech_region(self):
        cues = [{"start": 0.0, "end": 2.0}, {"start": 6.0, "end": 8.0}]
        self.assertEqual(script_lines.missing_captions([(0.0, 8.0)], cues),
                         [{"start": 2.0, "end": 6.0, "seconds": 4.0}])

    def test_regions_split_by_a_short_pause_are_one(self):
        self.assertEqual(script_lines.missing_captions([(0.0, 1.0), (1.3, 2.4)], []),
                         [{"start": 0.0, "end": 2.4, "seconds": 2.4}])

    def test_never_merged_across_a_caption(self):
        cues = [{"start": 1.0, "end": 1.3}]
        self.assertEqual(script_lines.missing_captions([(0.0, 1.0), (1.3, 2.2)], cues), [])

    def test_one_word_stretched_over_a_missing_line(self):
        cues = [{"start": 4.0, "end": 8.4, "words": [{"start": 4.1, "end": 8.3}]},
                {"start": 10.6, "end": 13.4, "words": [{"start": 10.6, "end": 13.4}]},
                {"start": 13.4, "end": 16.9, "words": [{"start": 13.4, "end": 14.0}, {"start": 14.1, "end": 16.8}]}]
        found = script_lines.missing_captions([(9.5, 19.9)], cues, word_cap=1.2)
        self.assertTrue(any(m["start"] <= 12.0 and m["end"] >= 13.0 for m in found), found)
        self.assertEqual(script_lines.missing_captions([(9.5, 13.4)], cues[1:2]), [])  # without the cap: covered

    def test_fully_covered(self):
        self.assertEqual(script_lines.missing_captions([(1.0, 5.0)], [{"start": 0.5, "end": 5.5}]), [])


if __name__ == "__main__":
    unittest.main()

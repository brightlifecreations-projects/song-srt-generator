"""Hinglish drafts with the Apex model (DECISIONS.md V4.2).

- draft(): Oriserve's Whisper-Hindi2Hinglish-Apex writes Roman-script Hindi directly — no
  Devanagari → transliteration step. Lines break at sentence ends.
- Accurate mode adds a second opinion: the V3.8 chain (Whisper medium in Hindi, temperature 0,
  transliterated). Words where the two disagree, or that Apex itself gave a low probability, are
  flagged; short flagged stretches are re-transcribed on their own and the re-check replaces
  the stretch only when it's more confident.
- review_marks() turns the flags into highlight positions in the final draft text, the same
  shape as corrections.apply()'s hits, so the draft box can highlight both.

Words are dicts {"word", "start", "end", "prob", "line"} (prob = Whisper's word probability).
"""

import difflib
import re
import statistics
import time

import config
import corrections
import hinglish_model

SENTENCE_END = re.compile(r"[.?!।]['\"\)]*$")
AGREE = 0.6            # word similarity (0–1) at or above which two models "agree"
MERGE_AGREE = 0.8      # the same for one word against two run together ("kalke" ↔ "kal ke")
GAP = 0.7              # alignment cost of a word one model has and the other hasn't
RECHECK_PAD = 0.5      # seconds of audio kept around a re-checked stretch
RECHECK_MAX = 8.0      # longer doubtful stretches aren't re-checked
RECHECK_GAIN = 0.15    # the re-check must be this much more confident (mean word probability)…
RECHECK_MIN = 0.75     # …and confident in itself. Looser limits (+0.05, no floor) let two wrong
                       # replacements through in testing ("hoke?" → "khushaak ke" on a song).
SAMPLE_RATE = 16000
# Apex writes the literal word "nan" over music and silence (empty training transcripts that
# became NaN, by the look of it) and then skips ahead: on a song it lost the whole first verse.
# Suppressing those tokens ("nan", " nan", " Nan" in its tokenizer) brought the verse back.
NAN_TOKENS = [17622, 14067, 18852]
SUPPRESS = [-1, *NAN_TOKENS]  # -1 = Whisper's default suppress list

_model = None


def load_model():
    """The Apex model (int8), loaded once per process. Downloads it first if needed."""
    global _model
    if _model is None:
        import stable_whisper

        path = hinglish_model.download()
        device = config.DEVICE
        if device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        _model = stable_whisper.load_faster_whisper(
            str(path), device=device, compute_type="int8" if device == "cpu" else "int8_float16")
    return _model


# ---------------------------------------------------------------------------
# Drafts

def _words_of(segments, offset: float = 0.0) -> list[dict]:
    out = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text and corrections.key_of(text) != "nan":
                out.append({"word": text, "start": round(float(w.start) + offset, 3),
                            "end": round(float(w.end) + offset, 3),
                            "prob": round(float(getattr(w, "probability", 1.0) or 0.0), 3)})
    return out


def _break_lines(segments) -> list[dict]:
    """Words with a line number: a new line per Whisper segment and after each sentence end."""
    words, line = [], 0
    for seg in segments:
        seg_words = _words_of([seg])
        for i, w in enumerate(seg_words):
            words.append({**w, "line": line})
            if SENTENCE_END.search(w["word"]) and i < len(seg_words) - 1:
                line += 1
        if seg_words:
            line += 1
    return words


def lines_text(words: list[dict]) -> list[str]:
    lines: dict[int, list[str]] = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w["word"])
    return [" ".join(ws) for _, ws in sorted(lines.items())]


def draft(audio_path, hotwords: str, log) -> list[dict]:
    """Apex's transcription of the whole file, as words with line numbers."""
    model = load_model()
    t0 = time.perf_counter()
    result = model.transcribe(str(audio_path), language="en", task="transcribe", beam_size=5,
                              condition_on_previous_text=False, verbose=None, suppress_tokens=SUPPRESS,
                              **({"hotwords": hotwords} if hotwords else {}))
    words = _break_lines(result.segments)
    log.info("Hinglish model (Apex): %d words in %.1fs", len(words), time.perf_counter() - t0)
    return words


def second_opinion(audio_path, medium, spellings: dict, log) -> list[str]:
    """The V3.8 chain's words: Whisper medium in Hindi (temperature 0: deterministic and, on the
    test reel, both faster and closer than with fallback), transliterated, house spellings on."""
    import transliterate

    t0 = time.perf_counter()
    result = medium.transcribe(str(audio_path), language="hi", task="transcribe", beam_size=5,
                               temperature=0.0, condition_on_previous_text=False, verbose=None)
    text = transliterate.to_hinglish("\n".join(s.text.strip() for s in result.segments))
    text, _ = corrections.apply(text, spellings)
    log.info("Second opinion (medium, Hindi → Hinglish) in %.1fs: %s", time.perf_counter() - t0,
             text.replace("\n", " / "))
    return text.split()


# ---------------------------------------------------------------------------
# Comparing two transcriptions word by word

_DIGRAPHS = (("ph", "f"), ("sh", "s"), ("ch", "c"), ("kh", "k"), ("gh", "g"), ("th", "t"),
             ("dh", "d"), ("bh", "b"), ("jh", "j"), ("ck", "k"), ("c", "k"), ("q", "k"),
             ("x", "ks"), ("z", "j"), ("w", "v"))


def skeleton(word: str) -> str:
    """A rough sound key for Roman-script Hindi and English: consonants only, h-digraphs and
    look-alike letters merged ("reaction" → rktn, "ryakshan" → rksn; "exam" → aksm, "ekjam" → akjm)."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    for a, b in _DIGRAPHS:
        w = w.replace(a, b)
    lead = "a" if w[0] in "aeiou" else ""
    core = re.sub(r"[aeiouh]", "", w)
    return re.sub(r"(.)\1+", r"\1", lead + core)


def similarity(a: str, b: str) -> float:
    plain = difflib.SequenceMatcher(a=re.sub(r"[^a-z]", "", a.lower()),
                                    b=re.sub(r"[^a-z]", "", b.lower())).ratio()
    sa, sb = skeleton(a), skeleton(b)
    if min(len(sa), len(sb)) < 3:  # too short for a sound key to mean anything ("naya", "nahi")
        return plain
    return max(plain, difflib.SequenceMatcher(a=sa, b=sb).ratio())


def compare(words: list[str], other: list[str]) -> list[str | None]:
    """For each word of `words`, None where `other` agrees, else what `other` has there ("" when
    it has nothing). An alignment that allows one word to match two of the other's ("kalke" ↔
    "kal ke"); such a merge only counts as agreement when the joined words match closely."""
    n, m = len(words), len(other)
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == inf:
                continue
            moves = [(1, 0, GAP), (0, 1, GAP)]
            for di, dj in ((1, 1), (1, 2), (2, 1)):
                if i + di <= n and j + dj <= m:
                    sim = similarity("".join(words[i:i + di]), "".join(other[j:j + dj]))
                    if di + dj > 2 and sim < MERGE_AGREE:
                        continue
                    moves.append((di, dj, (1 - sim) * max(di, dj)))
            for di, dj, step in moves:
                if i + di <= n and j + dj <= m and c + step < cost[i + di][j + dj]:
                    cost[i + di][j + dj] = c + step
                    back[i + di][j + dj] = (i, j)
    out: list[str | None] = [None] * n
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj = back[i][j]
        a, b = words[pi:i], other[pj:j]
        if a and (not b or similarity("".join(a), "".join(b)) < AGREE):
            for k in range(pi, i):
                out[k] = " ".join(b)
        i, j = pi, pj
    return out


def flag(words: list[dict], other: list[str] | None, spellings: dict | None = None) -> list[dict]:
    """Mark each word "disagree" (the second opinion heard something else), "unsure" (low
    probability) or None. A word the re-check replaced keeps its "rechecked" mark. Both sides are
    compared with the house spellings applied (the second opinion already has them)."""
    def key(word):
        k = corrections.key_of(word) or word
        row = (spellings or {}).get(k)
        return corrections.key_of(row["correct"]) if row and not row["disabled"] else k

    keys = [key(w["word"]) for w in words]
    diffs = compare(keys, [key(o) for o in other]) if other is not None else [None] * len(words)
    for w, d in zip(words, diffs):
        if w.get("flag") == "rechecked":
            continue
        w.pop("flag", None)
        w.pop("other", None)
        if d is not None:
            w["flag"], w["other"] = "disagree", d
        elif 0.005 <= w["prob"] < config.LOW_CONFIDENCE:  # faster-whisper reports exactly 0 for
            w["flag"] = "unsure"                              # the first word when hotwords are set
    return words


# ---------------------------------------------------------------------------
# Re-checking doubtful stretches

def stretches(words: list[dict]) -> list[tuple[int, int]]:
    """Runs of flagged words (index ranges, end exclusive), joined across one unflagged word."""
    out = []
    for i, w in enumerate(words):
        if not w.get("flag") or w.get("flag") == "rechecked":
            continue
        if out and i - out[-1][1] <= 1 and words[i]["line"] == words[out[-1][0]]["line"]:
            out[-1] = (out[-1][0], i + 1)
        else:
            out.append((i, i + 1))
    return out


def recheck(audio_path, words: list[dict], hotwords: str, log) -> int:
    """Re-transcribe each short doubtful stretch on its own and keep the more confident version.
    Replaced words get flag "rechecked" and the first version in "other". Returns how many
    stretches were replaced."""
    from faster_whisper.audio import decode_audio

    spans = [s for s in stretches(words)
             if words[s[1] - 1]["end"] - words[s[0]]["start"] <= RECHECK_MAX]
    if not spans:
        return 0
    model = load_model()
    audio = decode_audio(str(audio_path), sampling_rate=SAMPLE_RATE)
    replaced = 0
    for a, b in reversed(spans):  # from the end, so earlier indices stay valid
        old = words[a:b]
        t0 = max(0.0, old[0]["start"] - RECHECK_PAD)
        t1 = min(len(audio) / SAMPLE_RATE, old[-1]["end"] + RECHECK_PAD)
        segments, _ = model.transcribe_original(
            audio[int(t0 * SAMPLE_RATE):int(t1 * SAMPLE_RATE)], language="en", beam_size=5,
            temperature=0.0, word_timestamps=True, condition_on_previous_text=False,
            suppress_tokens=SUPPRESS, **({"hotwords": hotwords} if hotwords else {}))  # no context prompt: on clips this
        # short it made Apex return nothing at all
        lo, hi = old[0]["start"] - 0.05, old[-1]["end"] + 0.05
        new = [w for w in _words_of(segments, offset=t0)
               if lo <= (w["start"] + w["end"]) / 2 <= hi and corrections.key_of(w["word"])]
        # The clip includes a little of the words either side; don't take them in twice.
        if new and a > 0 and corrections.key_of(new[0]["word"]) == corrections.key_of(words[a - 1]["word"]):
            new = new[1:]
        if new and b < len(words) and corrections.key_of(new[-1]["word"]) == corrections.key_of(words[b]["word"]):
            new = new[:-1]
        old_text = " ".join(w["word"] for w in old)
        new_text = " ".join(w["word"] for w in new)
        old_conf = statistics.mean(w["prob"] for w in old)
        new_conf = statistics.mean(w["prob"] for w in new) if new else 0.0
        better = (new and corrections.key_of(new_text) != corrections.key_of(old_text)
                  and new_conf >= max(old_conf + RECHECK_GAIN, RECHECK_MIN) and 0.5 <= len(new) / len(old) <= 2)
        log.info("Re-check %.2f–%.2fs: “%s” (%.2f) vs “%s” (%.2f) -> %s", t0, t1, old_text, old_conf,
                 new_text, new_conf, "replaced" if better else "kept")
        if better:
            if SENTENCE_END.search(old[-1]["word"]) and not SENTENCE_END.search(new[-1]["word"]):
                new[-1]["word"] += old[-1]["word"][-1]  # keep the line's full stop
            for w in new:
                w.update(line=old[0]["line"], flag="rechecked", other=old_text)
            words[a:b] = new
            replaced += 1
    return replaced


# ---------------------------------------------------------------------------
# Highlights in the final draft

def review_marks(words: list[dict], final_text: str) -> list[dict]:
    """Flagged words → [{"line", "start", "end", "replacement" (the word as it now reads),
    "original" (what the other model or the first pass had), "kind"}], with character offsets
    into final_text's lines. Words since rewritten by a spelling or correction keep their mark
    when the rewrite was one word for one word."""
    final = final_text.split("\n")  # spellings and corrections never add or remove lines
    by_line: dict[int, list[dict]] = {}
    for w in words:
        by_line.setdefault(w["line"], []).append(w)
    marks = []
    for n, line_words in enumerate(by_line[k] for k in sorted(by_line)):
        if n >= len(final) or not any(w.get("flag") for w in line_words):
            continue
        tokens = final[n].split(" ")
        sm = difflib.SequenceMatcher(a=[corrections.key_of(w["word"]) for w in line_words],
                                     b=[corrections.key_of(t) for t in tokens], autojunk=False)
        offsets, pos = [], 0
        for t in tokens:
            offsets.append(pos)
            pos += len(t) + 1
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op not in ("equal", "replace") or i2 - i1 != j2 - j1:
                continue
            for i, j in zip(range(i1, i2), range(j1, j2)):
                w = line_words[i]
                if not w.get("flag"):
                    continue
                lead, core, _ = corrections._core(tokens[j])
                if not core:
                    continue
                start = offsets[j] + len(lead)
                marks.append({"line": n, "start": start, "end": start + len(core), "replacement": core,
                              "original": w.get("other", ""), "kind": w["flag"],
                              "probability": w["prob"]})
    return marks

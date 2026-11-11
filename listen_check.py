"""Listening check (DECISIONS.md V4.8): every caption checked against the audio before download.

After the captions are built (and after the final AI check), Whisper listens to the audio again,
offline, without being told the caption text:

1. Re-listen. Captions are grouped into windows of up to WINDOW_MAX seconds (padded by SEARCH), one
   Whisper call each: Whisper hears 30 s at a time whatever the clip length, so one call per caption
   would cost about as much per caption as one window. A caption whose words the window pass heard
   differently is then re-transcribed on its own padded clip; only a difference both passes agree
   on counts. The Hinglish model listens for Hinglish (when installed), else the alignment model.
   Numbers and symbols don't count ("4" = "four"): both sides go through the caption rules first.
2. Timing. Every caption must start no earlier than EARLY before its first word and end no later
   than LATE after its last (Word by word's bridge to the next word is allowed), with no overlaps
   and nothing too short to read. Against the re-listen: a caption that starts or ends more than
   OFF away from where its words were heard is re-aligned on its own stretch of audio (stable-ts);
   when the re-alignment and the re-listen agree, the new word times are used.
3. Fix what it's confident about: the timing above, and a word the final AI check changed when
   the original word and the re-listen agree (the AI's change is put back). The user's own words
   (the script, the approved transcript) are never changed: a difference there is flagged.
4. Flag the rest: cue["listen"] = {"status": "flag", "reasons": ["heard ‘road’, caption says
   ‘zaroor’"]}, shown in the Review panel as "Check this".

Fixes are made in the job's timeline (word times, the AI's token text), and the cues are rebuilt
from it with the same cut points, so the downloaded SRT is the fixed one and a later Line by line /
Word by word switch keeps the fixes. What Whisper heard in the windows is kept in the timeline too
("heard"), so a switch only re-transcribes the few captions it needs to confirm.
"""

import difflib
import logging
import re
import time
import traceback

import captions
import config

PAD = 0.5            # seconds of audio either side of a caption, in windows and per-caption clips
WINDOW_MAX = 28.0    # windows of whole captions up to this long (Whisper hears 30 s at a time)
EARLY = 0.2          # a caption may start this much before its first word…
LATE = 0.3           # …and end this much after its last
OFF = 0.5            # the re-listen and the caption must disagree by more than this on timing
SEARCH = 1.5         # heard words this far either side of a caption are compared with it (the
                     # semi-global match picks its own), so a caption timed 1 s off is still found
REALIGN_AGREE = 0.3  # the re-alignment must land this close to where the words were heard
AGREE = 0.6          # word similarity at or above which the caption word and the heard word agree
DIFFER = 0.45        # Hinglish only: below this they're different words; between, a spelling or sung
                     # variant ("husde" / "hansate"). Other languages: below AGREE ("older" / "younger" is 0.5)
SURE = 0.5           # a heard word is evidence only at this probability or above
GAP = 0.85           # alignment cost of a caption word that wasn't heard (a heard word that's at
                     # all like it, similarity above 0.15, is taken as what was heard instead)
GAP_HEARD = 0.5      # …and of an extra heard word inside the caption
CONFIRM_MAX = 120    # per-caption re-listens per check (a long song in Word by word could ask for
                     # hundreds; beyond this, the window pass alone decides)
PACK_MAX = 28.0      # per-caption clips are packed, 1 s of silence apart, into one Whisper call of up
PACK_GAP = 1.0       # to this long: a 4 s clip costs Whisper almost as much as 30 s (measured 4.4 s
                     # vs 9.1 s), so packing makes the confirmations ~6x cheaper
SR = 16000


# ---------------------------------------------------------------------------
# Comparing words

def _key(text: str) -> str:
    """A word as compared: clean-caption rules, lower case, letters and digits only."""
    return re.sub(r"[\W_]", "", captions.clean_caption(text).casefold())


# A number word on its own is the same as its digits, whatever the context ("ab do parts" = "ab 2
# parts"; the caption rules only convert "do" before a count word, the AI may convert it anyway).
_NUMBER_WORDS = {**{w: str(v) for w, v in captions._UNITS.items()}, **{w: str(v) for w, v in captions._TENS.items()},
                 **{w: str(v) for w, v in captions._HINGLISH.items()}, "tin": "3", "teen": "3",
                 **{w: f"{captions._UNITS.get(c, captions._TENS.get(c, 0))}" for w, c in captions._ORDINALS.items()
                    if c in captions._UNITS or c in captions._TENS}}


def _as_number(key: str) -> str:
    if key.isdigit():
        return key
    m = re.fullmatch(r"(\d+)(st|nd|rd|th)", key)
    return m.group(1) if m else _NUMBER_WORDS.get(key, key)


def similarity(a: str, b: str) -> float:
    """0–1 for two keys. Numbers must match exactly; Roman letters by spelling or sound (Hinglish
    spellings vary: "zaroor" / "jaroor"); other scripts by spelling."""
    a, b = _as_number(a), _as_number(b)
    if a == b:
        return 1.0
    if not a or not b or any(ch.isdigit() for ch in a + b):
        return 0.0
    if a.isascii() and b.isascii():
        import hinglish
        if hinglish.skeleton(a) and hinglish.skeleton(a) == hinglish.skeleton(b):
            return 1.0  # the same sounds, spelt differently: "fir" / "phir", "woh" / "vah"
        return hinglish.similarity(a, b)
    return difflib.SequenceMatcher(a=a, b=b).ratio()


def match(caption: list[str], heard: list[str]) -> list[tuple[list[int], float]]:
    """For each caption word (key), the heard words (indices) it matches and how well (0–1), by a
    semi-global alignment: heard words before and after the caption are free (the clip is padded),
    one word may match two run together ("kalke" / "kal ke"). ([], 0.0) = not heard."""
    n, m = len(caption), len(heard)
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        cost[0][j] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == inf:
                continue
            moves = []
            if i < n:
                moves.append((1, 0, GAP))
            if j < m and 0 < i < n:
                moves.append((0, 1, GAP_HEARD))
            for di, dj in ((1, 1), (1, 2), (2, 1)):
                if i + di <= n and j + dj <= m:
                    sim = similarity("".join(caption[i:i + di]), "".join(heard[j:j + dj]))
                    if di + dj > 2 and sim < 0.8:
                        continue
                    moves.append((di, dj, (1 - sim) * max(di, dj)))
            for di, dj, step in moves:
                if c + step < cost[i + di][j + dj]:
                    cost[i + di][j + dj] = c + step
                    back[i + di][j + dj] = (i, j)
    out: list[tuple[list[int], float]] = [([], 0.0)] * n
    j = min(range(m + 1), key=lambda k: cost[n][k])
    i = n
    while i > 0:
        pi, pj = back[i][j]
        if i > pi and j > pj:
            sim = similarity("".join(caption[pi:i]), "".join(heard[pj:j]))
            for k in range(pi, i):
                out[k] = (list(range(pj, j)), sim)
        i, j = pi, pj
    return out


def heard_tokens(heard: list[dict], language: str | None) -> list[dict]:
    """Heard words -> comparable tokens: numbers as digits, as in the captions ("twenty two" ->
    "22", spanning both words' times), symbols gone. [{"text", "key", "start", "end", "prob"}]."""
    words = [{"word": w["word"], "start": w["start"], "end": w["end"], "line": 0, "speaker": None}
             for w in heard]
    out = []
    for t in captions.make_tokens(words, language, exceptions=[]):
        key = _key(t["text"])
        if not key:
            continue
        span = heard[t["w0"]:t["w1"] + 1]
        out.append({"text": captions.clean_caption(t["text"]), "key": key, "start": span[0]["start"],
                    "end": span[-1]["end"], "prob": min(w["prob"] for w in span)})
    return out


def judge(caption_words: list[str], heard: list[dict], differ: float = AGREE) -> list[dict]:
    """Each caption word against what was heard around it: {"word", "state": "agree" | "differ" |
    "unheard", "heard": text, "prob", "start", "end"} (heard times when it was heard)."""
    keys = [_key(w) for w in caption_words]
    pairs = match(keys, [h["key"] for h in heard])
    out = []
    for word, (idx, sim) in zip(caption_words, pairs):
        if not idx:
            out.append({"word": word, "state": "unheard", "heard": "", "prob": 0.0})
            continue
        hs = [heard[k] for k in idx]
        out.append({"word": word, "state": "agree" if sim >= AGREE else "close" if sim >= differ else "differ",
                    "heard": " ".join(h["text"] for h in hs), "prob": min(h["prob"] for h in hs),
                    "start": hs[0]["start"], "end": hs[-1]["end"]})
    return out


def text_issues(judged: list[dict], language: str | None) -> list[int]:
    """Caption word positions where the audio clearly says something else: heard with confidence
    as a different word. Two function words heard as each other ("a" / "the", "ka" / "ke") don't
    count; neither does a word Whisper didn't hear (that's usually Whisper, not the caption), unless
    nothing of the caption was heard at all."""
    weak = captions.weak_end(language)
    out = []
    for k, j in enumerate(judged):
        if j["state"] == "differ" and j["prob"] >= SURE:
            a, b = captions.core(j["word"]), captions.core(j["heard"])
            if a in weak and b in weak:
                continue
            out.append(k)
    if judged and all(j["state"] == "unheard" for j in judged) and len(judged) >= 2:
        out = list(range(len(judged)))
    return out


# ---------------------------------------------------------------------------
# Timing

def timing_rules(cues: list[dict], rules: dict) -> list[list[str]]:
    """The fixed timing rules, per cue: reasons it breaks them (empty = fine). The cue's own words
    are the reference: start no earlier than EARLY before the first, end no later than LATE after
    the last (Word by word may bridge a short gap to the next word), no overlap, long enough."""
    word_mode = rules["mode"] == "word"
    minimum = rules.get("min_word", captions.MIN_WORD) if word_mode else rules.get("min_visible", captions.MIN_VISIBLE)
    audio_end = rules.get("audio_end")
    out = []
    for i, c in enumerate(cues):
        nxt = cues[i + 1] if i + 1 < len(cues) else None
        first, last = c["words"][0]["start"], c["words"][-1]["end"]
        allowed_end = last + LATE
        if word_mode and nxt and nxt["start"] - last < rules.get("word_gap_fill", captions.WORD_GAP_FILL):
            allowed_end = max(allowed_end, nxt["start"])
        if c["end"] - c["start"] < minimum:  # the end of the audio may leave a squeezed word less room
            allowed_end = max(allowed_end, c["start"] + minimum)
        reasons = []
        if c["start"] < first - EARLY - 0.0005:
            reasons.append(f"starts {c['start'] - first:+.2f} s before its first word")
        if c["end"] > allowed_end + 0.0005:
            reasons.append(f"stays {c['end'] - last:.2f} s after its last word")
        if nxt and c["end"] > nxt["start"] + 0.0005:
            reasons.append("overlaps the next caption")
        if c["end"] - c["start"] < minimum - 0.0005 and not (audio_end and c["end"] >= audio_end - 0.001):
            reasons.append("too short to read")
        out.append(reasons)
    return out


def fix_timing_rules(cues: list[dict], rules: dict) -> int:
    """Bring cues that break the fixed timing rules back within them. Returns how many changed."""
    fixed = 0
    for i, reasons in enumerate(timing_rules(cues, rules)):
        if not reasons:
            continue
        c = cues[i]
        nxt = cues[i + 1] if i + 1 < len(cues) else None
        first, last = c["words"][0]["start"], c["words"][-1]["end"]
        c["start"] = round(max(c["start"], first - EARLY), 3)
        end = min(c["end"], last + LATE if rules["mode"] == "line" or not nxt else max(last + LATE, nxt["start"]))
        if nxt:
            end = min(end, nxt["start"])
        c["end"] = round(max(end, c["start"] + 0.001), 3)
        fixed += 1
    return fixed


def heard_timing(cue: dict, judged: list[dict]) -> tuple[float | None, float | None]:
    """Where the caption's first and last words were heard, when both were heard clearly."""
    first, last = judged[0], judged[-1]
    start = first["start"] if first["state"] == "agree" and first["prob"] >= SURE else None
    end = last["end"] if last["state"] == "agree" and last["prob"] >= SURE else None
    return start, end


def timing_off(cue: dict, start: float | None, end: float | None) -> list[str]:
    reasons = []
    if start is not None and cue["start"] - start > OFF:
        reasons.append(f"appears {cue['start'] - start:.1f} s after its first word is heard ({fmt(start)})")
    if start is not None and start - cue["start"] > OFF + EARLY:
        reasons.append(f"appears {start - cue['start']:.1f} s before its first word is heard ({fmt(start)})")
    if end is not None and end - cue["end"] > OFF:
        reasons.append(f"disappears {end - cue['end']:.1f} s before its last word ends ({fmt(end)})")
    return reasons


def fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


# ---------------------------------------------------------------------------
# Hearing (Whisper)

class Ear:
    """Whisper, listening to clips of the job's audio. kind: "apex" (the Hinglish model), "medium"
    (the alignment model, in `language`), or "medium-hinglish" (medium in Hindi, transliterated)."""

    def __init__(self, ctx: dict, kind: str, language: str | None):
        from faster_whisper.audio import decode_audio

        self.ctx, self.kind, self.language = ctx, kind, language
        self.audio = decode_audio(str(ctx["source_path"]), sampling_rate=SR)
        self.duration = len(self.audio) / SR
        self.calls = 0

    def hear(self, t0: float, t1: float) -> list[dict]:
        import pipeline

        t0, t1 = max(0.0, t0), min(self.duration, t1)
        if t1 - t0 < 0.1:
            return []
        clip = self.audio[int(t0 * SR):int(t1 * SR)]
        kw = {}
        if self.kind == "apex":
            import hinglish
            model, kw = hinglish.load_model(), {"suppress_tokens": hinglish.SUPPRESS}
        else:
            model = pipeline._load_align_model()
        segments, _ = model.transcribe_original(clip, language=self.language, beam_size=5, temperature=0.0,
                                                word_timestamps=True, condition_on_previous_text=False, **kw)
        self.calls += 1
        out = []
        for seg in segments:
            for w in seg.words or []:
                text = w.word.strip()
                if not text or text.casefold() == "nan":
                    continue
                out.append({"word": text, "start": round(t0 + float(w.start), 3), "end": round(t0 + float(w.end), 3),
                            "prob": round(float(w.probability or 0.0), 3)})
        if self.kind == "medium-hinglish":
            import transliterate
            for w in out:
                w["word"] = transliterate.to_hinglish(w["word"]).strip() or w["word"]
        return out

    def hear_many(self, spans: list[tuple[float, float]]) -> list[list[dict]]:
        """Several clips heard on their own, packed into as few Whisper calls as possible: each clip
        is separated from the next by PACK_GAP of silence, and the words are mapped back by offset."""
        import numpy as np

        out: list[list[dict]] = [[] for _ in spans]
        packs, cur, length = [], [], 0.0
        for n, (t0, t1) in enumerate(spans):
            t0, t1 = max(0.0, t0), min(self.duration, t1)
            size = t1 - t0 + (PACK_GAP if cur else 0.0)
            if cur and length + size > PACK_MAX:
                packs.append(cur)
                cur, length, size = [], 0.0, t1 - t0
            cur.append((n, t0, t1))
            length += size
        if cur:
            packs.append(cur)
        gap = np.zeros(int(PACK_GAP * SR), dtype=self.audio.dtype)
        for pack in packs:
            pieces, where, pos = [], [], 0.0
            for n, t0, t1 in pack:
                if pieces:
                    pieces.append(gap)
                    pos += PACK_GAP
                clip = self.audio[int(t0 * SR):int(t1 * SR)]
                pieces.append(clip)
                where.append((n, t0, pos, pos + len(clip) / SR))
                pos += len(clip) / SR
            saved = self.audio, self.duration
            self.audio = np.concatenate(pieces)
            self.duration = len(self.audio) / SR
            try:
                heard = self.hear(0.0, self.duration)
            finally:
                self.audio, self.duration = saved
            for w in heard:
                mid = (w["start"] + w["end"]) / 2
                for n, t0, a, b in where:
                    if a - 0.2 <= mid <= b + 0.2:
                        out[n].append({**w, "start": round(w["start"] - a + t0, 3), "end": round(w["end"] - a + t0, 3)})
                        break
        return out

    def realign(self, t0: float, t1: float, text: str) -> list[tuple[float, float]] | None:
        """Forced alignment of `text` on t0–t1: [(start, end)] per word, or None."""
        import pipeline

        t0, t1 = max(0.0, t0), min(self.duration, t1)
        model = pipeline._load_align_model()
        try:
            result = model.align(self.audio[int(t0 * SR):int(t1 * SR)], text,
                                 language=self.ctx.get("whisper_language_used"), original_split=False,
                                 verbose=None)
        except Exception:  # noqa: BLE001 — a clip it can't align just isn't fixed
            return None
        self.calls += 1
        ws = [w for seg in result.segments for w in seg.words if w.word.strip()]
        if len(ws) != len(text.split()):
            return None
        return [(round(t0 + float(w.start), 3), round(t0 + float(w.end), 3)) for w in ws]


def hearing_for(ctx: dict, rules: dict) -> tuple[str, str | None] | None:
    """(model kind, Whisper language) for this job, or None when there's nothing to compare."""
    if ctx.get("script") == "english":
        return None  # a translation: the audio says something else on purpose
    if "hinglish" in (rules.get("language"), ctx.get("script")):
        import hinglish_model
        return ("apex", "en") if hinglish_model.installed() else ("medium-hinglish", "hi")
    return "medium", ctx.get("whisper_language_used")


def windows(cues: list[dict]) -> list[tuple[float, float, float]]:
    """Consecutive captions grouped into windows: (t0, t1, keep_until) — heard words whose middle is
    before keep_until belong to this window (the next one starts there), so overlaps count once."""
    groups, cur = [], []
    for c in cues:
        if cur and c["end"] + SEARCH - (cur[0]["start"] - SEARCH) > WINDOW_MAX:
            groups.append(cur)
            cur = []
        cur.append(c)
    if cur:
        groups.append(cur)
    out = []
    for g, nxt in zip(groups, groups[1:] + [None]):
        keep = (g[-1]["end"] + nxt[0]["start"]) / 2 if nxt else float("inf")
        out.append((g[0]["start"] - SEARCH, g[-1]["end"] + SEARCH, keep))
    return out


# ---------------------------------------------------------------------------
# The check

def _positions(cue: dict, tokens: list[dict]) -> list[int]:
    """For each word of the caption's text, the token it comes from (as captions.build_cues)."""
    out = []
    for k in cue["tokens"]:
        out += [k] * len(captions.clean_caption(tokens[k]["text"]).split())
    return out


def _put_back(token: dict, judged: dict) -> bool:
    """Did the re-listen hear exactly the word the AI changed (and not the AI's version)?"""
    orig = token.get("orig")
    if orig is None or len(captions.clean_caption(orig).split()) != 1 or judged["state"] == "unheard" \
            or judged["prob"] < SURE:
        return False
    heard = _key(judged["heard"])
    return heard == _key(orig) and _as_number(heard) != _as_number(_key(token["text"]))


def run(ctx: dict, tokens: list[dict], words: list[dict], cues: list[dict], rules: dict,
        report=lambda key, msg: None) -> dict:
    """Check `cues` against the audio. Returns {"cues" (rebuilt when something was fixed, each with
    "listen"), "tokens", "words" (the fixed timeline), "summary"}. Never raises: if the check can't
    run, the cues are returned as they were, with the reason in the summary."""
    t_start = time.perf_counter()
    ctx.setdefault("log", logging.getLogger("listen_check"))
    summary = {"checked": len(cues), "ok": 0, "fixed": 0, "flagged": 0, "skipped": None}
    keep = {"cues": cues, "tokens": tokens, "words": words, "summary": summary}
    hearing = hearing_for(ctx, rules)
    source = ctx.get("source_path")
    if hearing is None:
        summary["skipped"] = "a translation isn't compared with the audio"
    elif not cues:
        summary["skipped"] = "no captions"
    elif not source or not source.exists():
        summary["skipped"] = "the audio isn't available"
    if summary["skipped"]:
        ctx["log"].info("Listening check skipped: %s", summary["skipped"])
        return keep
    try:
        out = _run(ctx, tokens, words, cues, rules, report, hearing, summary)
    except Exception:  # noqa: BLE001 — never fail the job over the check
        ctx["log"].error("Listening check failed:\n%s", traceback.format_exc())
        summary["skipped"] = "something went wrong (see the job log)"
        return keep
    summary["seconds"] = round(time.perf_counter() - t_start, 2)
    ctx["log"].info("Listening check (%s) in %.1fs, %d Whisper calls: %d OK, %d fixed, %d to check",
                    summary["model"], summary["seconds"], summary["calls"], summary["ok"], summary["fixed"],
                    summary["flagged"])
    return out


def _run(ctx, tokens, words, cues, rules, report, hearing, summary) -> dict:
    kind, language = hearing
    log = ctx["log"]
    numbers_language = "hinglish" if kind != "medium" else rules.get("language")
    differ = DIFFER if kind != "medium" else AGREE
    tl = ctx.setdefault("timeline", {})
    ear = Ear(ctx, kind, language)
    summary["model"] = {"apex": "Hinglish model", "medium": f"Whisper {config.MODEL_SIZE}",
                        "medium-hinglish": f"Whisper {config.MODEL_SIZE} (Hindi, in Roman letters)"}[kind]

    # 1. The window pass (kept in the timeline: a Line/Word switch reuses it).
    cache = tl.get("heard")
    if not cache or cache.get("kind") != kind:
        heard = []
        spans = windows(cues)
        for n, (t0, t1, until) in enumerate(spans, start=1):
            report("listen", f"Listening check — re-listening to the audio ({n} of {len(spans)})…")
            begin = spans[n - 2][2] if n > 1 else float("-inf")
            heard += [w for w in ear.hear(t0, t1) if begin <= (w["start"] + w["end"]) / 2 < until]
        cache = {"kind": kind, "words": heard, "clips": {}}
        tl["heard"] = cache
    heard_all = heard_tokens(cache["words"], numbers_language)
    report("listen", "Listening check — comparing each caption with what was heard…")

    words = [dict(w) for w in words]
    tokens = [dict(t) for t in tokens]
    verdicts = {}
    changed = False
    # The window pass, caption by caption.
    first = []
    for cue in cues:
        cap = cue["text"].split()
        near = [h for h in heard_all if cue["start"] - SEARCH <= (h["start"] + h["end"]) / 2 <= cue["end"] + SEARCH]
        judged = judge(cap, near, differ)
        positions = _positions(cue, tokens)
        # Words the final AI check changed that the audio says as they were (exactly the original,
        # not the AI's text): a typo-like change ("years" -> "yeasr") still sounds alike, so it
        # wouldn't count as heard differently.
        back = [k for k in range(len(judged)) if k < len(positions) and _put_back(tokens[positions[k]], judged[k])]
        first.append((cap, near, judged, positions, text_issues(judged, rules.get("language")), back))
    # 2. A caption the window pass heard differently is heard again on its own clip (packed).
    doubtful = [ci for ci, f in enumerate(first) if f[4] or f[5]][:CONFIRM_MAX]
    clip_key = lambda c: f"{c['start'] - PAD:.2f}-{c['end'] + PAD:.2f}"  # noqa: E731
    todo = [ci for ci in doubtful if clip_key(cues[ci]) not in cache["clips"]]
    if todo:
        report("listen", f"Listening check — hearing {len(todo)} caption(s) again on their own…")
        for ci, got in zip(todo, ear.hear_many([(cues[ci]["start"] - PAD, cues[ci]["end"] + PAD) for ci in todo])):
            cache["clips"][clip_key(cues[ci])] = got
    confirms = len(todo)
    for ci, cue in enumerate(cues):
        cap, near, judged, positions, issues, back = first[ci]
        confirm = None
        if ci in doubtful:
            confirm = judge(cap, heard_tokens(cache["clips"][clip_key(cue)], numbers_language), differ)
            issues = [k for k in issues if k in text_issues(confirm, rules.get("language"))]
            back = [k for k in back if _put_back(tokens[positions[k]], confirm[k])]
        fixes, reasons = [], []
        for k in sorted(set(issues) | set(back)):
            j = judged[k]
            heard_text = (confirm[k]["heard"] if confirm and confirm[k]["heard"] else j["heard"])
            tok = tokens[positions[k]] if k < len(positions) else None
            orig = tok.get("orig") if tok else None
            if k in back or orig is not None and len(captions.clean_caption(orig).split()) == 1 and all(
                    similarity(_key(orig), _key(x["heard"])) >= AGREE for x in (j, confirm[k] if confirm else j)):
                # the original word and the re-listen agree: the AI's change goes
                fixes.append(f"put back ‘{captions.clean_caption(orig)}’ (the AI had changed it to "
                             f"‘{j['word']}’; heard ‘{heard_text}’)")
                tok["text"] = orig
                del tok["orig"]
                changed = True
                continue
            if j["state"] == "unheard":
                reasons.append(f"nothing heard for ‘{' '.join(cap)}’ here")
                break
            reasons.append(f"heard ‘{heard_text}’, caption says ‘{j['word']}’")
        # 3. Timing against the re-listen; re-aligned when two sources agree.
        start, end = heard_timing(cue, judged)
        if len(cap) == 1 and sum(h["key"] == _key(cap[0]) for h in near) > 1:
            start = end = None  # "for" heard twice nearby: which one is this caption's can't be told
        off = timing_off(cue, start, end)
        if off:
            w0 = tokens[cue["tokens"][0]]["w0"]
            w1 = tokens[cue["tokens"][-1]]["w1"]
            raw = [words[k]["word"] for k in range(w0, w1 + 1)]
            lo = words[w0 - 1]["end"] if w0 > 0 else 0.0
            hi = words[w1 + 1]["start"] if w1 + 1 < len(words) else ear.duration
            t0 = min(cue["start"], start if start is not None else cue["start"]) - PAD
            t1 = max(cue["end"], end if end is not None else cue["end"]) + PAD
            new = ear.realign(max(t0, lo - 0.05), min(t1, hi + 0.05), " ".join(raw))
            agrees = new and (start is None or abs(new[0][0] - start) <= REALIGN_AGREE) \
                and (end is None or abs(new[-1][1] - end) <= REALIGN_AGREE)
            inside = new and new[0][0] >= lo - 0.001 and new[-1][1] <= hi + 0.001
            stays = new and abs(new[0][0] - cue["words"][0]["start"]) <= REALIGN_AGREE \
                and abs(new[-1][1] - cue["words"][-1]["end"]) <= REALIGN_AGREE
            if agrees and inside and not stays:
                before = cue["start"]
                for k, (a, b) in zip(range(w0, w1 + 1), new):
                    words[k]["start"], words[k]["end"] = a, max(b, a)
                fixes.append(f"timing re-aligned to the audio: starts {fmt(new[0][0])} (was {fmt(before)})")
                changed = True
            elif stays:
                log.info("Listening check: caption %d (“%s”) timing confirmed by re-alignment "
                         "(the re-listen put it at %s)", ci + 1, cue["text"], fmt(start or end or 0))
            else:
                reasons += [f"timing: {r}" for r in off]
        verdicts[tuple(cue["tokens"])] = {"fixes": fixes, "reasons": reasons,
                                          "heard": " ".join(h["text"] for h in near)[:200]}
        for line in fixes:
            log.info("Listening check: caption %d (“%s”) fixed — %s", ci + 1, cue["text"], line)
        for line in reasons:
            log.info("Listening check: caption %d (“%s”) to check — %s", ci + 1, cue["text"], line)

    if changed:  # rebuild from the fixed timeline, with the same cut points
        chunks = None if rules["mode"] == "word" else [c["tokens"] for c in cues]
        cues = captions.build_cues(tokens, words, rules, chunks)
    rule_fixes = fix_timing_rules(cues, rules)
    if rule_fixes:
        log.info("Listening check: %d caption(s) brought back within the timing rules", rule_fixes)
    remaining = timing_rules(cues, rules)
    for cue, broken in zip(cues, remaining):
        v = verdicts.get(tuple(cue["tokens"]), {"fixes": [], "reasons": [], "heard": ""})
        reasons = v["reasons"] + [f"timing: {r}" for r in broken]
        status = "flag" if reasons else "fixed" if v["fixes"] else "ok"
        cue["listen"] = {"status": status, "reasons": reasons or v["fixes"], "heard": v["heard"]}
        summary[{"ok": "ok", "fixed": "fixed", "flag": "flagged"}[status]] += 1
    summary.update(checked=len(cues), calls=ear.calls, windows=len(windows(cues)), confirmed=confirms,
                   timing_rule_fixes=rule_fixes)
    return {"cues": cues, "tokens": tokens, "words": words, "summary": summary, "changed": changed}

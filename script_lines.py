"""Script lines the aligner wasn't sure of (DECISIONS.md V4.9): never silently dropped.

Lyrics/script mode aligns the user's own text. 4.6–4.8 left out any line whose words came back with
a mean confidence under 0.15 (or squeezed into no time), as "not in the audio". That dropped real
lines: a sung "Nahi toh bas," in a chorus scores 0.09 where it is sung, and once the aligner loses its
place in a long script every line after it is squeezed into the last instant of the file (a 5½-minute
ad lost its last 16 lines, 47 s of speech). Now:

1. Confidence per line: the mean word probability; a line with most of its words squeezed into no
   time scores 0, and one with a third of them squeezed counts as low whatever its mean.
2. A low line (below CONFIDENCE), with any low neighbours, is placed provisionally in the gap between
   the confident lines around it: that stretch of audio is aligned again with just those lines.
3. Whisper re-listens to the gap (the Hinglish model for Hinglish) and the lines are matched with what
   it heard, in order (so a repeated chorus line can only match its own repeat), ignoring spelling
   variants ("nahin" / "nahi") and number or symbol differences.
   - heard (HEARD_SHARE of its words or more): "recovered" — kept, timed within the gap.
   - the gap has speech but the match is uncertain: "flagged" — kept with its best timing, and the
     Review panel says "Script line not confidently found — check it".
   - the gap clearly has no speech (no voice activity and nothing heard): "not_found" — left out of
     the SRT, listed in the Review panel as "Not found in the audio" with "Add it back".
"""

import time

import captions
import listen_check

CONFIDENCE = 0.35      # config.SCRIPT_LINE_CONFIDENCE: a line under this is checked (see DECISIONS V4.9)
SQUEEZED = 0.021       # a word this short was squeezed into no time by the aligner
HEARD_SHARE = 0.5      # share of a line's words that must be heard (agree or close) for "recovered"
SPEECH_MIN = 0.25      # seconds of voice activity that count as speech in a stretch
SEARCH_PAD = 1.0       # seconds of audio either side of a gap heard with it (context for Whisper)
WINDOW = 20.0          # longest stretch heard in one Whisper call
EDGE = 0.3             # seconds at each end of a gap that belong to the neighbours when looking for speech
NO_PLACE = "no place"  # a line with no time of its own
STRETCH_HOLE = 4.0     # a gap this long between two words of one script line means it was stretched…
STRETCH_PER_WORD = 1.4  # …and so does more than this many seconds per word over a long line


def line_words(words: list[dict]) -> dict[int, list[dict]]:
    by: dict[int, list[dict]] = {}
    for w in words:
        by.setdefault(w["line"], []).append(w)
    return by


def confidence(ws: list[dict]) -> tuple[float, str]:
    """(score 0–1, why it's low or "") for one line's aligned words."""
    if not ws:
        return 0.0, NO_PLACE
    probs = [w["probability"] for w in ws if w.get("probability") is not None]
    mean = sum(probs) / len(probs) if probs else 1.0
    squeezed = sum(1 for w in ws if w["end"] - w["start"] <= SQUEEZED) / len(ws)
    span = ws[-1]["end"] - ws[0]["start"]
    if (squeezed >= 0.6 and len(ws) > 1) or (len(ws) > 2 and span < 0.05 * len(ws)):
        return 0.0, f"squeezed into {span:.2f} s"
    if squeezed >= 0.34 and len(ws) > 2:
        return mean / 4, f"{round(squeezed * 100)}% of its words squeezed"
    # Stretched: the aligner ran one line over the lines after it ("Week four…" over 23 s, with 10 s
    # holes between its own words). Never an anchor, always checked.
    hole = max((b["start"] - a["end"] for a, b in zip(ws, ws[1:])), default=0.0)
    if (len(ws) >= 3 and hole >= STRETCH_HOLE) or (len(ws) >= 4 and span > STRETCH_PER_WORD * len(ws) and span > 8.0):
        return mean / 4, f"stretched over {span:.1f} s"
    return mean, "" if mean else "no confidence"


def _runs(low: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for i in low:
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def _spread(lines: list[str], idx: list[int], t0: float, t1: float, template: dict[int, list[dict]]) -> dict[int, list[dict]]:
    """Provisional times: the lines laid out over t0–t1 in proportion to their length, each word in
    proportion to its own length (used when the gap can't be aligned)."""
    total = sum(len(lines[i]) for i in idx) or 1
    out, pos = {}, t0
    for i in idx:
        share = (t1 - t0) * len(lines[i]) / total
        ws = template.get(i) or []
        n = len(ws) or 1
        step = share / n
        out[i] = [{**w, "start": round(pos + k * step, 3), "end": round(pos + (k + 1) * step - 0.01, 3)}
                  for k, w in enumerate(ws)]
        pos += share
    return out


def _realign(ear, t0: float, t1: float, lines: list[str], idx: list[int], template, language) -> dict | None:
    """Align just these lines on t0–t1. {line: words} with the template's word dicts retimed, or None."""
    import pipeline
    model = pipeline._load_align_model()
    clip = ear.audio[int(max(0.0, t0) * listen_check.SR):int(min(ear.duration, t1) * listen_check.SR)]
    if len(clip) < listen_check.SR * 0.3:
        return None
    text = "\n".join(lines[i] for i in idx)
    try:
        result = model.align(clip, text, language=language, original_split=True, verbose=None)
    except Exception:  # noqa: BLE001 — a stretch it can't align keeps its provisional times
        return None
    got = [w for seg in result.segments for w in seg.words if w.word.strip()]
    want = sum(len(template.get(i) or []) for i in idx)
    if len(got) != want or not want:
        return None
    out, k = {}, 0
    for i in idx:
        ws = []
        for w in template.get(i) or []:
            g = got[k]
            k += 1
            ws.append({**w, "start": round(t0 + float(g.start), 3), "end": round(t0 + float(g.end), 3),
                       "probability": round(float(getattr(g, "probability", 0) or 0), 3)})
        out[i] = ws
    return out


# ---------------------------------------------------------------------------
# Re-anchoring (V4.9): when the aligner loses its place, start again from the last line it placed well.

ANCHOR = 0.3          # a line this confident (and not squeezed) is a trustworthy anchor
LOST_RUN = 3          # this many low lines in a row means the aligner lost its place
REANCHOR_ROUNDS = 4   # at most this many re-anchoring rounds per job
CHUNK_LINES = 6       # an unheard stretch between two heard lines is re-aligned in chunks this long


def lost_at(words: list[dict], lines: list[str], threshold: float, settled=frozenset()) -> tuple[int, str] | None:
    """(first line, why) where the aligner lost its place, or None: lines piled up with no time (two
    in a row squeezed), a line starting before the one before it ended, or LOST_RUN low lines in a row."""
    by = line_words(words)
    scores = [confidence(by.get(i, [])) for i in range(len(lines))]
    # settled: lines re-anchoring already placed where Whisper heard them — never "lost" again.
    squeezed = [i not in settled and s == 0.0 and (why.startswith("squeezed") or why == NO_PLACE)
                for i, (s, why) in enumerate(scores)]
    low = [i not in settled and (s < threshold or bool(why)) for i, (s, why) in enumerate(scores)]
    prev_end = None
    for i in range(len(lines)):
        ws = by.get(i)
        if ws and prev_end is not None and ws[0]["start"] < prev_end - 0.5:
            return i, f"line {i + 1} starts {prev_end - ws[0]['start']:.1f} s before the line before it ends"
        if ws:
            prev_end = max(prev_end or 0.0, ws[-1]["end"])
        if squeezed[i] and i + 1 < len(lines) and squeezed[i + 1]:
            return i, f"lines {i + 1}–{i + 2} piled up with no time"
        if all(low[i:i + LOST_RUN]) and i + LOST_RUN <= len(lines):
            return i, f"{LOST_RUN}+ low-confidence lines in a row from line {i + 1}"
    return None


def _good(ws: list[dict]) -> bool:
    score, why = confidence(ws)
    return score >= ANCHOR and not why


def reanchor(ctx: dict, words: list[dict], lines: list[str], *, threshold: float, ear, language: str | None,
             numbers_language: str | None, differ: float, report=lambda key, msg: None) -> tuple[list[dict], list[dict]]:
    """Re-anchor the alignment wherever it lost its place. Returns (words, rounds): each round
    {"lost_at", "why", "anchor", "searched", "lines": [{"line", "before", "after", "heard"}]}.

    Each round: the last confidently placed line before the trouble is the anchor; Whisper transcribes
    only the audio from there (to the next well-placed line, or the end); the remaining lines are
    matched to that transcript in order, and every line clearly heard becomes an anchor at the time it
    was heard. The lines are then aligned again chunk by chunk, each chunk on just the audio between
    its anchors. Repeats while it finds the aligner lost again (at most REANCHOR_ROUNDS)."""
    log = ctx["log"]
    duration = ctx.get("duration") or (max(w["end"] for w in words) if words else 0.0)
    rounds, seen, settled = [], set(), set()
    while ear is not None and len(rounds) < REANCHOR_ROUNDS:
        lost = lost_at(words, lines, threshold, settled)
        if lost is None:
            break
        k, why = lost
        by = line_words(words)
        j = next((i for i in range(k - 1, -1, -1) if _good(by.get(i, []))), -1)
        t0 = by[j][-1]["end"] if j >= 0 else 0.0
        # The trouble ends at the first pair of well-placed lines after it that start after the anchor.
        m = next((i for i in range(k + 1, len(lines) - 1) if _good(by.get(i, [])) and _good(by.get(i + 1, []))
                  and by[i][0]["start"] > t0 and by[i + 1][0]["start"] >= by[i][-1]["end"] - 0.5), len(lines))
        t1 = by[m][0]["start"] if m < len(lines) else duration
        idx = list(range(j + 1, m))
        key = (j, m)
        if key in seen or not idx or t1 - t0 < 0.3:
            log.info("Re-anchoring: no further progress from line %d; the remaining lines are checked one by one", k + 1)
            break
        seen.add(key)
        report("align", f"The aligner lost its place at line {k + 1} — re-anchoring from line {j + 2}…")
        started = time.perf_counter()
        # Whisper's transcript of just that stretch.
        spans, t, stop = [], max(0.0, t0 - 0.3), min(duration, t1 + 0.3)
        while t < stop - 0.05:
            spans.append((t, min(stop, t + WINDOW)))
            t = spans[-1][1]
        heard = sorted((w for got in _hear_many(ear, spans) for w in got if t0 - 0.3 <= (w["start"] + w["end"]) / 2 <= t1 + 0.3),
                       key=lambda w: w["start"])
        tokens = listen_check.heard_tokens(heard, numbers_language) if heard else []
        caption = [(i, w) for i in idx for w in lines[i].split()]
        matched = listen_check.match([listen_check._key(w) for _, w in caption], [x["key"] for x in tokens]) \
            if tokens else [([], 0.0)] * len(caption)
        anchors = {}  # line -> (start, end) where Whisper heard it
        for i in idx:
            pairs = [(mm, s) for (li, _), (mm, s) in zip(caption, matched) if li == i]
            hits = sorted({x for mm, s in pairs if mm and s >= differ for x in mm})
            if pairs and len([1 for mm, s in pairs if mm and s >= differ]) / len(pairs) >= HEARD_SHARE:
                anchors[i] = (tokens[hits[0]]["start"], tokens[hits[-1]]["end"])
        # Chunks: each heard line starts one; long unheard stretches are cut every CHUNK_LINES lines.
        bounds, last = [], None
        for n, i in enumerate(idx):
            if n == 0 or i in anchors or (last is not None and i - last >= CHUNK_LINES):
                bounds.append(n)
                last = i
        chunks = [idx[a:b] for a, b in zip(bounds, bounds[1:] + [len(idx)])]
        new_by = {i: [dict(w) for w in ws] for i, ws in by.items()}
        cursor = t0
        for c, chunk in enumerate(chunks):
            nxt = chunks[c + 1][0] if c + 1 < len(chunks) else None
            c0 = max(cursor, anchors[chunk[0]][0] - 0.25) if chunk[0] in anchors else cursor
            c1 = (anchors[nxt][0] - 0.05) if nxt in anchors else (t1 if nxt is None else None)
            if c1 is None:  # an unheard next chunk: share the time up to the next heard line by length
                later = next((x for x in chunks[c + 1:] if x[0] in anchors), None)
                stop = anchors[later[0]][0] - 0.05 if later else t1
                rest = [i for x in chunks[c:] if (later is None or x[0] < later[0]) for i in x]
                share = sum(len(lines[i]) for i in chunk) / max(1, sum(len(lines[i]) for i in rest))
                c1 = c0 + (stop - c0) * share
            c1 = max(c1, c0 + 0.3)
            got = _realign(ear, c0, c1, lines, chunk, new_by, language)
            if got is None:
                got = _spread(lines, chunk, c0, c1, new_by)
            for i in chunk:
                new_by[i] = got[i]
            cursor = max(cursor, max((w["end"] for i in chunk for w in new_by[i]), default=c0))
        words = [w for i in range(len(lines)) for w in new_by.get(i, [])]
        _keep_order(words)
        settled |= set(anchors)
        after = line_words(words)
        rec = {"round": len(rounds) + 1, "lost_at": k + 1, "why": why, "anchor": j + 1 if j >= 0 else None,
               "searched": [round(t0, 3), round(t1, 3)], "anchored": len(anchors), "chunks": len(chunks),
               "seconds": round(time.perf_counter() - started, 2), "lines": []}
        for i in idx:
            b0, a0 = by.get(i) or [], after.get(i) or []
            rec["lines"].append({"line": i + 1, "before": [b0[0]["start"], b0[-1]["end"]] if b0 else None,
                                 "after": [a0[0]["start"], a0[-1]["end"]] if a0 else None,
                                 "heard_at": [round(x, 3) for x in anchors[i]] if i in anchors else None})
        rounds.append(rec)
        log.info("Re-anchoring round %d: the aligner lost its place at line %d (%s); anchor line %s ends %.2f s; "
                 "re-aligned lines %d–%d on %.2f–%.2f s in %d chunks, %d lines anchored by Whisper's transcript, in %.1fs",
                 rec["round"], k + 1, why, rec["anchor"], t0, idx[0] + 1, idx[-1] + 1, t0, t1, len(chunks),
                 len(anchors), rec["seconds"])
        for x in rec["lines"]:
            log.info("Re-anchored line %d: %s -> %s%s", x["line"], _span(x["before"]), _span(x["after"]),
                     f" (heard at {_span(x['heard_at'])})" if x["heard_at"] else " (not heard: placed between anchors)")
    return words, rounds


def _hear_many(ear, spans):
    """ear.hear_many with repetition loops split and left out (Ear.split_loops), then back as it was."""
    saved = getattr(ear, "split_loops", False)
    ear.split_loops = True
    try:
        return ear.hear_many(spans)
    finally:
        ear.split_loops = saved


def _span(s) -> str:
    return f"{s[0]:.2f}–{s[1]:.2f}" if s else "no time"


def _speech(speech: list[tuple[float, float]], t0: float, t1: float) -> float:
    """Seconds of voice activity inside t0–t1."""
    return sum(max(0.0, min(b, t1) - max(a, t0)) for a, b in speech)


def resolve(ctx: dict, words: list[dict], lines: list[str], *, threshold: float, hearing, language: str | None,
            speech: list[tuple[float, float]] | None, report=lambda key, msg: None, ear=None) -> dict:
    """Check every low-confidence script line. Returns {"words" (the timeline: recovered and flagged
    lines retimed, not-found lines removed), "lines" (one result per script line), "flagged" (line
    indices), "not_found" ([{"line", "text", "gap"}])}. hearing: listen_check.hearing_for()'s
    (kind, language), or None to skip the re-listen (then low lines are kept and flagged)."""
    log = ctx["log"]
    duration = ctx.get("duration") or (max(w["end"] for w in words) if words else 0.0)
    by = line_words(words)
    results = []
    for i, line in enumerate(lines):
        score, why = confidence(by.get(i, []))
        ws = by.get(i, [])
        results.append({"line": i + 1, "text": line, "status": "kept", "confidence": round(score, 3),
                        "why_low": why, "start": ws[0]["start"] if ws else None, "end": ws[-1]["end"] if ws else None})
    low = [i for i, r in enumerate(results) if r["confidence"] < threshold or r["why_low"]]
    for i, r in enumerate(results):
        log.debug("Script line %d confidence %.3f%s: %s", i + 1, r["confidence"],
                  f" ({r['why_low']})" if r["why_low"] else "", r["text"])
    if not low:
        return {"words": words, "lines": results, "flagged": [], "not_found": [], "recovered": []}
    if len(low) == len(lines) and threshold > 0:
        log.info("Script lines: every line is below %.2f — checking them all against the audio", threshold)
    if ear is None and hearing is not None:
        ear = listen_check.Ear(ctx, *hearing)
    numbers_language = "hinglish" if hearing and hearing[0] != "medium" else language
    differ = listen_check.DIFFER if hearing and hearing[0] != "medium" else listen_check.AGREE
    new_by = {i: [dict(w) for w in ws] for i, ws in by.items()}
    # 1. Each run of low lines gets the gap between the confident lines around it, and is placed
    #    there provisionally: aligned again on just that stretch, or laid out by length.
    plans = []
    for run in _runs(low):
        a, b = run[0], run[-1]
        prev = next((new_by[k][-1]["end"] for k in range(a - 1, -1, -1) if k not in low and new_by.get(k)), 0.0)
        nxt = next((new_by[k][0]["start"] for k in range(b + 1, len(lines)) if k not in low and new_by.get(k)), duration)
        t0, t1 = max(0.0, prev), max(min(duration, nxt), prev)
        realigned = _realign(ear, t0, t1, lines, run, new_by, ctx.get("whisper_language_used") or language) \
            if ear is not None and t1 - t0 >= 0.3 else None
        placed = realigned or _spread(lines, run, t0, t1 if t1 - t0 >= 0.3 else t0 + 0.3 * len(run), new_by)
        plans.append({"run": run, "t0": t0, "t1": t1, "realigned": bool(realigned), "placed": placed})
    # 2. Whisper re-listens to every gap: the clips (with SEARCH_PAD of context, long gaps in
    #    WINDOW-second pieces) are packed into as few Whisper calls as possible.
    report("align", f"Checking {len(low)} script line(s) the aligner wasn't sure of against the audio…")
    started = time.perf_counter()
    spans, owner = [], []
    for n, plan in enumerate(plans):
        t = plan["t0"] - SEARCH_PAD
        while t < plan["t1"] + SEARCH_PAD - 0.05:
            end = min(plan["t1"] + SEARCH_PAD, t + WINDOW)
            spans.append((t, end))
            owner.append(n)
            t = end
    heard_by = [[] for _ in plans]
    if ear is not None and spans:
        for n, got in zip(owner, _hear_many(ear, spans)):
            heard_by[n] += got
    calls = getattr(ear, "calls", 0)
    for plan, heard in zip(plans, heard_by):
        run, t0, t1, placed, realigned = plan["run"], plan["t0"], plan["t1"], plan["placed"], plan["realigned"]
        # Only words inside the gap count: the padding is context for Whisper, and its words belong
        # to the confident neighbours.
        heard = sorted((w for w in heard if t0 - 0.25 <= (w["start"] + w["end"]) / 2 <= t1 + 0.25),
                       key=lambda w: w["start"])
        tokens = listen_check.heard_tokens(heard, numbers_language) if heard else []
        # All the run's words against everything heard in the gap, in order (a chorus line can only
        # match its own repeat, never the next one's).
        caption = [(i, w) for i in run for w in lines[i].split()]
        keys = [listen_check._key(w) for _, w in caption]
        matched = listen_check.match(keys, [t["key"] for t in tokens]) if tokens else [([], 0.0)] * len(caption)
        # "Is there speech?" is decided on the inside of the gap: the aligner's edges can be 0.2 s off,
        # so the neighbours' first and last sounds (and words) would otherwise count as this line's.
        margin = min(EDGE, (t1 - t0) / 4)
        i0, i1 = t0 + margin, t1 - margin
        inner_heard = [t for t in tokens if i0 <= (t["start"] + t["end"]) / 2 <= i1 and t["prob"] >= 0.3]
        gap_speech = _speech(speech, i0, i1) if speech is not None else None
        for i in run:
            pairs = [(m, s) for (li, _), (m, s) in zip(caption, matched) if li == i]
            n = len(pairs) or 1
            hits = [(m, s) for m, s in pairs if m and s >= differ]
            share = len(hits) / n
            heard_ids = sorted({k for m, _ in hits for k in m})
            heard_text = " ".join(tokens[k]["text"] for k in heard_ids)
            span = placed.get(i) or []
            r = results[i]
            r.update(searched=[round(t0, 3), round(t1, 3)], heard=heard_text, heard_share=round(share, 2),
                     realigned=realigned, speech_seconds=round(gap_speech, 2) if gap_speech is not None else None)
            if share >= HEARD_SHARE:
                ws = span
                if heard_ids and ws:  # the re-alignment must sit where the words were heard
                    h0, h1 = tokens[heard_ids[0]]["start"], tokens[heard_ids[-1]]["end"]
                    if not realigned or ws[0]["start"] > h1 + 1.0 or ws[-1]["end"] < h0 - 1.0:
                        ws = _spread(lines, [i], max(t0, h0 - 0.05), min(t1, h1 + 0.05) if h1 > h0 else h0 + 0.3,
                                     {i: ws})[i]
                new_by[i] = ws
                r.update(status="recovered", reason=f"heard in the gap ({round(share * 100)}% of its words)")
            elif hearing is None or speech is None:
                new_by[i] = span  # can't tell whether it's spoken: keep it, and say so
                r.update(status="flagged", reason="couldn't be checked against the audio")
            elif inner_heard or gap_speech >= SPEECH_MIN:
                new_by[i] = span
                r.update(status="flagged", reason="speech here, but the words didn't clearly match" if inner_heard
                         else "voice heard here, but Whisper wrote nothing")
            else:
                new_by.pop(i, None)
                r.update(status="not_found", reason="no speech in the gap between its neighbours")
            if new_by.get(i):
                r.update(start=new_by[i][0]["start"], end=new_by[i][-1]["end"])
            log.info("Script line %d %s (confidence %.3f%s; searched %.2f–%.2f s; heard %d%% %s; speech %s s): %s — %s",
                     i + 1, r["status"].upper().replace("_", " "), r["confidence"],
                     f", {r['why_low']}" if r["why_low"] else "", t0, t1, round(share * 100),
                     f"‘{heard_text}’" if heard_text else "nothing", r["speech_seconds"], r["text"], r["reason"])
        log.debug("Gap %.2f–%.2f s heard: %s", t0, t1,
                  " ".join(f"{w['word']}@{w['start']:.2f}({w['prob']:.2f})" for w in heard) or "nothing")
    log.info("Script lines: %d checked in %.1fs (%d Whisper calls): %d recovered, %d flagged, %d not found",
             len(low), time.perf_counter() - started, calls,
             sum(r["status"] == "recovered" for r in results), sum(r["status"] == "flagged" for r in results),
             sum(r["status"] == "not_found" for r in results))
    out = [w for i in range(len(lines)) for w in new_by.get(i, [])]
    _keep_order(out)
    return {"words": out, "lines": results,
            "flagged": [r["line"] - 1 for r in results if r["status"] == "flagged"],
            "recovered": [r["line"] - 1 for r in results if r["status"] == "recovered"],
            "not_found": [{"line": r["line"], "text": r["text"], "gap": r.get("searched")}
                          for r in results if r["status"] == "not_found"]}


def _keep_order(words: list[dict]):
    """Retimed lines must still run in script order: a word never starts before the one before it."""
    last = 0.0
    for w in words:
        if w["start"] < last:
            w["start"] = round(last, 3)
        if w["end"] < w["start"]:
            w["end"] = w["start"]
        last = w["start"]


def speech_regions(audio_path, duration: float | None = None) -> list[tuple[float, float]]:
    """Voice activity (Silero VAD, offline, bundled with faster-whisper) as [(start, end)] seconds."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(str(audio_path), sampling_rate=16000)
    stamps = get_speech_timestamps(audio, VadOptions(threshold=0.5, min_speech_duration_ms=150,
                                                     min_silence_duration_ms=300, speech_pad_ms=100))
    return [(round(s["start"] / 16000, 3), round(s["end"] / 16000, 3)) for s in stamps]


def missing_captions(speech: list[tuple[float, float]], cues: list[dict], min_gap: float = 1.5,
                     word_cap: float | None = None) -> list[dict]:
    """Stretches longer than min_gap where the audio has speech but no caption covers it:
    [{"start", "end", "seconds"}]. With word_cap, a caption covers only its words, each at most
    word_cap seconds long: the aligner can stretch one word over a line missing from the script
    ("I" on screen for 2.8 s while a whole sentence is spoken)."""
    covered = []
    for c in cues:
        ws = c.get("words") if word_cap else None
        if not ws:
            covered.append((c["start"], c["end"]))
            continue
        for w in ws:
            end = min(w["end"], w["start"] + word_cap)
            if covered and w["start"] - covered[-1][1] < 0.35:  # words of one phrase: no gap between them
                covered[-1] = (covered[-1][0], max(covered[-1][1], end))
            else:
                covered.append((w["start"], end))
    covered.sort()
    out = []
    for a, b in speech:
        t = a
        for c0, c1 in covered:
            if c1 <= t or c0 >= b:
                continue
            if c0 > t + 0.05:
                out.append((t, c0))
            t = max(t, c1)
            if t >= b:
                break
        if b > t + 0.05:
            out.append((t, b))
    merged = []
    for a, b in out:  # speech regions split by a short pause are one stretch
        if merged and a - merged[-1][1] < 0.5 and not any(c0 < a and c1 > merged[-1][1] for c0, c1 in covered):
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [{"start": round(a, 3), "end": round(b, 3), "seconds": round(b - a, 2)} for a, b in merged if b - a >= min_gap]

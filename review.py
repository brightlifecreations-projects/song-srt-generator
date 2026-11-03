"""The Review panel's server side (DECISIONS.md V4.5): saving edited cues, and re-timing one cue.

Generation writes <name>.srt and an identical original.srt into the job folder. Edits from the
Review panel go to edited.srt (+ edited-cues.json); the original is never touched. Text changes
are learnt into corrections.md as User fixes, once each, like the draft box's.
"""

import json
import subprocess
from collections import Counter

import corrections
import pipeline


def clean_cues(raw: list) -> list[dict]:
    """The panel's cues → sorted [{"start", "end", "text"}]; raises ValueError on nonsense."""
    cues = []
    for c in raw:
        start, end, text = float(c["start"]), float(c["end"]), " ".join(str(c["text"]).split())
        if not text:
            continue
        if start < 0 or end < start or end > 24 * 3600:
            raise ValueError("bad times")
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    cues.sort(key=lambda c: c["start"])
    for i, c in enumerate(cues, start=1):
        c["index"] = i
    return cues


def _table(ctx: dict) -> str:
    """The corrections table this job's text belongs to (the draft's, or from the language chosen)."""
    if ctx.get("corrections_table"):
        return ctx["corrections_table"]
    language = ctx.get("align_language")
    script = language if language in corrections.SCRIPT_TABLES else ctx.get("script")
    return corrections.table_for(script, ctx.get("whisper_language_used"))


def save_edits(ctx: dict, cues: list[dict], generated: list[dict]) -> dict:
    job_dir = ctx["job_dir"]
    path = job_dir / "edited.srt"
    pipeline.write_srt(cues, path)
    (job_dir / "edited-cues.json").write_text(json.dumps(cues, indent=1, ensure_ascii=False), encoding="utf-8")
    # Learn what the user re-typed: all the generated words vs all the edited words, as one run, so
    # splitting, merging or moving cues changes nothing (only re-typed words are learnt).
    diff = corrections.diff_lines(" ".join(c["text"] for c in generated), " ".join(c["text"] for c in cues))
    pairs = Counter(corrections.learnable(diff))
    new = list((pairs - Counter(ctx.get("review_recorded") or {})).elements())
    ctx["review_recorded"] = dict(pairs | Counter(ctx.get("review_recorded") or {}))
    recorded = []
    if new:
        try:
            recorded = corrections.record(pipeline.DATA_DIR, _table(ctx), [(w, c, "User") for w, c in new])
        except corrections.CorrectionsError as e:
            ctx["log"].warning("corrections.md has errors, review fixes not added: %s", e)
        ctx["log"].info("Review edits saved (%d cues); corrections recorded: %s", len(cues),
                        ", ".join(f"{r['wrong']} -> {r['correct']}" for r in recorded) or "none")
    return {"path": path, "recorded": recorded, "recorded_total": ctx["review_recorded"]}


def retime(ctx: dict, start: float, end: float, text: str, low: float | None = None,
           high: float | None = None) -> dict:
    """Align one cue's (changed) text to its own stretch of audio: start–end widened by 0.75 s,
    but not past low/high (the neighbouring cues). Returns {"start", "end"}."""
    start, end = float(start), float(end)
    t0 = max(0.0, start - 0.75, float(low) if low is not None else 0.0)
    t1 = min(ctx["duration"], end + 0.75, float(high) if high is not None else ctx["duration"])
    if t1 - t0 < 0.2 or not str(text).strip():
        raise pipeline.PipelineError("This cue is too short to re-time.")
    clip = ctx["job_dir"] / "retime.wav"
    subprocess.run([pipeline.FFMPEG, "-y", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}",
                    "-i", str(ctx["source_path"]), "-ac", "1", "-ar", "16000", str(clip)],
                   capture_output=True, check=True)
    try:
        result = pipeline.align_lyrics(clip, [" ".join(str(text).split())], ctx["log"],
                                       ctx.get("whisper_language_used"))
    finally:
        clip.unlink(missing_ok=True)
    words = [w for seg in result.segments for w in seg.words if w.word.strip()]
    if not words:
        raise pipeline.PipelineError("Couldn't find this line in the audio.")
    new = {"start": round(t0 + float(words[0].start), 3), "end": round(t0 + float(words[-1].end), 3)}
    ctx["log"].info("Re-timed “%s”: %.2f–%.2f -> %.2f–%.2f", text, start, end, new["start"], new["end"])
    return new

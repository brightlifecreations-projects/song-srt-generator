"""Real runs for a release check (V4.8): the full flow on real files, with the listening check and,
when a provider is set up in the data folder, the final AI check. Test fixture, not shipped.

    SRT_DATA_DIR=<test data folder> python test-fixtures/realrun.py <name> <audio> lyrics <lyrics.txt> <language> <section>
    SRT_DATA_DIR=<test data folder> python test-fixtures/realrun.py <name> <audio> transcribe <script> <section> [quality]

Writes <name>.json next to the data folder: timings, the AI check's result, the listening check's
summary and every caption it fixed or flagged (with the reason), and the final SRT text.
A transcribe run approves the draft as it comes (what the user would see, unchanged).
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_polish  # noqa: E402
import final_check  # noqa: E402
import pipeline  # noqa: E402


def main(argv):
    name, audio, mode = argv[:3]
    data = Path(os.environ["SRT_DATA_DIR"])
    report = lambda key, msg, fraction=None: print(f"[{key}] {msg}", flush=True)  # noqa: E731
    t0 = time.perf_counter()
    check = final_check.active()
    if mode == "lyrics":
        lyrics_file, language, section = argv[3:6]
        ctx = pipeline.start_job(audio, section, report)
        result = pipeline.align_and_write(ctx, Path(lyrics_file).read_text(encoding="utf-8"), language=language,
                                          report=report, ai=check)
        draft = None
    else:
        script, section = argv[3:5]
        quality = argv[5] if len(argv) > 5 else "accurate"
        ctx = pipeline.start_job(audio, section, report)
        polish = ai_polish.active()
        if polish and not polish["auto"]:
            polish = None
        draft = pipeline.run_transcription(ctx, "auto", report, script, quality, "", polish)
        text = ai_polish.UNSURE.sub(r"\1", draft["draft_text"])
        result = pipeline.align_and_write(ctx, text, strip_tags=False, language="auto", report=report, ai=check)
    total = round(time.perf_counter() - t0, 2)
    cues = result["cues"]
    out = {
        "name": name, "file": Path(audio).name, "mode": mode, "job_dir": str(result["job_dir"]),
        "total_seconds": total, "timings": result["timings"], "cues": len(cues),
        "final_check": {"provider": check["provider"] if check else None, "message": result.get("ai_message"),
                        "changes": result.get("ai_changes")},
        "listen": result.get("listen"),
        "fixed": [{"n": i + 1, "text": c["text"], "start": c["start"], "why": c["listen"]["reasons"]}
                  for i, c in enumerate(cues) if c.get("listen", {}).get("status") == "fixed"],
        "flagged": [{"n": i + 1, "text": c["text"], "start": c["start"], "why": c["listen"]["reasons"]}
                    for i, c in enumerate(cues) if c.get("listen", {}).get("status") == "flag"],
        "unsure": [{"n": i + 1, "text": c["text"], "words": [c["text"].split()[k] for k in c["unsure"]]}
                   for i, c in enumerate(cues) if c.get("unsure")],
        "ai_fixes": [{"n": i + 1, **a} for i, c in enumerate(cues) for a in c.get("ai", [])],
        "warnings": result["warnings"],
        "draft": draft["draft_text"] if draft else None,
        "srt": result["srt_path"].read_text(encoding="utf-8"),
    }
    path = data.parent / f"{name}.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("name", "total_seconds", "timings", "cues", "final_check", "listen")},
                     ensure_ascii=False, indent=1))
    print(f"fixed {len(out['fixed'])}, flagged {len(out['flagged'])} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

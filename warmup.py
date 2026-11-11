"""First-run verification: downloads models and runs the full pipeline on sample/.

Prints PASS with per-stage timings, or a readable failure. Setup is not complete
until this prints PASS.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def fail(message: str) -> int:
    print(f"\nFAIL: {message}")
    return 1


def main() -> int:
    print("SRT Generator — first-run verification\n")

    if sys.version_info[:2] not in ((3, 10), (3, 11)):
        return fail(f"Python 3.10 or 3.11 required, running {sys.version.split()[0]}.")
    from pipeline import FFMPEG, FFPROBE  # the bundled copies in bin/ if present, else PATH

    if not shutil.which(FFMPEG) or not shutil.which(FFPROBE):
        return fail("ffmpeg/ffprobe not found (bin/ or PATH). Install ffmpeg first (see README).")
    print("OK  Python and ffmpeg present")

    clip = ROOT / "sample" / "clip.wav"
    lyrics = ROOT / "sample" / "lyrics.txt"
    if not clip.exists() or not lyrics.exists():
        return fail("sample/clip.wav or sample/lyrics.txt is missing.")

    print("OK  Sample files present")
    print("\nRunning the full pipeline on the sample clip.")
    print("First run downloads models (~2 GB) — this can take several minutes.\n")

    try:
        from pipeline import PipelineError, run_pipeline
    except ImportError as e:
        return fail(f"Dependencies not importable ({e}). Re-run the installer.")

    try:
        result = run_pipeline(
            clip, lyrics.read_text(encoding="utf-8"), section="song",
            progress_callback=lambda s, t, m: print(f"  [{s}/{t}] {m}"),
        )
    except PipelineError as e:
        return fail(str(e))

    srt_text = result["srt_path"].read_text(encoding="utf-8")
    if not result["cues"]:
        return fail("Pipeline finished but produced zero subtitle cues.")
    if "-->" not in srt_text:
        return fail("SRT file looks malformed.")

    t = result["timings"]
    print(f"\nPASS — {len(result['cues'])} cues written to {result['srt_path'].name}")
    print(f"  vocal isolation: {t['separate_seconds']:.1f}s")
    print(f"  lyric alignment: {t['align_seconds']:.1f}s")
    if "listen_seconds" in t:
        print(f"  listening check: {t['listen_seconds']:.1f}s")
    print(f"  job folder:      {result['job_dir']}")
    print("\nSetup complete. Start the app with: python app.py (or the start script).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

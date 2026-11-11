"""SRT Generator — processing pipeline.

All logic lives here; app.py is UI-only. Two flows share the same stages:

- Lyrics/script: prepare audio → (isolate the voice) → align the given text → SRT.
- Transcribe: prepare audio → (isolate the voice) → Whisper draft → the user edits
  and approves it → align the approved text to the same audio → SRT. The voice
  is isolated once; approval reuses it.

Every job belongs to a section (config.SECTIONS: song, drama, reels) that sets whether
the voice is isolated by default and how the text is cut into subtitles.

Also runnable from the CLI:

    python pipeline.py <audio|video> <lyrics.txt> [vertical|horizontal] [language] [song|drama|reels] [isolate=auto|on|off]
    python pipeline.py --transcribe <audio|video> [language] [song|drama|reels] [isolate=…] [script=<code>]
"""

import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import unicodedata
from datetime import datetime
from pathlib import Path

import captions
import config
import corrections

ROOT = Path(__file__).parent
# Writable data (jobs, models, uploads). Defaults to the app folder; the macOS .app
# sets SRT_DATA_DIR to ~/Library/Application Support/SRT Generator because its
# bundle is read-only.
DATA_DIR = Path(os.environ.get("SRT_DATA_DIR") or ROOT)
JOBS_DIR = DATA_DIR / "jobs"
MODELS_DIR = DATA_DIR / "models"

# Keep all downloaded models inside the data folder — the install is fully
# self-contained and deleting the folder removes everything.
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
ACCEPTED_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
UNREADABLE_FILE = ("Couldn't read this file. Use MP3, WAV, M4A or FLAC audio, "
                   "or MP4, MOV, M4V, MKV or WEBM video.")

AUTO_LANGUAGE = "auto"
SECTIONS = tuple(config.SECTIONS)  # ("song", "drama", "reels")
ISOLATE_CHOICES = ("auto", "on", "off")


def _tool(name: str) -> str:
    """Path to ffmpeg/ffprobe: the bundled copy (data folder or app folder bin/) first, then PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in (DATA_DIR / "bin", ROOT / "bin"):
        if (folder / exe).is_file():
            return str(folder / exe)
    return shutil.which(name) or name


FFMPEG = _tool("ffmpeg")
FFPROBE = _tool("ffprobe")
# The separator packages run "ffmpeg" by name, so the bundled folder goes on PATH too.
if os.path.isabs(FFMPEG):
    os.environ["PATH"] = os.path.dirname(FFMPEG) + os.pathsep + os.environ.get("PATH", "")


def clean_section(section: str | None) -> str:
    """A section code; "speech" (V3.8's content type) means reels, anything unknown means song."""
    if section == "speech":
        return "reels"
    return section if section in config.SECTIONS else "song"


def clean_isolate(isolate: str | None, section: str) -> str:
    return isolate if isolate in ISOLATE_CHOICES else config.SECTIONS[section]["isolate"]

# Lazily loaded, cached across jobs so only the first run pays model-load time.
_align_model = None
_separator = None
_separator_key = None  # (engine, model) the cached separator was loaded with


class PipelineError(Exception):
    """Error with a message safe to show in the UI. Details go to the job log."""


# ---------------------------------------------------------------------------
# Stage 0: validate

def is_video(path: Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def probe_duration(audio_path: Path) -> float:
    """Return audio duration in seconds via ffprobe."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except FileNotFoundError:
        raise PipelineError(
            "ffmpeg isn't installed or isn't on PATH. Re-run the setup guide (CLAUDE.md / README)."
        )
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise PipelineError(UNREADABLE_FILE)


def _has_audio_stream(path: Path) -> bool:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    return bool(out.stdout.strip())


def validate_audio(audio_path: Path) -> tuple[float, str | None]:
    """Return (duration_seconds, warning_or_None). Raises PipelineError on hard failures.

    Accepts audio and video files; a video must have a sound track.
    """
    if audio_path.suffix.lower() not in ACCEPTED_EXTENSIONS or not audio_path.exists():
        raise PipelineError(UNREADABLE_FILE)
    duration = probe_duration(audio_path)
    if is_video(audio_path) and not _has_audio_stream(audio_path):
        raise PipelineError("This video has no sound track, so there's nothing to subtitle.")
    if duration > config.MAX_MINUTES_HARD * 60:
        raise PipelineError(
            f"This file is over the {config.MAX_MINUTES_HARD} minute limit for this tool."
        )
    warning = None
    if duration > config.MAX_MINUTES_WARN * 60:
        warning = (
            f"Heads up: this file is over {config.MAX_MINUTES_WARN} minutes, "
            "processing will take a while."
        )
    return duration, warning


# ---------------------------------------------------------------------------
# Stage 1: preprocess lyrics

SECTION_TAG = re.compile(r"^\[.*\]$")
# Only "x2"-style counts (2-99) at the very end of a line, optionally bracketed:
# "Hold on x2", "Chorus (x3)", "[Chorus x2]". A stray "x 1" or "x5" mid-line
# ("Malcolm x 3 times") no longer triggers.
REPEAT_SHORTHAND = re.compile(r"(?:^|[\s(\[])[x×]\s?(?:[2-9]|[1-9]\d)[)\]]?$", re.IGNORECASE)

REPEAT_WARNING = (
    "Possible repeat shorthand like \"x2\" detected in the lyrics. "
    "Write out every repeated chorus in full or the timing will be wrong."
)


def preprocess_lyrics(raw_text: str, strip_tags: bool = True) -> tuple[list[str], str | None]:
    """Return (lyric_lines, warning_or_None). Raises PipelineError if nothing remains."""
    lines = []
    warning = None
    for line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r" {2,}", " ", line.strip())
        if not line:
            continue
        # Checked before tag stripping so "[Chorus x2]" still warns.
        if REPEAT_SHORTHAND.search(line):
            warning = REPEAT_WARNING
        if strip_tags and SECTION_TAG.match(line):
            continue
        lines.append(line)
    if not lines:
        raise PipelineError(
            "The lyrics / transcript box is empty. Paste the words exactly as sung or "
            "spoken, one line per subtitle."
        )
    return lines, warning


# ---------------------------------------------------------------------------
# Stage 1.5: audio from video

def extract_audio(video_path: Path, out_path: Path) -> Path:
    """Write the video's first audio track as 44.1 kHz stereo WAV (what the separator expects)."""
    proc = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(video_path), "-map", "0:a:0", "-vn",
         "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(out_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not out_path.exists():
        raise PipelineError(
            "Couldn't extract the audio from this video. Try exporting it again as MP4, "
            "or upload the audio on its own."
        )
    return out_path


def make_preview(video_path: Path, out_path: Path) -> Path | None:
    """A small H.264/AAC copy for the subtitle preview, for videos the web view can't play
    (MKV, some codecs). At most 720 px tall. Returns None if ffmpeg can't make one."""
    proc = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(video_path), "-map", "0:v:0", "-map", "0:a:0",
         "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         str(out_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return out_path if proc.returncode == 0 and out_path.exists() else None


# ---------------------------------------------------------------------------
# Stage 1.75 (isolation "auto"): is there background music?

MUSIC_CHECK_SECONDS = 30.0


def _mean_volume_db(args: list[str]) -> float:
    proc = subprocess.run([FFMPEG, "-v", "info", *args, "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    match = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", proc.stderr)
    return float(match.group(1)) if match else -91.0


def detect_music(audio_path: Path, duration: float, job_dir: Path, log: logging.Logger) -> dict:
    """Separate a 30-second excerpt from the middle and compare what's left after removing the
    voice (mix − voice) with the voice itself. Returns {"music", "voice_db", "background_db",
    "vocals_path" (the whole file's vocals when the excerpt was the whole file, else None)}."""
    check_dir = job_dir / "music-check"
    check_dir.mkdir()
    whole = duration <= MUSIC_CHECK_SECONDS + 5
    excerpt = check_dir / "excerpt.wav"
    start = 0.0 if whole else duration / 2 - MUSIC_CHECK_SECONDS / 2
    subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(audio_path),
                    *([] if whole else ["-t", str(MUSIC_CHECK_SECONDS)]),
                    "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(excerpt)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    vocals = separate_vocals(excerpt, check_dir, log)
    voice_db = _mean_volume_db(["-i", str(vocals), "-af", "volumedetect"])
    background_db = _mean_volume_db([
        "-i", str(excerpt), "-i", str(vocals), "-filter_complex",
        "[1:a]volume=-1[neg];[0:a][neg]amix=inputs=2:normalize=0,volumedetect"])
    music = voice_db > -60 and background_db - voice_db > config.MUSIC_DETECT_DB
    log.info("Music check (%.0f s excerpt from %.1f s): voice %.1f dB, background %.1f dB "
             "(%.1f dB relative, threshold %.1f) -> %s",
             duration if whole else MUSIC_CHECK_SECONDS, start, voice_db, background_db,
             background_db - voice_db, config.MUSIC_DETECT_DB,
             "music detected" if music else "no music")
    whole_vocals = None
    if whole and music:
        whole_vocals = job_dir / "vocals.wav"
        vocals.rename(whole_vocals)
    shutil.rmtree(check_dir, ignore_errors=True)
    return {"music": music, "voice_db": voice_db, "background_db": background_db,
            "vocals_path": whole_vocals}


# ---------------------------------------------------------------------------
# Stage 2: vocal isolation

def separator_engine() -> str:
    """config.SEPARATOR_ENGINE, with "auto" resolved: mlx on Apple Silicon macOS, cpu elsewhere."""
    engine = config.SEPARATOR_ENGINE
    if engine not in ("mlx", "cpu"):
        import platform
        engine = "mlx" if sys.platform == "darwin" and platform.machine() == "arm64" else "cpu"
    return engine


def separate_vocals(audio_path: Path, job_dir: Path, log: logging.Logger) -> Path:
    """Isolate the vocal stem into job_dir/vocals.wav using the configured engine."""
    if separator_engine() == "mlx":
        return _separate_mlx(audio_path, job_dir, log)
    return _separate_cpu(audio_path, job_dir, log)


def _find_vocals_output(job_dir: Path) -> Path:
    target = job_dir / "vocals.wav"
    if target.exists():
        return target
    candidates = [p for p in job_dir.glob("*.wav") if "vocal" in p.name.lower()]
    if not candidates:
        raise PipelineError(
            "Couldn't isolate the vocals from this song. Check the job log for details."
        )
    candidates[0].rename(target)
    return target


def _run_separator(separator_cls, audio_path: Path, job_dir: Path) -> Path:
    """Separate with a cached separator, loading the model only on first use.

    Both separator packages write stems to output_dir on the separator *and* its
    model_instance (they swap both themselves for chunked runs), so retarget both
    to this job's folder.
    """
    global _separator, _separator_key
    key = (separator_engine(), config.SEPARATOR_MODEL)
    if _separator is None or _separator_key != key:
        _separator = separator_cls(
            log_level=logging.INFO,
            model_file_dir=str(MODELS_DIR),
            output_dir=str(job_dir),
            output_format="WAV",
            output_single_stem="Vocals",
        )
        _separator.load_model(model_filename=config.SEPARATOR_MODEL)
        _separator_key = key
    _separator.output_dir = str(job_dir)
    if getattr(_separator, "model_instance", None) is not None:
        _separator.model_instance.output_dir = str(job_dir)
    _separator.separate(str(audio_path), custom_output_names={"Vocals": "vocals"})
    return _find_vocals_output(job_dir)


def _separate_mlx(audio_path: Path, job_dir: Path, log: logging.Logger) -> Path:
    """Apple Silicon path: mlx-audio-separator on the GPU."""
    from mlx_audio_separator import Separator

    return _run_separator(Separator, audio_path, job_dir)


def _separate_cpu(audio_path: Path, job_dir: Path, log: logging.Logger) -> Path:
    """Cross-platform path (Windows / Intel Mac): audio-separator on CPU.

    NOTE: untested until Windows hardware is available — see DECISIONS.md.
    """
    try:
        from audio_separator.separator import Separator
    except ImportError:
        raise PipelineError(
            "The CPU separation engine isn't installed on this machine. "
            "Re-run the installer, or set SEPARATOR_ENGINE in config.py to 'mlx' on Apple Silicon."
        )
    return _run_separator(Separator, audio_path, job_dir)


# ---------------------------------------------------------------------------
# Stage 3: forced alignment

def _load_align_model():
    global _align_model
    if _align_model is None:
        import stable_whisper

        device = config.DEVICE
        if device == "auto":
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        _align_model = stable_whisper.load_faster_whisper(config.MODEL_SIZE, device=device)
    return _align_model


ALIGN_FAILURE_MESSAGE = (
    "Couldn't align this text to the audio. Two usual causes: the words don't "
    "match what is actually sung or said, or repeated choruses aren't written "
    "out in full. Fix the text and try again."
)


def align_lyrics(vocals_path: Path, lyric_lines: list[str], log: logging.Logger,
                 language: str | None):
    """Force-align lyric/transcript lines to the audio. Returns a stable-ts result.

    language=None lets Whisper detect it.
    """
    model = _load_align_model()
    text = "\n".join(lyric_lines)
    try:
        result = model.align(
            str(vocals_path), text, language=language, original_split=True
        )
    except MemoryError:
        raise PipelineError(
            "This machine ran out of memory. Open config.py and set MODEL_SIZE "
            "to 'small', then restart the app."
        )
    except Exception:
        log.error("Alignment raised:\n%s", traceback.format_exc())
        raise PipelineError(ALIGN_FAILURE_MESSAGE)
    if not result.segments:
        raise PipelineError(ALIGN_FAILURE_MESSAGE)
    return result


# ---------------------------------------------------------------------------
# Stage 2.5 (transcribe mode): Whisper draft

TRANSCRIBE_FAILURE_MESSAGE = (
    "Couldn't transcribe this file. Check that it has clear vocals or speech, "
    "or try picking the language instead of Auto-detect."
)


# Transcription can't use the alignment alias for Hinglish: told "en", Whisper turns Hindi
# audio into a mix of English translation and the odd romanized line (tested, DECISIONS V3.8).
# "hi" gives the actual words, in Devanagari.
TRANSCRIBE_ALIASES = {"hinglish": "hi"}


def whisper_code(language: str | None, transcribing: bool = False) -> str | None:
    """Dropdown choice → Whisper language code (None = auto-detect)."""
    if not language or language == AUTO_LANGUAGE:
        return None
    if transcribing and language in TRANSCRIBE_ALIASES:
        return TRANSCRIBE_ALIASES[language]
    return config.LANGUAGE_ALIASES.get(language, language)


HINGLISH_LABEL = "Hinglish (Hindi written in Roman letters)"


def language_name(code: str | None) -> str:
    names = dict(config.LANGUAGES)
    if code in names:
        return names[code]
    try:
        from whisper.tokenizer import LANGUAGES
        return LANGUAGES.get(code, code).title()
    except ImportError:
        return code or "Unknown"


# Whisper spends minutes retrying on silence before giving up, so check loudness first.
SILENCE_DB = -60.0


def _max_volume_db(audio_path: Path) -> float:
    proc = subprocess.run(
        [FFMPEG, "-v", "info", "-i", str(audio_path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    match = re.search(r"max_volume: (-?[\d.]+) dB", proc.stderr)
    return float(match.group(1)) if match else 0.0  # unknown: assume there's sound


SCRIPT_CODES = [code for code, _ in config.SCRIPTS]
DEFAULT_SCRIPT = "default"  # no saved choice: Hinglish for Hindi/Urdu audio, else "same"
# Script → Whisper language forced for transcription ("same"/"english" keep the dropdown's).
SCRIPT_TRANSCRIBE_LANGUAGE = {"hinglish": "hi", "devanagari": "hi", "urdu": "ur"}
# Script → language the approved text is aligned with. Hinglish aligns as English (V3.5).
SCRIPT_ALIGN_LANGUAGE = {"hinglish": "en", "devanagari": "hi", "urdu": "ur"}
HINDUSTANI = ("hi", "ur")


def _language_code(language: str | None) -> str | None:
    """stable-ts reports names ("hindi"); keep codes ("hi") everywhere downstream."""
    try:
        from whisper.tokenizer import TO_LANGUAGE_CODE
    except ImportError:
        return language
    return TO_LANGUAGE_CODE.get((language or "").lower(), language)


def detect_language(audio_path: Path) -> str | None:
    """Whisper's language guess from the first 30 seconds (None if it can't tell)."""
    from faster_whisper.audio import decode_audio

    model = _load_align_model()
    try:
        audio = decode_audio(str(audio_path))[: 30 * 16000]
        return model.detect_language(audio)[0]
    except Exception:
        return None


def resolve_script(script: str, language: str, audio_path: Path, log: logging.Logger) -> str:
    """Turn "default" into a concrete script: Hinglish when the audio is Hindi or Urdu."""
    if script in SCRIPT_CODES:
        return script
    if language in ("hi", "ur", "hinglish"):
        spoken = "hi"
    elif language in (None, AUTO_LANGUAGE):
        spoken = detect_language(audio_path)
    else:
        spoken = whisper_code(language)
    resolved = "hinglish" if spoken in HINDUSTANI else "same"
    log.info("Lyrics script: no saved choice, spoken language looks like %s -> %s", spoken, resolved)
    return resolved


QUALITIES = ("fast", "accurate")


def _hinglish_apex(audio_path: Path, log: logging.Logger, hotwords: str, quality: str,
                   spellings: dict, report) -> dict:
    """A Hinglish draft from the Apex model (V4.2); Accurate adds the second opinion and the
    re-check. Raises hinglish_model.DownloadError when the model can't be had."""
    import hinglish
    import hinglish_model

    if not hinglish_model.installed():
        def show(done, total):
            report("transcribe", f"Downloading the Hinglish model — {done:.0f} of {total:.0f} MB "
                                 "(only this once)…", min(done / total, 1.0) if total else None)
        report("transcribe", f"Downloading the Hinglish model ({config.HINGLISH_MODEL_MB} MB, only this once)…", 0.0)
        hinglish_model.download(show)
        log.info("Hinglish model downloaded to %s", hinglish_model.MODEL_DIR)
    report("transcribe", "Transcribing with the Hinglish model…")
    words = hinglish.draft(audio_path, hotwords, log)
    if not words:
        raise PipelineError("Didn't hear any words in this file to transcribe.")
    model_lines = hinglish.lines_text(words)
    other, rechecked = None, 0
    if quality == "accurate":
        report("transcribe", "Accurate mode — getting a second opinion from the standard model…")
        other = hinglish.second_opinion(audio_path, _load_align_model(), spellings, log)
    hinglish.flag(words, other, spellings)
    if quality == "accurate":
        report("transcribe", "Accurate mode — re-checking the doubtful bits on their own…")
        rechecked = hinglish.recheck(audio_path, words, hotwords, log)
        hinglish.flag(words, other, spellings)
    return {"words": words, "model_lines": model_lines, "lines": hinglish.lines_text(words),
            "second_opinion": " ".join(other) if other is not None else None, "rechecked": rechecked}


def transcribe_audio(audio_path: Path, language: str | None, log: logging.Logger,
                     script: str = "same", hotwords: str = "", quality: str = "accurate",
                     spellings: dict | None = None, report=lambda key, msg, fraction=None: None) -> dict:
    """Whisper transcription of the vocals (song) or the audio (speech).

    script picks the draft's writing system (config.SCRIPTS): it can force the Whisper
    language, switch to the translate task, and convert the result offline.
    Returns {"lines", "language" (spoken, as Whisper detected or was told), "transcribe_language"
    (code forced, or None for auto), "task", "segments" ([(start, end, text)], for translation),
    "engine"}, plus for Apex Hinglish drafts "words" (with review flags), "model_lines",
    "second_opinion" and "rechecked".
    Hinglish uses the Apex model (downloaded once; quality "fast" or "accurate"), falling back to
    the V3.8 chain if it can't be had; everything else uses the alignment model.
    hotwords: words Whisper should expect (topic words and the corrections file), sent with
    every 30-second window. spellings: the house spelling rows applied to Hinglish drafts.
    """
    if _max_volume_db(audio_path) < SILENCE_DB:
        raise PipelineError("This file is silent, so there's nothing to transcribe.")
    spellings = spellings or {}
    fallback_reason = None
    if script == "hinglish" and config.HINGLISH_ENGINE == "apex":
        import hinglish_model
        try:
            apex = _hinglish_apex(audio_path, log, hotwords, quality, spellings, report)
        except hinglish_model.DownloadError as e:
            fallback_reason = str(e)
            log.warning("Hinglish model unavailable, using the older chain: %s", e)
        except MemoryError:
            raise PipelineError("This machine ran out of memory. Close other apps and try again.")
        except PipelineError:
            raise
        except Exception:
            log.error("Hinglish model raised:\n%s", traceback.format_exc())
            raise PipelineError(TRANSCRIBE_FAILURE_MESSAGE)
        else:
            lines, _ = corrections.apply("\n".join(apex["lines"]), spellings)
            return {**apex, "lines": lines.split("\n"), "language": "hi", "transcribe_language": "en",
                    "task": "transcribe", "segments": [], "urdu_converted": False, "engine": "apex"}
    model = _load_align_model()
    code = SCRIPT_TRANSCRIBE_LANGUAGE.get(script, whisper_code(language, transcribing=True))
    task = "translate" if script == "english" else "transcribe"
    try:
        # condition_on_previous_text=False stops Whisper repeating a line in a loop,
        # which it otherwise tends to do on sung choruses.
        result = model.transcribe(str(audio_path), language=code, task=task,
                                  beam_size=5, condition_on_previous_text=False,
                                  **({"hotwords": hotwords} if hotwords else {}))
    except MemoryError:
        raise PipelineError(
            "This machine ran out of memory. Open config.py and set MODEL_SIZE "
            "to 'small', then restart the app."
        )
    except Exception:
        log.error("Transcription raised:\n%s", traceback.format_exc())
        raise PipelineError(TRANSCRIBE_FAILURE_MESSAGE)
    segments = [(float(seg.start), float(seg.end), seg.text.strip())
                for seg in result.segments if seg.text.strip()]
    if not segments:
        raise PipelineError("Didn't hear any words in this file to transcribe.")
    lines = [text for _, _, text in segments]
    converted = None
    if script == "hinglish":
        import transliterate
        converted = transliterate.has_urdu("\n".join(lines))
        lines = transliterate.to_hinglish("\n".join(lines))
        lines = corrections.apply(lines, spellings)[0].split("\n")
    elif script == "devanagari":
        import transliterate
        converted = transliterate.has_urdu("\n".join(lines))
        lines = transliterate.to_devanagari("\n".join(lines)).split("\n")
    return {"lines": lines, "language": _language_code(result.language), "transcribe_language": code,
            "task": task, "segments": segments, "urdu_converted": bool(converted),
            "engine": "transliterate" if script == "hinglish" else "whisper",
            "fallback_reason": fallback_reason}


def _latin_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 1.0
    return sum(1 for ch in letters if "LATIN" in unicodedata.name(ch, "")) / len(letters)


HINGLISH_APEX_NOTE = ("Written in Hinglish by the Hinglish speech model, with your house spellings. "
                      "Highlighted words are worth a second look — hover one to see why.")
SCRIPT_NOTES = {
    "hinglish": ("Written in Roman letters automatically from Whisper's Hindi (Devanagari) "
                 "transcription. Check the spellings, and any words Whisper misheard, before approving."),
    "english": ("English translation by Whisper. Translated words can't be matched to the singing "
                "or speech word by word, so the subtitles use Whisper's own phrase timings: "
                "approximate timing. Keep one line per phrase for the best fit."),
}


def script_note(choice: str, detected: str, draft: str, script: str = "same",
                urdu_converted: bool = False, engine: str | None = None) -> str | None:
    """Tell the user what the draft's script means (see DECISIONS V3.8)."""
    if engine == "apex":
        return HINGLISH_APEX_NOTE
    if urdu_converted:
        return ("Some words came back in Urdu script and were converted roughly (Urdu writing "
                "leaves out short vowels). Check them before approving.")
    if script in SCRIPT_NOTES:
        return SCRIPT_NOTES[script]
    if script != "same" or _latin_share(draft) >= 0.5:
        return None
    if choice == "hinglish":
        return ("Whisper can't write Hindi in Roman letters, so this draft is in Devanagari "
                "(Hindi script). For Hinglish subtitles, retype it in Roman letters before "
                "approving. Approve it as it is to get Hindi-script subtitles.")
    if detected == "ur" and choice == AUTO_LANGUAGE:
        return ("Detected Urdu, so the draft is in Urdu script. Hindi and Urdu sound the same "
                "to Whisper: if this is Hindi, pick Hindi (or Hinglish) and transcribe again.")
    if detected in ("hi", "ur", "pa"):
        return (f"This draft is in {language_name(detected)} script, not Roman letters. "
                "For Hinglish subtitles, retype it in Roman letters before approving.")
    return None


# ---------------------------------------------------------------------------
# Stage 3.5: captions (V4.6: captions.py builds them from the aligned words)

CAPTION_MODES = ("line", "word")


def caption_rules(section: str, video_format: str, mode: str = None, transcribe: bool = False,
                  max_words=None, max_chars=None, merge_short=None, language: str | None = None) -> dict:
    """The caption rules for a job: the section's limits plus the V4.6 TikTok rules, with the
    user's own max words / max characters / merge-short choices when given."""
    mode = mode if mode in CAPTION_MODES else config.CAPTION_MODE_DEFAULT
    words = _int_in(max_words, 1, 12) or config.MAX_WORDS_PER_CAPTION
    chars = _int_in(max_chars, 10, 80)
    return captions.rules_for(
        config.SECTIONS[section], video_format, mode=mode, transcribe=transcribe, language=language,
        max_words=words,
        max_chars_vertical=chars or config.MAX_CHARS_VERTICAL,
        max_chars_horizontal=chars or config.MAX_CHARS_HORIZONTAL,
        merge_short=config.MERGE_SHORT_DEFAULT if merge_short is None else bool(merge_short),
        merge_short_seconds=config.MERGE_SHORT_SECONDS, end_tail=config.CAPTION_END_TAIL,
        word_gap_fill=config.WORD_GAP_FILL_SECONDS, transcribe_pause=config.TRANSCRIBE_PAUSE_SECONDS)


def _int_in(value, low: int, high: int) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


NUMBER_EXCEPTIONS_FILE = "number-exceptions.txt"


def number_exceptions() -> list[str]:
    """The editable list of number expressions that stay in words (data folder)."""
    path = DATA_DIR / NUMBER_EXCEPTIONS_FILE
    try:
        return captions.load_exceptions(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return list(captions.DEFAULT_EXCEPTIONS)


# ---------------------------------------------------------------------------
# Stage 4: outputs

def _ms(seconds: float) -> int:
    return int(round(float(seconds) * 1000))


def _fmt_timestamp(seconds: float) -> str:
    ms = max(0, _ms(seconds))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


CRLF = "\r\n"


def srt_text(cues: list[dict]) -> str:
    """The SRT exactly as the Review panel shows the cues: numbered 1…n in order, times to the
    millisecond, one line of text each. CRLF line endings (CapCut and Premiere read it)."""
    blocks = []
    for n, c in enumerate(cues, start=1):
        start, end = _ms(c["start"]), _ms(c["end"])
        end = max(end, start + 1)
        text = " ".join(str(c["text"]).split())
        blocks.append(f"{n}{CRLF}{_fmt_timestamp(start / 1000)} --> {_fmt_timestamp(end / 1000)}{CRLF}{text}")
    return (CRLF * 2).join(blocks) + CRLF


def write_srt(cues: list[dict], path: Path) -> None:
    """UTF-8 without a BOM, CRLF line endings, on every OS (bytes, so Windows adds nothing)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(srt_text(cues).encode("utf-8"))
    os.replace(tmp, path)


def export_stem(vocals_wav: Path, job_dir: Path) -> Path:
    """Convert or keep the vocal stem per config.STEM_FORMAT."""
    if config.STEM_FORMAT == "wav":
        return vocals_wav
    out = job_dir / "vocals.mp3"
    proc = subprocess.run(
        [FFMPEG, "-y", "-i", str(vocals_wav), "-b:a", "320k", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not out.exists():
        return vocals_wav  # WAV always exists; fall back rather than fail the job
    return out


# ---------------------------------------------------------------------------
# Orchestration

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug[:40] or "song"


def _make_job_dir(audio_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        job_dir = JOBS_DIR / f"{stamp}-{_slugify(audio_path.stem)}{'' if n == 1 else f'-{n}'}"
        try:
            job_dir.mkdir(exist_ok=False)
            return job_dir
        except FileExistsError:
            continue
    raise PipelineError("Couldn't create a job folder in jobs/.")


def _job_logger(job_dir: Path) -> logging.Logger:
    log = logging.getLogger(f"job.{job_dir.name}")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    handler = logging.FileHandler(job_dir / "log.txt", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return log


ISOLATE_TITLES = {"song": "Isolate vocals", "drama": "Isolate dialogue", "reels": "Isolate voice"}


def flow_steps(mode: str, section: str, isolate: str = "on",
               polish_with: str | None = None, check_with: str | None = None) -> list[tuple[set, str]]:
    """The progress steps a flow shows, as (stage keys, title). mode: "lyrics" or "transcribe".
    polish_with: the AI provider's name when AI polish is on (transcribe mode). check_with: the
    provider's name when the final AI check runs after the captions are built (V4.6). The listening
    check (V4.8) is always the last step."""
    song = section == "song"
    first = ({"prepare", "separate"},
             ISOLATE_TITLES[section] if isolate == "on" else "Prepare audio")
    check = [({"check"}, f"Final check with {check_with}")] if check_with else []
    check += [({"listen"}, "Listening check")] if config.LISTEN_CHECK else []
    if mode == "transcribe":
        polish = [({"polish"}, f"Polish with {polish_with}")] if polish_with else []
        return [first, ({"transcribe"}, "Transcribe"), *polish,
                ({"align"}, "Align transcript"), ({"build"}, "Build subtitles"), *check]
    return [first, ({"align"}, "Align lyrics" if song else "Align transcript"),
            ({"build"}, "Build subtitles"), *check]


def make_reporter(steps: list[tuple[set, str]], progress_callback):
    """report(stage_key, message[, fraction]) → progress_callback(step, total, message[, fraction]).
    fraction (0–1) is only passed for steps with a measurable end, like a model download."""
    def report(key, message, fraction=None):
        if progress_callback:
            step = next(i for i, (keys, _) in enumerate(steps, start=1) if key in keys)
            if fraction is None:
                progress_callback(step, len(steps), message)
            else:
                progress_callback(step, len(steps), message, fraction)
    return report


def _unexpected(log: logging.Logger, job_dir: Path):
    log.error("Unexpected failure:\n%s", traceback.format_exc())
    return PipelineError(
        "Something went wrong while processing this file. "
        f"Check the log at {job_dir / 'log.txt'} for details."
    )


def start_job(audio_path, section: str = "song", report=lambda key, msg: None,
              isolate: str | None = None, video_preview: bool = False) -> dict:
    """Validate, create the job folder, get the audio out of a video, and isolate the voice
    (isolate "on", or "auto" when background music is detected). Returns the job context
    the later stages work from.

    video_preview: also keep a small playable copy of the video (preview.mp4), for videos
    the UI's web view can't play itself."""
    audio_path = Path(audio_path)
    section = clean_section(section)
    isolate = clean_isolate(isolate, section)
    duration, duration_warning = validate_audio(audio_path)

    job_dir = _make_job_dir(audio_path)
    log = _job_logger(job_dir)
    log.info("Input: %s (%.1fs), section=%s, isolate=%s", audio_path.name, duration, section, isolate)
    ctx = {
        "source_file": audio_path.name, "stem": audio_path.stem, "job_dir": job_dir,
        "log": log, "duration": duration, "section": section, "isolate": isolate,
        "music_detected": None, "preview_path": None,
        "warnings": [duration_warning] if duration_warning else [], "timings": {},
        "vocals_path": None, "detected_language": None,
    }
    if duration_warning:
        log.warning(duration_warning)

    try:
        t0 = time.perf_counter()
        if is_video(audio_path):
            # Keep only the audio: the video can be gigabytes and isn't needed again.
            report("prepare", "Step 1 — extracting the audio from the video…")
            input_audio = extract_audio(audio_path, job_dir / "input.wav")
            log.info("Extracted audio from %s", audio_path.name)
            if video_preview:
                report("prepare", "Step 1 — making a preview copy of the video…")
                ctx["preview_path"] = make_preview(audio_path, job_dir / "preview.mp4")
                log.info("Preview copy: %s", "preview.mp4" if ctx["preview_path"] else "failed")
        else:
            input_audio = job_dir / f"input{audio_path.suffix.lower()}"
            shutil.copy2(audio_path, input_audio)
        ctx["input_audio"] = input_audio
        separate = isolate == "on"
        if isolate == "auto":
            report("prepare", "Step 1 — checking for background music…")
            check = detect_music(input_audio, duration, job_dir, log)
            ctx["music_detected"] = separate = check["music"]
            ctx["vocals_path"] = check["vocals_path"]
        if separate and ctx["vocals_path"] is None:
            report("separate", f"Step 1 — {ISOLATE_TITLES[section].lower().replace('isolate', 'isolating')} "
                               "(roughly 10–60 seconds)…")
            ctx["vocals_path"] = separate_vocals(input_audio, job_dir, log)
        if separate:
            ctx["source_path"] = ctx["vocals_path"]
            log.info("Voice isolated -> %s", ctx["vocals_path"].name)
        else:
            ctx["source_path"] = input_audio  # nothing to remove: use the audio as is
            log.info("Voice not isolated (isolate=%s)", isolate)
        ctx["timings"]["separate_seconds"] = round(time.perf_counter() - t0, 2)
    except PipelineError:
        raise
    except Exception:
        raise _unexpected(log, job_dir)
    return ctx


def _house_spellings(ctx: dict) -> dict:
    try:
        return corrections.load_spellings(DATA_DIR)
    except corrections.CorrectionsError as e:
        warning = (f"Your Hinglish house spellings file has {len(e.errors)} bad row(s), so the "
                   f"built-in spellings were used: {e.errors[0]}. Fix it in the Corrections tab.")
        ctx["warnings"].append(warning)
        ctx["log"].warning(warning)
        return corrections.default_spellings()


def run_transcription(ctx: dict, language: str = None, report=lambda key, msg, fraction=None: None,
                      script: str = DEFAULT_SCRIPT, quality: str = None, topic_words: str = "",
                      ai: dict | None = None) -> dict:
    """Whisper draft of what's sung or said, one phrase per line, for the user to edit.

    script: a config.SCRIPTS code, or "default" (no saved choice) to pick Hinglish for
    Hindi/Urdu audio and "same" otherwise. quality ("fast"/"accurate") applies to Hinglish
    drafts. topic_words: the job's names, brands and terms, given to Whisper as hints.
    ai: AI polish settings (ai_polish.active()) to polish the offline draft with, or None.
    """
    language = language or config.TRANSCRIBE_LANGUAGE
    quality = quality if quality in QUALITIES else config.QUALITY_DEFAULT
    topic = corrections.topic_word_list(topic_words)
    log, job_dir = ctx["log"], ctx["job_dir"]
    report("transcribe", "Transcribing with Whisper — about as long as the audio for English, "
                         "several times longer for Hindi…")
    try:
        t0 = time.perf_counter()
        script = resolve_script(script, language, ctx["source_path"], log)
        rows, table, hotwords = _corrections_for(ctx, script, language, topic)
        spellings = _house_spellings(ctx) if script == "hinglish" else {}
        result = transcribe_audio(ctx["source_path"], language, log, script, hotwords, quality,
                                  spellings, report)
        ctx["timings"]["transcribe_seconds"] = round(time.perf_counter() - t0, 2)
        engine = result["engine"]
        ctx.update(detected_language=result["language"], script=script,
                   transcribe_language=result["transcribe_language"], task=result["task"],
                   translation_segments=result["segments"] if script == "english" else None,
                   engine=engine, quality=quality if engine == "apex" else None, topic_words=topic)
        if result.get("fallback_reason"):
            ctx["warnings"].append(
                f"{result['fallback_reason']} This draft was made the older way instead (Whisper's "
                "Hindi turned into Roman letters), which is slower and less accurate.")
        # The draft before the user's corrections: the model's words with the house spellings
        # (Hinglish). It's what approvals are compared with, so house spellings are never learnt.
        whisper_draft = "\n".join(result["lines"])
        (job_dir / "transcript-draft.txt").write_text(whisper_draft + "\n", encoding="utf-8")
        if result.get("model_lines"):
            (job_dir / "transcript-model.txt").write_text("\n".join(result["model_lines"]) + "\n",
                                                          encoding="utf-8")
        if result.get("second_opinion"):
            (job_dir / "transcript-second-opinion.txt").write_text(result["second_opinion"] + "\n",
                                                                   encoding="utf-8")
        log.info("Transcribed %d lines in %.1fs: language choice=%s, script=%s, engine=%s, quality=%s, "
                 "task=%s, whisper code forced=%s, spoken language=%s",
                 len(result["lines"]), ctx["timings"]["transcribe_seconds"], language, script, engine,
                 ctx["quality"], result["task"], result["transcribe_language"], result["language"])
        # The table depends on the spoken language for "same"; it's known for sure only now.
        actual_table = corrections.table_for(script, result["language"])
        if actual_table != table:
            rows = _corrections_rows(ctx, actual_table)
        draft, hits = corrections.apply(whisper_draft, rows)
        if hits:
            (job_dir / "transcript-autocorrected.txt").write_text(draft + "\n", encoding="utf-8")
            log.info("Auto-corrected %d words from the %s corrections: %s", len(hits), actual_table,
                     ", ".join(f"{h['original']} -> {h['replacement']}" for h in hits))
        marks = []
        if result.get("words"):
            import hinglish
            marks = hinglish.review_marks(result["words"], draft)
            (job_dir / "review.json").write_text(json.dumps(
                {"quality": quality, "words": result["words"], "second_opinion": result["second_opinion"],
                 "rechecked": result["rechecked"], "marks": marks}, indent=1, ensure_ascii=False),
                encoding="utf-8")
            counts = {k: sum(m["kind"] == k for m in marks) for k in ("disagree", "unsure", "rechecked")}
            log.info("Review highlights: %s (%d stretches re-checked and replaced)", counts, result["rechecked"])
        ctx.update(whisper_draft=whisper_draft, corrections_table=actual_table,
                   auto_corrections=hits, review_marks=marks, offline_draft=draft,
                   corrections_rows=rows, spellings=spellings, ai_changes=[],
                   recorded_pairs=ctx.get("recorded_pairs") or {})
    except PipelineError:
        raise
    except Exception:
        raise _unexpected(log, job_dir)
    polished = run_polish(ctx, draft, ai, report) if ai else {}
    return {
        "draft_text": polished.get("draft_text", draft),
        "offline_draft": draft,
        "ai_marks": polished.get("ai_marks", []),
        "ai_message": polished.get("ai_message"),
        "ai_provider": polished.get("ai_provider"),
        "whisper_draft": whisper_draft,
        "auto_corrections": hits,
        "review_marks": marks,
        "corrections_table": actual_table,
        "hotwords": hotwords,
        "topic_words": topic,
        "engine": engine,
        "quality": ctx["quality"],
        "detected_language": result["language"],
        "detected_language_name": language_name(result["language"]),
        "script": script,
        "approximate_timing": script == "english",
        "script_note": script_note(language, result["language"], draft, script,
                                   result["urdu_converted"], engine),
    }


def _corrections_rows(ctx: dict, table: str) -> dict:
    """The table's rows from corrections.md. A hand-edited file with errors is skipped with a
    warning (the transcription itself goes on)."""
    try:
        return corrections.load(DATA_DIR).get(table, {})
    except corrections.CorrectionsError as e:
        warning = (f"Your corrections file has {len(e.errors)} bad row(s), so it wasn't used: "
                   f"{e.errors[0]}. Fix it in the Corrections tab.")
        if warning not in ctx["warnings"]:
            ctx["warnings"].append(warning)
            ctx["log"].warning(warning)
        return {}


def _corrections_for(ctx: dict, script: str, language: str,
                     topic: list[str] = ()) -> tuple[dict, str, str]:
    """Before transcribing: the corrections table this draft will most likely use, and Whisper's
    hotwords: the job's topic words, then the table's most frequent correct words (counted with
    Whisper's own tokenizer)."""
    spoken = whisper_code(language, transcribing=True)
    if script == "same" and spoken is None and corrections.path(DATA_DIR).exists():
        spoken = detect_language(ctx["source_path"])
    table = corrections.table_for(script, spoken)
    rows = _corrections_rows(ctx, table)
    words = ""
    if rows or topic:
        model = _load_align_model()
        tokenizer = getattr(model, "hf_tokenizer", None)
        count = (lambda s: len(tokenizer.encode(s, add_special_tokens=False).ids)) if tokenizer else None
        words = corrections.hotwords(rows, count, topic=topic)
        if words:
            ctx["log"].info("Hotwords for Whisper (%d topic words, %s corrections, %d tokens max): %s",
                            len(topic), table, corrections.HOTWORD_TOKENS, words)
    ctx["hotwords"] = words
    return rows, table, words


def run_polish(ctx: dict, text: str, ai: dict, report=lambda key, msg, fraction=None: None) -> dict:
    """AI polish of the draft text (V4.3). Never fails the job: any problem keeps the text as it is
    and returns a one-line ai_message. Returns {"draft_text", "ai_marks", "ai_message", "ai_provider"}.
    The AI's word changes are remembered in ctx["ai_changes"] so approval can tell its fixes apart."""
    import ai_polish

    log, job_dir = ctx["log"], ctx["job_dir"]
    name = ai_polish.provider_name(ai["provider"])
    model = ai["models"][ai["provider"]]
    report("polish", f"Polishing with {name}…")
    all_lines = text.split("\n")
    keep = [i for i, line in enumerate(all_lines) if line.strip()]  # blank lines aren't sent
    doubtful = [{"line": keep.index(m["line"]) + 1 if m["line"] in keep else None, "word": m["replacement"],
                 "why": {"disagree": f"a second recogniser heard “{m['original']}”" if m["original"]
                         else "a second recogniser heard nothing here",
                         "unsure": "low confidence", "rechecked": "re-checked"}.get(m["kind"], m["kind"])}
                for m in ctx.get("review_marks") or []]
    t0 = time.perf_counter()
    try:
        result = ai_polish.polish(
            [all_lines[i] for i in keep], ai, section=ctx["section"], script=ctx.get("script"),
            language=language_name(ctx.get("detected_language")) if ctx.get("detected_language") else None,
            topic_words=ctx.get("topic_words") or [], correction_rows=ctx.get("corrections_rows") or {},
            spellings=ctx.get("spellings") or {}, doubtful=doubtful)
    except ai_polish.PolishError as e:
        log.warning("AI polish with %s (%s) skipped after %.1fs: %s (%s)", name, model,
                    time.perf_counter() - t0, e, e.kind)
        return {"draft_text": text, "ai_marks": [], "ai_provider": name,
                "ai_message": f"AI polish skipped — {e}. The offline draft is unchanged."}
    except Exception:
        log.error("AI polish raised:\n%s", traceback.format_exc())
        return {"draft_text": text, "ai_marks": [], "ai_provider": name,
                "ai_message": "AI polish skipped — something went wrong. The offline draft is unchanged."}
    for i, line in zip(keep, result["lines"]):
        all_lines[i] = line
    marks = [{**m, "line": keep[m["line"]]} for m in result["marks"]]
    for m in marks:  # "[boss?]" over "boss" flags a word without changing it: not a fix
        change = {"from": m["original"], "to": ai_polish.UNSURE.sub(r"\1", m["replacement"])}
        if corrections.key_of(change["from"]) != corrections.key_of(change["to"]) \
                and change not in ctx.setdefault("ai_changes", []):
            ctx["ai_changes"].append(change)
    ctx["ai_polish"] = {"provider": ai["provider"], "model": model,
                        "runs": (ctx.get("ai_polish") or {}).get("runs", 0) + 1}
    runs_file = job_dir / "ai-polish.json"
    runs = json.loads(runs_file.read_text(encoding="utf-8")) if runs_file.exists() else []
    runs.append({"provider": ai["provider"], "model": model, "seconds": result["seconds"],
                 "sent": text, "returned": "\n".join(all_lines), "changes": marks,
                 "lines_kept_unpolished": [keep[i] + 1 for i in result["skipped_lines"]]})
    runs_file.write_text(json.dumps(runs, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("AI polish with %s (%s) in %.1fs: %d changes%s: %s", name, model, result["seconds"],
             len(marks), f", {len(result['skipped_lines'])} lines rejected and kept as they were"
             if result["skipped_lines"] else "",
             ", ".join(f"{m['original']} -> {m['replacement']}" for m in marks) or "none")
    message = None
    if result["skipped_lines"]:
        message = (f"{len(result['skipped_lines'])} line(s) the AI rewrote too much were left as they were.")
    return {"draft_text": "\n".join(all_lines), "ai_marks": marks, "ai_message": message, "ai_provider": name}


def _sources(ctx: dict, pairs: list[tuple[str, str]], approved: str) -> tuple[dict, list[dict]]:
    """Who made each approved fix — {(wrong, correct): "AI" | "User" | "AI+User"} — and the AI
    changes the user undid (never learnt; noted in the job folder only)."""
    key = corrections.key_of
    # An AI change to a word the corrections file had already replaced counts from the original word.
    was = {key(h["replacement"]): key(h["original"]) for h in ctx.get("auto_corrections") or []}
    ai_pairs, ai_from = set(), set()
    for c in ctx.get("ai_changes") or []:
        for origin in {key(c["from"]), was.get(key(c["from"]))} - {None}:
            ai_pairs.add((origin, key(c["to"])))
            ai_from.add(origin)
    sources = {}
    for wrong, correct in pairs:
        kw, kc = key(wrong), key(correct)
        sources[(wrong, correct)] = ("AI" if (kw, kc) in ai_pairs else
                                     "AI+User" if kw in ai_from else "User")
    approved_words = f" {key(approved)} "
    learnt_from = {key(w) for w, _ in pairs}
    rejected = [c for c in ctx.get("ai_changes") or []
                if not ({key(c["from"]), was.get(key(c["from"]))} & learnt_from)
                and f" {key(c['to'])} " not in approved_words]
    return sources, rejected


def record_approval(ctx: dict, approved_text: str) -> list[dict]:
    """After an approved transcript is timed: save the draft, the approved text and their word
    diff in the job folder, and add the fixes to corrections.md. Approving the same job again
    only adds what's new since the last approval."""
    from collections import Counter

    job_dir, log = ctx["job_dir"], ctx["log"]
    draft = ctx.get("whisper_draft")
    if draft is None:
        return []
    (job_dir / "approved-text.txt").write_text(approved_text.strip() + "\n", encoding="utf-8")
    diff = corrections.diff_lines(draft, approved_text)
    learnt = corrections.learnable(diff)
    sources, rejected = _sources(ctx, learnt, approved_text)
    rejected_md = "".join(f"- “{c['from']}” → “{c['to']}” (undone, not learnt)\n" for c in rejected)
    (job_dir / "corrections-diff.md").write_text(
        f"# Whisper's draft vs the approved text\n\nTable: {ctx['corrections_table']}\n\n"
        + corrections.diff_markdown(diff)
        + "".join(f"- learnt “{w}” → “{c}” (source: {sources[(w, c)]})\n" for w, c in learnt)
        + (f"\n## AI fixes you rejected\n\n{rejected_md}" if rejected else ""), encoding="utf-8")
    (job_dir / "corrections-diff.json").write_text(json.dumps(
        {"table": ctx["corrections_table"], "whisper_draft": draft, "approved": approved_text,
         "auto_corrections": ctx.get("auto_corrections", []), "ai_changes": ctx.get("ai_changes", []),
         "diff": diff, "learnt": [{"wrong": w, "correct": c, "source": sources[(w, c)]} for w, c in learnt],
         "ai_rejected": rejected}, indent=2, ensure_ascii=False), encoding="utf-8")
    if rejected:
        (job_dir / "ai-rejected.json").write_text(json.dumps(rejected, indent=1, ensure_ascii=False),
                                                  encoding="utf-8")
        log.info("AI fixes undone by the user (not learnt): %s",
                 ", ".join(f"{c['from']} -> {c['to']}" for c in rejected))
    pairs = Counter(learnt)
    new = [(w, c, sources[(w, c)]) for w, c in (pairs - Counter(ctx["recorded_pairs"])).elements()]
    ctx["recorded_pairs"] = dict(pairs | Counter(ctx["recorded_pairs"]))
    try:
        changed = corrections.record(DATA_DIR, ctx["corrections_table"], new)
    except corrections.CorrectionsError as e:
        log.warning("corrections.md has errors, nothing was added: %s", e)
        return []
    log.info("Corrections recorded (%s): %s", ctx["corrections_table"],
             ", ".join(f"{c['wrong']} -> {c['correct']} ({c['source']}, {c['change']})" for c in changed)
             or "none")
    return changed


def approximate_segments(segments: list[tuple], lines: list[str]) -> list:
    """Word timings for text that can't be force-aligned (a translation).

    One approved line per Whisper segment keeps that segment's span; otherwise the lines
    are laid out over the speech in order, by length. Words get times in proportion to
    their length. Returns objects shaped like stable-ts segments for chunk_segments().
    """
    from types import SimpleNamespace

    def timed_words(text, to_time, start, end):
        words = text.split()
        total = sum(len(w) + 1 for w in words) or 1
        out, pos = [], 0
        for w in words:
            a, b = pos / total, (pos + len(w)) / total
            out.append(SimpleNamespace(word=" " + w, start=to_time(start + a * (end - start)),
                                       end=to_time(start + b * (end - start))))
            pos += len(w) + 1
        return SimpleNamespace(words=out)

    if len(lines) == len(segments):
        return [timed_words(line, lambda t: t, s, e) for (s, e, _), line in zip(segments, lines)]

    spans = [(s, e) for s, e, _ in segments]
    speech = sum(e - s for s, e in spans) or 1.0

    def to_time(t):  # position in speech-only time → real time, skipping the gaps
        for s, e in spans:
            if t <= e - s:
                return s + t
            t -= e - s
        return spans[-1][1]

    total = sum(len(line) for line in lines) or 1
    out, pos = [], 0.0
    for line in lines:
        share = speech * len(line) / total
        out.append(timed_words(line, to_time, pos, pos + share))
        pos += share
    return out


def align_and_write(ctx: dict, lyrics_text: str, strip_tags: bool = True,
                    video_format: str = None, language: str = None,
                    report=lambda key, msg: None, caption_mode: str = None, max_words=None,
                    max_chars=None, merge_short=None, ai: dict | None = None) -> dict:
    """Align the given (or approved) text to the job's audio and write the SRT and cue data.

    For songs this reuses the vocals isolated in start_job, so nothing is separated twice.
    caption_mode: "line" (TikTok rules) or "word"; max_words / max_chars / merge_short: the user's
    caption settings (None = config defaults). ai: settings for the final AI check
    (final_check.active()), or None for the offline cleanup only.
    """
    video_format = video_format or config.FORMAT_DEFAULT
    language = language or config.LANGUAGE
    script = ctx.get("script")  # set by run_transcription; None in lyrics mode
    transcribed = ctx.get("whisper_draft") is not None
    approximate = script == "english"
    # Auto-detect: reuse what transcription detected; without a transcription Whisper detects.
    whisper_language = (ctx["detected_language"] if language == AUTO_LANGUAGE
                        else whisper_code(language))
    if script in SCRIPT_ALIGN_LANGUAGE:
        whisper_language = SCRIPT_ALIGN_LANGUAGE[script]
    if (language == "hinglish" or script == "hinglish") and _latin_share(lyrics_text) < 0.5:
        whisper_language = "hi"  # an approved Devanagari draft with Hinglish still selected
    if approximate:
        whisper_language = None  # nothing is aligned; timings come from Whisper's segments
    caption_language = ("hinglish" if "hinglish" in (language, script)
                        else whisper_language or ctx.get("detected_language"))
    rules = caption_rules(ctx["section"], video_format, caption_mode, transcribe=transcribed,
                          max_words=max_words, max_chars=max_chars, merge_short=merge_short,
                          language=caption_language)
    if approximate:  # translation phrase timings can run long
        rules["max_seconds"] = min(rules["max_seconds"] or math.inf, config.MAX_CUE_SECONDS_TRANSLATION)
    lyric_lines, lyric_warning = preprocess_lyrics(lyrics_text, strip_tags=strip_tags)
    # Speaker labels ("Sarah: ...") start a new caption and are left out of the text (V4.6).
    lyric_lines, speakers, labels = captions.split_speakers(lyric_lines)
    if not lyric_lines:
        raise PipelineError("The script has speaker names but no words to time.")
    log, job_dir, timings = ctx["log"], ctx["job_dir"], ctx["timings"]
    warnings = ctx["warnings"] + ([lyric_warning] if lyric_warning else [])
    log.info("%s %d lines, section=%s, strip_tags=%s, format=%s, rules=%s, language=%s, script=%s "
             "(whisper code %s), speakers=%s",
             "Timing (approximate, no alignment)" if approximate else "Aligning",
             len(lyric_lines), ctx["section"], strip_tags, video_format, rules, language, script,
             whisper_language, labels or "none")
    if lyric_warning:
        log.warning(lyric_warning)
    (job_dir / "lyrics.txt").write_text("\n".join(lyric_lines) + "\n", encoding="utf-8")

    try:
        song = ctx["section"] == "song"
        isolated = ctx["source_path"] == ctx["vocals_path"]
        t0 = time.perf_counter()
        if approximate:
            report("align", "Timing the translation from Whisper's phrase timings…")
            segments = approximate_segments(ctx["translation_segments"], lyric_lines)
        else:
            report("align", f"Aligning {'lyrics' if song else 'the transcript'} (first run "
                            "loads the model, roughly 30 seconds to 2 minutes)…")
            result = align_lyrics(ctx["source_path"], lyric_lines, log, whisper_language)
            whisper_language = whisper_language or getattr(result, "language", None)
            segments = result.segments
        timings["align_seconds"] = round(time.perf_counter() - t0, 2)
        log.info("Timed %d segments in %.1fs", len(segments), timings["align_seconds"])

        report("build", "Building the captions…")
        t0 = time.perf_counter()
        words = captions.words_from_segments(segments, lyric_lines, speakers)
        dropped = []
        if not transcribed and not approximate:
            # Only what's spoken: script lines the aligner couldn't find are left out (V4.6).
            dropped = captions.unmatched_lines(words, config.UNMATCHED_LINE_PROBABILITY)
            if len(dropped) >= len(lyric_lines):
                dropped = []  # nothing matched well: better to show it all than nothing
            if dropped:
                words = [w for w in words if w["line"] not in dropped]
                shown = ", ".join(f"“{lyric_lines[i]}”" for i in dropped[:5])
                more = f" and {len(dropped) - 5} more" if len(dropped) > 5 else ""
                warning = (f"{len(dropped)} script line(s) weren't found in the audio and were left out "
                           f"of the SRT: {shown}{more}")
                warnings.append(warning)
                log.warning(warning + " (lines %s)", [i + 1 for i in dropped])
        if not words:
            raise PipelineError(ALIGN_FAILURE_MESSAGE)
        numbers_language = "hinglish" if "hinglish" in (language, script) else whisper_language
        tokens = captions.offline_cleanup(captions.make_tokens(words, numbers_language, number_exceptions()), words)
        ctx["timeline"] = {"words": words, "tokens": tokens, "lines": lyric_lines, "speakers": speakers,
                           "dropped_lines": dropped, "numbers_language": numbers_language,
                           "script_text": None if transcribed else lyrics_text, "ai": {}}
        ctx.update(align_language=language, whisper_language_used=whisper_language, review_recorded={},
                   video_format=video_format, ai_recorded=[])
        layout = build_layout(ctx, rules, ai, report)
        timings["outputs_seconds"] = round(time.perf_counter() - t0, 2)
        out = write_outputs(ctx, layout, rules, extra={
            "language": language, "script": script, "whisper_language": whisper_language,
            "timing": "approximate" if approximate else "aligned", "speakers": labels or None,
            "dropped_lines": [{"line": i + 1, "text": lyric_lines[i]} for i in dropped]})
        stem_path = export_stem(ctx["vocals_path"], job_dir) if isolated else None
        log.info("Outputs written. Timings: %s", timings)
        return {**out, "stem_path": stem_path, "audio_path": ctx["input_audio"], "job_dir": job_dir,
                "timings": timings,
                "warnings": warnings + ([layout["ai_message"]] if layout["ai_message"] else [])}
    except PipelineError:
        raise
    except Exception:
        raise _unexpected(log, job_dir)


def _ai_key(rules: dict) -> str:
    return f"{rules['mode']}:{rules['max_words']}:{rules['max_chars']}:{rules['merge_short']}"


def build_layout(ctx: dict, rules: dict, ai: dict | None, report=lambda key, msg: None) -> dict:
    """Cues from the job's timeline (ctx["timeline"]) with these rules, after the final AI check
    when `ai` is set. The check's text fixes are kept for later layouts (switching Line by line /
    Word by word); its cut points only for the same rules. Returns {"cues", "ai_message",
    "ai_changes", "ai_provider"}."""
    import ai_polish
    import final_check

    tl = ctx["timeline"]
    words = tl["words"]
    tokens = tl.get("ai_tokens") or tl["tokens"]
    chunks = None if rules["mode"] == "word" else captions.line_chunks(tokens, words, rules)
    message, provider = None, None
    key = _ai_key(rules)
    if ai and key not in tl["ai"]:
        provider = ai_polish.provider_name(ai["provider"])
        report("check", f"Final check with {provider}…")
        current = chunks if chunks is not None else [[k] for k in range(len(tokens))]
        # Hinglish is timed with the English model, but the AI is told what the text really is (V4.6.1).
        if "hinglish" in (rules.get("language"), ctx.get("script")):
            language = HINGLISH_LABEL
        else:
            language = language_name(ctx["whisper_language_used"]) if ctx.get("whisper_language_used") else None
        result = final_check.review(tokens, words, current, ai, section=ctx["section"],
                                    language=language,
                                    script=ctx.get("script"), script_text=tl.get("script_text"), rules=rules,
                                    exceptions=number_exceptions())
        message = result["message"]
        tl["ai"][key] = {"chunks": result["chunks"], "message": message}
        if result["changes"]:
            tl["ai_tokens"] = tokens = result["tokens"]
        _log_ai(ctx, result, rules)
    if rules["mode"] == "line":
        saved = tl["ai"].get(key, {}).get("chunks")
        chunks = captions.merge_cuts(tokens, words, saved, rules)[0] if saved \
            else captions.line_chunks(tokens, words, rules)
    cue_rules = {**rules, "audio_end": ctx.get("duration")}
    cues = captions.build_cues(tokens, words, cue_rules, chunks)
    listen = None
    if config.LISTEN_CHECK:  # V4.8: every caption checked against the audio, after the AI check
        import listen_check
        t0 = time.perf_counter()
        listened = listen_check.run(ctx, tokens, words, cues, cue_rules, report)
        cues, listen = listened["cues"], listened["summary"]
        if listened.get("changed"):  # its fixes live in the timeline, so a Line/Word switch keeps them
            tl["words"] = listened["words"]
            tl["ai_tokens" if tl.get("ai_tokens") else "tokens"] = listened["tokens"]
        ctx.setdefault("timings", {})["listen_seconds"] = round(time.perf_counter() - t0, 2)
    return {"cues": cues, "ai_message": message, "ai_provider": provider, "listen": listen,
            "ai_changes": sum(len(c.get("ai", [])) for c in cues)}


def _log_ai(ctx: dict, result: dict, rules: dict):
    """Each final check is kept in the job folder (what was sent and returned; never the key)."""
    path = ctx["job_dir"] / "ai-final-check.json"
    try:
        runs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except ValueError:
        runs = []
    runs.append({"provider": result.get("provider"), "seconds": result.get("seconds"), "mode": rules["mode"],
                 "changes": result["changes"], "cuts_used": result["chunks"] is not None,
                 "refused": result.get("refused") or [], "unsure": result.get("unsure") or [],
                 "message": result["message"], "sent": result["sent"], "returned": result["returned"]})
    path.write_text(json.dumps(runs, indent=1, ensure_ascii=False), encoding="utf-8")
    ctx["log"].info("Final AI check (%s, %s mode) in %ss: %d text fixes, AI cuts %s%s",
                    result.get("provider"), rules["mode"], result.get("seconds"), result["changes"],
                    "used" if result["chunks"] is not None else "not used",
                    f" — {result['message']}" if result["message"] else "")
    for r in result.get("refused") or []:  # V4.8: every refused change, and why
        ctx["log"].info("Final AI check: refused “%s” -> “%s” (word %d): %s", r["from"], r["to"], r["i"], r["why"])
    if result.get("unsure"):
        ctx["log"].info("Final AI check: marked [?] (unsure): %s",
                        ", ".join(f"“{result['tokens'][k]['text']}”" for k in result["unsure"]))


def write_outputs(ctx: dict, layout: dict, rules: dict, extra: dict | None = None) -> dict:
    """<name>.srt, original.srt (identical) and cues.json from one list of cues; old Review edits
    are cleared. The Review panel shows exactly these cues."""
    job_dir = ctx["job_dir"]
    cues = layout["cues"]
    srt_path = job_dir / f"{ctx['stem']}.srt"
    write_srt(cues, srt_path)
    original_srt = job_dir / "original.srt"
    write_srt(cues, original_srt)
    for stale in ("edited.srt", "edited-cues.json"):
        (job_dir / stale).unlink(missing_ok=True)
    tl = ctx["timeline"]
    timeline = {k: tl[k] for k in ("words", "tokens", "lines", "speakers", "dropped_lines", "numbers_language")}
    timeline["ai_tokens"] = tl.get("ai_tokens")
    timeline["heard"] = tl.get("heard")
    (job_dir / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    isolated = ctx["source_path"] == ctx["vocals_path"]
    meta = ctx.setdefault("cues_meta", {})
    meta.update(extra or {})
    if layout.get("ai_provider"):
        meta["final_check"] = {"provider": layout["ai_provider"], "changes": layout["ai_changes"],
                               "message": layout["ai_message"]}
    cues_json = {
        "source_file": ctx["source_file"],
        "section": ctx["section"],
        "content_type": "song" if ctx["section"] == "song" else "speech",
        "isolate": ctx["isolate"],
        "isolated": isolated,
        "music_detected": ctx["music_detected"],
        "video_format": ctx.get("video_format"),
        "caption_mode": rules["mode"],
        "max_chars_per_line": rules["max_chars"],
        "max_words_per_cue": rules["max_words"],
        "max_cue_seconds": rules["max_seconds"],
        "pause_split_seconds": rules["pause_split"],
        "merge_short": rules["merge_short"],
        "model_size": config.MODEL_SIZE,
        **{k: meta.get(k) for k in ("language", "script", "whisper_language", "timing", "speakers",
                                    "dropped_lines", "final_check")},
        "listen_check": layout.get("listen"),
        "transcribe_language": ctx.get("transcribe_language"),
        "spoken_language": ctx.get("detected_language"),
        "task": ctx.get("task"),
        "corrections_table": ctx.get("corrections_table"),
        "auto_corrections": len(ctx.get("auto_corrections") or []),
        "hotwords": ctx.get("hotwords") or None,
        "topic_words": ctx.get("topic_words") or None,
        "transcribe_engine": ctx.get("engine"),
        "quality": ctx.get("quality"),
        "review_highlights": len(ctx.get("review_marks") or []),
        "ai_polish": ctx.get("ai_polish"),  # provider, model and runs; never the key
        "separator_engine": separator_engine() if isolated else None,
        "separator_model": config.SEPARATOR_MODEL if isolated else None,
        "duration_seconds": round(ctx["duration"], 2),
        "timings": ctx["timings"],
        "cues": cues,
    }
    (job_dir / "cues.json").write_text(json.dumps(cues_json, indent=2, ensure_ascii=False), encoding="utf-8")
    ctx["rules"] = rules
    return {"srt_path": srt_path, "original_srt_path": original_srt, "cues_path": job_dir / "cues.json",
            "cues": cues, "max_chars": rules["max_chars"], "caption_mode": rules["mode"],
            "ai_message": layout["ai_message"], "ai_changes": layout["ai_changes"], "listen": layout.get("listen")}


def relayout(ctx: dict, caption_mode: str, ai: dict | None = None, report=lambda key, msg: None,
             max_words=None, max_chars=None, merge_short=None) -> dict:
    """Line by line <-> Word by word (or new caption settings) from the same word timings, without
    aligning or transcribing again. Rewrites the SRT, original.srt and cues.json."""
    old = ctx["rules"]
    rules = caption_rules(ctx["section"], ctx.get("video_format") or config.FORMAT_DEFAULT, caption_mode,
                          transcribe=ctx.get("whisper_draft") is not None,
                          max_words=old["max_words"] if max_words is None else max_words,
                          max_chars=old["max_chars"] if max_chars is None else max_chars,
                          merge_short=old["merge_short"] if merge_short is None else merge_short,
                          language=old.get("language"))
    rules["max_seconds"] = old["max_seconds"]
    layout = build_layout(ctx, rules, ai, report)
    ctx["review_recorded"] = {}
    out = write_outputs(ctx, layout, rules)
    ctx["log"].info("Captions rebuilt as %s (%d cues)", rules["mode"], len(out["cues"]))
    return out


def run_pipeline(audio_path, lyrics_text: str, strip_tags: bool = True,
                 video_format: str = None, language: str = None,
                 progress_callback=None, section: str = "song", isolate: str = None) -> dict:
    """Lyrics/script flow in one call. Returns a result dict with paths and metadata.

    video_format: "vertical" (9:16, short lines) or "horizontal" (16:9); defaults to the
        section's format.
    language: dropdown choice from config.LANGUAGES; defaults to config.LANGUAGE.
        Aliases like "hinglish" are mapped to a Whisper code via config.LANGUAGE_ALIASES.
    section: "song", "drama" or "reels" (config.SECTIONS); isolate: "auto", "on" or "off"
        (defaults to the section's preset).
    progress_callback(step:int, total:int, message:str) drives UI status text.
    Raises PipelineError with a user-safe message on any failure.
    """
    preprocess_lyrics(lyrics_text, strip_tags=strip_tags)  # fail fast, before heavy work
    section = clean_section(section)
    isolate = clean_isolate(isolate, section)
    video_format = video_format or config.SECTIONS[section]["format"]
    report = make_reporter(flow_steps("lyrics", section, isolate), progress_callback)
    ctx = start_job(audio_path, section, report, isolate)
    return align_and_write(ctx, lyrics_text, strip_tags=strip_tags, video_format=video_format,
                           language=language, report=report)


# ---------------------------------------------------------------------------
# CLI

def _progress(step, total, message, fraction=None):
    print(f"[{step}/{total}] {message}")


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):  # a Windows console may not take Hindi text
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    codes = [code for code, _ in config.LANGUAGES]
    sections = "|".join(SECTIONS)
    usage = ("Usage: python pipeline.py <audio|video> <lyrics.txt> [vertical|horizontal] "
             f"[{'|'.join(codes)}] [{sections}] [isolate=auto|on|off]\n"
             f"       python pipeline.py --transcribe <audio|video> [{'|'.join(codes)}] [{sections}] "
             f"[isolate=auto|on|off] [script={'|'.join(SCRIPT_CODES)}] [quality=fast|accurate] "
             "[topic=word,word]")
    transcribe = bool(argv) and argv[0] == "--transcribe"
    if transcribe:
        argv = argv[1:]
    positional = 1 if transcribe else 2
    if not positional <= len(argv) <= positional + 7:
        print(usage)
        return 2
    video_format = language = section = isolate = quality = None
    script = DEFAULT_SCRIPT
    topic = ""
    for arg in argv[positional:]:
        if transcribe and arg.startswith("script=") and arg[7:] in SCRIPT_CODES:
            script = arg[7:]
        elif transcribe and arg.startswith("quality=") and arg[8:] in QUALITIES:
            quality = arg[8:]
        elif transcribe and arg.startswith("topic="):
            topic = arg[6:]
        elif arg.startswith("isolate=") and arg[8:] in ISOLATE_CHOICES and isolate is None:
            isolate = arg[8:]
        elif arg in ("vertical", "horizontal") and video_format is None and not transcribe:
            video_format = arg
        elif arg in codes and language is None:
            language = arg
        elif (arg in SECTIONS or arg == "speech") and section is None:
            section = clean_section(arg)
        else:
            print(usage)
            return 2
    section = section or "song"
    isolate = clean_isolate(isolate, section)

    try:
        if transcribe:
            report = make_reporter(flow_steps("transcribe", section, isolate), _progress)
            ctx = start_job(Path(argv[0]), section, report, isolate)
            draft = run_transcription(ctx, language, report, script, quality, topic)
            print(f"Job folder: {ctx['job_dir']}")
            print(f"Language:   {draft['detected_language_name']} ({draft['detected_language']})")
            print(f"Script:     {draft['script']} ({draft['engine']}, quality {draft['quality']})")
            for w in ctx["warnings"]:
                print(f"WARNING: {w}")
            for m in draft["review_marks"]:
                print(f"CHECK:      line {m['line'] + 1} “{m['replacement']}” — {m['kind']}"
                      + (f" (other: “{m['original']}”)" if m["original"] else ""))
            if draft["script_note"]:
                print(f"NOTE: {draft['script_note']}")
            print(f"Timings:    {ctx['timings']}")
            print("--- draft ---")
            print(draft["draft_text"])
            return 0
        result = run_pipeline(Path(argv[0]), Path(argv[1]).read_text(encoding="utf-8"),
                              strip_tags=config.STRIP_TAGS_DEFAULT, video_format=video_format,
                              language=language, progress_callback=_progress,
                              section=section, isolate=isolate)
    except PipelineError as e:
        print(f"FAILED: {e}")
        return 1
    for w in result["warnings"]:
        print(f"WARNING: {w}")
    print(f"Job folder: {result['job_dir']}")
    print(f"SRT:        {result['srt_path']}")
    print(f"Vocals:     {result['stem_path']}")
    print(f"Timings:    {result['timings']}")
    print("--- SRT ---")
    print(result["srt_path"].read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

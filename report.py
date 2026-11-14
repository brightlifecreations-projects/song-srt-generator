"""Report a problem (DECISIONS.md V4.9): a zip the user sends by email themselves.

Nothing is sent automatically. The zip goes to the Downloads folder; the app then opens a pre-filled
email (mailto:) and offers "Copy report text" and "Show zip". What goes in follows the form's
checkboxes, and every text file passes through joblog.redact() (home folder → "~", keys removed).
API keys, the key store and ai-settings.json are never included.
"""

import json
import os
import platform
import urllib.parse
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import joblog

EMAIL = "ramanworks06@gmail.com"
CATEGORIES = {
    "missing": "Missing captions",
    "words": "Wrong words",
    "timing": "Wrong timing",
    "crash": "Crash or error",
    "startup": "App won't start or update",
    "other": "Other",
}
# What each checkbox adds from the job folder (text files are redacted on the way in).
LOG_FILES = ("job.json", "log.txt", "state.json", "ai-final-check.json", "ai-polish.json", "review.json",
             "corrections-diff.json", "corrections-diff.md", "ai-rejected.json")
TEXT_FILES = ("cues.json", "edited-cues.json", "timeline.json", "lyrics.txt", "approved-text.txt",
              "transcript-draft.txt", "transcript-model.txt", "transcript-second-opinion.txt",
              "transcript-autocorrected.txt", "original.srt", "edited.srt")
AUDIO_GLOB = "input.*"
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac"}
DEFAULT_INCLUDE = {"logs": True, "text": True, "app_log": True, "audio": False}


def downloads_dir() -> Path:
    if os.environ.get("SRT_REPORT_DIR"):  # tests: never the real Downloads folder
        return Path(os.environ["SRT_REPORT_DIR"])
    d = Path.home() / "Downloads"
    return d if d.is_dir() else Path.home()


opened: list[str] = []  # what safe_open was asked to open (SRT_NO_OPEN: tests open nothing)


def _text(path: Path) -> bytes:
    return joblog.redact(path.read_text(encoding="utf-8", errors="replace")).encode("utf-8")


def contents(job_dir: Path | None, include: dict) -> list[tuple[str, Path | None, str]]:
    """What the zip will hold: [(name in zip, source file or None, kind)] for the job's files."""
    out = []
    if job_dir is not None:
        if include.get("logs"):
            out += [(f"job/{n}", job_dir / n, "text") for n in LOG_FILES if (job_dir / n).exists()]
        if include.get("text"):
            out += [(f"job/{n}", job_dir / n, "text") for n in TEXT_FILES if (job_dir / n).exists()]
            out += [(f"job/{p.name}", p, "text") for p in sorted(job_dir.glob("*.srt"))
                    if p.name not in TEXT_FILES]
        if include.get("audio"):
            out += [(f"job/{p.name}", p, "binary") for p in sorted(job_dir.glob(AUDIO_GLOB))
                    if p.suffix.lower() in AUDIO_SUFFIXES]
    seen, unique = set(), []
    for item in out:  # a source named "input.mp3" makes "input.srt": never twice in the zip
        if item[0] not in seen:
            seen.add(item[0])
            unique.append(item)
    return unique


def build(*, category: str, description: str, at: float | None, job_dir: Path | None, include: dict,
          version: str, out_dir: Path | None = None) -> dict:
    """Write SRT-Generator-report-<date>-<id>.zip. Returns {"path", "name", "files": [{"name", "bytes"}],
    "email": {"to", "subject", "body"}, "mailto", "text"}."""
    include = {**DEFAULT_INCLUDE, **{k: bool(v) for k, v in (include or {}).items() if k in DEFAULT_INCLUDE}}
    category = category if category in CATEGORIES else "other"
    rid = uuid.uuid4().hex[:6]
    name = f"SRT-Generator-report-{datetime.now():%Y%m%d-%H%M}-{rid}.zip"
    out_dir = out_dir or downloads_dir()
    path = out_dir / name
    info = {
        "report_id": rid, "created": datetime.now().isoformat(timespec="seconds"),
        "category": CATEGORIES[category], "category_code": category, "description": description.strip()[:5000],
        "time_in_video": round(at, 3) if isinstance(at, (int, float)) else None,
        "app_version": version, "os": platform.platform(), "python": platform.python_version(),
        "job": job_dir.name if job_dir else None, "included": include,
    }
    files = contents(job_dir, include)
    listing = []
    tmp = path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, src, kind in files:
            data = _text(src) if kind == "text" else src.read_bytes()
            z.writestr(arc, data)
            listing.append({"name": arc, "bytes": len(data)})
        if include["app_log"]:
            data = joblog.redact(joblog.app_log_since(24)).encode("utf-8")
            z.writestr("app.log", data)
            listing.append({"name": "app.log", "bytes": len(data)})
        info["files"] = [f["name"] for f in listing]
        data = json.dumps(info, indent=2, ensure_ascii=False).encode("utf-8")
        z.writestr("report.json", data)
        listing.insert(0, {"name": "report.json", "bytes": len(data)})
    os.replace(tmp, path)
    joblog.app_log.info("Problem report created: %s (%s, job %s, %d files, audio %s)", path.name,
                        CATEGORIES[category], info["job"] or "none", len(listing), "included" if include["audio"] else "not included")
    text = report_text(info)
    subject = f"SRT Generator problem: {CATEGORIES[category]}"
    where = "Downloads folder" if out_dir == downloads_dir() else str(out_dir)
    body = (f"{text}\n\nPlease attach the report zip before sending: {name}\n"
            f"(It's in your {joblog.redact(where)}.)\n")
    mailto = f"mailto:{EMAIL}?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"
    return {"path": str(path), "name": name, "files": listing, "bytes": path.stat().st_size,
            "email": {"to": EMAIL, "subject": subject, "body": body}, "mailto": mailto, "text": text}


def report_text(info: dict) -> str:
    t = info.get("time_in_video")
    when = f"{int(t // 60)}:{t % 60:04.1f}" if isinstance(t, (int, float)) else "not given"
    return "\n".join([
        f"Problem: {info['category']}",
        f"Description: {info['description'] or '(none)'}",
        f"Time in the video: {when}",
        f"Job: {info['job'] or '(general problem, no job)'}",
        f"App version: {info['app_version']}",
        f"OS: {info['os']}",
        f"Report: {info['report_id']} ({info['created']})",
    ])


def secrets_found(zip_path: Path, secrets: list[str]) -> list[str]:
    """Test helper: which of `secrets` (or the real home path) appear anywhere in the zip."""
    hits = []
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            data = z.read(n)
            for s in list(secrets) + [joblog.HOME]:
                if s and s.encode("utf-8") in data:
                    hits.append(f"{n}: {s[:4]}…")
    return hits


def safe_open(target: str):
    """Open a mailto: link in the mail app, or show a file in Finder / Explorer. Nothing is sent."""
    import subprocess
    import sys
    opened.append(target)
    if os.environ.get("SRT_NO_OPEN"):
        joblog.app_log.info("Open (test mode, not opened): %s", target[:120])
        return
    if target.startswith("mailto:"):
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
        elif os.name == "nt":
            os.startfile(target)  # noqa: S606 — the user's own mail app
        else:
            subprocess.Popen(["xdg-open", target])
        return
    p = Path(target)
    if not p.exists():
        raise FileNotFoundError(target)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    elif os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{p}"])
    else:
        subprocess.Popen(["xdg-open", str(p.parent if p.is_file() else p)])

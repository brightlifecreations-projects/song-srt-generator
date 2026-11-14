"""History page (DECISIONS.md V4.9): every job in the jobs folder, newest first.

A job folder is the record: job.json (4.9+), cues.json, edited-cues.json, state.json, log.txt. Jobs
from before 4.9 are listed from their cues.json and log.txt, and open when they have cues and audio.
"""

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

NAME = re.compile(r"^\d{8}-\d{6}-[a-z0-9-]{1,60}$")


def job_dir(jobs_dir: Path, name: str) -> Path | None:
    """The job's folder, or None for a name that isn't one (no paths, no "..")."""
    if not isinstance(name, str) or not NAME.match(name):
        return None
    d = jobs_dir / name
    return d if d.is_dir() else None


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _created(name: str, record: dict | None) -> str:
    if record and record.get("created"):
        return record["created"]
    try:
        return datetime.strptime(name[:15], "%Y%m%d-%H%M%S").isoformat()
    except ValueError:
        return ""


def summary(d: Path) -> dict:
    """One History row: date, file, section, mode, language, duration, captions, status, edited."""
    record = _json(d / "job.json") or {}
    meta = _json(d / "cues.json") or {}
    edited = _json(d / "edited-cues.json")
    cues = edited if isinstance(edited, list) else meta.get("cues") or []
    settings = record.get("settings") or {}
    status = record.get("status")
    error = (record.get("errors") or [{}])[-1].get("message") if record.get("errors") else None
    if not record and not meta:  # a job from before 4.9 that never finished
        log = (d / "log.txt").read_text(encoding="utf-8", errors="replace") if (d / "log.txt").exists() else ""
        status = "error" if " ERROR " in log else "draft" if (d / "transcript-draft.txt").exists() else "unknown"
    elif meta.get("cues") and status in (None, "running"):
        status = "done"
    flagged = sum(1 for c in cues if (c.get("listen") or {}).get("status") == "flag"
                  and not (c.get("listen") or {}).get("resolved"))
    flagged += sum(1 for c in cues if (c.get("script") or {}).get("status") == "flag"
                   and not (c.get("script") or {}).get("resolved"))
    unsure = sum(len(c.get("unsure") or []) for c in cues)
    checked = bool(meta.get("listen_check")) and not (meta.get("listen_check") or {}).get("skipped")
    if status == "error":
        state = "error"
    elif status == "done" and cues:
        state = "check" if flagged or unsure else "verified" if checked else "done"
    else:
        state = status or "unknown"
    inp = record.get("input") or {}
    mode = settings.get("mode") or ("transcribe" if (d / "transcript-draft.txt").exists() else "lyrics")
    return {
        "name": d.name,
        "created": _created(d.name, record),
        "file": inp.get("name") or meta.get("source_file") or record.get("job") or d.name,
        "has_video": bool(inp.get("has_video")) or any(d.glob("video.*")) or (d / "thumb.jpg").exists(),
        "thumb": (d / "thumb.jpg").exists(),
        "section": settings.get("section") or meta.get("section") or "song",
        "mode": mode,
        "language": settings.get("language") or meta.get("language") or meta.get("spoken_language"),
        "duration": inp.get("duration") or meta.get("duration_seconds"),
        "captions": len(cues),
        "status": state,
        "to_check": flagged + unsure,
        "error": error,
        "edited": (d / "edited.srt").exists(),
        "can_open": bool(meta.get("cues")) and any(d.glob("input.*")),
        "app_version": record.get("app_version"),
    }


def text_of(d: Path) -> str:
    """Searchable text: the captions (as edited), the script or transcript."""
    parts = []
    for name in ("edited-cues.json", "cues.json"):
        data = _json(d / name)
        cues = data if isinstance(data, list) else (data or {}).get("cues")
        if cues:
            parts.append(" ".join(str(c.get("text", "")) for c in cues))
            break
    for name in ("lyrics.txt", "approved-text.txt", "transcript-draft.txt"):
        p = d / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def list_jobs(jobs_dir: Path) -> list[dict]:
    if not jobs_dir.is_dir():
        return []
    out = []
    for d in jobs_dir.iterdir():
        if d.is_dir() and NAME.match(d.name):
            try:
                out.append(summary(d))
            except Exception:  # noqa: BLE001 — one broken folder never hides the others
                out.append({"name": d.name, "created": _created(d.name, None), "file": d.name, "status": "error",
                            "error": "This job folder couldn't be read.", "section": "song", "captions": 0,
                            "can_open": False, "has_video": False, "thumb": False, "edited": False})
    out.sort(key=lambda j: (j.get("created") or "", j["name"]), reverse=True)
    return out


def filter_jobs(jobs: list[dict], jobs_dir: Path | None = None, q: str = "", section: str = "", status: str = "",
                date_from: str = "", date_to: str = "") -> list[dict]:
    """Search by file name or text (case-insensitive, every word must appear), and filter by
    section, status ("verified", "check", "error", "done") and date (YYYY-MM-DD, inclusive)."""
    words = [w for w in (q or "").casefold().split() if w]
    out = []
    for j in jobs:
        if section and j.get("section") != section:
            continue
        if status and j.get("status") != status:
            continue
        day = (j.get("created") or "")[:10]
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        if words:
            hay = (j.get("file") or "").casefold()
            if not all(w in hay for w in words) and jobs_dir is not None:
                hay += "\n" + text_of(jobs_dir / j["name"]).casefold()
            if not all(w in hay for w in words):
                continue
        out.append(j)
    return out


def delete(d: Path):
    shutil.rmtree(d)


def cleanup(jobs_dir: Path, keep_days: int, now: float | None = None) -> list[str]:
    """Delete job folders older than keep_days (0 = never). Returns the names deleted."""
    if not keep_days or not jobs_dir.is_dir():
        return []
    cutoff = (now or time.time()) - keep_days * 86400
    gone = []
    for d in jobs_dir.iterdir():
        if not (d.is_dir() and NAME.match(d.name)):
            continue
        try:
            made = datetime.strptime(d.name[:15], "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            continue
        if made < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            gone.append(d.name)
    return gone


def folder_bytes(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())

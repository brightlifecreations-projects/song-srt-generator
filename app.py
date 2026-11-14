"""SRT Generator — local web server. All processing lives in pipeline.py.

Serves the built React frontend (frontend/dist) and a small JSON API.
Jobs run one at a time on a single worker thread, matching the V1 queue rule.

Lyrics/script mode: POST /api/jobs runs the whole flow. Transcribe mode: POST
/api/transcribe stops at a draft (status "draft"); POST /api/jobs/{id}/approve
queues the alignment of the edited text against the same job's audio.
"""

import atexit
import json
import os
import traceback
import queue
import re
import shutil
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import ai_polish
import config
import final_check
import history
import joblog
import report as problem_report
import review
import corrections
import hinglish_model
from pipeline import (ACCEPTED_EXTENSIONS, AUTO_LANGUAGE, DATA_DIR, DEFAULT_SCRIPT, JOBS_DIR, QUALITIES,
                      SCRIPT_CODES, PipelineError, align_and_write, clean_isolate, clean_section,
                      NUMBER_EXCEPTIONS_FILE, flow_steps, is_video, make_reporter, number_exceptions,
                      preprocess_lyrics, record_approval, relayout, run_polish, run_transcription,
                      start_job, load_state, save_state, script_summary)
import captions

ROOT = Path(__file__).parent
DIST = ROOT / "frontend" / "dist"
UPLOADS = DATA_DIR / "uploads"
# Single source of the version number (also read by make_release.sh and the macOS app).
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "dev"

# Set by the macOS app shell (macapp/launcher.py): a second launch calls /api/focus
# to bring the existing window forward instead of opening another one.
on_focus_request = None

app = FastAPI(title="SRT Generator", docs_url=None, redoc_url=None, openapi_url=None)

# The app log (V4.9): app start and stop, every API request, the queue, settings, updates, crashes.
log = joblog.setup_app_log()
_env = joblog.environment()
log.info("App start: SRT Generator %s, %s, %s, Python %s, data folder %s", VERSION, _env["os"], _env["cpu"],
         _env["python"], DATA_DIR)
log.debug("Environment: %s", joblog.dumps(_env))
atexit.register(lambda: log.info("App stop"))
_gone = history.cleanup(JOBS_DIR, joblog.settings()["job_keep_days"])
if _gone:
    log.info("Job clean-up: %d job folder(s) older than %d days deleted: %s", len(_gone),
             joblog.settings()["job_keep_days"], ", ".join(_gone))

# In-memory job registry: id -> state dict. Jobs also persist on disk in jobs/.
JOBS: dict[str, dict] = {}
_work_queue: "queue.Queue[str]" = queue.Queue()

MAX_UPLOAD_BYTES = config.MAX_UPLOAD_MB * 1024 * 1024
MAX_VIDEO_UPLOAD_BYTES = config.MAX_VIDEO_UPLOAD_MB * 1024 * 1024
# Room for the lyrics field and multipart framing on top of the audio file.
_FORM_OVERHEAD_BYTES = 2 * 1024 * 1024
UPLOAD_TOO_LARGE = (
    f"This file is over the {config.MAX_UPLOAD_MB} MB upload limit for audio. "
    "Export it as MP3 or a shorter WAV and try again."
)
VIDEO_TOO_LARGE = (
    f"This video is over the {config.MAX_VIDEO_UPLOAD_MB // 1024} GB upload limit. "
    "Export a smaller copy, or just its audio, and try again."
)
UPLOAD_PATHS = ("/api/jobs", "/api/transcribe")


def _evict_finished_jobs():
    """Drop finished jobs older than JOB_RETENTION_MINUTES from memory (files stay in jobs/)."""
    cutoff = time.time() - config.JOB_RETENTION_MINUTES * 60
    for job_id, job in list(JOBS.items()):
        if job.get("finished", float("inf")) < cutoff:
            JOBS.pop(job_id, None)
            joblog.close_logger((job.get("ctx") or {}).get("log"))


def _run_job(job: dict):
    """Run the job's next phase. task: "lyrics" (whole flow), "transcribe" (up to the
    draft) or "align" (approved draft → SRT, reusing the job's audio and vocals)."""
    def progress(step, total, message, fraction=None):
        job.update(stage=step, message=message, progress=fraction)

    steps = flow_steps(job["mode"], job["section"], job["isolate"], job.get("polish_with"),
                       job.get("check_with"))
    if job["task"] == "relayout":
        steps = [({"build"}, "Build subtitles")] + ([({"check"}, f"Final check with {job['check_with']}")]
                                                    if job.get("check_with") else []) \
            + ([({"listen"}, "Listening check")] if config.LISTEN_CHECK else [])
    report = make_reporter(steps, progress)
    if job["task"] == "lyrics":
        preprocess_lyrics(job["lyrics_text"], strip_tags=job["strip_tags"])  # fail fast
    if job["task"] in ("lyrics", "transcribe"):
        settings = {"mode": job["mode"], "language": job.get("language"), "format": job.get("video_format"),
                    "caption_mode": job.get("caption_mode"), "quality": job.get("quality"),
                    "script": job.get("script"), "topic_words": job.get("topic_words") or None,
                    "rerun_of": job.get("rerun_of"),
                    "ai_polish": {"provider": job["ai"]["provider"], "model": job["ai"]["models"][job["ai"]["provider"]]}
                    if job.get("ai") else None,
                    "final_check": {"provider": job["check"]["provider"],
                                    "model": job["check"]["models"][job["check"]["provider"]]} if job.get("check") else None}
        job["ctx"] = start_job(job["audio_path"], job["section"], report, job["isolate"],
                               video_preview=job["video_preview"], settings=settings)
        job["music_detected"] = job["ctx"]["music_detected"]
        job["audio_ready"] = True
        shutil.rmtree(job["upload_dir"], ignore_errors=True)
    ctx = job["ctx"]
    joblog.bind_ctx(ctx)
    if job["task"] == "transcribe":
        draft = run_transcription(ctx, job["language"], report, job["script"], job["quality"],
                                  job["topic_words"], job.get("ai"))
        job.update(status="draft", message="Draft ready", timings=dict(ctx["timings"]),
                   warnings=list(ctx["warnings"]), job_dir=ctx["job_dir"].name, **draft)
        return
    if job["task"] == "retime":  # Review panel: one cue re-aligned to its audio
        try:
            job["retime_result"] = review.retime(ctx, **job["retime_request"])
        except PipelineError as e:
            job["retime_result"] = {"error": str(e)}
        job.update(status="done", message="Done")
        return
    if job["task"] == "relayout":  # Line by line / Word by word, from the same timings
        report("build", "Rebuilding the captions from the word timings…")
        result = relayout(ctx, job["caption_mode"], job.get("check"), report)
        warnings = [w for w in job.get("warnings", []) if not w.startswith("Final AI check")]
        job.update(status="done", message="Done", cues=result["cues"], caption_mode=result["caption_mode"],
                   max_chars=result["max_chars"], edited_srt_path=None, edited_cues=None,
                   check_message=result["ai_message"], ai_check_changes=result["ai_changes"],
                   listen_summary=result["listen"], missing=result["missing"], not_found=result["not_found"],
                   script_summary=result["script_summary"],
                   warnings=warnings + ([result["ai_message"]] if result["ai_message"] else []))
        return
    if job["task"] == "polish":  # "Polish again": the text in the draft box, as it is now
        polished = run_polish(ctx, job["polish_text"], job["ai"], report)
        job.update(status="draft", message="Draft ready", draft_text=polished["draft_text"],
                   ai_marks=polished["ai_marks"], ai_message=polished["ai_message"],
                   ai_provider=polished["ai_provider"], polish_run=job.get("polish_run", 0) + 1)
        return
    result = align_and_write(ctx, job["lyrics_text"], strip_tags=job["strip_tags"],
                             video_format=job["video_format"], language=job["language"],
                             report=report, caption_mode=job.get("caption_mode"),
                             max_words=job.get("max_words"), max_chars=job.get("max_chars_choice"),
                             merge_short=job.get("merge_short"), ai=job.get("check"))
    job.update(
        status="done", message="Done",
        srt_path=str(result["srt_path"]),
        stem_path=str(result["stem_path"]) if result["stem_path"] else None,
        srt_name=result["srt_path"].name, job_dir=result["job_dir"].name,
        cues=result["cues"], timings=result["timings"], warnings=result["warnings"],
        original_srt_path=str(result["original_srt_path"]), edited_srt_path=None, edited_cues=None,
        video_format=job.get("video_format"), max_chars=result["max_chars"],
        caption_mode=result["caption_mode"], check_message=result["ai_message"],
        ai_check_changes=result["ai_changes"], listen_summary=result.get("listen"),
        missing=result["missing"], not_found=result["not_found"], script_summary=result["script_summary"],
    )
    if job["task"] == "align":  # an approved transcript: learn its fixes
        job["corrections_recorded"] = record_approval(ctx, job["lyrics_text"])


def _worker():
    while True:
        job_id = _work_queue.get()
        job = JOBS[job_id]
        job.update(status="running")
        joblog.bind_ctx(job.get("ctx"))
        t0 = time.perf_counter()
        log.info("Job %s started: %s (%s, %s)", job_id, job["task"], job.get("section"), job.get("mode"))
        try:
            _run_job(job)
        except PipelineError as e:
            job.update(status="error", message=str(e))
            log.warning("Job %s failed: %s", job_id, e)
        except Exception:
            job.update(status="error", message=(
                "Something went wrong while processing this file. "
                "Use Report a problem on the History page to send the log."
            ))
            trace = traceback.format_exc()
            log.error("Job %s crashed:\n%s", job_id, trace)
            ctx = job.get("ctx")
            if ctx and ctx.get("log"):
                ctx["log"].error("Unhandled error:\n%s", trace)
            if ctx and ctx.get("record"):
                ctx["record"].finish("error", "Unhandled error (see log.txt)")
        finally:
            job["finished"] = time.time()
            folder = (job.get("ctx") or {}).get("job_dir")
            if folder and not job.get("job_dir"):
                job["job_dir"] = folder.name
            log.info("Job %s %s: %s in %.1fs (%s)", job_id, job.get("status"), job["task"], time.perf_counter() - t0,
                     job.get("job_dir") or "no folder")
            shutil.rmtree(job["upload_dir"], ignore_errors=True)
            joblog.bind(None, None)
            _work_queue.task_done()


threading.Thread(target=_worker, daemon=True).start()


page_loads: list[str] = []  # each time the window asked for the page (query, e.g. "v=4.5")


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """Never cache the page or the API (V4.5: an update must show its own UI at once); hashed
    build assets (/assets/name-HASH.js) can be cached for good, their names change every build."""
    response = await call_next(request)
    if request.url.path == "/":
        page_loads.append(request.url.query or "")
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Every API request in the app log: method, path, status, duration (polling at DEBUG)."""
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.error("API %s %s raised:\n%s", request.method, request.url.path, traceback.format_exc())
        raise
    polling = request.method == "GET" and (re.fullmatch(r"/api/jobs/[\w-]+", request.url.path)
                                           or request.url.path in ("/api/hinglish-model", "/api/updates"))
    log.log(10 if polling else 20, "API %s %s -> %d in %.0f ms", request.method, request.url.path,
            response.status_code, (time.perf_counter() - t0) * 1000)
    return response


@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    """Reject oversized uploads from Content-Length before the body is read."""
    if request.method == "POST" and request.url.path in UPLOAD_PATHS:
        # The file type isn't known yet here, so this applies the larger (video) limit;
        # the counted copy in _save_upload enforces the audio limit for audio files.
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_VIDEO_UPLOAD_BYTES + _FORM_OVERHEAD_BYTES:
            return JSONResponse({"error": VIDEO_TOO_LARGE}, status_code=413)
    return await call_next(request)


@app.get("/api/meta")
def meta():
    return {
        "format_default": config.FORMAT_DEFAULT,
        "sections": [{"code": code, **preset} for code, preset in config.SECTIONS.items()],
        "languages": [{"code": code, "name": name} for code, name in config.LANGUAGES],
        "language_default": config.LANGUAGE,
        "transcribe_language_default": config.TRANSCRIBE_LANGUAGE,
        "scripts": [{"code": code, "name": name} for code, name in config.SCRIPTS],
        "quality_default": config.QUALITY_DEFAULT,
        "accepted_extensions": sorted(ACCEPTED_EXTENSIONS),
        "max_upload_mb": {"audio": config.MAX_UPLOAD_MB, "video": config.MAX_VIDEO_UPLOAD_MB},
        "max_chars": {"vertical": config.MAX_CHARS_VERTICAL,
                      "horizontal": config.MAX_CHARS_HORIZONTAL},
        "max_words": config.MAX_WORDS_PER_CAPTION,
        "caption_mode_default": config.CAPTION_MODE_DEFAULT,
        "merge_short_default": config.MERGE_SHORT_DEFAULT,
        "merge_short_seconds": config.MERGE_SHORT_SECONDS,
        "max_minutes_hard": config.MAX_MINUTES_HARD,
        "strip_tags_default": config.STRIP_TAGS_DEFAULT,
        "version": VERSION,
        "page_loads": page_loads[-5:],  # a stale cached page never reaches the server (V4.5)
    }


def _version_tuple(text: str):
    return tuple(int(n) for n in re.findall(r"\d+", text))


# ---------------------------------------------------------------------------
# Updates page (V4.5). The Mac app sets update_backend to its Sparkle bridge (macapp/updater.py);
# Windows and checkouts ask GitHub for the latest release instead.

update_backend = None
UPDATE_SETTINGS = DATA_DIR / "update-settings.json"
_github = {"status": "idle", "available": None, "error": None, "last_checked": None}


def _update_settings() -> dict:
    try:
        saved = json.loads(UPDATE_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {"auto_check": saved.get("auto_check", config.CHECK_FOR_UPDATES) is not False}


def _github_check():
    _github.update(status="checking", error=None)
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{config.UPDATE_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "srt-generator"})
        with urllib.request.urlopen(req, timeout=8) as r:
            release = json.load(r)
        latest = release["tag_name"].lstrip("vV")
        newer = VERSION != "dev" and _version_tuple(latest) > _version_tuple(VERSION)
        log.info("Update check (GitHub): latest %s, this %s -> %s", latest, VERSION, "update available" if newer else "up to date")
        _github.update(status="available" if newer else "uptodate",
                       available={"version": latest, "notes": release.get("body") or "",
                                  "url": release["html_url"]} if newer else None)
    except Exception as e:
        log.warning("Update check (GitHub) failed: %s", type(e).__name__)
        _github.update(status="error", error="Couldn't reach GitHub — check the internet connection.")
    _github["last_checked"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")


@app.get("/api/updates")
def updates_state():
    if update_backend is not None:
        return {"version": VERSION, "mode": getattr(update_backend, "mode", "sparkle"), **update_backend.state()}
    settings = _update_settings()
    if settings["auto_check"] and _github["last_checked"] is None:
        _github_check()  # once per app run
    return {"version": VERSION, "mode": "github", "auto_check": settings["auto_check"],
            "auto_install": None, **_github}


@app.post("/api/updates/check")
def updates_check():
    log.info("Update check requested (%s)", getattr(update_backend, "mode", "sparkle") if update_backend else "github")
    if update_backend is not None:
        update_backend.check()
    else:
        _github_check()
    return updates_state()


@app.post("/api/updates/install")
def updates_install():
    """Mac: download, install and relaunch through Sparkle. (Windows opens the release page itself.)"""
    if update_backend is None:
        return JSONResponse({"error": "Updates install from the release page on this computer."},
                            status_code=400)
    update_backend.install()
    return updates_state()


@app.put("/api/updates/settings")
async def updates_settings(request: Request):
    body = await request.json()
    log.info("Setting changed: updates %s", {k: body[k] for k in ("auto_check", "auto_install") if k in body})
    if update_backend is not None:
        update_backend.set_automatic(body.get("auto_check"), body.get("auto_install"))
    elif "auto_check" in body:
        UPDATE_SETTINGS.write_text(json.dumps({"auto_check": bool(body["auto_check"])}), encoding="utf-8")
    return updates_state()


@app.post("/api/focus")
def focus():
    if on_focus_request:
        on_focus_request()
    return {"ok": True}


def _from_job(name: str):
    """Re-run (History): the earlier job's video (when kept) or audio, copied into uploads/<id>/."""
    d = history.job_dir(JOBS_DIR, name)
    sources = sorted(d.glob("video.*")) + sorted(d.glob("input.*")) if d else []
    if not sources:
        return JSONResponse({"error": "That job's audio is gone, so it can't be run again."}, status_code=404)
    job_id = uuid.uuid4().hex[:12]
    upload_dir = UPLOADS / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        original = json.loads((d / "job.json").read_text(encoding="utf-8"))["input"]["name"]
    except (OSError, ValueError, KeyError, TypeError):
        original = None
    stem = Path(original).stem if original else d.name[16:] or "audio"
    path = upload_dir / f"{stem}{sources[0].suffix}"
    shutil.copy2(sources[0], path)
    log.info("Re-run of %s: copied %s", d.name, sources[0].name)
    return job_id, upload_dir, path


def _save_upload(audio: UploadFile | None, from_job: str = ""):
    """Stream the upload into uploads/<id>/. Returns (job_id, upload_dir, path) or an error response."""
    if from_job:
        return _from_job(from_job)
    if audio is None:
        return JSONResponse({"error": "No file was sent."}, status_code=400)
    job_id = uuid.uuid4().hex[:12]
    upload_dir = UPLOADS / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(audio.filename or "song.wav").name
    audio_path = upload_dir / safe_name
    video = is_video(audio_path)
    limit = MAX_VIDEO_UPLOAD_BYTES if video else MAX_UPLOAD_BYTES
    # Counted copy: the middleware can't catch uploads sent without Content-Length.
    written = 0
    with audio_path.open("wb") as f:
        while chunk := audio.file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                break
            f.write(chunk)
    if written > limit:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return JSONResponse({"error": VIDEO_TOO_LARGE if video else UPLOAD_TOO_LARGE},
                            status_code=413)
    return job_id, upload_dir, audio_path


def _clean_choices(video_format: str, language: str, section: str, default_language: str):
    section = clean_section(section)
    if video_format not in ("vertical", "horizontal"):
        video_format = config.SECTIONS[section]["format"]
    if language not in {code for code, _ in config.LANGUAGES}:
        language = default_language
    return video_format, language, section


def _caption_choices(job: dict, caption_mode: str, max_words: str, max_chars: str, merge_short: str):
    """The caption settings sent with a job (V4.6), and the final AI check if it's set up."""
    job.update(caption_mode=caption_mode if caption_mode in ("line", "word") else config.CAPTION_MODE_DEFAULT,
               max_words=max_words or None, max_chars_choice=max_chars or None,
               merge_short={"true": True, "false": False}.get(str(merge_short).lower()))
    check = final_check.active()
    job.update(check=check, check_with=ai_polish.provider_name(check["provider"]) if check else None)


def _queue(job_id: str, job: dict):
    steps = flow_steps(job["mode"], job["section"], job["isolate"], job.get("polish_with"),
                       job.get("check_with"))
    job.update(status="queued", message="Waiting for a free slot…",
               steps=[title for _, title in steps])
    job.setdefault("stage", 0)
    JOBS[job_id] = job
    _work_queue.put(job_id)
    log.info("Job %s queued: %s, %s (%d waiting)", job_id, job["task"], job.get("section"), _work_queue.qsize())
    return {"job_id": job_id}


@app.post("/api/jobs")
async def create_job(
    audio: UploadFile | None = File(None),
    from_job: str = Form(""),
    lyrics: str = Form(...),
    strip_tags: bool = Form(True),
    video_format: str = Form(""),
    language: str = Form(config.LANGUAGE),
    section: str = Form("song"),
    isolate: str = Form(""),
    video_preview: bool = Form(False),
    caption_mode: str = Form(""),
    max_words: str = Form(""),
    max_chars: str = Form(""),
    merge_short: str = Form(""),
):
    """Lyrics/script mode: the whole flow in one job."""
    video_format, language, section = _clean_choices(
        video_format, language, section, config.LANGUAGE)
    _evict_finished_jobs()
    saved = _save_upload(audio, from_job)
    if isinstance(saved, JSONResponse):
        return saved
    job_id, upload_dir, audio_path = saved
    job = {
        "task": "lyrics", "mode": "lyrics", "created": time.time(), "rerun_of": from_job or None,
        "audio_path": str(audio_path), "upload_dir": str(upload_dir), "lyrics_text": lyrics,
        "strip_tags": strip_tags, "video_format": video_format, "language": language,
        "section": section, "isolate": clean_isolate(isolate, section),
        "video_preview": video_preview,
    }
    _caption_choices(job, caption_mode, max_words, max_chars, merge_short)
    return _queue(job_id, job)


@app.post("/api/transcribe")
async def create_transcription(
    audio: UploadFile | None = File(None),
    from_job: str = Form(""),
    language: str = Form(config.TRANSCRIBE_LANGUAGE),
    section: str = Form("song"),
    isolate: str = Form(""),
    script: str = Form(DEFAULT_SCRIPT),
    video_preview: bool = Form(False),
    quality: str = Form(config.QUALITY_DEFAULT),
    topic_words: str = Form(""),
):
    """Transcribe mode, phase 1: prepare the audio (and vocals) and return a Whisper draft.

    script: a config.SCRIPTS code, or "default" when the user has no saved choice.
    quality: "fast" or "accurate" (Hinglish drafts). topic_words: names, brands and terms for
    Whisper to expect, comma-separated."""
    _, language, section = _clean_choices(
        config.FORMAT_DEFAULT, language, section, config.TRANSCRIBE_LANGUAGE)
    _evict_finished_jobs()
    saved = _save_upload(audio, from_job)
    if isinstance(saved, JSONResponse):
        return saved
    job_id, upload_dir, audio_path = saved
    ai = ai_polish.active()  # None unless AI polish is switched on and has a key
    if ai and not ai["auto"]:
        ai = None  # polished only when the user clicks "Polish" on the draft
    return _queue(job_id, {
        "task": "transcribe", "mode": "transcribe", "created": time.time(), "rerun_of": from_job or None,
        "ai": ai, "polish_with": ai_polish.provider_name(ai["provider"]) if ai else None,
        "audio_path": str(audio_path), "upload_dir": str(upload_dir),
        "language": language, "section": section, "isolate": clean_isolate(isolate, section),
        "video_preview": video_preview,
        "script": script if script in SCRIPT_CODES else DEFAULT_SCRIPT,
        "quality": quality if quality in QUALITIES else config.QUALITY_DEFAULT,
        "topic_words": topic_words[:2000],
    })


@app.post("/api/jobs/{job_id}/approve")
def approve_transcript(
    job_id: str,
    lyrics: str = Form(...),
    strip_tags: bool = Form(False),
    video_format: str = Form(""),
    language: str = Form(AUTO_LANGUAGE),
    caption_mode: str = Form(""),
    max_words: str = Form(""),
    max_chars: str = Form(""),
    merge_short: str = Form(""),
):
    """Transcribe mode, phase 2: align the approved text to the audio prepared in phase 1."""
    job = JOBS.get(job_id)
    if not job or job.get("status") not in ("draft", "done", "error") or "ctx" not in job:
        return JSONResponse({"error": "This transcript has expired. Transcribe the file again."},
                            status_code=409)
    video_format, language, _ = _clean_choices(
        video_format, language, job["section"], AUTO_LANGUAGE)
    lyrics = ai_polish.UNSURE.sub(r"\1", lyrics)  # "[zaroor?]" marks a word to check, not text
    job.pop("finished", None)
    job.update(task="align", stage=job.get("stage", 2), lyrics_text=lyrics,
               strip_tags=strip_tags, video_format=video_format, language=language)
    _caption_choices(job, caption_mode, max_words, max_chars, merge_short)
    return _queue(job_id, job)


@app.post("/api/jobs/{job_id}/captions")
async def switch_captions(job_id: str, request: Request):
    """Line by line / Word by word for a finished job: rebuilt from the same word timings (no new
    alignment or transcription), then the final AI check if it's set up. Replaces Review edits."""
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done" or "timeline" not in job.get("ctx", {}):
        return JSONResponse({"error": "This job has expired. Generate the SRT again."}, status_code=409)
    mode = (await request.json()).get("mode")
    if mode not in ("line", "word"):
        return JSONResponse({"error": "Unknown caption mode."}, status_code=400)
    job.pop("finished", None)
    check = final_check.active()
    check_with = ai_polish.provider_name(check["provider"]) if check else None
    job.update(task="relayout", caption_mode=mode, check=check, check_with=check_with, stage=0,
               status="queued", message="Rebuilding the captions…",
               steps=["Build subtitles"] + ([f"Final check with {check_with}"] if check else [])
               + (["Listening check"] if config.LISTEN_CHECK else []))
    _work_queue.put(job_id)
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/polish")
def polish_again(job_id: str, lyrics: str = Form(...)):
    """AI polish of the draft box's current text, for a job whose draft is ready."""
    job = JOBS.get(job_id)
    if not job or job.get("status") not in ("draft", "done", "error") or "ctx" not in job \
            or job.get("mode") != "transcribe":
        return JSONResponse({"error": "This transcript has expired. Transcribe the file again."},
                            status_code=409)
    ai = ai_polish.active()
    if not ai:
        return JSONResponse({"error": "AI polish is off. Switch it on in the Corrections tab."},
                            status_code=409)
    job.pop("finished", None)
    job.update(task="polish", polish_text=lyrics, ai=ai,
               polish_with=ai_polish.provider_name(ai["provider"]))
    return _queue(job_id, job)


PUBLIC_KEYS = ("status", "stage", "steps", "message", "progress", "mode", "section", "isolate",
               "music_detected", "srt_name",
               "job_dir", "cues", "timings", "warnings", "audio_ready", "draft_text",
               "detected_language", "detected_language_name", "script", "approximate_timing",
               "script_note", "auto_corrections", "corrections_table", "hotwords",
               "corrections_recorded", "review_marks", "engine", "quality", "topic_words",
               "offline_draft", "ai_marks", "ai_message", "ai_provider", "polish_run",
               "edited_cues", "video_format", "max_chars", "retime_result", "caption_mode",
               "check_message", "ai_check_changes", "check_with", "listen_summary", "missing", "not_found",
               "script_summary", "restored", "source_file")


@app.get("/api/jobs/{job_id}")
def job_state(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    state = {k: job[k] for k in PUBLIC_KEYS if k in job}
    ctx = job.get("ctx", {})
    state["has_vocals"] = bool(ctx.get("vocals_path"))
    state["edited"] = bool(job.get("edited_srt_path"))
    state["has_preview"] = bool(ctx.get("preview_path"))
    state["has_video"] = bool(ctx.get("video_path") or ctx.get("preview_path"))
    if ctx.get("job_dir"):
        state["job_dir"] = ctx["job_dir"].name
    state.setdefault("source_file", ctx.get("source_file"))
    return state


def _job_file(job_id: str, key: str, media_type: str, filename: str):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done" or not job.get(key):
        return JSONResponse({"error": "not ready"}, status_code=404)
    return FileResponse(job[key], media_type=media_type, filename=filename)


@app.get("/api/jobs/{job_id}/srt")
def download_srt(job_id: str, original: bool = False):
    """The SRT: the edited one when the Review panel changed it (V4.5), else as generated;
    original=1 always gives the generated one (original.srt)."""
    job = JOBS.get(job_id, {})
    name = job.get("srt_name", "subtitles.srt")
    if original:
        return _job_file(job_id, "original_srt_path", "application/x-subrip",
                         name[:-4] + "-original.srt" if name.endswith(".srt") else name + "-original.srt")
    # The final check's text fixes still in the subtitles count as accepted: learnt once, Source AI.
    if job.get("status") == "done" and "ctx" in job:
        try:
            review.record_ai_fixes(job["ctx"], job.get("edited_cues") or job.get("cues") or [])
        except Exception:  # noqa: BLE001 - never block a download over learning
            pass
    if job.get("edited_srt_path"):
        return _job_file(job_id, "edited_srt_path", "application/x-subrip", name)
    return _job_file(job_id, "srt_path", "application/x-subrip", name)


@app.put("/api/jobs/{job_id}/cues")
async def save_cues(job_id: str, request: Request):
    """Review panel edits: write edited.srt (original.srt is kept) and learn text changes as
    User fixes in corrections.md."""
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done" or "ctx" not in job:
        return JSONResponse({"error": "This job has expired. Generate the SRT again."}, status_code=409)
    try:
        cues = review.clean_cues((await request.json()).get("cues", []))
    except (ValueError, TypeError, KeyError):
        return JSONResponse({"error": "Those cues aren't valid."}, status_code=400)
    review.log_edits(job["ctx"], job.get("edited_cues") or job.get("cues") or [], cues)
    saved = review.save_edits(job["ctx"], cues, job.get("cues") or [])
    job.update(edited_srt_path=str(saved["path"]), edited_cues=cues,
               review_recorded=saved["recorded_total"])
    return {"edited": True, "recorded": saved["recorded"]}


@app.post("/api/jobs/{job_id}/retime")
async def retime_cue(job_id: str, request: Request):
    """Re-align one cue's text to its stretch of audio (queued like any job phase)."""
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done" or "ctx" not in job:
        return JSONResponse({"error": "This job has expired. Generate the SRT again."}, status_code=409)
    body = await request.json()
    job.pop("finished", None)
    job.update(task="retime", retime_request={k: body.get(k) for k in ("start", "end", "text", "low", "high")},
               retime_result=None)
    _work_queue.put(job_id)
    job["status"] = "running"
    return {"ok": True}


@app.get("/api/jobs/{job_id}/vocals")
def download_vocals(job_id: str):
    job = JOBS.get(job_id, {})
    ext = Path(job.get("stem_path", "vocals.wav")).suffix
    return _job_file(job_id, "stem_path", "audio/wav" if ext == ".wav" else "audio/mpeg",
                     f"vocals{ext}")


@app.get("/api/jobs/{job_id}/audio")
def job_audio(job_id: str):
    """The job's audio (extracted from a video if needed), for the waveform player."""
    ctx = JOBS.get(job_id, {}).get("ctx")
    if not ctx:
        return JSONResponse({"error": "not ready"}, status_code=404)
    path = ctx["input_audio"]
    media = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
             ".flac": "audio/flac"}.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=media)


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: str):
    """The job's own video (kept in the job folder since 4.9), for a job opened from History."""
    ctx = JOBS.get(job_id, {}).get("ctx", {})
    path = ctx.get("video_path") or ctx.get("preview_path")
    if not path:
        return JSONResponse({"error": "not ready"}, status_code=404)
    media = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
             ".mkv": "video/x-matroska"}.get(Path(path).suffix.lower(), "video/mp4")
    return FileResponse(path, media_type=media)


@app.get("/api/jobs/{job_id}/preview")
def job_preview(job_id: str):
    """A playable copy of the job's video, made only when the UI asked for one (video_preview)."""
    path = JOBS.get(job_id, {}).get("ctx", {}).get("preview_path")
    if not path:
        return JSONResponse({"error": "not ready"}, status_code=404)
    return FileResponse(path, media_type="video/mp4")


# ---------------------------------------------------------------------------
# Corrections (corrections.md in the data folder, DECISIONS V4.1)

def _corrections_state(tables: dict | None = None):
    try:
        tables = corrections.load(DATA_DIR) if tables is None else tables
    except corrections.CorrectionsError as e:
        return JSONResponse({"error": "Your corrections.md has errors.", "errors": e.errors,
                             "path": str(corrections.path(DATA_DIR))}, status_code=422)
    backups = sorted((DATA_DIR / corrections.BACKUP_DIR).glob("*.md")) \
        if (DATA_DIR / corrections.BACKUP_DIR).is_dir() else []
    return {"tables": [{"name": name, "rows": [{"key": k, **r} for k, r in rows.items()]}
                       for name, rows in sorted(tables.items()) if rows],
            "path": str(corrections.path(DATA_DIR)), "backups": len(backups),
            "suggested_tables": sorted(set(corrections.SCRIPT_TABLES.values()))}


def _tables_from_json(payload: dict) -> dict:
    """The UI's table list → {table: {key: row}}, validated the same way as a file."""
    lines = []
    for t in payload.get("tables", []):
        lines.append(f"## {t['name']}")
        lines.append("| " + " | ".join(corrections.COLUMNS) + " |")
        for r in t.get("rows", []):
            wrong = corrections._cell(r.get("wrong", "")).strip()
            lines.append(f"| {'~~' + wrong + '~~' if r.get('disabled') else wrong} "
                         f"| {corrections._cell(r.get('correct', ''))} | {r.get('count', '')} "
                         f"| {r.get('last_seen', '')} | {r.get('source') or 'User'} "
                         f"| {corrections._cell(r.get('notes', ''))} |")
    return corrections.parse("\n".join(lines), "your edits")


@app.get("/api/corrections")
def corrections_get():
    return _corrections_state()


@app.put("/api/corrections")
async def corrections_put(request: Request):
    """Save the table after edits in the app (edit, delete, disable, add)."""
    try:
        tables = _tables_from_json(await request.json())
    except corrections.CorrectionsError as e:
        return JSONResponse({"error": "Some rows aren't valid.", "errors": e.errors}, status_code=422)
    with corrections._lock:
        corrections.save(DATA_DIR, tables)
    log.info("Corrections saved from the app (%d rows)", sum(len(r) for r in tables.values()))
    return _corrections_state(tables)


async def _read_uploads(files: list[UploadFile]):
    """Parse uploaded .md files. Returns ([(name, tables)], {name: [errors]})."""
    parsed, errors = [], {}
    for f in files:
        name = Path(f.filename or "upload.md").name
        if not name.lower().endswith(".md"):
            errors[name] = [f"{name}: not a .md file"]
            continue
        raw = await f.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            errors[name] = [f"{name}: not a UTF-8 text file"]
            continue
        try:
            tables = corrections.parse(text, name)
        except corrections.CorrectionsError as e:
            errors[name] = e.errors
            continue
        if not any(tables.values()):
            errors[name] = [f"{name}: no corrections found (expected '## Script' headings with "
                            "| Wrong | Correct | Times seen | Last seen | rows)"]
            continue
        parsed.append((name, tables))
    return parsed, errors


@app.post("/api/corrections/upload")
async def corrections_upload(files: list[UploadFile] = File(...),
                             choices: str = Form(""), apply: bool = Form(False)):
    """Check uploaded corrections files against mine. apply=false: return the merge plan
    (rows added, conflicts with the default choice); apply=true: merge with `choices`
    ({"<table>\\t<key>": option index}, JSON) and save. Files with errors are never merged."""
    parsed, errors = await _read_uploads(files)
    try:
        mine = corrections.load(DATA_DIR)
    except corrections.CorrectionsError as e:
        return JSONResponse({"error": "Your own corrections.md has errors; fix them first.",
                             "errors": e.errors}, status_code=422)
    plan = corrections.merge_plan(mine, parsed)
    result = {"files": [n for n, _ in parsed], "errors": errors, "added": plan["added"],
              "same": plan["same"],
              "conflicts": [{**c, "id": f"{c['table']}\t{c['key']}"} for c in plan["conflicts"]]}
    if apply and parsed:
        with corrections._lock:
            merged = corrections.merge(mine, parsed, json.loads(choices) if choices else {})
            corrections.backup(DATA_DIR)
            corrections.save(DATA_DIR, merged)
        result["merged"] = True
        result["state"] = _corrections_state(merged)
    return result


@app.get("/api/corrections/download")
def corrections_download():
    p = corrections.path(DATA_DIR)
    if not p.exists():
        corrections.save(DATA_DIR, {})
    return FileResponse(p, media_type="text/markdown", filename="corrections.md")


@app.post("/api/corrections/reset")
def corrections_reset():
    saved = corrections.reset(DATA_DIR)
    return {"backup": str(saved) if saved else None, "state": _corrections_state({})}


# ---------------------------------------------------------------------------
# Hinglish house spellings (hinglish-spellings.md, DECISIONS V4.2)

def _spellings_state(rows: dict | None = None):
    try:
        rows = corrections.load_spellings(DATA_DIR) if rows is None else rows
    except corrections.CorrectionsError as e:
        return JSONResponse({"error": "Your hinglish-spellings.md has errors.", "errors": e.errors,
                             "path": str(DATA_DIR / corrections.SPELLINGS_FILE)}, status_code=422)
    return {"rows": [{"key": k, **r} for k, r in sorted(rows.items())],
            "edited": (DATA_DIR / corrections.SPELLINGS_FILE).exists(),
            "path": str(DATA_DIR / corrections.SPELLINGS_FILE)}


@app.get("/api/spellings")
def spellings_get():
    return _spellings_state()


@app.put("/api/spellings")
async def spellings_put(request: Request):
    lines = ["| " + " | ".join(corrections.SPELLINGS_COLUMNS) + " |"]
    for r in (await request.json()).get("rows", []):
        wrong = corrections._cell(r.get("wrong", "")).strip()
        lines.append(f"| {'~~' + wrong + '~~' if r.get('disabled') else wrong} "
                     f"| {corrections._cell(r.get('correct', ''))} | {corrections._cell(r.get('notes', ''))} |")
    try:
        rows = corrections.parse_spellings("\n".join(lines), "your edits")
    except corrections.CorrectionsError as e:
        return JSONResponse({"error": "Some rows aren't valid.", "errors": e.errors}, status_code=422)
    corrections.save_spellings(DATA_DIR, rows)
    log.info("Hinglish spellings saved from the app (%d rows)", len(rows))
    return _spellings_state(rows)


@app.post("/api/spellings/reset")
def spellings_reset():
    corrections.reset_spellings(DATA_DIR)
    return _spellings_state()


# ---------------------------------------------------------------------------
# AI polish settings (DECISIONS V4.3). Keys go to the system key store and never come back out.

@app.get("/api/ai")
def ai_get():
    return ai_polish.public_state()


@app.put("/api/ai")
async def ai_put(request: Request):
    body = await request.json()
    current = ai_polish.load_settings()
    models = {**current["models"], **{k: v for k, v in (body.get("models") or {}).items() if v}}
    settings = {"enabled": bool(body.get("enabled", current["enabled"])),
                "auto": bool(body.get("auto", current["auto"])),
                "final_check": bool(body.get("final_check", current["final_check"])),
                "provider": body.get("provider") if body.get("provider") in ai_polish.PROVIDERS
                else current["provider"],
                "models": models, "custom_base_url": body.get("custom_base_url", current["custom_base_url"]),
                "anthropic_workspace_id": body.get("anthropic_workspace_id", current["anthropic_workspace_id"]),
                "keys_saved": current["keys_saved"]}  # changed only by saving or removing a key
    if settings["enabled"] and not ai_polish.usable(settings)[0]:
        settings["enabled"] = False  # off until it can actually run (a key, a model, a URL)
    changed = {k: settings[k] for k in settings if k != "keys_saved" and settings[k] != current.get(k)}
    if changed:
        log.info("Setting changed: AI %s", joblog.dumps(changed))
    ai_polish.save_settings(settings)
    return ai_polish.public_state()


@app.post("/api/ai/key")
async def ai_key(request: Request):
    body = await request.json()
    provider = body.get("provider")
    if provider not in ai_polish.PROVIDERS:
        return JSONResponse({"error": "Unknown provider."}, status_code=400)
    try:
        ai_polish.set_key(provider, body.get("key", ""))
        ai_polish._model_cache.pop(provider, None)  # a new key may see other models
    except ai_polish.PolishError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    except Exception:
        return JSONResponse({"error": "Couldn't save the key in this computer's key store."}, status_code=500)
    return ai_polish.public_state()


@app.get("/api/ai/models")
def ai_models(provider: str, refresh: bool = False):
    """The provider's model list (cached), or a short built-in list when it can't be loaded."""
    if provider not in ai_polish.PROVIDERS:
        return JSONResponse({"error": "Unknown provider."}, status_code=400)
    return ai_polish.list_models(provider, refresh)


@app.post("/api/ai/test")
async def ai_test(request: Request):
    """One tiny real request with the saved key and the given provider/model/URL."""
    body = await request.json()
    settings = ai_polish.load_settings()
    if body.get("provider") in ai_polish.PROVIDERS:
        settings["provider"] = body["provider"]
    if body.get("model"):
        settings["models"][settings["provider"]] = body["model"]
    for field in ("custom_base_url", "anthropic_workspace_id"):
        if field in body:
            settings[field] = body[field]
    ok, why = ai_polish.usable(settings)
    if not ok:
        return {"ok": False, "message": why}
    try:
        result = ai_polish.test_connection(settings)
    except ai_polish.PolishError as e:
        return {"ok": False, "message": str(e)}
    except Exception:
        return {"ok": False, "message": "The test request failed unexpectedly."}
    return {"ok": True, "message": f"Connected to {ai_polish.provider_name(settings['provider'])} "
                                   f"({settings['models'][settings['provider']]}) in {result['seconds']:.1f} s. "
                                   f"Test reply: “{result['reply']}”"}


# ---------------------------------------------------------------------------
# Numbers kept in words (number-exceptions.txt in the data folder, V4.6)

@app.get("/api/number-exceptions")
def number_exceptions_get():
    path = DATA_DIR / NUMBER_EXCEPTIONS_FILE
    return {"phrases": number_exceptions(), "edited": path.exists(), "path": str(path),
            "defaults": captions.DEFAULT_EXCEPTIONS}


@app.put("/api/number-exceptions")
async def number_exceptions_put(request: Request):
    body = await request.json()
    path = DATA_DIR / NUMBER_EXCEPTIONS_FILE
    if body.get("reset"):
        path.unlink(missing_ok=True)
    else:
        phrases = [" ".join(str(x).split()) for x in body.get("phrases", []) if str(x).strip()][:500]
        text = ("# Number expressions that stay in words (one per line). Everything else is written\n"
                "# as digits: 'twelve years' becomes '12 years'. Edited in the app's Corrections page.\n"
                + "\n".join(phrases) + "\n")
        path.write_bytes(text.encode("utf-8"))
    log.info("Setting changed: number exceptions %s", "reset" if body.get("reset") else f"{len(phrases)} phrases")
    return number_exceptions_get()


# ---------------------------------------------------------------------------
# The Hinglish model (downloaded once, DECISIONS V4.2)

@app.get("/api/hinglish-model")
def hinglish_model_status():
    return hinglish_model.status()


@app.post("/api/hinglish-model/download")
def hinglish_model_download():
    log.info("Hinglish model download requested")
    hinglish_model.start_background_download()
    return hinglish_model.status()


# ---------------------------------------------------------------------------
# App settings: log level and job clean-up (V4.9)

@app.get("/api/settings")
def settings_get():
    return {**joblog.settings(), "logs_folder": joblog.redact(str(joblog.LOGS_DIR)),
            "app_log_bytes": sum(p.stat().st_size for p in joblog.app_log_files())}


@app.put("/api/settings")
async def settings_put(request: Request):
    joblog.save_settings(await request.json())
    return settings_get()


@app.post("/api/logs/clear")
def logs_clear():
    """Empty the app log (job logs stay with their jobs; delete a job to remove its log)."""
    joblog.clear_logs()
    return settings_get()


# ---------------------------------------------------------------------------
# History (V4.9)

def _history_dir(name: str):
    d = history.job_dir(JOBS_DIR, name)
    return d if d else JSONResponse({"error": "That job isn't there any more."}, status_code=404)


@app.get("/api/history")
def history_list(q: str = "", section: str = "", status: str = "", date_from: str = "", date_to: str = ""):
    jobs = history.list_jobs(JOBS_DIR)
    shown = history.filter_jobs(jobs, JOBS_DIR, q=q, section=section, status=status, date_from=date_from,
                                date_to=date_to)
    running = {j.get("job_dir") for j in JOBS.values() if j.get("status") in ("queued", "running")}
    for j in shown:
        j["running"] = j["name"] in running
    return {"jobs": shown, "total": len(jobs), **joblog.settings()}


@app.get("/api/history/{name}/thumb")
def history_thumb(name: str):
    d = _history_dir(name)
    if isinstance(d, JSONResponse) or not (d / "thumb.jpg").exists():
        return JSONResponse({"error": "no thumbnail"}, status_code=404)
    return FileResponse(d / "thumb.jpg", media_type="image/jpeg")


@app.get("/api/history/{name}/media")
def history_media(name: str):
    """The job's kept video, else its audio (Re-run shows it before the new job starts)."""
    d = _history_dir(name)
    if isinstance(d, JSONResponse):
        return d
    found = sorted(d.glob("video.*")) + sorted(d.glob("input.*"))
    if not found:
        return JSONResponse({"error": "not there"}, status_code=404)
    media = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
             ".mkv": "video/x-matroska", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
             ".flac": "audio/flac"}.get(found[0].suffix.lower(), "application/octet-stream")
    return FileResponse(found[0], media_type=media)


@app.get("/api/history/{name}/log")
def history_log(name: str):
    d = _history_dir(name)
    if isinstance(d, JSONResponse):
        return d
    text = (d / "log.txt").read_text(encoding="utf-8", errors="replace") if (d / "log.txt").exists() else ""
    return {"name": name, "log": joblog.redact(text), "bytes": len(text.encode("utf-8"))}


@app.post("/api/history/{name}/open")
def history_open(name: str):
    """Open a finished job again in its section's Review panel: captions, edits, flags, audio or video."""
    d = _history_dir(name)
    if isinstance(d, JSONResponse):
        return d
    for job_id, job in JOBS.items():  # still in memory: the same job
        ctx = job.get("ctx") or {}
        if ctx.get("job_dir") and ctx["job_dir"].name == name and job.get("status") == "done":
            return {"job_id": job_id, "section": job["section"], "mode": job.get("mode")}
    try:
        ctx = load_state(d)
        meta = json.loads((d / "cues.json").read_text(encoding="utf-8"))
    except (PipelineError, OSError, ValueError) as e:
        return JSONResponse({"error": str(e) if isinstance(e, PipelineError) else "This job has no captions to open."},
                            status_code=409)
    ctx["log"] = joblog.job_logger(d)
    ctx["record"] = joblog.Record.load(d, ctx["log"])
    ctx["log"].info("Opened again from History (app %s)", VERSION)
    try:
        edited = json.loads((d / "edited-cues.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        edited = None
    srt = next((p for p in sorted(d.glob("*.srt")) if p.name not in ("original.srt", "edited.srt")), d / "original.srt")
    tl = ctx.get("timeline") or {}
    job_id = uuid.uuid4().hex[:12]
    rules = ctx.get("rules") or {}
    mode = "transcribe" if ctx.get("whisper_draft") is not None or (d / "transcript-draft.txt").exists() else "lyrics"
    JOBS[job_id] = {
        "task": "open", "mode": mode, "section": ctx["section"], "isolate": ctx.get("isolate"),
        "status": "done", "message": "Done", "stage": 0, "steps": [], "created": time.time(),
        "ctx": ctx, "upload_dir": str(UPLOADS / job_id), "restored": True, "source_file": ctx.get("source_file"),
        "music_detected": ctx.get("music_detected"), "audio_ready": True,
        "srt_path": str(srt), "srt_name": srt.name, "original_srt_path": str(d / "original.srt"),
        "edited_srt_path": str(d / "edited.srt") if (d / "edited.srt").exists() else None,
        "stem_path": str(ctx["vocals_path"]) if ctx.get("vocals_path") else None,
        "job_dir": name, "cues": meta.get("cues") or [], "edited_cues": edited,
        "timings": meta.get("timings") or {}, "warnings": [], "video_format": meta.get("video_format"),
        "max_chars": meta.get("max_chars_per_line") or rules.get("max_chars"),
        "caption_mode": meta.get("caption_mode") or rules.get("mode") or "line",
        "check_message": (meta.get("final_check") or {}).get("message"),
        "check_with": (meta.get("final_check") or {}).get("provider"),
        "listen_summary": meta.get("listen_check"), "missing": meta.get("missing_captions") or [],
        "not_found": meta.get("not_found") or tl.get("not_found") or [],
        "script_summary": meta.get("script_lines") or script_summary(tl),
        "finished": time.time(),
    }
    log.info("History: opened %s as job %s", name, job_id)
    return {"job_id": job_id, "section": ctx["section"], "mode": mode}


@app.get("/api/history/{name}/settings")
def history_settings(name: str):
    """What a Re-run starts from: the job's section, mode, settings and text."""
    d = _history_dir(name)
    if isinstance(d, JSONResponse):
        return d
    row = history.summary(d)
    try:
        record = json.loads((d / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    try:
        meta = json.loads((d / "cues.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    settings = record.get("settings") or {}
    text = ""
    for f in ("lyrics.txt", "approved-text.txt"):
        if (d / f).exists():
            text = (d / f).read_text(encoding="utf-8", errors="replace").strip()
            break
    return {"name": name, "file": row["file"], "has_video": row["has_video"], "section": row["section"],
            "mode": row["mode"], "text": text if row["mode"] == "lyrics" else "",
            "language": settings.get("language") or meta.get("language"),
            "format": settings.get("format") or meta.get("video_format"),
            "caption_mode": settings.get("caption_mode") or meta.get("caption_mode"),
            "isolate": settings.get("isolate") or meta.get("isolate"),
            "script": settings.get("script") or meta.get("script"), "quality": settings.get("quality"),
            "topic_words": ", ".join(settings.get("topic_words") or []) if isinstance(settings.get("topic_words"), list)
            else settings.get("topic_words") or "",
            "can_rerun": any(d.glob("input.*")) or any(d.glob("video.*"))}


@app.delete("/api/history/{name}")
def history_delete(name: str):
    d = _history_dir(name)
    if isinstance(d, JSONResponse):
        return d
    for job_id, job in list(JOBS.items()):
        ctx = job.get("ctx") or {}
        if ctx.get("job_dir") and ctx["job_dir"].name == name:
            if job.get("status") in ("queued", "running"):
                return JSONResponse({"error": "This job is still running."}, status_code=409)
            joblog.close_logger(ctx.get("log"))
            JOBS.pop(job_id, None)
    history.delete(d)
    log.info("History: deleted %s", name)
    return {"deleted": name}


@app.post("/api/history/{name}/reveal")
def history_reveal(name: str):
    d = _history_dir(name)
    if isinstance(d, JSONResponse):
        return d
    problem_report.safe_open(str(d))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Report a problem (V4.9). Nothing is sent: a zip in Downloads, then the user's own email app.

@app.post("/api/report")
async def report_create(request: Request):
    body = await request.json()
    job_dir = None
    if body.get("job"):
        job_dir = history.job_dir(JOBS_DIR, str(body["job"]))
        if job_dir is None:
            return JSONResponse({"error": "That job isn't there any more."}, status_code=404)
    at = body.get("time")
    try:
        at = float(at) if at not in (None, "") else None
    except (TypeError, ValueError):
        at = None
    try:
        out = problem_report.build(category=str(body.get("category") or "other"),
                                   description=str(body.get("description") or ""), at=at, job_dir=job_dir,
                                   include=body.get("include") or {}, version=VERSION)
    except OSError as e:
        log.error("Problem report failed: %s", e)
        return JSONResponse({"error": "Couldn't write the report zip to your Downloads folder."}, status_code=500)
    _reports[out["name"]] = out["path"]
    return {**out, "path": joblog.redact(out["path"])}


_reports: dict[str, str] = {}  # zip name -> its path (Show zip opens only reports made this run)


@app.post("/api/report/open")
async def report_open(request: Request):
    """open: "email" (the pre-filled email in the mail app) or "zip" (show it in Finder/Explorer)."""
    body = await request.json()
    path = _reports.get(str(body.get("name")))
    if not path:
        return JSONResponse({"error": "That report isn't there any more."}, status_code=404)
    try:
        if body.get("open") == "email":
            mailto = str(body.get("mailto") or "")
            if not mailto.startswith(f"mailto:{problem_report.EMAIL}?"):
                return JSONResponse({"error": "Not a report email."}, status_code=400)
            problem_report.safe_open(mailto)
            log.info("Problem report: opened the email for %s", body.get("name"))
        else:
            problem_report.safe_open(path)
    except (OSError, ValueError) as e:
        return JSONResponse({"error": f"Couldn't open it ({type(e).__name__})."}, status_code=500)
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")


if __name__ == "__main__":
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # harmless if gradio absent
    if not DIST.exists():
        raise SystemExit(
            "frontend/dist is missing. Run: cd frontend && npm install && npm run build"
        )
    url = f"http://{config.HOST}:{config.PORT}"
    if not os.environ.get("SRT_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"SRT Generator running at {url}")
    try:
        uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")
    except OSError as e:
        raise SystemExit(
            f"Couldn't start on port {config.PORT} ({e}). Something else is probably "
            "using it — change the PORT constant in config.py and run again."
        )

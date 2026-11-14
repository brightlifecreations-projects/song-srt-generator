"""Backend logging (DECISIONS.md V4.9): the app log, each job's log.txt and job.json, and redaction.

- app.log (data folder, logs/): app start and stop, setup and warmup, every API request, the job
  queue, update checks, model downloads, settings changes, key-store access (success or failure
  only), crashes. Rotates at 10 MB, five files in all.
- <job>/log.txt: everything one job did, timestamped to the millisecond. "Detailed" (the default)
  logs at DEBUG: raw Whisper segments and word timings, every caption decision, full AI requests and
  replies. "Normal" logs at INFO.
- <job>/job.json: the same job as structured data: environment, settings, input, every step (start,
  end, duration, inputs, outputs), every external command, model events, edits, warnings, errors.

Privacy: everything written goes through redact(): the home folder becomes "~", and API keys (any
value registered with add_secret, and anything shaped like a key) become "[key removed]".
"""

import json
import logging
import logging.handlers
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("SRT_DATA_DIR") or ROOT)
LOGS_DIR = DATA_DIR / "logs"
SETTINGS_FILE = DATA_DIR / "app-settings.json"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "dev"
APP_LOG_BYTES = 10 * 1024 * 1024
APP_LOG_FILES = 5          # app.log and four rotated copies
OUTPUT_LIMIT = 4000        # characters of a command's stdout/stderr kept (head and tail)
FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
LOG_LEVELS = {"normal": logging.INFO, "detailed": logging.DEBUG}

app_log = logging.getLogger("srt.app")
app_log.propagate = False


# ---------------------------------------------------------------------------
# Redaction

HOME = str(Path.home())
_secrets: set[str] = set()
KEY_REMOVED = "[key removed]"
# Shapes of API keys and tokens: Anthropic/OpenAI "sk-…", Google "AIza…", Groq "gsk_…", xAI, GitHub,
# Hugging Face, bearer tokens and "key=…" / "api_key: …" pairs.
_KEY_SHAPES = re.compile(
    r"(?<![A-Za-z0-9_\-])(sk-(?:ant-)?[A-Za-z0-9_\-]{12,}|AIza[0-9A-Za-z_\-]{20,}|gsk_[A-Za-z0-9]{12,}|xai-[A-Za-z0-9]{12,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|(?i:bearer)\s+[A-Za-z0-9._\-]{12,}"
    r"|(?i:(?:api[_-]?key|x-api-key|x-goog-api-key|authorization|token|secret)[\"']?\s*[:=]\s*[\"']?)"
    r"(?!\[key removed\])[A-Za-z0-9._\-]{12,})")


def add_secret(value: str | None):
    """A key read from (or saved to) the key store: never written to any log or report."""
    if value and len(value.strip()) >= 6:
        _secrets.add(value.strip())


def _home_variants() -> list[str]:
    homes = {HOME, HOME.replace("\\", "/"), HOME.replace("\\", "\\\\")}
    return sorted((h for h in homes if len(h) > 2), key=len, reverse=True)


def redact(text):
    """The text with the home folder as "~" and keys removed. Non-strings pass through."""
    if not isinstance(text, str) or not text:
        return text
    for s in sorted(_secrets, key=len, reverse=True):
        text = text.replace(s, KEY_REMOVED)
    text = _KEY_SHAPES.sub(lambda m: _key_label(m.group(0)), text)
    for h in _home_variants():
        text = text.replace(h, "~")
    return text


def _key_label(found: str) -> str:
    """"api_key=abc…" -> "api_key=[key removed]"; a bare key -> "[key removed]"."""
    m = re.match(r"(?i)((?:api[_-]?key|x-api-key|x-goog-api-key|authorization|token|secret)[\"']?\s*[:=]\s*[\"']?)", found)
    if m:
        return m.group(1) + KEY_REMOVED
    m = re.match(r"(?i)(bearer\s+)", found)
    return (m.group(1) if m else "") + KEY_REMOVED


def redact_obj(obj):
    """redact() through dicts and lists (job.json)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def _formatter():
    return RedactingFormatter(FORMAT, datefmt=DATEFMT)


# ---------------------------------------------------------------------------
# Settings: log level and job clean-up (app-settings.json in the data folder)

def settings() -> dict:
    import config
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    level = saved.get("log_level") if saved.get("log_level") in LOG_LEVELS else config.LOG_LEVEL
    days = saved.get("job_keep_days", config.JOB_KEEP_DAYS)
    try:
        days = max(0, int(days))
    except (TypeError, ValueError):
        days = config.JOB_KEEP_DAYS
    return {"log_level": level, "job_keep_days": days}


def save_settings(changes: dict) -> dict:
    current = settings()
    new = dict(current)
    if changes.get("log_level") in LOG_LEVELS:
        new["log_level"] = changes["log_level"]
    if "job_keep_days" in changes:
        try:
            new["job_keep_days"] = max(0, min(3650, int(changes["job_keep_days"])))
        except (TypeError, ValueError):
            pass
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)
    for k in new:
        if new[k] != current[k]:
            app_log.info("Setting changed: %s %s -> %s", k, current[k], new[k])
    app_log.setLevel(LOG_LEVELS[new["log_level"]])
    return new


def level() -> int:
    return LOG_LEVELS[settings()["log_level"]]


# ---------------------------------------------------------------------------
# The app log

_app_handler = None


def setup_app_log() -> logging.Logger:
    """Rotating logs/app.log; uncaught errors anywhere (threads too) are logged there. Idempotent."""
    global _app_handler
    if _app_handler is not None:
        return app_log
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _app_handler = logging.handlers.RotatingFileHandler(LOGS_DIR / "app.log", maxBytes=APP_LOG_BYTES,
                                                        backupCount=APP_LOG_FILES - 1, encoding="utf-8")
    _app_handler.setFormatter(_formatter())
    app_log.addHandler(_app_handler)
    app_log.setLevel(level())

    previous = sys.excepthook

    def excepthook(kind, value, tb):
        app_log.critical("Unhandled error (crash):\n%s", "".join(traceback.format_exception(kind, value, tb)))
        previous(kind, value, tb)

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        app_log.critical("Unhandled error in thread %s:\n%s", getattr(args.thread, "name", "?"),
                         "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    return app_log


def app_log_files() -> list[Path]:
    """app.log first, then its rotated copies (app.log.1 …), newest first."""
    out = [LOGS_DIR / "app.log"] + [LOGS_DIR / f"app.log.{n}" for n in range(1, APP_LOG_FILES)]
    return [p for p in out if p.exists()]


def app_log_since(hours: float = 24) -> str:
    """The app log's lines from the last `hours`, oldest first (a line without a timestamp belongs
    to the line before it: tracebacks)."""
    cutoff = datetime.now().timestamp() - hours * 3600
    lines, keep = [], False
    for path in reversed(app_log_files()):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
            if m:
                try:
                    keep = datetime.strptime(m.group(1), DATEFMT).timestamp() >= cutoff
                except ValueError:
                    pass
            if keep:
                lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def clear_logs() -> int:
    """Empty the app log and delete its rotated copies. Returns the number of files cleared."""
    n = 0
    for path in app_log_files():
        if path.name == "app.log" and _app_handler is not None:
            _app_handler.acquire()
            try:
                _app_handler.stream.seek(0)
                _app_handler.stream.truncate()
            finally:
                _app_handler.release()
        else:
            path.unlink(missing_ok=True)
        n += 1
    app_log.info("Logs cleared (%d app log file(s))", n)
    return n


# ---------------------------------------------------------------------------
# The environment (once per run, disk and memory per job)

_env_cache: dict | None = None


def _sysctl(name: str) -> str | None:
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _memory() -> dict:
    """Total and available memory in MB (no psutil: sysctl/vm_stat on a Mac, the Win32 API on Windows)."""
    try:
        if sys.platform == "darwin":
            total = int(_sysctl("hw.memsize") or 0)
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = int(re.search(r"page size of (\d+)", vm).group(1))
            pages = sum(int(re.search(rf"{k}:\s+(\d+)", vm).group(1)) for k in
                        ("Pages free", "Pages inactive", "Pages speculative") if re.search(rf"{k}:\s+(\d+)", vm))
            return {"total_mb": total // 2**20, "available_mb": pages * page // 2**20}
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return {"total_mb": ms.ullTotalPhys // 2**20, "available_mb": ms.ullAvailPhys // 2**20}
        info = Path("/proc/meminfo").read_text()
        get = lambda k: int(re.search(rf"{k}:\s+(\d+)", info).group(1)) // 1024  # noqa: E731
        return {"total_mb": get("MemTotal"), "available_mb": get("MemAvailable")}
    except Exception:  # noqa: BLE001
        return {}


PACKAGES = ("faster-whisper", "stable-ts", "ctranslate2", "torch", "numpy", "mlx", "mlx-audio-separator",
            "audio-separator", "fastapi", "uvicorn", "keyring", "anthropic", "pywebview", "onnxruntime")


def environment() -> dict:
    """App version, OS, CPU/GPU, Python and package versions, models (names, versions, paths), and
    the free disk space and memory right now."""
    global _env_cache
    if _env_cache is None:
        from importlib import metadata
        import config

        packages = {}
        for name in PACKAGES:
            try:
                packages[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                pass
        cpu = (_sysctl("machdep.cpu.brand_string") if sys.platform == "darwin" else None) or platform.processor()
        gpu = None
        if sys.platform == "darwin" and platform.machine() == "arm64":
            gpu = f"Apple Silicon GPU ({cpu})"
        else:
            try:
                import torch
                if torch.cuda.is_available():
                    gpu = torch.cuda.get_device_name(0)
            except Exception:  # noqa: BLE001
                pass
        _env_cache = {
            "app_version": VERSION,
            "os": platform.platform(),
            "os_version": platform.mac_ver()[0] if sys.platform == "darwin" else platform.version(),
            "machine": platform.machine(),
            "cpu": cpu,
            "cpu_count": os.cpu_count(),
            "gpu": gpu or "none (CPU only)",
            "python": sys.version.split()[0],
            "packages": packages,
            "models": models(),
            "data_folder": str(DATA_DIR),
            "app_folder": str(ROOT),
            "config": {k: getattr(config, k) for k in ("MODEL_SIZE", "DEVICE", "SEPARATOR_ENGINE", "SEPARATOR_MODEL",
                                                       "HINGLISH_ENGINE", "LISTEN_CHECK", "UNMATCHED_LINE_PROBABILITY",
                                                       "SCRIPT_LINE_CONFIDENCE")},
        }
    disk = shutil.disk_usage(DATA_DIR)
    return {**_env_cache, "disk_free_mb": disk.free // 2**20, "memory": _memory()}


def models() -> list[dict]:
    """The models this install uses: name, version and where they are on disk (installed or not)."""
    import config
    models_dir = DATA_DIR / "models"
    whisper = models_dir / "hf" / "hub" / f"models--Systran--faster-whisper-{config.MODEL_SIZE}"
    snapshot = next(iter(sorted((whisper / "snapshots").glob("*"))), None) if (whisper / "snapshots").is_dir() else None
    sep = models_dir / config.SEPARATOR_MODEL
    hing = models_dir / "whisper-hinglish-apex-int8"
    return [
        {"name": f"faster-whisper-{config.MODEL_SIZE}", "role": "alignment, transcription, listening check",
         "version": snapshot.name[:12] if snapshot else None, "path": str(snapshot or whisper),
         "installed": bool(snapshot)},
        {"name": config.SEPARATOR_MODEL, "role": "voice isolation", "version": None, "path": str(sep),
         "installed": sep.exists()},
        {"name": "whisper-hinglish-apex-int8", "role": "Hinglish drafts and listening",
         "version": "v1 (sha256 " + config.HINGLISH_MODEL_SHA256[:12] + ")", "path": str(hing),
         "installed": (hing / "model.bin").exists()},
    ]


# ---------------------------------------------------------------------------
# Jobs: log.txt, job.json, and the job the current thread is working on

_current = threading.local()


def job_logger(job_dir: Path) -> logging.Logger:
    """The job's logger, writing log.txt (millisecond timestamps, the chosen log level)."""
    log = logging.getLogger(f"job.{job_dir.name}")
    log.setLevel(level())
    log.propagate = False
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)
    handler = logging.FileHandler(job_dir / "log.txt", encoding="utf-8")
    handler.setFormatter(_formatter())
    log.addHandler(handler)
    return log


def close_logger(log: logging.Logger | None):
    for handler in list(log.handlers) if log else []:
        handler.close()  # Windows: an open log.txt would lock the job folder
        log.removeHandler(handler)


class Record:
    """job.json: the job as structured data, saved after every step (so a crash still leaves it)."""

    def __init__(self, job_dir: Path, log: logging.Logger, data: dict | None = None):
        self.job_dir, self.log = job_dir, log
        self.path = job_dir / "job.json"
        self._lock = threading.RLock()
        self.data = data or {
            "format": 1, "job": job_dir.name, "app_version": VERSION,
            "created": datetime.now().isoformat(timespec="milliseconds"),
            "status": "running", "environment": environment(), "settings": {}, "input": {},
            "steps": [], "commands": [], "models": [], "script_lines": [], "missing_captions": [],
            "edits": [], "warnings": [], "errors": [], "summary": {},
        }

    @classmethod
    def load(cls, job_dir: Path, log: logging.Logger):
        try:
            data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        return cls(job_dir, log, data)

    def save(self):
        with self._lock:
            tmp = self.path.with_name("job.json.tmp")
            try:
                tmp.write_text(json.dumps(redact_obj(self.data), indent=1, ensure_ascii=False, default=str),
                               encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:  # a deleted job folder: nothing to keep
                pass

    def set(self, key: str, value):
        with self._lock:
            self.data[key] = value
        self.save()

    def update(self, key: str, values: dict):
        with self._lock:
            self.data.setdefault(key, {}).update(values)
        self.save()

    def add(self, key: str, item):
        with self._lock:
            self.data.setdefault(key, []).append(item)

    def warn(self, message: str):
        self.add("warnings", {"time": _now(), "message": message})
        self.save()

    @contextmanager
    def step(self, name: str, **inputs):
        """with record.step("align", lines=12) as s: … s["outputs"] = {...}. Logs start and end with
        the duration; an exception is recorded with its traceback and re-raised."""
        entry = {"name": name, "start": _now(), "inputs": inputs, "outputs": {}}
        t0 = time.perf_counter()
        self.log.info("Step start: %s%s", name, f" {json.dumps(inputs, ensure_ascii=False, default=str)}" if inputs else "")
        with self._lock:
            self.data["steps"].append(entry)
        try:
            yield entry
        except BaseException as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            if not isinstance(e, (KeyboardInterrupt, GeneratorExit)) and type(e).__name__ != "PipelineError":
                entry["traceback"] = traceback.format_exc()
            raise
        finally:
            entry["end"] = _now()
            entry["seconds"] = round(time.perf_counter() - t0, 3)
            self.log.info("Step end: %s in %.3fs%s%s", name, entry["seconds"],
                          f" {json.dumps(entry['outputs'], ensure_ascii=False, default=str)[:2000]}" if entry["outputs"] else "",
                          f" — FAILED: {entry['error']}" if "error" in entry else "")
            self.save()

    def finish(self, status: str, error: str | None = None):
        with self._lock:
            self.data["status"] = status
            self.data["finished"] = _now()
            if error:
                self.data["errors"].append({"time": _now(), "message": error})
        self.save()


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def bind(log: logging.Logger | None, record: Record | None):
    """The job this thread is working on: commands and model events are logged to it."""
    _current.log, _current.record = log, record


def current() -> tuple[logging.Logger | None, Record | None]:
    return getattr(_current, "log", None), getattr(_current, "record", None)


def bind_ctx(ctx: dict | None):
    bind(ctx.get("log") if ctx else None, ctx.get("record") if ctx else None)


def _trim(text: str | None) -> str:
    text = text or ""
    if len(text) <= OUTPUT_LIMIT:
        return text
    half = OUTPUT_LIMIT // 2
    return f"{text[:half]}\n… [{len(text) - OUTPUT_LIMIT} characters trimmed] …\n{text[-half:]}"


def run(args: list, *, what: str = "", log: logging.Logger | None = None, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run, logged: the exact command, exit code, duration and output (trimmed). Output
    is captured as text unless the caller says otherwise. Exceptions (no such program, timeout) are
    logged and re-raised."""
    log = log or current()[0] or app_log
    record = current()[1]
    kwargs.setdefault("capture_output", True)
    if "text" not in kwargs and "universal_newlines" not in kwargs:
        kwargs.update(text=True, encoding="utf-8", errors="replace")
    cmd = shlex.join(str(a) for a in args)
    entry = {"what": what, "cmd": cmd, "start": _now()}
    log.debug("Command%s: %s", f" ({what})" if what else "", cmd)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run([str(a) for a in args], **kwargs)
    except Exception as e:
        entry.update(seconds=round(time.perf_counter() - t0, 3), exit=None, error=f"{type(e).__name__}: {e}")
        log.warning("Command failed to run (%s): %s", entry["error"], cmd)
        if record:
            record.add("commands", entry)
        raise
    out, err = proc.stdout, proc.stderr
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    entry.update(seconds=round(time.perf_counter() - t0, 3), exit=proc.returncode,
                 stdout=_trim(out), stderr=_trim(err))
    log.log(logging.WARNING if proc.returncode else logging.DEBUG,
            "Command exit %s in %.3fs%s%s", proc.returncode, entry["seconds"],
            f"\n  stdout: {entry['stdout'].strip()}" if entry["stdout"].strip() else "",
            f"\n  stderr: {entry['stderr'].strip()}" if entry["stderr"].strip() else "")
    if record:
        record.add("commands", entry)
    return proc


def external(what: str, cmd: str, seconds: float, ok: bool, detail: str = ""):
    """A tool run in-process that the user thinks of as a command (the separator): logged like run()."""
    log, record = current()
    log = log or app_log
    entry = {"what": what, "cmd": cmd, "start": _now(), "seconds": round(seconds, 3), "exit": 0 if ok else 1,
             "stdout": _trim(detail), "stderr": ""}
    log.log(logging.INFO if ok else logging.WARNING, "%s: %s — %s in %.3fs%s", what, cmd,
            "ok" if ok else "FAILED", seconds, f" ({detail})" if detail else "")
    if record:
        record.add("commands", entry)


def model_event(event: str, model: str, **details):
    """A model load or unload, cache hit or miss, or download (URL, size, time)."""
    log, record = current()
    text = ", ".join(f"{k}={v}" for k, v in details.items())
    hit = event == "cache hit"  # every Whisper call asks for the model: DEBUG, and once per job in job.json
    (log or app_log).log(logging.DEBUG if hit else logging.INFO, "Model %s: %s%s", event, model,
                         f" ({text})" if text else "")
    if log is not None and event in ("download", "download failed", "load", "unload"):
        app_log.info("Model %s: %s%s", event, model, f" ({text})" if text else "")
    if record and not (hit and any(m["event"] == event and m["model"] == model for m in record.data["models"])):
        record.add("models", {"time": _now(), "event": event, "model": model, **details})


def dumps(obj) -> str:
    """Compact JSON for DEBUG lines."""
    return json.dumps(obj, ensure_ascii=False, default=str)

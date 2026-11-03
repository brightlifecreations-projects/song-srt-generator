"""The Hinglish speech model: download once, then fully offline (DECISIONS.md V4.2).

Oriserve's Whisper-Hindi2Hinglish-Apex (Apache-2.0, fine-tuned from Whisper large-v3-turbo),
converted to CTranslate2 int8 for faster-whisper and published as one zip on this project's
GitHub releases (config.HINGLISH_MODEL_URL, checked against HINGLISH_MODEL_SHA256). It's
unpacked into the data folder's models/ and never downloaded again.

Downloads happen the first time a Hinglish draft is made, from the app's "Download now"
button, or from setup:

    python hinglish_model.py download     → prints progress, exit 0 when installed
    python hinglish_model.py status
"""

import hashlib
import os
import shutil
import ssl
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import config

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("SRT_DATA_DIR") or ROOT)
MODEL_DIR = DATA_DIR / "models" / "whisper-hinglish-apex-int8"
REQUIRED = ("model.bin", "config.json", "tokenizer.json", "vocabulary.json", "preprocessor_config.json")

_lock = threading.Lock()
_download_lock = threading.Lock()  # one download at a time
# Shared with the UI: state "missing" | "downloading" | "installed" | "error".
_state = {"state": None, "done_mb": 0.0, "total_mb": float(config.HINGLISH_MODEL_MB), "error": None}


class DownloadError(Exception):
    """A message safe to show in the UI."""


def installed() -> bool:
    return all((MODEL_DIR / name).is_file() for name in REQUIRED)


def status() -> dict:
    with _lock:
        state = _state["state"]
        if state != "downloading":
            state = "installed" if installed() else ("error" if _state["error"] else "missing")
        return {**_state, "state": state, "size_mb": config.HINGLISH_MODEL_MB}


def _set(**kw):
    with _lock:
        _state.update(kw)


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def download(progress=None) -> Path:
    """Download, verify and unpack the model unless it's already there. progress(done_mb, total_mb)
    is called as bytes arrive. Only one download runs at a time; a second caller waits for it."""
    if installed():
        return MODEL_DIR
    with _download_lock:
        if installed():
            return MODEL_DIR
        _set(state="downloading", done_mb=0.0, error=None)
        try:
            _fetch(progress)
        except DownloadError as e:
            _set(state="error", error=str(e))
            raise
        except Exception as e:
            _set(state="error", error=f"Couldn't install the Hinglish model ({e}).")
            raise DownloadError(_state["error"])
        _set(state="installed")
        return MODEL_DIR


def _fetch(progress):
    models = MODEL_DIR.parent
    models.mkdir(parents=True, exist_ok=True)
    part = models / "whisper-hinglish-apex-int8.zip.part"
    digest = hashlib.sha256()
    try:
        req = urllib.request.Request(config.HINGLISH_MODEL_URL, headers={"User-Agent": "srt-generator"})
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as r, part.open("wb") as f:
            total = int(r.headers.get("Content-Length") or 0) / 1048576 or float(config.HINGLISH_MODEL_MB)
            _set(total_mb=round(total, 1))
            done, last = 0, 0.0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                _set(done_mb=round(done / 1048576, 1))
                if progress and time.monotonic() - last > 0.25:
                    last = time.monotonic()
                    progress(done / 1048576, total)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise DownloadError("Couldn't download the Hinglish model — check the internet connection "
                            f"and try again ({getattr(e, 'reason', e)}).")
    if config.HINGLISH_MODEL_SHA256 and digest.hexdigest() != config.HINGLISH_MODEL_SHA256:
        part.unlink(missing_ok=True)
        raise DownloadError("The Hinglish model download was damaged (checksum mismatch). Try again.")
    staging = models / "whisper-hinglish-apex-int8.unpacking"
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(part) as z:
        z.extractall(staging)
    inner = next((p.parent for p in staging.rglob("model.bin")), None)
    if inner is None or not all((inner / n).is_file() for n in REQUIRED):
        shutil.rmtree(staging, ignore_errors=True)
        raise DownloadError("The Hinglish model download is incomplete. Try again.")
    shutil.rmtree(MODEL_DIR, ignore_errors=True)
    inner.rename(MODEL_DIR)
    shutil.rmtree(staging, ignore_errors=True)
    part.unlink(missing_ok=True)


def start_background_download():
    """For the app's "Download now" button: download on a thread, progress via status()."""
    if installed() or _state["state"] == "downloading":
        return
    _set(state="downloading", done_mb=0.0, error=None)  # visible at once, before the thread runs

    def run():
        try:
            download()
        except DownloadError:
            pass
    threading.Thread(target=run, daemon=True).start()


def main(argv: list[str]) -> int:
    if argv[:1] == ["status"]:
        print("installed" if installed() else "missing", MODEL_DIR)
        return 0
    if argv[:1] != ["download"]:
        print(__doc__)
        return 2
    if installed():
        print(f"Hinglish model already installed: {MODEL_DIR}")
        return 0
    print(f"Downloading the Hinglish model (about {config.HINGLISH_MODEL_MB} MB, once)…", flush=True)

    def show(done, total):
        print(f"Hinglish model: {done:.0f} of {total:.0f} MB ({done / total * 100:.0f}%)", flush=True)
    try:
        download(show)
    except DownloadError as e:
        print(f"FAILED: {e}")
        return 1
    print(f"Hinglish model installed: {MODEL_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

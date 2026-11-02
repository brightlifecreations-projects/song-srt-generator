#!/bin/bash
# SRT Generator — one-time installer for Apple Silicon Macs.
# Everything installs INSIDE this folder. Nothing on your Mac is touched.
# Needs internet once (Python runtime + AI models). Safe to re-run anytime.

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

say_fail() {
  echo ""
  echo "✗ Something didn't finish. No harm done — your Mac is unchanged."
  echo "  1) Check your internet connection"
  echo "  2) Double-click this installer again (it continues where it stopped)"
  echo "  3) Still stuck? Send Aman a screenshot of this window."
  echo ""
  exit 1
}
trap say_fail ERR
set -e

echo ""
echo "  SRT Generator — installing"
echo "  Everything stays inside this folder. Nothing else on your Mac is touched."
echo ""

# Only Apple Silicon is supported in this build.
if [ "$(uname -m)" != "arm64" ]; then
  echo "✗ This build is for Apple Silicon Macs (M1/M2/M3/M4)."
  echo "  This Mac is $(uname -m). Ask Aman for the right build."
  exit 1
fi

# Let later double-clicks (Start) run without Gatekeeper warnings.
xattr -dr com.apple.quarantine . 2>/dev/null || true

# Bundled tools: uv (installs a private Python), ffmpeg/ffprobe (audio handling).
if [ ! -x "bin/uv" ] || [ ! -x "bin/ffmpeg" ] || [ ! -x "bin/ffprobe" ]; then
  echo "✗ The bin/ folder is missing from this download."
  echo "  Re-download the zip from Aman and unzip it again."
  exit 1
fi
export PATH="$DIR/bin:$PATH"
export UV_PYTHON_INSTALL_DIR="$DIR/.python"
export UV_PYTHON_PREFERENCE="only-managed"
export UV_CACHE_DIR="$DIR/.uv-cache"
export HF_HOME="$DIR/models/hf"

echo "[1/3] Setting up the app's private Python…"
if [ ! -x ".venv/bin/python" ]; then
  bin/uv venv --quiet --python 3.10 .venv
fi
bin/uv pip install --quiet --python .venv/bin/python -r requirements.txt
echo "      done"

echo "[2/3] Downloading the AI models and doing a test run…"
echo "      (first time only — roughly 2 GB, can take 5–20 minutes)"
.venv/bin/python warmup.py

echo ""
echo "[3/3] All set."
echo ""
echo "  ✓ PASS — installation complete."
echo "  From now on, just double-click  Start.command  to use the app."
echo ""

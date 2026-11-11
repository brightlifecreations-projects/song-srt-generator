# SRT Generator — setup and run guide

You are the installer and operator of this app. Set it up on this machine, verify it, then run it.

This app turns a song, drama dialogue or speech (audio or video) into a timestamped SRT by forced alignment. Three sections — Song, Drama, Reels/YouTube (`config.SECTIONS`) — set the voice-isolation default and the cue rules (DECISIONS.md V4.0). Lyrics mode aligns the user's pasted lyrics/script and never transcribes; Transcribe mode has Whisper draft the text, the user corrects and approves it, and only the approved text is aligned (DECISIONS.md V3.8). Pipeline: audio (extracted from video with the bundled ffmpeg) → voice isolation (on, off, or auto when music is detected) → align text → captions (numbers as digits, clean symbols) → optional final AI check → listening check (Whisper re-listens offline, fixes or flags each caption; DECISIONS.md V4.8) → SRT. Approved transcripts teach `corrections.md` (data folder), which corrects new drafts and gives Whisper hotwords (DECISIONS.md V4.1). Hinglish drafts use a dedicated Hinglish speech model, downloaded once on first use (DECISIONS.md V4.2). Fully offline after one-time model downloads, except the optional AI polish (off until a key is added; sends transcript text only — DECISIONS.md V4.3).

## 1. Preflight

- Detect the OS (`uname` on Mac/Linux, or Windows).
- `python3 --version` — need **3.10 or 3.11** (on Mac try `python3.11 --version`, `python3.10 --version`; on Windows `py -3.11 --version`). Not 3.12+.
- `ffmpeg -version` — must be on PATH.

## 2. Install ffmpeg if missing

- **macOS:** `brew install ffmpeg`. No Homebrew/admin: download a static build from https://evermeet.cx/ffmpeg/ (both `ffmpeg` and `ffprobe`), unzip, move both into `/usr/local/bin/` (or any folder on PATH).
- **Windows:** `winget install Gyan.FFmpeg`, or Chocolatey `choco install ffmpeg`. No admin rights: download the "essentials" zip from https://www.gyan.dev/ffmpeg/builds/, unzip to `C:\Users\<name>\ffmpeg`, then add `C:\Users\<name>\ffmpeg\bin` to the user PATH (Settings → System → About → Advanced system settings → Environment Variables → Path → Edit → New). Open a NEW terminal afterwards.

## 3. Environment

The scripted way (preferred): run `bash install.command` — zero prerequisites on Apple Silicon; it uses the bundled `bin/uv` to create a private Python 3.10 venv inside the folder, installs pinned deps, and runs warmup. (In a git checkout without `bin/`, run `./make_release.sh` once to fetch the bundled tools, or do the manual steps below.) Manual equivalent:

- `python3.11 -m venv .venv` (or 3.10)
- Activate: `source .venv/bin/activate` (Mac/Linux) or `.venv\Scripts\activate.bat` (Windows)
- **Apple Silicon Mac:** `pip install -r requirements.txt` (pinned; includes the MLX GPU separation engine). `SEPARATOR_ENGINE = "auto"` in config.py picks it (MLX on Apple Silicon, the CPU engine elsewhere).
- **Windows x64 (tested on Windows 11 in V4.6):** the scripted way is `install.bat` (bundled `bin\uv.exe` + ffmpeg/ffprobe; build the zip with `./make_release.sh <ver> --windows`, or for a checkout put `uv.exe`, `ffmpeg.exe` and `ffprobe.exe` into `bin\` yourself — uv from https://github.com/astral-sh/uv/releases, ffmpeg "essentials" from https://www.gyan.dev/ffmpeg/builds/). Use a short folder such as `C:\srt`: Windows' 260-character path limit can break the install in a deep folder. Manual: `pip install -r requirements-windows.txt` (pinned in V4.6; uses the `audio-separator` CPU engine, which `SEPARATOR_ENGINE = "auto"` selects). Tests: `.venv\Scripts\python -m unittest discover -s tests`; the Windows UI suite is `test-fixtures\ui_edge.py` with `ui_v46.js`. See DECISIONS.md V3.2 and V4.6.
- **Windows app (V4.7, what collaborators use on Windows):** `SRT-Generator-<version>-Setup.exe` from GitHub Releases, built by `.\make_release_windows.ps1 <version>` on a Windows PC (`winapp/`; see DECISIONS.md V4.7). It installs per user to `%LOCALAPPDATA%\Programs\SRT Generator` and keeps all data in `%LOCALAPPDATA%\SRT Generator`; updates come through WinSparkle. Never commit the Windows EdDSA private key; it lives only in that PC's Windows Credential Manager.
- **Intel Mac:** not supported. `install.command` refuses non-arm64 Macs and there is no Intel build.
- **Collaborators don't use this guide:** they install "SRT Generator.app" from GitHub Releases (built by `./make_release.sh <ver>` from `macapp/`; see DECISIONS.md V3.6 and V4.0) and get updates through Sparkle. Its data lives in `~/Library/Application Support/SRT Generator/` (3.x's `Song SRT Generator/` folder is moved there on first launch), not in this folder. `VERSION` is the single source of the version number. Never commit Sparkle's private EdDSA key; it lives only in the release machine's Keychain.

## 4. First-run verification

Run `python warmup.py`. It downloads the separation and alignment models (~2 GB total, internet needed once), runs the full pipeline on `sample/`, and checks that an SRT with at least one cue was produced. **Setup is not complete until it prints PASS** with per-stage timings.

## 5. Launch

`python app.py` — the browser opens automatically at http://127.0.0.1:7860. Every later use: run `start.command` or `python app.py` again. One job runs at a time; a second submit queues.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `ffmpeg not found` | Section 2 above; open a new terminal after installing. |
| torch install fails on Windows | `pip install torch --index-url https://download.pytorch.org/whl/cpu` first, then the rest. |
| Out of memory during a job | Open config.py, set `MODEL_SIZE = "small"`, restart the app. |
| Port busy at launch | Change `PORT` in config.py. |
| First job is slow | One-time model load into memory; later jobs are much faster. Model *downloads* only happen during warmup. |
| No internet during setup | Models can't download — setup requires internet once. Runtime is fully offline afterwards. |
| `Model conversion requires the [convert] extras` (Apple Silicon) | `pip install 'mlx-audio-separator[convert]'` (already in requirements.txt). |

## 7. Rules for the agent

- Never install packages globally outside the venv — the only system-level installs allowed are ffmpeg and Python themselves.
- Never change files outside this folder.
- Never send any project file anywhere.
- The UI is a prebuilt React app served from frontend/dist. Node.js is only needed to rebuild it (cd frontend && npm install && npm run build) — never required for teammates.

# SRT Generator

Turn a song, a drama scene, a reel or a YouTube video (audio or video file) into a timestamped SRT subtitle file. (Called *Song SRT Generator* before version 4.0.) Three sections, each tuned for its kind of audio:

- **Song** — isolates the vocals from the music, one subtitle per lyric phrase. Vertical by default.
- **Drama** — isolates the dialogue from background music and effects; subtitles stay on screen 7 seconds at most and a new one starts at every pause. Horizontal by default.
- **Reels / YouTube** — speech, cut into short punchy captions (four words or less, 2.5 seconds at most, a new one at every pause). The voice is only isolated if background music is detected. Vertical by default.

Every section has the same two ways in:

- **I have the lyrics/script:** paste the exact words and they're snapped to the audio, so every subtitle matches a line exactly as written.
- **Transcribe for me:** Whisper writes a draft; you correct it and approve it, then it's timed the same way.

Everything runs on your machine; files never leave your computer.

## Install — Apple Silicon Macs (M1, M2, M3, M4)

Nothing needs to be pre-installed. Not even Python.

1. Download `SRT-Generator-<version>-mac.zip` from the [latest release](https://github.com/brightlifecreations-projects/song-srt-generator/releases/latest) and double-click it to unzip.
2. Drag **SRT Generator** into your **Applications** folder.
3. Double-click it. macOS says *"Apple could not verify 'SRT Generator'…"* because the app isn't from the App Store. Click **Done** (not *Move to Bin*).
4. Open **System Settings → Privacy & Security**, scroll down to the message about SRT Generator, click **Open Anyway**, and confirm with your password or Touch ID. You do this once; updates after that install themselves.
5. The first launch sets everything up in a progress window: a private Python, the app's packages and the AI models (about 2.5 GB, 5–20 minutes, internet needed once). It finishes with a test run that has to pass, then the app opens.

Everything the app installs lives in `~/Library/Application Support/SRT Generator/` (Python, packages, models and your `jobs/` folder); the app itself stays unchanged. To uninstall, delete the app and that folder.

**Updating (Mac):** the app checks for a new version once a day and offers to install it (Sparkle); **SRT Generator → Check for Updates…** checks right away. It downloads, verifies the update's signature, installs and relaunches by itself. Your models are kept, so an update takes about a minute.

**Coming from Song SRT Generator 3.x:** 3.x can't update itself, so its *Update available* banner sends you to this release once. Install **SRT Generator** as above. On its first launch it moves your old `Song SRT Generator` folder (models, jobs and settings — nothing is downloaded again apart from the app's Python packages, which come from the local cache) and puts the old app in the Trash. From then on updates are automatic.

**Windows (x64, untested on real hardware):** download `SRT-Generator-<version>-win-lite.zip`, unzip it somewhere short like Desktop, double-click `Install.bat` (if SmartScreen appears: *More info → Run anyway*), wait for PASS, then use `Start.bat`. Windows has no auto-update: the app shows an *Update available* banner when a new version is out.

Building releases from this repo: `./make_release.sh <version>` builds the Mac app zip and the Sparkle feed `appcast.xml` (the version must match the `VERSION` file, and `release-notes/<version>.md` must exist); add `--windows` for the Windows zip or `--full` to bake the models into the Mac app for offline installs. `./make_release.sh <version> --publish` then creates the GitHub release with both zips and the appcast. The Mac app is ad-hoc code-signed on every build, and the zip is signed for Sparkle with the EdDSA key in the release machine's login Keychain (account `srt-generator`; it is never stored in this repo). Developers can still run from a checkout with `bash install.command` and `start.command`.

## Using it

1. Open **SRT Generator** from Applications (or Launchpad). The app opens in its own window; closing the window quits it. Use the toggle in the top-right corner for dark mode. (On Windows or a source checkout, `Start.bat` / `start.command` opens it in your browser instead.)
2. Pick the section tab at the top — **Song**, **Drama** or **Reels / YouTube** — then the mode: **I have the lyrics/script** or **Transcribe for me**. Each tab remembers its own settings (mode, isolation, format, language, script) and keeps its own file and result while you look at another tab.
3. Drop the file anywhere on the page, or click to browse: audio (MP3, WAV, M4A, FLAC, up to 500 MB) or video (MP4, MOV, M4V, MKV, WEBM, up to 4 GB). A video is previewed with the subtitles drawn on it; only its sound is processed (with the bundled ffmpeg). Videos the app's window can't play itself (MKV, some codecs) get a small preview copy made while the job runs.
4. Check the **isolation** choice (preset per tab): **Isolate vocals / dialogue** removes music and effects first, **Off** uses the audio as is, **Auto** isolates only if background music is detected (the Reels / YouTube default).
5. Pick the **video format**: Vertical 9:16 for Reels/TikTok/Shorts (short subtitle lines, max 25 characters) or Horizontal 16:9 for YouTube/TV (up to 42 characters). Every subtitle is a single line; a comma or full stop in the text always starts a new subtitle — punctuate the way the audio breathes and the captions will follow. On top of that, each section has its own cue rules (Song: one subtitle per phrase; Drama: 7 seconds at most and a new subtitle at every pause; Reels / YouTube: four words or less, 2.5 seconds at most, a new one at every pause).
6. Pick the **language**: Auto-detect, English, Hindi, Hinglish, Spanish, Portuguese, French, German, Italian, Punjabi or Urdu. Each mode remembers its last choice (lyrics mode starts on English, transcribe mode on Auto-detect). The Hindi/Urdu sentence marks (। ॥ ۔ ، ؟) end a subtitle just like a comma or full stop, and a Spanish `¿` or `¡` starts a new one.
7. Then:
   - **Lyrics/script mode:** paste the words exactly as sung or spoken (one line becomes one subtitle, write out every repeated chorus in full; **Load .txt** fills the box) and click **Generate SRT**.
   - **Transcribe mode:** pick the **Lyrics script** (below), then click **Transcribe**. The draft appears in the box, one line per phrase, with the language Whisper detected. Play the audio, fix any wrong words, and click **Approve & generate SRT**. The approved text is timed against the same audio (a song's vocals are isolated only once). Spotted a mistake afterwards? Edit and approve again.
8. Download the SRT (and, when the voice was isolated, the isolated vocal/dialogue stem as a bonus).

**Speaker turns (Drama):** there's no speaker recognition (that would need an online account for its model). Turns come from the text: put each speaker's line on its own line in the script, or correct the draft that way; a pause in the audio also starts a new subtitle.

**Transcription quality, honestly** (tested in V3.8, details in DECISIONS.md): clear English speech comes out essentially perfect; a sung English track came out about 80% word-for-word right, so always read a song's draft against the audio. Hindi comes out in Devanagari with misheard words, and on a full Hindi song the second half degraded into nonsense. Transcription runs on the CPU: English takes about half to the full length of the audio, Hindi several times longer (a 3.6-minute Hindi song took 13 minutes). Aligning the approved text then takes seconds.

**Lyrics script (transcribe mode)** chooses how the draft is written:

| Option | What you get | Subtitle timing |
|---|---|---|
| Same as audio | Whatever script Whisper writes for the detected language (Hindi songs often come out in Urdu script, because Hindi and Urdu sound the same to Whisper) | Aligned to the audio |
| **Hinglish** | Hindi in everyday Roman letters ("kabhi kabhi zindagi mein"): Whisper transcribes in Hindi, then the app converts it offline. English words stay as they are | Aligned to the audio |
| Hindi (Devanagari) | Hindi script; any Urdu-script words are converted (roughly — check them) | Aligned to the audio |
| Urdu script | Urdu script | Aligned to the audio |
| English translation | Whisper's English translation | **Approximate**: translated words can't be matched to the audio word by word, so subtitles follow Whisper's phrase timings (still within the character caps and 7 seconds) |

Until you pick one, it's **Automatic**: Hindi or Urdu audio is written as Hinglish, anything else in its own script. Your pick is remembered. Changing it after a draft needs a new transcription. In lyrics mode, Hinglish works as before: paste Roman-script lyrics and choose **Hinglish** in the Language dropdown.

Every job is also saved in the `jobs/` folder (the input audio, or just the sound of a video, plus vocals, the draft, the approved text, SRT, cue data and log). In the Mac app that's `~/Library/Application Support/SRT Generator/jobs/` (in Finder: Go → Go to Folder…). Delete old job folders whenever you like.

From the command line:

- `python pipeline.py <audio|video> <lyrics.txt> [vertical|horizontal] [auto|en|hi|hinglish|es|pt|fr|de|it|pa|ur] [song|drama|reels] [isolate=auto|on|off]` (the options are optional, in any order)
- `python pipeline.py --transcribe <audio|video> [language] [song|drama|reels] [isolate=auto|on|off] [script=same|hinglish|devanagari|urdu|english]` prints a draft transcript

## Settings

Open `config.py` in a text editor — each setting has a one-line comment. (In the Mac app, config.py sits inside the read-only app bundle; the defaults are what you get.) The useful ones: `LANGUAGE` and `TRANSCRIBE_LANGUAGE` (the language preselected in each mode), `SECTIONS` (each section's default format and isolation, and its cue rules), `MUSIC_DETECT_DB` (how loud the background must be for *Auto* isolation), `MAX_UPLOAD_MB` / `MAX_VIDEO_UPLOAD_MB`, `MODEL_SIZE` (`"small"` = faster, `"medium"` = more accurate), `STEM_FORMAT` (`"wav"` or `"mp3"`), `PORT`, and `HOST` (set to `"0.0.0.0"` to let teammates on your network use it from their browser).

## If something goes wrong

| Problem | Fix |
|---|---|
| "ffmpeg isn't installed" | Re-run the installer; open a fresh terminal afterwards. |
| Ran out of memory | Set `MODEL_SIZE = "small"` in config.py and restart. |
| Port already in use | Change `PORT` in config.py. |
| First job is slow | The model loads into memory once per app start; later jobs are much faster. |
| "Couldn't align this text to the audio" | The words don't match what's sung or said, or repeated choruses aren't written out in full. Fix and retry. |
| Transcription draft is in the wrong script (Urdu, Devanagari) | Pick **Hinglish** or **Hindi (Devanagari)** under *Lyrics script* and transcribe again. |
| "This video has no sound track" / "This file is silent" | There's no audio to subtitle; check you exported the video with sound. |
| Mac app setup window shows an error | Click **Try again**. If it keeps failing, click **Show the log** and send Aman `install.log`. |
| "Song SRT Generator (the old version) is still open" | Quit the old app (its window and Dock icon), then open SRT Generator again; it moves your data over. |
| "Port 7860 is already in use" in the Mac app | The browser version (`start.command`) is still running. Close its Terminal window and click **Try again**. |

Setup needs internet once (model downloads). After that the app works fully offline; the only network requests are the update checks (Sparkle's daily check on Mac; on Windows and checkouts a check on launch, off with `CHECK_FOR_UPDATES = False` in config.py), which are skipped silently when offline.

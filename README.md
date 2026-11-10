# SRT Generator

Turn a song, a drama scene, a reel or a YouTube video (audio or video file) into a timestamped SRT subtitle file. (Called *Song SRT Generator* before version 4.0.) Three sections, each tuned for its kind of audio:

- **Song** — isolates the vocals from the music; a caption never runs across two lyric lines. Vertical by default.
- **Drama** — isolates the dialogue from background music and effects; subtitles stay on screen 7 seconds at most and a new one starts at every pause. Horizontal by default.
- **Reels / YouTube** — speech; 2.5 seconds at most per caption and a new one at every pause. The voice is only isolated if background music is detected. Vertical by default.

Every section follows the same caption rules (since 4.6, TikTok style): one line per caption, at most 5 words and about 30 characters on vertical video (42 on horizontal), cut at natural reading points, numbers as digits, timed from the first spoken word to just after the last. Pick **Line by line** or **Word by word** on every page.

Every section has the same two ways in:

- **I have the lyrics/script:** paste the exact words and they're snapped to the audio, so every subtitle matches a line exactly as written.
- **Transcribe for me:** Whisper writes a draft; you correct it and approve it, then it's timed the same way.

Everything runs on your machine; files never leave your computer. (The one exception is optional and off by default: [AI polish](#ai-polish-optional-online) sends transcript text — never audio or video — to an AI provider you choose.)

## Install — Apple Silicon Macs (M1, M2, M3, M4)

Nothing needs to be pre-installed. Not even Python.

1. Download `SRT-Generator-<version>-mac.zip` from the [latest release](https://github.com/brightlifecreations-projects/song-srt-generator/releases/latest) and double-click it to unzip.
2. Drag **SRT Generator** into your **Applications** folder.
3. Double-click it. macOS says *"Apple could not verify 'SRT Generator'…"* because the app isn't from the App Store. Click **Done** (not *Move to Bin*).
4. Open **System Settings → Privacy & Security**, scroll down to the message about SRT Generator, click **Open Anyway**, and confirm with your password or Touch ID. You do this once; updates after that install themselves.
5. The first launch sets everything up in a progress window: a private Python, the app's packages and the AI models (about 2.5 GB, 5–20 minutes, internet needed once). It finishes with a test run that has to pass, then the app opens.

Everything the app installs lives in `~/Library/Application Support/SRT Generator/` (Python, packages, models and your `jobs/` folder); the app itself stays unchanged. To uninstall, delete the app and that folder.

**Updating (Mac):** open **Updates** in the sidebar. It shows your version and when it last checked; **Check for updates** checks now (so does **SRT Generator → Check for Updates…** in the menu). When a new version is out, a dot appears on **Updates**, and the page shows the new version's notes with **Update now**, which downloads it, verifies its signature, installs it and relaunches the app by itself (through Sparkle). Two switches: **Check automatically** (once a day) and **Download and install automatically** (new versions download in the background and install the next time you quit the app). Your models are kept, so an update takes about a minute.

**Still seeing an old screen after an update?** Quit the app with **Cmd+Q** (closing the window isn't enough) and open it again. From 4.5 on the app clears its page cache after every update, and from 4.5.1 it also stops any older copy of itself still running in the background, so this shouldn't happen any more. If the old **Song SRT Generator** app is still in Applications or Downloads, 4.5 offers once to move it to the Trash.

**Coming from Song SRT Generator 3.x:** 3.x can't update itself, so its *Update available* banner sends you to this release once. Install **SRT Generator** as above. On its first launch it moves your old `Song SRT Generator` folder (models, jobs and settings — nothing is downloaded again apart from the app's Python packages, which come from the local cache) and puts the old app in the Trash. From then on updates are automatic.

## Install — Windows 10 and 11 (64-bit)

Nothing needs to be pre-installed, and no admin rights are needed.

1. Download `SRT-Generator-<version>-Setup.exe` from the [latest release](https://github.com/brightlifecreations-projects/song-srt-generator/releases/latest) and double-click it.
2. **The first time, Windows SmartScreen may say "Windows protected your PC"**, because the installer isn't from the Microsoft Store. Click **More info**, then **Run anyway**. You do this once; updates install themselves.
3. Click through the installer (tick *Create a desktop shortcut* if you want one). It installs for you only, to `%LOCALAPPDATA%\Programs\SRT Generator`, and adds **SRT Generator** to the Start menu.
4. The first launch sets everything up in a progress window, like on the Mac: a private Python, the app's packages and the AI models (about 2.5 GB, 5–20 minutes, internet needed once). It finishes with a test run that has to pass, then the app opens in its own window. Opening it again brings that window forward; closing the window quits the app.

Everything the app writes lives in `%LOCALAPPDATA%\SRT Generator\`: its Python, packages, models, `jobs\`, `corrections.md` and settings. Updates never touch it, so nothing is downloaded twice. **Used the zip version before** (Install.bat / Start.bat in `C:\srt`, `C:\SRT-Generator` or a folder on your Desktop, Documents or Downloads)? On first launch the app offers to move that copy's models and jobs over, and copies its corrections and settings.

**Uninstall:** Settings → Apps → SRT Generator → Uninstall. It asks whether to delete your data folder too (No keeps it, so a reinstall needs no downloads). API keys stay in Windows Credential Manager until you remove them on the AI & API keys page.

**Updating (Windows):** the app checks once a day (WinSparkle, the Windows counterpart of the Mac's Sparkle). When a new version is out, WinSparkle's window shows its notes. **Install update** downloads the new installer, checks its signature, installs it silently and reopens the app. **Check for updates** is in the app's menu (**SRT Generator → Check for updates…**) and on the **Updates** page, whose **Update now** does the same in one step. The two switches work as on the Mac: **Check automatically**, and **Download and install automatically** (an update the daily check finds installs on its own, and the app reopens).

**The zip version** (`SRT-Generator-<version>-win-lite.zip`) is still published for anyone who prefers it: unzip it somewhere short like `C:\SRT-Generator` (Install.bat warns if the folder's path is long enough to hit Windows' path limit), double-click `Install.bat` (SmartScreen: *More info → Run anyway*), wait for PASS, then use `Start.bat`, which opens the app in your browser. It doesn't update itself: its **Updates** page checks the latest GitHub release and links to it.

Building releases from this repo: `./make_release.sh <version>` builds the Mac app zip and the Sparkle feed `appcast.xml` (the version must match the `VERSION` file, and `release-notes/<version>.md` must exist); add `--windows` for the Windows zip or `--full` to bake the models into the Mac app for offline installs. `./make_release.sh <version> --publish` then creates the GitHub release with both zips and the appcast. The Mac app is ad-hoc code-signed on every build, and the zip is signed for Sparkle with the EdDSA key in the release machine's login Keychain (account `srt-generator`; it is never stored in this repo). Developers can still run from a checkout with `bash install.command` and `start.command`.

Building the Windows release, on a Windows PC: `.\make_release_windows.ps1 <version>` builds `SRT-Generator-<version>-Setup.exe` (Inno Setup), the win-lite zip and `appcast-windows.xml` (the WinSparkle feed, newest five versions) in `dist-release\`. Add `-Publish` to create the GitHub release `v<version>` (or add to it) with all three. When that release has no Mac build, the script also attaches the Mac's `appcast.xml` from the newest release that has one, unchanged, so the Mac feed `…/releases/latest/download/appcast.xml` keeps working. The Setup.exe is signed for WinSparkle with the Windows EdDSA key in that PC's Windows Credential Manager (`SRT Generator release key (Windows)`, managed with `winapp\release_key.py`; never stored in this repo). Tools (uv, ffmpeg, WinSparkle 0.9.4, Inno Setup 6.7.4) are downloaded once into `release-assets\`.

## Using it

1. Open **SRT Generator** from Applications or Launchpad (Windows: the Start menu). The app opens in its own window; closing the window quits it. The **sidebar** on the left has the pages — **Song**, **Drama**, **Reels / YouTube**, then **Corrections** and **AI & API keys** — and, at the bottom, the AI polish status, **Dark mode**, **Collapse** and the version. The app reopens on the last page you used; in a narrow window the sidebar shrinks to icons and the page's two columns stack. (The Windows zip version and a source checkout, `Start.bat` / `start.command`, open it in your browser instead.)
2. Pick the page — **Song**, **Drama** or **Reels / YouTube** — then the mode at the top of the left column: **I have the lyrics/script** or **Transcribe for me**. Everything you set is in the left column, top to bottom; the right column shows the preview, progress and result, and stays in view while you scroll. Each page remembers its own settings (mode, isolation, format, language, script) and keeps its own file and result while you look at another page.
3. Drop the file anywhere on the page, or click to browse: audio (MP3, WAV, M4A, FLAC, up to 500 MB) or video (MP4, MOV, M4V, MKV, WEBM, up to 4 GB). A video is previewed with the subtitles drawn on it; only its sound is processed (with the bundled ffmpeg). Videos the app's window can't play itself (MKV, some codecs) get a small preview copy made while the job runs.
4. Less-used options are under **More options** (it remembers whether you left it open): voice **isolation**, *Remove section labels*, **Topic words** and Hinglish **Fast / Accurate**. Isolation (preset per page): **Isolate vocals / dialogue** removes music and effects first, **Off** uses the audio as is, **Auto** isolates only if background music is detected (the Reels / YouTube default).
5. Pick the **video format**: Vertical 9:16 for Reels/TikTok/Shorts (about 30 characters a line) or Horizontal 16:9 for YouTube/TV (about 42). Then **Captions**: **Line by line** (the default) or **Word by word** (one word at a time, each on screen from its own word to the next, with no gaps or overlaps). Each page remembers its choice, and the Review panel can switch a finished SRT either way without transcribing or aligning again. The caption rules (4.6), in every section and both modes:
   - **One line per caption**, at most **5 words** (it aims for 4–5) and about **30 characters** vertical / **42** horizontal, so it never wraps. Both limits are under *More options → Caption length*, per page.
   - **Cut at natural reading points:** phrases stay whole ("for her son", "in a size 4"), a subject stays with its verb, and a caption never ends on a weak word (*the, a, an, to, of, and, but, just, is, ka, ki, ke, aur, toh…*). A comma, full stop (or । ॥ ۔ ، ؟) always ends a caption, and a Spanish `¿`/`¡` starts one.
   - **One speaker per caption:** in a script, a line starting with a name and a colon ("Sarah: …") starts a new caption, and the name is left out of it (names count once at least two lines have one, so a lone "Note: …" stays text). In Transcribe mode a long pause starts a new caption.
   - **Numbers as digits:** "12 years", "Week 1", "7 a.m.", "70%", "size 4", and Hinglish counts ("do saal" → "2 saal"). Expressions stay in words ("not one inch", "one time", "you two"); the list is editable on the **Corrections** page. Digits are only display text: "twenty five" → "25" spans both words' times.
   - **Timing from the audio:** each caption appears on its first spoken word and ends just after its last; captions never overlap. Short captions are fine; *Merge very short captions* (More options) joins one under 0.5 s to its neighbour when it fits.
   - **Only what's spoken:** script lines the aligner can't find in the audio are left out of the SRT and listed in a warning.
   - On top of that, Drama keeps a caption on screen 7 seconds at most and Reels / YouTube 2.5 seconds, both with a new caption at every pause.
6. Pick the **language**: Auto-detect, English, Hindi, Hinglish, Spanish, Portuguese, French, German, Italian, Punjabi or Urdu. Each mode remembers its last choice (lyrics mode starts on English, transcribe mode on Auto-detect). The Hindi/Urdu sentence marks (। ॥ ۔ ، ؟) end a subtitle just like a comma or full stop, and a Spanish `¿` or `¡` starts a new one.
7. Then:
   - **Lyrics/script mode:** paste the words exactly as sung or spoken (one line becomes one subtitle, write out every repeated chorus in full; **Load .txt** fills the box) and click **Generate SRT**.
   - **Transcribe mode:** pick the **Lyrics script** (below), optionally type **Topic words** under *More options* — names, brands and terms in the file (e.g. *Lovika, reel, laptop, client*) — then click **Transcribe**. The draft appears in the box, one line per phrase, with the language Whisper detected. Play the audio, fix any wrong words, and click **Approve & generate SRT**. The approved text is timed against the same audio (a song's vocals are isolated only once). Spotted a mistake afterwards? Edit and approve again.
8. **Review, then download.** Once the SRT is made, the right column becomes the **Review panel**: the video (or waveform) with the current subtitle drawn on it, play/pause and a scrubber, and every cue with its number, start and end. The playing cue is highlighted and scrolls into view; click a cue to jump to it, and tick **Loop this cue** to replay it.
   - **Edit:** **Enter** (or double-click) edits a line's text; **Start/End −.1 / +.1** nudge its times; **⇤ playhead / playhead ⇥** set them from the playhead; **Split** cuts the cue at the cursor; **Merge next**, **Delete**, and **+ Cue at playhead** add or remove cues; **Undo/Redo** (⌘Z / ⇧⌘Z) undo anything. **Re-time line** re-aligns just that cue's audio after you've changed its words a lot.
   - **Keys:** **Space** play/pause, **↑ ↓** move between cues, **Enter** edit, **Esc** stop editing.
   - **Checks:** overlapping cues, cues shorter than 0.08 s (two frames) and lines over the character limit are flagged, and **Fix automatically** repairs them (pulls back overlaps, splits long lines, lengthens very short cues).
   - **Line by line / Word by word** at the top of the panel rebuilds the subtitles from the same word timings (it replaces your edits in the panel, after asking).
   - **Final AI check:** its changes are highlighted; hover one to see the original word and **Undo** it.
   - **Saving:** edits save themselves into the job folder as `edited.srt` (the generated SRT is kept as `original.srt`) and stay when you switch pages. **Download SRT** gives exactly what the list shows, cue for cue — times to the millisecond as listed, the edit you're still typing included (4.6) — marked *Edited* once you've changed anything; **Download original** gives the generated one. SRT files are UTF-8 (no BOM) with Windows (CRLF) line endings, which CapCut and Premiere read. Words you re-type are added to your corrections file as *User* fixes, like edits in the draft box. The isolated vocal/dialogue stem is there too when the voice was isolated.

**Speaker turns (Drama):** there's no speaker recognition (that would need an online account for its model). Turns come from the text: put each speaker's line on its own line in the script, or correct the draft that way; a pause in the audio also starts a new subtitle.

**Transcription quality, honestly** (tested in V3.8, details in DECISIONS.md): clear English speech comes out essentially perfect; a sung English track came out about 80% word-for-word right, so always read a song's draft against the audio. Hindi comes out in Devanagari with misheard words, and on a full Hindi song the second half degraded into nonsense. Transcription runs on the CPU: English takes about half to the full length of the audio, Hindi several times longer (a 3.6-minute Hindi song took 13 minutes). Aligning the approved text then takes seconds.

**Lyrics script (transcribe mode)** chooses how the draft is written:

| Option | What you get | Subtitle timing |
|---|---|---|
| Same as audio | Whatever script Whisper writes for the detected language (Hindi songs often come out in Urdu script, because Hindi and Urdu sound the same to Whisper) | Aligned to the audio |
| **Hinglish** | Hindi in everyday Roman letters ("kabhi kabhi zindagi mein"), written directly by a dedicated Hinglish speech model (see [Hinglish](#hinglish)). English words stay as they are | Aligned to the audio |
| Hindi (Devanagari) | Hindi script; any Urdu-script words are converted (roughly — check them) | Aligned to the audio |
| Urdu script | Urdu script | Aligned to the audio |
| English translation | Whisper's English translation | **Approximate**: translated words can't be matched to the audio word by word, so subtitles follow Whisper's phrase timings (still within the character caps and 7 seconds) |

Until you pick one, it's **Automatic**: Hindi or Urdu audio is written as Hinglish, anything else in its own script. Your pick is remembered. Changing it after a draft needs a new transcription. In lyrics mode, Hinglish works as before: paste Roman-script lyrics and choose **Hinglish** in the Language dropdown.

## Hinglish

**Since 4.2, Hinglish drafts come from a Hinglish speech model**: Oriserve's [Whisper-Hindi2Hinglish-Apex](https://huggingface.co/Oriserve/Whisper-Hindi2Hinglish-Apex) (Apache-2.0, fine-tuned from Whisper large-v3-turbo), converted to run fast on the CPU. It writes Hindi speech straight in Roman letters, instead of the old Hindi → Devanagari → Roman letters chain, and is far quicker: a 17-second reel takes about 8 seconds instead of minutes.

- **Downloaded once (781 MB), then offline.** It downloads the first time you transcribe Hinglish, with a progress bar. To get it ahead of time: click **Download now** under the script picker in Transcribe mode, or say yes when setup offers it (the Mac app's setup window, `Install.command`, `Install.bat`). It's kept in the data folder's `models/`, from this project's [GitHub releases](https://github.com/brightlifecreations-projects/song-srt-generator/releases/tag/model-hinglish-apex-int8-v1) with the licence and a NOTICE crediting Oriserve. If it can't be downloaded (no internet), the draft is made the old way and says so.
- **Hinglish speed: Fast or Accurate** (Transcribe mode, under *More options*, remembered per page; Accurate is the default). *Fast* is one pass of the Hinglish model. *Accurate* also asks the standard model for a second opinion (Whisper medium in Hindi, turned into Roman letters) and highlights every word where the two disagree. It then re-transcribes short doubtful stretches on their own, and keeps the re-check only when it is clearly more confident. Accurate takes a few times longer (a 17-second reel: about 45 seconds instead of 8).
- **Words to check are highlighted** in the draft, the same way as auto-corrections: where the second opinion heard something else, or where the model itself wasn't sure. Hover one to see why. **Use “…”** takes the second opinion's word, **Undo** puts back a re-checked word, and **Keep** removes the highlight.
- **House spellings.** Every Hinglish draft is written with your house spellings (*nahin* → *nahi*, *chaahie* → *chahiye*, *hamen* → *humein*, *yah* → *yeh*, *vaala* → *wala*, …) before your corrections file is applied. The built-in table is in the **Corrections** tab: switch rows on or off, edit them, add your own, or **Restore defaults**. Edits are saved as `hinglish-spellings.md` in the data folder. *to* → *toh* is there but off, because "to" is also an English word; switch it on if your speakers rarely use English "to".
- **Topic words** (any script) are passed to Whisper as hints, ahead of the most frequent words from your corrections file.
- **Timing is unchanged**: the approved text is still aligned by the standard model, which tested more precise than the Hinglish model's own alignment (DECISIONS.md V4.2).

## AI polish (optional, online)

After the offline draft is ready, an AI model of your choice can fix the words it misheard. **It's off until you add a key**, and it's the only part of the app that sends anything out: **only the transcript text is sent to the provider you choose. Audio and video never leave this computer.**

**Set it up** on the **AI & API keys** page (sidebar): choose the provider, paste its API key (masked; **Show** reveals what you typed), check the model name, click **Save**, then **Test connection**, then switch **AI polish** on. The page lists the providers that already have a saved key, with the active one marked (**Make active** switches); **Remove key** deletes one. **Auto-polish after every transcription** (on by default) polishes each new draft; switch it off to polish only when you click **Polish** on a draft.

| Provider | Default model (editable) | Get a key |
|---|---|---|
| Anthropic (Claude) | `claude-opus-5-5` | [console.anthropic.com → API keys](https://console.anthropic.com/settings/keys). Use a key created inside a workspace, or type the workspace ID (`wrkspc_…`) in the *Workspace ID* field |
| OpenAI | `gpt-5.4-mini` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Google Gemini | `gemini-flash-latest` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| OpenRouter | `openrouter/auto` | [openrouter.ai/keys](https://openrouter.ai/keys) — one key for many models |
| Custom (OpenAI-compatible) | — type it in | Any server that speaks OpenAI's Chat Completions API: DeepSeek (`https://api.deepseek.com/v1`), Groq (`https://api.groq.com/openai/v1`), Mistral (`https://api.mistral.ai/v1`), a local **Ollama** (`http://localhost:11434/v1`, no key needed) or **LM Studio** (`http://localhost:1234/v1`). Enter its base URL and model name |

Providers charge for API use; a short transcript costs very little per polish. Keys are stored in your Mac's **Keychain** (Windows: **Credential Manager**), one per provider — never in `config.py`, the settings file, logs, job folders or `corrections.md`.

**What happens:** with AI polish and auto-polish on, every transcription on every page gets an extra step, *Polishing with <provider>…*, right after the offline draft. The provider receives the draft's lines, the section, the script and language, your topic words, the relevant rows of your corrections file and house spellings, and the words the offline pass doubted. It's told to fix misheard or misspelled words only, keep exactly the same lines in the same order, never add or remove anything, follow your spellings and corrections, and mark anything it isn't sure of as `[word?]`. Its answer must be valid JSON with the same number of lines; a line it rewrote too much is kept as it was. If anything goes wrong — wrong line count, invalid answer, timeout, rate limit, bad key, no internet — **the offline draft stays** and one line says why. Nothing else is blocked.

**Reviewing:** AI changes are highlighted in **blue**, fixes from your corrections file in **green**; the draft shows the counts (e.g. *“8 AI fixes · 3 from your corrections file”*). Hover a highlight to see the original word and **Undo** it. A `[word?]` is the AI's unsure guess: **Keep** drops the brackets (any left are removed when you approve). **Polish again** sends the box's current text for another pass.

**Learning from it:** on approval, every kept change is added to `corrections.md` with a **Source**: *AI* (the AI's fix, kept), *User* (your own) or *AI+User* (the AI changed it and you edited it further). AI fixes you undid are **not** learnt; they're listed in the job folder (`ai-rejected.json`, and in `corrections-diff.md`) so a wrong AI fix is never taught to the app. Each job folder also keeps `ai-polish.json` (what was sent and returned, the model and the time).

## Final AI check (optional, online)

When an AI provider is set up on the **AI & API keys** page, every SRT — every page, both caption modes, lyrics and transcribe — gets one last check before it's shown (*Final check with <provider>* in the progress steps). Switch it off on that page (**Final AI check before output**, on by default). The provider receives the caption texts with their word lists, your script (lyrics mode), the section, the language and script, and the caption rules — never audio or video. It fixes stray or unmatched quotes and apostrophes, capitals (sentence case, names, "I"; words your script writes in CAPS for emphasis stay in caps), punctuation, numbers as digits, misspellings, and line cuts that break the rules.

It can only change a word's spelling or move a cut between existing words; it answers with word numbers and corrected text, and the app rebuilds every caption's timing from the word timestamps, so the timing can't drift and no spoken word can be added or dropped. If its answer isn't valid, changes the number of words, replaces a word with a different one, or doesn't come within 60 seconds, the rule-based captions are used and one line says so. Its fixes are highlighted in the Review panel (original on hover, **Undo**); the spelling fixes still in the subtitles when you download them are added to `corrections.md` with Source *AI*.

Without a provider, an offline cleanup does what it can instead: stray quotes removed, sentence case, "I", and the number rules.

## Corrections — teach it your words

Every time you approve a transcript, the app compares Whisper's draft with what you approved, word by word on each line, and adds each fix to **`corrections.md`** (in the Mac app: `~/Library/Application Support/SRT Generator/corrections.md`; in the Windows app: `%LOCALAPPDATA%\SRT Generator\corrections.md`; in a checkout or the Windows zip folder: next to `app.py`). Then, on every page:

- **Every new draft is corrected for you.** Words and short phrases from your corrections (for the draft's script — Hinglish, Hindi (Devanagari), Urdu, English, …) are replaced as whole words, keeping capitals ("Adti" → "Aditi", "KABI" → "KABHI"). The draft says how many, e.g. *"12 words auto-corrected from your corrections file"*, and highlights them; hover one to see what Whisper wrote and **Undo** it if it's wrong. An undone fix isn't counted again.
- **Whisper is given hints.** The most frequent correct words are passed to Whisper as *hotwords* with every 30-second window (kept under Whisper's 223-token prompt limit), so it hears them right more often.
- **What gets learnt:** replacements of up to four words. Respellings are learnt word by word (`adti` → `Aditi`); a run Whisper split differently stays a phrase (`bichadhja i` → `bichhad jaaye`), so no rule like `i` → `jaaye` is ever made. Fixing the same word again raises **Times seen**; fixing it to something different keeps the newest and notes the old one. Each job folder keeps Whisper's draft (`transcript-draft.txt`), the auto-corrected draft, the approved text and the diff (`corrections-diff.md` / `.json`).

The file is plain Markdown that you can also edit in any text editor — one table per script:

```markdown
## Hinglish
| Wrong | Correct | Times seen | Last seen | Source | Notes |
|---|---|---|---|---|---|
| adti | Aditi | 5 | 2026-11-02 | User | |
| ~~main~~ | mein | 2 | 2026-11-01 | AI | struck through = switched off |
```

**Source** (since 4.3) says who made the fix: *User*, *AI* or *AI+User* (see [AI polish](#ai-polish-optional-online)). Files from before 4.3 have no Source column; they still upload and merge, and their rows count as *User*.

**Numbers kept in words** (bottom of the Corrections page) lists the expressions that stay in words when every other number becomes digits; edit it, **Save**, or **Restore defaults** (saved as `number-exceptions.txt` in the data folder).

The **Corrections** page (in the sidebar) shows them all in a searchable table: edit, switch off or delete any row, or add one by hand. Filter by **Source**, and **Switch off AI rows** turns off every fix that came from AI polish alone in one go. **Upload .md** (or drop files on the page) merges teammates' files into yours: bad rows are listed with their line numbers and the file isn't merged; when two files fix the same wrong word differently you pick one (the one seen more often is preselected). **Download corrections.md** saves the merged file to share. **Reset** clears it after saving a backup in `corrections-backups/` (merges back up the old file too).

Every job is also saved in the `jobs/` folder (the input audio, or just the sound of a video, plus vocals, the draft, the approved text, SRT, cue data and log). In the Mac app that's `~/Library/Application Support/SRT Generator/jobs/` (in Finder: Go → Go to Folder…). Delete old job folders whenever you like.

From the command line:

- `python pipeline.py <audio|video> <lyrics.txt> [vertical|horizontal] [auto|en|hi|hinglish|es|pt|fr|de|it|pa|ur] [song|drama|reels] [isolate=auto|on|off]` (the options are optional, in any order)
- `python pipeline.py --transcribe <audio|video> [language] [song|drama|reels] [isolate=auto|on|off] [script=same|hinglish|devanagari|urdu|english] [quality=fast|accurate] [topic=word,word]` prints a draft transcript, with the words to check
- `python hinglish_model.py download` downloads the Hinglish model ahead of time

## Settings

Open `config.py` in a text editor — each setting has a one-line comment. (In the Mac app, config.py sits inside the read-only app bundle; the defaults are what you get.) The useful ones: `LANGUAGE` and `TRANSCRIBE_LANGUAGE` (the language preselected in each mode), `SECTIONS` (each section's default format and isolation, and its cue rules), `MUSIC_DETECT_DB` (how loud the background must be for *Auto* isolation), `MAX_UPLOAD_MB` / `MAX_VIDEO_UPLOAD_MB`, `MODEL_SIZE` (`"small"` = faster, `"medium"` = more accurate), `STEM_FORMAT` (`"wav"` or `"mp3"`), `HINGLISH_ENGINE` (`"apex"`, or `"transliterate"` for the pre-4.2 chain), `QUALITY_DEFAULT` (`"accurate"` or `"fast"`), `LOW_CONFIDENCE` (below this word probability a Hinglish word is highlighted), `PORT`, and `HOST` (set to `"0.0.0.0"` to let teammates on your network use it from their browser). Captions (4.6): `MAX_WORDS_PER_CAPTION`, `MAX_CHARS_VERTICAL` / `MAX_CHARS_HORIZONTAL` (the defaults the UI starts from), `CAPTION_MODE_DEFAULT`, `CAPTION_END_TAIL` (how long a caption stays after its last word), `WORD_GAP_FILL_SECONDS`, `TRANSCRIBE_PAUSE_SECONDS`, `MERGE_SHORT_DEFAULT` / `MERGE_SHORT_SECONDS`, `UNMATCHED_LINE_PROBABILITY` and `AI_REVIEW_TIMEOUT_SECONDS`. `SEPARATOR_ENGINE` is `"auto"` (the GPU engine on Apple Silicon, the CPU engine on Windows).

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
| Windows app: "Getting SRT Generator ready" fails, or setup shows an error | Click **Try again**. If it keeps failing, click **Show the log** and send Aman `install.log` (in `%LOCALAPPDATA%\SRT Generator`). |
| Windows: install fails with a path or "file name too long" error | Move the folder somewhere short, like `C:\SRT-Generator`, and run `Install.bat` again. |
| A script line is missing from the SRT | It wasn't found in the audio (the warning lists it). Check it's really said, and spelled the way it's said. |
| Mac app setup window shows an error | Click **Try again**. If it keeps failing, click **Show the log** and send Aman `install.log`. |
| "Song SRT Generator (the old version) is still open" | Quit the old app (its window and Dock icon), then open SRT Generator again; it moves your data over. |
| "Port 7860 is already in use" in the Mac app | The browser version (`start.command`) is still running. Close its Terminal window and click **Try again**. |

Setup needs internet once (model downloads). After that the app works fully offline; the only network requests are the update checks (Sparkle's daily check on the Mac and WinSparkle's in the Windows app, plus the Updates page; in the Windows zip version and checkouts a check on launch, off with `CHECK_FOR_UPDATES = False` in config.py), which are skipped silently when offline.

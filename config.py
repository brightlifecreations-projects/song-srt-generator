"""App configuration. Plain constants, edit in a text editor and restart the app."""

# Whisper model size for lyric alignment: "small" is ~3x faster, "medium" is more accurate.
MODEL_SIZE = "medium"

# Default lyrics/vocal language (ISO code). Users pick per job in the UI; this is preselected
# in "I have the lyrics/script" mode.
LANGUAGE = "en"

# Preselected language in "Transcribe for me" mode. "auto" lets Whisper detect it.
TRANSCRIBE_LANGUAGE = "auto"

# Languages offered in the UI dropdown as (Whisper code, name). No Chinese/Japanese/Thai yet:
# they write without spaces and need a different line splitter.
LANGUAGES = [
    ("auto", "Auto-detect"),
    ("en", "English"),
    ("hi", "Hindi"),
    ("hinglish", "Hinglish"),
    ("es", "Spanish"),
    ("pt", "Portuguese"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pa", "Punjabi"),
    ("ur", "Urdu"),
]

# Transcribe mode's "Lyrics script" dropdown: which script the draft is written in.
# Hinglish = Hindi in Roman letters (transliterated offline from Devanagari). "English translation"
# uses Whisper's translate task; its subtitle timing is approximate (Whisper's own segments).
# With no saved choice, Hindi or Urdu audio defaults to Hinglish, anything else to "same".
SCRIPTS = [
    ("same", "Same as audio"),
    ("hinglish", "Hinglish"),
    ("devanagari", "Hindi (Devanagari)"),
    ("urdu", "Urdu script"),
    ("english", "English translation"),
]

# Dropdown choices that aren't Whisper codes, mapped to the code actually used for alignment.
# Hinglish (Hindi in Roman script) aligns best as English.
LANGUAGE_ALIASES = {"hinglish": "en"}

# Hinglish drafts (V4.2): Oriserve's Whisper-Hindi2Hinglish-Apex (Apache-2.0), converted to
# CTranslate2 int8. Downloaded once, the first time Hinglish is used (or from setup), into
# models/; fully offline after that. "transliterate" uses the V3.8 chain instead (Whisper
# medium in Hindi → Devanagari → Roman letters), which is also the fallback when the model
# can't be downloaded.
HINGLISH_ENGINE = "apex"
HINGLISH_MODEL_URL = ("https://github.com/brightlifecreations-projects/song-srt-generator/releases/"
                      "download/model-hinglish-apex-int8-v1/whisper-hinglish-apex-int8.zip")
HINGLISH_MODEL_SHA256 = "fbac3003e9a94c7ff2f209b0c501b5b0f13dd6d81ec7188c18df84cf7585b42d"
HINGLISH_MODEL_MB = 781

# Transcribe mode's speed switch for Hinglish drafts, "fast" or "accurate" (the default).
# Accurate also runs the V3.8 chain as a second opinion, highlights words where the two disagree,
# and re-transcribes short doubtful stretches on their own, keeping the better result.
QUALITY_DEFAULT = "accurate"

# Words the Hinglish model gives a probability below this are highlighted for checking.
LOW_CONFIDENCE = 0.5

# AI polish (V4.3, optional and online — the one part of the app that sends anything out): after
# the offline draft, the transcript text goes to the AI provider chosen in the Corrections tab,
# which fixes misheard words only. Off until a key is added. Provider, model and the on/off switch
# are set in the app (saved in ai-settings.json); keys live in the system key store, never here.
# Longest wait for the provider before keeping the offline draft as it is:
AI_TIMEOUT_SECONDS = 90

# Windows and source checkouts: on launch, ask GitHub whether a newer release exists and show a
# small banner if so. It fails silently when offline. The Mac app uses Sparkle instead (V4.0).
CHECK_FOR_UPDATES = True
UPDATE_REPO = "brightlifecreations-projects/song-srt-generator"

# Bind address. "127.0.0.1" = this machine only. "0.0.0.0" = teammates on your LAN can open it.
HOST = "127.0.0.1"

# Port the app runs on. Change if something else already uses it.
PORT = 7860

# Compute device for alignment: "auto" picks CUDA if available, else CPU.
DEVICE = "auto"

# Warn the user above this many minutes of audio.
MAX_MINUTES_WARN = 10

# Refuse to process audio above this many minutes.
MAX_MINUTES_HARD = 20

# Refuse uploads larger than this many megabytes (a 20-minute 48 kHz 32-bit stereo WAV is ~460 MB).
MAX_UPLOAD_MB = 500

# Same for video files (MP4, MOV, M4V, MKV, WEBM): a 20-minute 4K phone video is about 1–2 GB, and
# only its audio is kept (extracted with ffmpeg; the video itself isn't copied into jobs/).
MAX_VIDEO_UPLOAD_MB = 4096

# Forget finished jobs in the app after this many minutes (download links stop working).
# Their files stay in jobs/ on disk either way.
JOB_RETENTION_MINUTES = 120

# Default state of the "Strip [section] tags" checkbox in the UI.
STRIP_TAGS_DEFAULT = True

# Format of the downloadable isolated vocal stem: "wav" (exact) or "mp3" (small, 320 kbps).
STEM_FORMAT = "wav"

# Caption rules (V4.6, TikTok style, every section and mode). One line per caption, at most
# MAX_WORDS_PER_CAPTION words (the line breaker aims for 4–5) and about this many characters, so a
# line never wraps: 30 on vertical 9:16, 42 on horizontal 16:9. The UI can change all three per
# section (More options); these are the defaults.
MAX_WORDS_PER_CAPTION = 5
MAX_CHARS_VERTICAL = 30
MAX_CHARS_HORIZONTAL = 42

# "line" (Line by line, the TikTok rules above) or "word" (Word by word: one caption per word).
CAPTION_MODE_DEFAULT = "line"

# A caption stays this many seconds after its last word ends (never into the next caption).
CAPTION_END_TAIL = 0.1

# Word by word: a gap to the next word shorter than this is bridged, so words don't flicker.
WORD_GAP_FILL_SECONDS = 1.0

# Transcribe mode: a pause at least this long starts a new caption (sections with their own,
# shorter pause rule keep it).
TRANSCRIBE_PAUSE_SECONDS = 0.7

# "Merge very short captions" (off by default; a switch under More options): a caption shorter
# than this joins its neighbour when the result still fits the rules. Short captions are allowed.
MERGE_SHORT_DEFAULT = False
MERGE_SHORT_SECONDS = 0.5

# Script mode: a script line the aligner finds with mean word confidence below this (or squeezes
# into almost no time) isn't in the audio. It's left out of the SRT and listed in a warning.
UNMATCHED_LINE_PROBABILITY = 0.15

# Final AI check (V4.6): longest wait for the provider before the rule-based captions are used.
AI_REVIEW_TIMEOUT_SECONDS = 60

# Fallback video format when none is sent: "vertical" (9:16 Reels/TikTok) or "horizontal" (16:9 YouTube).
# Each section has its own default (SECTIONS below).
FORMAT_DEFAULT = "vertical"

# The app's three sections (tabs). Each is a preset the user can still change in the UI
# (format, isolation); the cue rules are fixed per section, on top of the character caps.
#   isolate: "on" = separate the voice from music/effects first, "off" = use the audio as is,
#            "auto" = only if background music is detected.
#   max_cue_seconds: longest a subtitle stays on screen (None = no cap; a held note can be long).
#   pause_split_seconds: a silence at least this long between two words starts a new subtitle.
#   max_words: most words in one subtitle (None = MAX_WORDS_PER_CAPTION).
SECTIONS = {
    "song": {"name": "Song", "format": "vertical", "isolate": "on",
             "max_cue_seconds": None, "pause_split_seconds": None, "max_words": None},
    "drama": {"name": "Drama", "format": "horizontal", "isolate": "on",
              "max_cue_seconds": 7.0, "pause_split_seconds": 0.6, "max_words": None},
    "reels": {"name": "Reels / YouTube", "format": "vertical", "isolate": "auto",
              "max_cue_seconds": 2.5, "pause_split_seconds": 0.35, "max_words": None},
}

# English translations (approximate timing, Whisper's own phrase spans) never stay on screen
# longer than this, in any section.
MAX_CUE_SECONDS_TRANSLATION = 7.0

# Isolation "auto": a 30-second excerpt is separated, and the file counts as having background
# music when what's left after removing the voice is within this many dB of the voice itself.
MUSIC_DETECT_DB = -18.0

# (V4.6: EARLY_APPEAR_SECONDS is gone. A caption now appears exactly on its first spoken word,
# and the Review panel, the downloaded SRT and cues.json all carry the same times.)

# Vocal separation engine: "mlx" (Apple Silicon GPU, fast), "cpu" (cross-platform audio-separator),
# or "auto" (mlx on an Apple Silicon Mac, cpu everywhere else, e.g. Windows). Leave it on "auto".
SEPARATOR_ENGINE = "auto"

# Separation model. Decided by benchmark at Milestone 2 (see DECISIONS.md).
SEPARATOR_MODEL = "htdemucs.yaml"

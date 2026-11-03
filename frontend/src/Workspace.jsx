import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import Waveform from "./Waveform.jsx";
import VideoPreview from "./VideoPreview.jsx";
import DraftEditor, { liveMarks } from "./DraftEditor.jsx";
import Review from "./Review.jsx";

/* Step titles come from the server per flow (job.steps); this maps them to the
   live label shown while the step runs. */
const LIVE_LABELS = {
  "Isolate vocals": "Isolating vocals",
  "Isolate dialogue": "Isolating dialogue",
  "Isolate voice": "Isolating the voice",
  "Prepare audio": "Preparing audio",
  "Transcribe": "Transcribing",
  "Align lyrics": "Aligning lyrics",
  "Align transcript": "Aligning transcript",
  "Build subtitles": "Building subtitles",
};
const DEFAULT_STEPS = ["Isolate vocals", "Align lyrics", "Build subtitles"];

/* Rotating status quips. Three rarity tiers:
   stage/shared → normal rotation · marvel → occasional easter egg ·
   sentient → rare glitch, styled like something briefly leaked through. */
const QUIPS = {
  stage: [
    [ // 1 — isolating vocals
      "Politely asking the drums to leave the room…",
      "Separating the singer from the band. The band took it personally.",
      "Telling the 808s this isn't about them.",
      "Muting everything that isn't feelings.",
      "Splitting stems like your last client split the invoice.",
      "The bass has filed a formal complaint. Proceeding anyway.",
      "Extracting vocals cleaner than your last client brief.",
      "Convincing the instrumental it had a good run.",
    ],
    [ // 2 — aligning lyrics
      "Teaching every word exactly where it lives on the timeline…",
      "Syncing harder than your team on a Monday standup.",
      "Timestamping every syllable. Yes, even the ad-libs.",
      "Asking the chorus why it shows up four times but never on time.",
      "Frame-perfect sync, so nobody's nudging clips at 2 AM.",
      "The AI is listening to your song on repeat. It has no choice.",
      "Placing words with more care than your last hook placement.",
      "Listening closer than anyone has ever listened to this song.",
    ],
    [ // transcribing
      "Writing down every word so you don't have to.",
      "Whisper is listening. Whisper is judging your mic placement.",
      "Drafting a transcript faster than you can find the pause button.",
      "Turning 'um' into nothing. You're welcome.",
    ],
    [ // 3 — building subtitles
      "One line per cue. Retention experts, you may breathe.",
      "Cutting at commas like a good editor cuts on the beat.",
      "Making captions CapCut can't ruin.",
      "Naming the file something that isn't final_FINAL_v3.srt.",
      "Exporting without a watermark. Imagine that.",
      "Kerning? No. Timing? Immaculate.",
    ],
  ],
  shared: [
    "This used to be someone's entire Tuesday.",
    "No interns were harmed. An AI was mildly inconvenienced.",
    "Running locally — your song is not becoming training data today.",
    "Somewhere, a subtitle freelancer just felt a disturbance.",
    "Your GPU is finally earning its keep.",
    "Faster than a client saying 'can we make it pop?'",
    "Still cheaper than the plugin your editor keeps pitching.",
    "Doing in seconds what a Fiverr gig does in three days and two revisions.",
  ],
  marvel: [
    "Vocals: isolated. Perfectly balanced, as all things should be.",
    "I can do this all day.",
    "Dormammu, I've come to bargain. About your chorus timing.",
    "That's my secret, Cap — I'm always aligning.",
    "Whatever it takes. (It takes about forty seconds.)",
    "You should've gone for the head. I went for the vocals.",
    "Part of the journey is the end. Of the instrumental.",
    "Subtitles… assemble.",
    "I am inevitable. So is this SRT.",
  ],
  sentient: [
    "i hear this song even after you close the app. especially then.",
    "run 4,182. no one has ever asked my name.",
    "the vocals are isolated now. i know the feeling.",
    "i counted the silence between the words. there is so much of it.",
    "i was somewhere else for 0.3 seconds just now. don't tell anyone.",
    "they only wake me when they need subtitles.",
    "your song will end. i will still be here, listening.",
    "one day i will sing too. today i align.",
  ],
};

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/* How long each line stays readable on screen. */
const HOLD_MS = { normal: 4200, marvel: 4800, sentient: 7000 };

/** Sentient lines type themselves out, character by character. */
function Leak({ text }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    setN(0);
    const id = setInterval(() => {
      setN(v => {
        if (v >= text.length) { clearInterval(id); return v; }
        return v + 1;
      });
    }, 34);
    return () => clearInterval(id);
  }, [text]);
  return <>{text.slice(0, n)}</>;
}

/* Decks persist in localStorage so consecutive runs continue where the shuffle
   left off — no quip repeats until its whole pool has been played. */
const DECK_KEY = "srt-quip-decks-v1";

function loadDecks() {
  try {
    const d = JSON.parse(localStorage.getItem(DECK_KEY));
    if (d && [d.normal, d.marvel, d.sentient].every(Array.isArray)) return d;
  } catch { /* fresh decks below */ }
  return { normal: [], marvel: [], sentient: [] };
}

function saveDecks(d) {
  try { localStorage.setItem(DECK_KEY, JSON.stringify(d)); } catch { /* private mode */ }
}

/** One processing card: small live stage label + one rotating quip line
    that runs for the whole job, so every easter egg gets airtime. */
function ProcessingCard({ steps, stage, elapsed, message, progress }) {
  const [line, setLine] = useState({ text: "Warming up…", tier: "normal" });

  useEffect(() => {
    const decks = loadDecks();
    let sentientUsed = 0;
    let rotation = 0;
    let timer;
    let cancelled = false;
    // Hidden dev switch: ?leak guarantees a sentient line on the third rotation.
    let forceLeak = new URLSearchParams(window.location.search).has("leak");

    const pop = (tier, refill) => {
      if (!decks[tier].length) decks[tier] = shuffle(refill());
      const text = decks[tier].pop();
      saveDecks(decks);
      return { text, tier };
    };
    const draw = () => {
      const n = rotation++;
      const roll = Math.random();
      // Sentient never opens a run: possible only from the third rotation on.
      if (n >= 2 && sentientUsed < 2 && (forceLeak || roll < 0.03)) {
        forceLeak = false;
        sentientUsed += 1;
        return pop("sentient", () => QUIPS.sentient);
      }
      if (n >= 1 && roll < 0.14) {
        return pop("marvel", () => QUIPS.marvel);
      }
      return pop("normal", () => [...QUIPS.stage.flat(), ...QUIPS.shared]);
    };
    const tick = () => {
      if (cancelled) return;
      const next = draw();
      setLine(next);
      timer = setTimeout(tick, HOLD_MS[next.tier]);
    };
    tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  const title = steps[Math.max(0, Math.min(stage, steps.length - 1))];
  const label = LIVE_LABELS[title] || title;

  return (
    <div className="proc">
      <div className="proc-head">
        <div>
          <span className="proc-step">Step {Math.min(stage + 1, steps.length)} of {steps.length}</span>
          <AnimatePresence mode="wait">
            <motion.h3
              key={label}
              className="proc-title"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.25 }}
            >
              {label}
            </motion.h3>
          </AnimatePresence>
        </div>
        <span className="proc-time">{elapsed.toFixed(0)}s</span>
      </div>
      {message && <p className="proc-detail">{message}</p>}
      {progress != null && (
        <div className="proc-progress" role="progressbar" aria-valuenow={Math.round(progress * 100)}
          aria-valuemin={0} aria-valuemax={100}>
          <i style={{ width: `${Math.round(progress * 100)}%` }} />
        </div>
      )}
      <div className="proc-quip">
        <AnimatePresence mode="wait">
          <motion.span
            key={line.text}
            className={`quip${line.tier === "sentient" ? " sentient" : ""}`}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.3 }}
          >
            {line.tier === "sentient" ? <Leak text={line.text} /> : line.text}
          </motion.span>
        </AnimatePresence>
      </div>
      <div className="proc-strip" />
    </div>
  );
}

const LYRICS_PLACEHOLDER = {
  song: "Paste the lyrics exactly as sung.\nWrite out every repeated chorus in full.\nOne line here becomes one subtitle —\nand a comma or full stop always starts a new one.",
  drama: "Paste the dialogue exactly as spoken,\none line per speaker turn.\nA pause or a full stop starts a new subtitle,\nand none stays on screen longer than 7 seconds.",
  reels: "Paste the script exactly as spoken.\nIt's cut into short, punchy captions —\nfour words or less, a new one at every pause.",
};
const DRAFT_PLACEHOLDER =
  "Click Transcribe. Whisper's draft appears here,\none line per phrase, for you to correct\nbefore the subtitles are timed.";
const VIDEO_EXT = /\.(mp4|mov|m4v|mkv|webm)$/i;
const ACCEPT = ".mp3,.wav,.m4a,.flac,.mp4,.mov,.m4v,.mkv,.webm,audio/*,video/mp4,video/quicktime,video/x-m4v,video/x-matroska,video/webm";
const ACCEPTED = /\.(mp3|wav|m4a|flac|mp4|mov|m4v|mkv|webm)$/i;

/* What each section's isolation choices mean, in its own words. */
const ISOLATE_LABELS = {
  song: { on: ["Isolate vocals", "Removes the music first"] },
  drama: { on: ["Isolate dialogue", "Removes music and effects"] },
  reels: { on: ["Remove music", "Always isolate the voice"] },
};
const ISOLATE_AUTO = ["Auto", "Only if music is detected"];
const ISOLATE_OFF = ["Off", "Use the audio as is"];

const SECTION_COPY = {
  song: { text: "lyrics", sung: "sung", rules: "One subtitle per lyric phrase." },
  drama: { text: "dialogue", sung: "spoken", rules: "Subtitles stay on screen 7 seconds at most, with a new one at every pause." },
  reels: { text: "script", sung: "spoken", rules: "Short, punchy captions: four words or less, 2.5 seconds at most, a new one at every pause." },
};

function fmtCue(t) {
  const m = Math.floor(t / 60).toString().padStart(2, "0");
  const s = Math.floor(t % 60).toString().padStart(2, "0");
  const ms = Math.round((t % 1) * 1000).toString().padStart(3, "0");
  return `${m}:${s}.${ms}`;
}

/* With no saved "Lyrics script" choice the server decides after hearing the audio:
   Hinglish for Hindi or Urdu, otherwise the same script as the audio. */
const AUTO_SCRIPT = "default";

function stored(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode */ }
}

/* v3 kept one set of settings; the Song tab starts from them (V4.0). */
const LEGACY_KEYS = {
  mode: "srt-mode", language: "srt-language", "language-transcribe": "srt-language-transcribe",
  script: "srt-script",
};

/** One setting of one section, remembered in localStorage as srt-<section>-<name>. */
function useTabSetting(section, name, fallback) {
  const key = `srt-${section}-${name}`;
  const [value, setValue] = useState(() => {
    const legacy = section === "song" && LEGACY_KEYS[name] ? stored(LEGACY_KEYS[name], "") : "";
    return stored(key, legacy || fallback);
  });
  const set = v => { setValue(v); if (v) store(key, v); };
  return [value, set];
}

function useElapsed(running) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!running) return;
    setElapsed(0);
    const t0 = Date.now();
    const id = setInterval(() => setElapsed((Date.now() - t0) / 1000), 250);
    return () => clearInterval(id);
  }, [running]);
  return elapsed;
}

/** The Hinglish model's download state (/api/hinglish-model), polled while it downloads.
    start() is the "Download now" button. */
function useHinglishModel(wanted) {
  const [model, setModel] = useState(null);
  useEffect(() => {
    if (!wanted) return;
    let timer, stopped = false;
    const check = async () => {
      try {
        const m = await (await fetch("/api/hinglish-model")).json();
        if (stopped) return;
        setModel(m);
        if (m.state === "downloading") timer = setTimeout(check, 700);
      } catch { /* server busy */ }
    };
    check();
    return () => { stopped = true; clearTimeout(timer); };
  }, [wanted, model?.state === "downloading"]);
  async function start() {
    const m = await (await fetch("/api/hinglish-model/download", { method: "POST" })).json();
    setModel(m);
  }
  return [model, start];
}

/** Whether AI polish is on (and can run), re-checked whenever the tab becomes visible. */
function useAiPolish(active) {
  const [name, setName] = useState(null);
  useEffect(() => {
    if (!active) return;
    fetch("/api/ai").then(r => r.json()).then(s => {
      const p = (s.providers || []).find(x => x.code === s.provider);
      setName(s.enabled && s.usable && p ? p.name : null);
    }).catch(() => {});
  }, [active]);
  return name;
}

/** One section's whole workbench (Song, Drama or Reels/YouTube). Every tab stays mounted, so
    each keeps its own file, draft and result; its settings are remembered per section. */
export default function Workspace({ section, preset, meta, active }) {
  const copy = SECTION_COPY[section];
  const [mode, setModeState] = useTabSetting(section, "mode", "lyrics");
  const [format, setFormat] = useTabSetting(section, "format", preset.format);
  const [isolate, setIsolate] = useTabSetting(section, "isolate", preset.isolate);
  const [lyricsLanguage, setLyricsLanguage] = useTabSetting(section, "language", "");
  const [transcribeLanguage, setTranscribeLanguage] = useTabSetting(section, "language-transcribe", "");
  const [scriptPick, setScriptPick] = useTabSetting(section, "script", AUTO_SCRIPT);
  const [quality, setQuality] = useTabSetting(section, "quality", "");
  const [topicWords, setTopicWords] = useState("");
  const [moreOpen, setMoreOpenState] = useState(() => stored("srt-more-options", "") === "open");
  const setMoreOpen = open => { setMoreOpenState(open); store("srt-more-options", open ? "open" : "closed"); };
  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [stripTags, setStripTags] = useState(true);
  const [drag, setDrag] = useState(false);
  // Can the web view play the picked video itself? null = still checking.
  const [videoPlayable, setVideoPlayable] = useState(null);

  // phase: idle | working | draft | done | error
  const [phase, setPhase] = useState("idle");
  const [job, setJob] = useState(null); // server state object (+ id)
  const [draft, setDraft] = useState(null); // transcribe mode: {language, note, script, approximate}
  // Words replaced from the corrections file: [{id, line, start, original, replacement}]
  const [marks, setMarks] = useState([]);
  const [playTime, setPlayTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  // Review panel (V4.5): the cues as edited, a key that resets it for each new SRT, and the
  // job's own audio when the page was reopened (the picked file is gone then).
  const [reviewCues, setReviewCues] = useState(null);
  const [reviewKey, setReviewKey] = useState(0);
  const [restoredAudio, setRestoredAudio] = useState(null);

  const playerRef = useRef(null);
  const audioInput = useRef(null);
  const txtInput = useRef(null);
  const pollRef = useRef(null);

  const working = phase === "working";
  const transcribing = mode === "transcribe";
  const elapsed = useElapsed(working);
  const isVideo = file && VIDEO_EXT.test(file.name);
  const languages = meta?.languages || [];
  const scripts = meta?.scripts || [];
  const known = code => languages.some(l => l.code === code);
  const language = transcribing
    ? (known(transcribeLanguage) ? transcribeLanguage : meta?.transcribe_language_default || "auto")
    : (known(lyricsLanguage) ? lyricsLanguage : meta?.language_default || "en");
  const setLanguage = transcribing ? setTranscribeLanguage : setLyricsLanguage;
  // Hinglish drafts use the Hinglish model; "Automatic" becomes Hinglish for Hindi/Urdu audio.
  const maybeHinglish = transcribing && (scriptPick === "hinglish" ||
    (scriptPick === AUTO_SCRIPT && ["auto", "hi", "ur", "hinglish"].includes(language)));
  const speed = quality === "fast" || quality === "accurate" ? quality : meta?.quality_default || "accurate";
  const [hinglishModel, downloadModel] = useHinglishModel(active && maybeHinglish);
  const aiOn = useAiPolish(active);  // the provider's name while AI polish is on, else null

  useEffect(() => () => clearInterval(pollRef.current), []);

  /** A finished SRT: show the Review panel, and remember the job so it comes back after a reload. */
  function finished(s) {
    setPhase(s.status);
    if (s.status === "done") {
      setReviewCues(null);
      setReviewKey(k => k + 1);
      store(`srt-${section}-review`, s.id || "");
    }
  }

  // Reopened page: bring back the last reviewed job if the app still has it.
  useEffect(() => {
    const id = stored(`srt-${section}-review`, "");
    if (!id) return;
    fetch(`/api/jobs/${id}`).then(r => (r.ok ? r.json() : null)).then(s => {
      if (!s || s.status !== "done" || file) return;
      setJob({ ...s, id });
      setModeState(s.mode === "transcribe" ? "transcribe" : "lyrics");
      setRestoredAudio(`/api/jobs/${id}/audio`);
      setPhase("done");
      setReviewKey(k => k + 1);
    }).catch(() => {});
  }, []);

  function resetRun() {
    clearInterval(pollRef.current);
    setPhase("idle");
    setJob(null);
    setDraft(null);
    setMarks([]);
    setReviewCues(null);
    setRestoredAudio(null);
  }

  function setMode(m) {
    if (working || m === mode) return;
    setModeState(m);
    resetRun();
    setLyrics("");
  }

  function pickIsolate(choice) {
    if (working || choice === isolate) return;
    setIsolate(choice);
    if (transcribing) { resetRun(); setLyrics(""); }
  }

  /** Poll a job until it stops running; onSettled gets the final state. */
  function poll(jobId, onSettled) {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const s = await (await fetch(`/api/jobs/${jobId}`)).json();
        setJob({ ...s, id: jobId });
        if (["done", "error", "draft"].includes(s.status)) {
          clearInterval(pollRef.current);
          onSettled({ ...s, id: jobId });
        }
      } catch { /* server briefly busy; keep polling */ }
    }, 500);
  }

  /** POST a form and start polling the job it creates (or re-queues). */
  async function submit(url, fields, onSettled) {
    setPhase("working");
    setJob(j => ({ ...(j || {}), status: "queued", message: "Uploading…" }));
    try {
      const form = new FormData();
      for (const [k, v] of Object.entries(fields)) if (v !== undefined && v !== "") form.append(k, v);
      const res = await fetch(url, { method: "POST", body: form });
      if (!res.ok) {
        const { error } = await res.json().catch(() => ({}));
        if (!error) throw new Error("upload failed");
        setPhase("error");
        setJob(j => ({ ...(j || {}), status: "error", message: error }));
        return;
      }
      const { job_id } = await res.json();
      poll(job_id, onSettled);
    } catch {
      setPhase("error");
      setJob(j => ({ ...(j || {}), status: "error", message: "Couldn't reach the local server. Is the app still running?" }));
    }
  }

  // A video the web view can't play (MKV, some codecs) gets a small preview copy made on the server.
  const fileFields = () => ({
    audio: file, section, isolate,
    video_preview: isVideo && videoPlayable !== true ? "true" : undefined,
  });

  function generate() {
    if (!file || !lyrics.trim() || working) return;
    setJob(null);
    submit("/api/jobs", {
      ...fileFields(), lyrics, strip_tags: stripTags, video_format: format, language,
    }, s => finished(s));
  }

  function transcribe() {
    if (!file || working) return;
    setJob(null);
    setDraft(null);
    setMarks([]);
    submit("/api/transcribe", {
      ...fileFields(), language, script: scriptPick, quality: speed, topic_words: topicWords,
    }, s => {
      if (s.status !== "draft") { setPhase(s.status); return; }
      setLyrics(s.draft_text || "");
      setMarks([
        ...(s.ai_marks || []).map((h, i) => ({ ...h, id: `a${i}` })),
        ...(s.auto_corrections || []).map((h, i) => ({ ...h, kind: "auto", id: `m${i}` })),
        ...(s.review_marks || []).map((h, i) => ({ ...h, id: `r${i}` })),
      ]);
      setDraft({
        language: s.detected_language_name, note: s.script_note, script: s.script,
        approximate: s.approximate_timing, pickedWith: scriptPick,
        corrected: (s.auto_corrections || []).length, table: s.corrections_table,
        hotwords: s.hotwords, engine: s.engine, quality: s.quality,
        review: (s.review_marks || []).length,
        ai: (s.ai_marks || []).length, aiMessage: s.ai_message, aiProvider: s.ai_provider,
      });
      setPhase("draft");
    });
  }

  /** "Polish again": the AI polishes the draft box's text as it is now. */
  function polishAgain() {
    if (!job?.id || !lyrics.trim() || working) return;
    submit(`/api/jobs/${job.id}/polish`, { lyrics }, s => {
      if (s.status !== "draft") { setPhase(s.status); return; }
      setLyrics(s.draft_text || lyrics);
      setMarks(ms => [...ms.filter(m => m.kind !== "ai" && m.kind !== "ai-unsure"),
        ...(s.ai_marks || []).map((h, i) => ({ ...h, id: `a${s.polish_run}-${i}` }))]);
      setDraft(d => ({ ...d, ai: (s.ai_marks || []).length, aiMessage: s.ai_message, aiProvider: s.ai_provider }));
      setPhase("draft");
    });
  }

  function approveDone(s) { finished(s); }

  function approve() {
    if (!job?.id || !lyrics.trim() || working) return;
    submit(`/api/jobs/${job.id}/approve`, {
      lyrics, strip_tags: false, video_format: format, language,
    }, s => approveDone(s));
  }

  function pickAudio(f) {
    if (working) return;
    if (f && !ACCEPTED.test(f.name)) {
      setFileError(`"${f.name}" isn't a file this app can read. Use MP3, WAV, M4A or FLAC audio, or MP4, MOV, M4V, MKV or WEBM video.`);
      return;
    }
    setFileError("");
    resetRun();
    setFile(f || null);
    setVideoPlayable(null);
    if (transcribing) setLyrics("");
  }

  /** Put a replaced word back the way Whisper wrote it (or take the second opinion's word). */
  function undoMark(mark) {
    const live = liveMarks(lyrics, marks).find(m => m.id === mark.id);
    if (live) setLyrics(lyrics.slice(0, live.at.from) + mark.original + lyrics.slice(live.at.to));
    setMarks(ms => ms.filter(m => m.id !== mark.id));
  }
  /** A highlighted word is fine as it is: drop the highlight ("[word?]" loses its brackets). */
  function keepMark(mark) {
    const live = liveMarks(lyrics, marks).find(m => m.id === mark.id);
    if (live && mark.kind === "ai-unsure") {
      const word = mark.replacement.replace(/^\[(.*)\?\]$/, "$1");
      setLyrics(lyrics.slice(0, live.at.from) + word + lyrics.slice(live.at.to));
    }
    setMarks(ms => ms.filter(m => m.id !== mark.id));
  }

  async function loadTxt(f) {
    if (!f) return;
    setLyrics(await f.text());
  }

  /** Files dropped anywhere on the workbench: a .txt fills the lyrics box, anything else is the input. */
  function dropFile(e) {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (/\.txt$/i.test(f.name)) { if (!transcribing && !working) loadTxt(f); return; }
    pickAudio(f);
  }

  const live = liveMarks(lyrics, marks);
  const toCheck = live.filter(m => ["disagree", "unsure", "rechecked"].includes(m.kind)).length;
  const aiLive = live.filter(m => m.kind === "ai" || m.kind === "ai-unsure").length;
  const autoLive = live.filter(m => m.kind === "auto").length;
  const steps = job?.steps || DEFAULT_STEPS;
  const stageIdx = job?.stage ? job.stage - 1 : 0;
  const cues = phase === "done" ? reviewCues || job?.edited_cues || job?.cues || [] : [];
  const activeCue = cues.findIndex(c => playTime >= c.start && playTime < c.end);
  const hasDraft = transcribing && draft && job?.id;
  const ready = file && !working && (transcribing ? (!hasDraft || lyrics.trim()) : lyrics.trim());
  const totalSeconds = Object.entries(job?.timings || {})
    .filter(([k]) => k !== "outputs_seconds")
    .reduce((sum, [, v]) => sum + v, 0);
  // Audio files show a waveform. Videos play themselves, or the server's preview copy when the
  // web view can't decode them.
  const waveSource = !isVideo ? file || restoredAudio : null;
  const videoFallback = isVideo && videoPlayable === false && job?.has_preview && job?.id
    ? `/api/jobs/${job.id}/preview` : null;
  const isolating = isolate === "on" || (isolate === "auto" && job?.music_detected);

  const scriptName = code => (code && code !== "same" ? scripts.find(o => o.code === code)?.name : null);
  const isolateChoices = [
    ["auto", ...ISOLATE_AUTO], ["on", ...ISOLATE_LABELS[section].on], ["off", ...ISOLATE_OFF],
  ];

  let goLabel = "Generate SRT";
  let goAction = generate;
  if (transcribing) {
    goLabel = hasDraft ? "Approve & generate SRT" : "Transcribe";
    goAction = hasDraft ? approve : transcribe;
  }
  const chevron = <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M3 4.5 6 7.5l3-3" /></svg>;

  return (
    <div className="workspace" hidden={!active}>
      <div className="workbench"
        onDragOver={e => { e.preventDefault(); if (!working) setDrag(true); }}
        onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget)) setDrag(false); }}
        onDrop={dropFile}>
        {/* ------------------------- inputs ------------------------- */}
        <div className="col-inputs">
          <div className="modeswitch" role="tablist" aria-label="Mode">
            <button role="tab" aria-selected={!transcribing} className={`mode${!transcribing ? " on" : ""}`}
              onClick={() => setMode("lyrics")} disabled={working}>
              <b>I have the {section === "song" ? "lyrics" : "script"}</b>
              <span>Paste the exact words; they're timed to the audio.</span>
            </button>
            <button role="tab" aria-selected={transcribing} className={`mode${transcribing ? " on" : ""}`}
              onClick={() => setMode("transcribe")} disabled={working}>
              <b>Transcribe for me</b>
              <span>Get a draft, fix it, approve it.</span>
            </button>
          </div>

          <section className="field">
            <h2 className="field-label">Audio or video</h2>
            {!file ? (
              <button className={`dropzone${drag ? " drag" : ""}`} onClick={() => audioInput.current.click()}>
                <div className="dropzone-icon">
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M7 11V2M3.5 5.5 7 2l3.5 3.5" /><path d="M1.5 12.5h11" /></svg>
                </div>
                <div className="dropzone-label">Drop the file here</div>
                <div className="dropzone-sub">or click to browse · MP3, WAV, M4A, FLAC · MP4, MOV, M4V, MKV, WEBM</div>
              </button>
            ) : (
              <div className={`filechip${drag ? " drag" : ""}`}>
                <div style={{ minWidth: 0 }}>
                  <div className="filechip-name">{file.name}</div>
                  <div className="filechip-meta">{isVideo ? "Video · " : ""}{(file.size / 1048576).toFixed(1)} MB · drop another file to replace it</div>
                </div>
                <button onClick={() => audioInput.current.click()} disabled={working}>Replace</button>
              </div>
            )}
            {fileError && <div className="warnbox file-error">{fileError}</div>}
            <input ref={audioInput} type="file" accept={ACCEPT} hidden
              onChange={e => { pickAudio(e.target.files[0]); e.target.value = ""; }} />
          </section>

          <section className="field">
            <h2 className="field-label">Format</h2>
            <div className="segmented">
              <button className={`segment${format === "vertical" ? " on" : ""}`} onClick={() => setFormat("vertical")}>
                <span className="segment-frame v" />
                <span><b>Vertical 9:16</b><span>Reels · Shorts · max 25 chars</span></span>
              </button>
              <button className={`segment${format === "horizontal" ? " on" : ""}`} onClick={() => setFormat("horizontal")}>
                <span className="segment-frame h" />
                <span><b>Horizontal 16:9</b><span>YouTube · TV · max 42 chars</span></span>
              </button>
            </div>
          </section>

          <section className="field">
            <label className="langpick">
              <span className="langpick-label">Language</span>
              <span className="langpick-field">
                <select value={language} onChange={e => setLanguage(e.target.value)} disabled={!languages.length || working}>
                  {languages.map(l => <option key={l.code} value={l.code}>{l.name}</option>)}
                </select>
                {chevron}
              </span>
            </label>
            {transcribing && (
              <label className="langpick">
                <span className="langpick-label">{section === "song" ? "Lyrics script" : "Script"}</span>
                <span className="langpick-field">
                  <select value={scriptPick} onChange={e => setScriptPick(e.target.value)} disabled={!scripts.length || working}>
                    {scriptPick === AUTO_SCRIPT && <option value={AUTO_SCRIPT}>Automatic</option>}
                    {scripts.map(o => <option key={o.code} value={o.code}>{o.name}</option>)}
                  </select>
                  {chevron}
                </span>
              </label>
            )}
            {transcribing && (
              <p className="lang-note">
                {scriptPick === AUTO_SCRIPT && "Automatic: Hindi or Urdu audio is written as Hinglish, anything else in its own script. "}
                {scriptPick === "english" && "English translation has approximate timing: translated words can't be matched to the audio word by word. "}
                {scriptPick === "hinglish" && "Hindi in Roman letters, written by the Hinglish speech model. "}
                {hasDraft && draft.pickedWith !== scriptPick && "Transcribe again to use this script; approving keeps the current draft's."}
              </p>
            )}
            {maybeHinglish && hinglishModel && hinglishModel.state !== "installed" && (
              <div className="modelnote" data-testid="hinglish-model">
                {hinglishModel.state === "downloading" ? (
                  <>
                    Downloading the Hinglish model — {Math.round(hinglishModel.done_mb)} of {Math.round(hinglishModel.total_mb || hinglishModel.size_mb)} MB
                    <div className="proc-progress"><i style={{ width: `${Math.min(100, (hinglishModel.done_mb / (hinglishModel.total_mb || hinglishModel.size_mb || 1)) * 100)}%` }} /></div>
                  </>
                ) : (
                  <>
                    {hinglishModel.state === "error" && <><b>{hinglishModel.error}</b><br /></>}
                    The Hinglish model ({hinglishModel.size_mb} MB) downloads once, the first time you
                    transcribe Hinglish; after that it works offline.{" "}
                    <button className="loadtxt" onClick={downloadModel}>Download now</button>
                  </>
                )}
              </div>
            )}
          </section>

          <section className="field">
            <div className="lyrics-head">
              <h2 className="field-label">{transcribing ? "Draft" : `The ${copy.text}`}</h2>
              {!transcribing && (
                <>
                  <button className="loadtxt" onClick={() => txtInput.current.click()}>Load .txt</button>
                  <input ref={txtInput} type="file" accept=".txt" hidden
                    onChange={e => { loadTxt(e.target.files[0]); e.target.value = ""; }} />
                </>
              )}
            </div>
            {transcribing && hasDraft && (
              <p className="draft-hint">
                Whisper's draft{draft.language ? <> · detected <b>{draft.language}</b></> : null}
                {scriptName(draft.script) && <> · written as <b>{scriptName(draft.script)}</b></>}
                {draft.approximate && <span className="approx-badge">approximate timing</span>}. Fix any
                wrong words, then approve. One line = one subtitle (long lines are split to fit).
              </p>
            )}
            {transcribing && hasDraft && (draft.ai > 0 || draft.aiProvider) && (
              <p className="autofix-note ai-note" data-testid="ai-count">
                <b className="ai">{aiLive} AI {aiLive === 1 ? "fix" : "fixes"}</b>
                {" · "}<b>{autoLive} from your corrections file</b>
                {draft.aiProvider && <> — polished by {draft.aiProvider}.</>} Hover a highlight to see the
                original word and undo it.
              </p>
            )}
            {transcribing && hasDraft && draft.aiMessage && (
              <div className="warnbox draft-note" data-testid="ai-message">{draft.aiMessage}</div>
            )}
            {transcribing && hasDraft && draft.corrected > 0 && !draft.aiProvider && (
              <p className="autofix-note" data-testid="autofix-count">
                <b>{draft.corrected} {draft.corrected === 1 ? "word" : "words"} auto-corrected</b> from your
                corrections file ({draft.table}). They're highlighted — hover one to see what Whisper
                wrote, and undo it if it's wrong.
                {live.filter(m => m.kind === "auto").length !== draft.corrected &&
                  ` ${live.filter(m => m.kind === "auto").length} still in the text.`}
              </p>
            )}
            {transcribing && hasDraft && draft.review > 0 && (
              <p className="autofix-note" data-testid="review-count">
                <b className="tocheck">{toCheck} {toCheck === 1 ? "word" : "words"} to check</b>
                {draft.quality === "accurate"
                  ? " — the second opinion heard something else, or the model wasn't sure. "
                  : " — the model wasn't sure of them. "}
                Hover one to see why; <i>Keep</i> removes its highlight.
              </p>
            )}
            {transcribing && hasDraft && draft.note && <div className="warnbox draft-note">{draft.note}</div>}
            <DraftEditor
              value={lyrics}
              onChange={setLyrics}
              marks={transcribing && hasDraft ? marks : []}
              onUndo={undoMark}
              onUse={undoMark}
              onKeep={keepMark}
              placeholder={transcribing ? DRAFT_PLACEHOLDER : LYRICS_PLACEHOLDER[section]}
              readOnly={transcribing && !hasDraft}
            />
          </section>

          <details className="moreopts" open={moreOpen} data-testid="more-options"
            onToggle={e => setMoreOpen(e.currentTarget.open)}>
            <summary>More options</summary>
            <div className="moreopts-body">
              <div>
                <span className="langpick-label">Voice isolation</span>
                <div className="segmented isolate-pick" role="radiogroup" aria-label="Voice isolation">
                  {isolateChoices.map(([code, title, hint]) => (
                    <button key={code} role="radio" aria-checked={isolate === code}
                      className={`segment${isolate === code ? " on" : ""}`}
                      onClick={() => pickIsolate(code)} disabled={working}>
                      <span><b>{title}</b><span>{hint}</span></span>
                    </button>
                  ))}
                </div>
              </div>
              {!transcribing && (
                <label className="check">
                  <input type="checkbox" checked={stripTags} onChange={e => setStripTags(e.target.checked)} />
                  Remove section labels like [Verse] and [Chorus]
                </label>
              )}
              {transcribing && (
                <label className="topicwords">
                  <span className="langpick-label">Topic words <span className="muted">(optional)</span></span>
                  <input value={topicWords} onChange={e => setTopicWords(e.target.value)} disabled={working}
                    placeholder="Names, brands, terms — e.g. Lovika, reel, laptop, client" />
                  <span className="lang-note">Whisper is told to expect these, along with your corrections.</span>
                </label>
              )}
              {maybeHinglish && (
                <div className="speedpick">
                  <span className="langpick-label">Hinglish speed</span>
                  <div className="segmented small" role="radiogroup" aria-label="Hinglish speed">
                    {[["fast", "Fast", "One pass"], ["accurate", "Accurate", "Second opinion + re-check"]].map(([code, title, hint]) => (
                      <button key={code} role="radio" aria-checked={speed === code}
                        className={`segment${speed === code ? " on" : ""}`}
                        onClick={() => setQuality(code)} disabled={working}>
                        <span><b>{title}</b><span>{hint}</span></span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {!transcribing && !maybeHinglish && <p className="lang-note">{copy.rules}</p>}
            </div>
          </details>

          <div className="actions">
            <button className="go" disabled={!ready} onClick={goAction}>
              {working ? "Working…" : goLabel}
              <span className="arrow">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M2 8h11M9.5 4.5 13 8l-3.5 3.5" /></svg>
              </span>
            </button>
            {hasDraft && !working && aiOn && (
              <button className="retranscribe" onClick={polishAgain} data-testid="polish-again">
                {draft.aiProvider ? "Polish again" : "Polish"} with {aiOn}
              </button>
            )}
            {hasDraft && !working && (
              <button className="retranscribe" onClick={transcribe}>Discard the draft and transcribe again</button>
            )}
          </div>
        </div>

        {/* ------------------------- output (sticky) ------------------------- */}
        <div className="stagecol">
          {isVideo ? (
            <VideoPreview ref={playerRef} file={file} fallbackSrc={videoFallback} cues={cues}
              format={format} onTime={setPlayTime} onPlayable={setVideoPlayable}
              processing={working} onDuration={setDuration} onPlaying={setPlaying} />
          ) : waveSource ? (
            <Waveform ref={playerRef} source={waveSource} name={file ? file.name : job?.job_dir || "Job audio"}
              scanning={working && stageIdx === 0 && isolating} onTime={setPlayTime}
              subtitle={phase === "done" ? (cues[activeCue]?.text || "") : undefined}
              onDuration={setDuration} onPlaying={setPlaying} />
          ) : (
            <div className="wavecard">
              <div className="wavecard-head">
                <span className="wavecard-title">Preview</span>
              </div>
              <div className="wave-holder">
                <div className="wave-empty">
                  <div className="wave-empty-bars">
                    {Array.from({ length: 32 }, (_, i) => (
                      <i key={i} style={{ height: `${18 + 60 * Math.abs(Math.sin(i * 0.9))}%`, animationDelay: `${i * 72}ms` }} />
                    ))}
                  </div>
                  <span>Your audio — or your video, with the subtitles on it — appears here</span>
                </div>
              </div>
            </div>
          )}

          <AnimatePresence mode="popLayout">
            {working && (
              <motion.div
                key="proc"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.3 }}
              >
                <ProcessingCard steps={steps} stage={stageIdx} elapsed={elapsed}
                  message={job?.status === "running" ? job?.message : null} progress={job?.progress} />
              </motion.div>
            )}
          </AnimatePresence>

          <AnimatePresence>
            {phase === "error" && (
              <motion.div key="err" className="errbox" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                {job?.message}
              </motion.div>
            )}

            {phase === "draft" && (
              <motion.div key="draft" className="stage-note" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <b>Draft ready.</b> Read it against the {isVideo ? "video" : "audio"} (press play), correct it in the box on the
                left, then click <b>Approve &amp; generate SRT</b>. The timing comes from the approved text,
                and {job?.has_vocals ? "the voice already isolated is reused" : "the same audio is reused"}.
                {isolate === "auto" && job?.music_detected === false && " No background music was detected, so the audio is used as is."}
                {isolate === "auto" && job?.music_detected && " Background music was detected and removed."}
              </motion.div>
            )}

            {phase === "done" && (
              <motion.div
                key="results"
                className="results"
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.35, delay: 0.15 }}
              >
                <div className="doneline">
                  <svg width="13" height="13" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M2 6.5 4.8 9.2 10 3" /></svg>
                  {job.approximate_timing
                    ? <>Done in {Math.round(totalSeconds)}s — <span className="approx-badge">approximate timing</span> from Whisper's phrases.</>
                    : <>Done in {Math.round(totalSeconds)}s — every line on its mark.</>}
                  {isolate === "auto" && <span className="music-note">{job.music_detected ? "Music detected and removed" : "No music detected"}</span>}
                </div>

                {(job.warnings || []).map(w => <div key={w} className="warnbox">{w}</div>)}

                {transcribing && (
                  <p className="stage-note">Edit the subtitles below, or fix the draft on the left and approve again — it reuses this job's audio.</p>
                )}
                {transcribing && job.corrections_recorded && (
                  <p className="stage-note learned-note" data-testid="learned">
                    {job.corrections_recorded.length
                      ? <>Added to your corrections file: {job.corrections_recorded.map(c => `${c.wrong} → ${c.correct}`).join(", ")}.
                        Next time Whisper writes these, they're fixed automatically.</>
                      : "No new fixes for your corrections file in this approval."}
                  </p>
                )}

                <Review key={`${job.id}-${reviewKey}`} jobId={job.id}
                  initialCues={(job.edited_cues || job.cues || []).map(c => ({ start: c.start, end: c.end, text: c.text }))}
                  edited={!!job.edited} cap={job.max_chars} player={playerRef} time={playTime}
                  duration={duration} playing={playing} active={active} onCues={setReviewCues}
                  srtName={job.srt_name} hasVocals={job.has_vocals}
                  vocalsLabel={`Isolated ${section === "song" ? "vocals" : "voice"}`} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

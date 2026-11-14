import { useEffect, useMemo, useRef, useState } from "react";

/* History (V4.9): every job, newest first. Open restores it in its section's Review panel; Re-run
   starts it again with the same (or changed) settings; View log, Show in Finder/Explorer, Report a
   problem, Delete. The job clean-up and log level settings live here too. */

const SECTION_NAMES = { song: "Song", drama: "Drama", reels: "Reels / YouTube" };
const STATUS = {
  verified: ["Verified", "ok"], check: [null, "check"], error: ["Error", "bad"], done: ["Done", ""],
  draft: ["Draft", ""], running: ["Running", ""], unknown: ["Unfinished", ""],
};
const KEEP = [[0, "Never delete"], [7, "7 days"], [30, "30 days"], [90, "90 days"], [365, "1 year"]];
const isWin = () => navigator.platform?.startsWith("Win");

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function fmtDuration(s) {
  if (!s) return "";
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.round(s % 60)).padStart(2, "0")}`;
}
const LANGS = { en: "English", hi: "Hindi", hinglish: "Hinglish", auto: "Auto", es: "Spanish", pt: "Portuguese",
  fr: "French", de: "German", it: "Italian", pa: "Punjabi", ur: "Urdu" };

function LogViewer({ name, onClose }) {
  const [text, setText] = useState(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    fetch(`/api/history/${name}/log`).then(r => r.json()).then(b => setText(b.log ?? b.error ?? "")).catch(() => setText("Couldn't load the log."));
  }, [name]);
  useEffect(() => {
    const onKey = e => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const lines = useMemo(() => (text || "").split("\n"), [text]);
  const needle = q.trim().toLowerCase();
  const shown = needle ? lines.filter(l => l.toLowerCase().includes(needle)) : lines;
  const mark = line => {
    if (!needle) return line;
    const i = line.toLowerCase().indexOf(needle);
    return i < 0 ? line : <>{line.slice(0, i)}<mark>{line.slice(i, i + needle.length)}</mark>{line.slice(i + needle.length)}</>;
  };
  return (
    <div className="modal-back" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal log-viewer" role="dialog" aria-modal="true" aria-label="Job log" data-testid="log-viewer">
        <div className="modal-head">
          <h2>Job log <span className="muted mono small">{name}</span></h2>
          <button className="modal-x" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="log-search">
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search the log (e.g. Script line, ERROR, Command)" autoFocus data-testid="log-search" />
          <span className="muted">{text == null ? "Loading…" : needle ? `${shown.length} of ${lines.length} lines` : `${lines.length} lines`}</span>
        </div>
        <pre className="log-text" data-testid="log-text">{shown.map((l, i) => <div key={i} className={/ (ERROR|CRITICAL) /.test(l) ? "log-err" : / WARNING /.test(l) ? "log-warn" : ""}>{mark(l)}</div>)}</pre>
      </div>
    </div>
  );
}

function RerunDialog({ job, onRun, onClose }) {
  const [s, setS] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetch(`/api/history/${job.name}/settings`).then(async r => {
      const b = await r.json();
      if (!r.ok) setError(b.error || "Couldn't read this job's settings."); else setS(b);
    }).catch(() => setError("Couldn't reach the app."));
  }, [job.name]);
  const summary = s && [SECTION_NAMES[s.section], s.mode === "transcribe" ? "Transcribe" : "Lyrics/script",
    LANGS[s.language] || s.language, s.format, s.caption_mode === "word" ? "word by word" : "line by line",
    s.isolate && `isolation ${s.isolate}`].filter(Boolean).join(" · ");
  return (
    <div className="modal-back" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Re-run" data-testid="rerun-dialog">
        <div className="modal-head"><h2>Re-run “{job.file}”</h2><button className="modal-x" onClick={onClose} aria-label="Close">×</button></div>
        {error && <div className="warnbox">{error}</div>}
        {s && !s.can_rerun && <div className="warnbox">This job's audio is gone, so it can't be run again.</div>}
        {s && s.can_rerun && (
          <>
            <p className="modal-sub">Same settings: <b>{summary}</b>{s.mode === "lyrics" && s.text ? `, with its ${s.section === "song" ? "lyrics" : "script"} (${s.text.split("\n").length} lines)` : ""}.</p>
            <p className="muted modal-sub">“Change settings first” opens it in the {SECTION_NAMES[s.section]} page with everything filled in, so you can change anything before you start.</p>
            <div className="modal-actions">
              <button className="loadtxt" onClick={() => onRun(s, false)} data-testid="rerun-change">Change settings first</button>
              <button className="go small" onClick={() => onRun(s, true)} data-testid="rerun-now">Re-run now</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function History({ active, onOpen, onRerun, onReport }) {
  const [data, setData] = useState(null);
  const [q, setQ] = useState("");
  const [section, setSection] = useState("");
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [settings, setSettings] = useState(null);
  const [viewing, setViewing] = useState(null);
  const [rerun, setRerun] = useState(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const timer = useRef(null);

  async function load() {
    const p = new URLSearchParams({ q, section, status, date_from: from, date_to: to });
    try {
      const r = await fetch(`/api/history?${p}`);
      setData(await r.json());
    } catch { setNotice("Couldn't load the history."); }
  }
  useEffect(() => {
    if (!active) return;
    clearTimeout(timer.current);
    timer.current = setTimeout(load, q ? 250 : 0);
    return () => clearTimeout(timer.current);
  }, [active, q, section, status, from, to]);
  useEffect(() => {
    if (active) fetch("/api/settings").then(r => r.json()).then(setSettings).catch(() => {});
  }, [active]);

  async function saveSetting(patch) {
    const r = await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
    setSettings(await r.json());
  }
  async function clearLogs() {
    if (!window.confirm("Clear the app log? Each job's own log stays with the job (delete a job to remove it).")) return;
    const r = await fetch("/api/logs/clear", { method: "POST" });
    setSettings(await r.json());
    setNotice("App log cleared.");
  }
  async function open(job) {
    setBusy(job.name);
    try {
      const r = await fetch(`/api/history/${job.name}/open`, { method: "POST" });
      const b = await r.json();
      if (!r.ok) { setNotice(b.error || "Couldn't open this job."); return; }
      onOpen(b);
    } finally { setBusy(""); }
  }
  async function remove(job) {
    if (!window.confirm(`Delete “${job.file}” from ${fmtDate(job.created)}? Its captions, edits, audio and log are removed from this computer.`)) return;
    const r = await fetch(`/api/history/${job.name}`, { method: "DELETE" });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) { setNotice(b.error || "Couldn't delete it."); return; }
    setNotice(`Deleted “${job.file}”.`);
    load();
  }
  async function reveal(job) {
    await fetch(`/api/history/${job.name}/reveal`, { method: "POST" }).catch(() => {});
  }

  const jobs = data?.jobs || [];
  const keep = settings?.job_keep_days ?? 0;
  return (
    <div className="history" hidden={!active} data-testid="history">
      <div className="card history-settings">
        <div className="history-setting">
          <span className="langpick-label">Delete jobs older than</span>
          <select value={KEEP.some(([d]) => d === keep) ? keep : "custom"} data-testid="keep-days"
            onChange={e => e.target.value !== "custom" && saveSetting({ job_keep_days: +e.target.value })}>
            {KEEP.map(([d, label]) => <option key={d} value={d}>{label}</option>)}
            {!KEEP.some(([d]) => d === keep) && <option value="custom">{keep} days</option>}
          </select>
          <span className="muted small">{keep ? `Checked when the app starts (JOB_KEEP_DAYS = ${keep}).` : "Jobs stay until you delete them (JOB_KEEP_DAYS = 0)."}</span>
        </div>
        <div className="history-setting">
          <span className="langpick-label">Log level</span>
          <div className="segmented small" role="radiogroup" aria-label="Log level">
            {[["normal", "Normal"], ["detailed", "Detailed"]].map(([code, label]) => (
              <button key={code} role="radio" aria-checked={settings?.log_level === code} data-testid={`log-${code}`}
                className={`segment${settings?.log_level === code ? " on" : ""}`} onClick={() => saveSetting({ log_level: code })}>
                <span><b>{label}</b></span>
              </button>
            ))}
          </div>
          <span className="muted small">Detailed keeps every step (word timings, Whisper's raw output, AI requests) — best for problem reports.</span>
        </div>
        <div className="history-setting">
          <button className="loadtxt" onClick={clearLogs} data-testid="clear-logs">Clear logs</button>
          <span className="muted small">App log: {settings ? `${Math.round((settings.app_log_bytes || 0) / 1024)} KB` : "…"}</span>
        </div>
      </div>

      <div className="history-filters">
        <input className="history-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Search file names and caption text"
          data-testid="history-search" aria-label="Search" />
        <select value={section} onChange={e => setSection(e.target.value)} aria-label="Section" data-testid="history-section">
          <option value="">All sections</option>
          {Object.entries(SECTION_NAMES).map(([c, n]) => <option key={c} value={c}>{n}</option>)}
        </select>
        <select value={status} onChange={e => setStatus(e.target.value)} aria-label="Status" data-testid="history-status">
          <option value="">Any status</option>
          <option value="verified">Verified</option>
          <option value="check">To check</option>
          <option value="done">Done</option>
          <option value="error">Error</option>
        </select>
        <label className="history-date">From <input type="date" value={from} onChange={e => setFrom(e.target.value)} data-testid="history-from" /></label>
        <label className="history-date">To <input type="date" value={to} onChange={e => setTo(e.target.value)} data-testid="history-to" /></label>
      </div>
      {notice && <p className="stage-note" data-testid="history-notice">{notice}</p>}
      <p className="muted small">{data ? `${jobs.length} of ${data.total} jobs` : "Loading…"}</p>

      <div className="history-list" data-testid="history-list">
        {jobs.map(job => {
          const [label, tone] = STATUS[job.running ? "running" : job.status] || STATUS.unknown;
          return (
            <div key={job.name} className="history-row" data-testid="history-row" data-name={job.name}>
              <div className="history-thumb">
                {job.thumb ? <img src={`/api/history/${job.name}/thumb`} alt="" loading="lazy" />
                  : <span className="history-thumb-icon">{job.has_video ? "▶" : "♪"}</span>}
              </div>
              <div className="history-main">
                <div className="history-file" title={job.file}>{job.file}</div>
                <div className="history-meta">
                  {fmtDate(job.created)} · {SECTION_NAMES[job.section] || job.section} · {job.mode === "transcribe" ? "Transcribe" : "Lyrics/script"}
                  {job.language ? ` · ${LANGS[job.language] || job.language}` : ""}{job.duration ? ` · ${fmtDuration(job.duration)}` : ""}
                  {` · ${job.captions} caption${job.captions === 1 ? "" : "s"}`}
                </div>
                {job.error && <div className="history-error">{job.error}</div>}
              </div>
              <div className="history-badges">
                <span className={`hstatus ${tone}`} data-testid="history-status-badge">{label || `${job.to_check} to check`}</span>
                {job.edited && <span className="edited-badge" data-testid="history-edited">Edited</span>}
              </div>
              <div className="history-actions">
                <button className="go small" onClick={() => open(job)} disabled={!job.can_open || busy === job.name}
                  title={job.can_open ? "Open in the Review panel" : "This job has no captions to open"} data-testid="history-open">
                  {busy === job.name ? "Opening…" : "Open"}</button>
                <button className="loadtxt" onClick={() => setRerun(job)} data-testid="history-rerun">Re-run</button>
                <button className="loadtxt" onClick={() => setViewing(job.name)} data-testid="history-log">View log</button>
                <button className="loadtxt" onClick={() => reveal(job)} data-testid="history-reveal">Show in {isWin() ? "Explorer" : "Finder"}</button>
                <button className="loadtxt" onClick={() => onReport({ job: job.name, label: job.file })} data-testid="history-report">Report a problem</button>
                <button className="loadtxt danger" onClick={() => remove(job)} disabled={job.running} data-testid="history-delete">Delete</button>
              </div>
            </div>
          );
        })}
        {data && !jobs.length && <p className="muted history-empty">{data.total ? "No jobs match." : "No jobs yet — your SRTs will be listed here."}</p>}
      </div>
      {viewing && <LogViewer name={viewing} onClose={() => setViewing(null)} />}
      {rerun && <RerunDialog job={rerun} onClose={() => setRerun(null)} onRun={(s, now) => { setRerun(null); onRerun(s, now); }} />}
    </div>
  );
}

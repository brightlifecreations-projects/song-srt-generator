import { useEffect, useRef, useState } from "react";

/* Report a problem (V4.9). Builds a zip in Downloads with what the checkboxes say, shows exactly
   what's inside, then opens a pre-filled email for the user to send themselves (and offers Copy
   report text / Show zip). Nothing is ever sent by the app. */

const CATEGORIES = [
  ["missing", "Missing captions"],
  ["words", "Wrong words"],
  ["timing", "Wrong timing"],
  ["crash", "Crash or error"],
  ["startup", "App won't start or update"],
  ["other", "Other"],
];

function fmtTime(t) {
  if (t == null || !isFinite(t)) return "";
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(1).padStart(4, "0");
  return `${m}:${s}`;
}
function parseTime(text) {
  const v = String(text || "").trim();
  if (!v) return null;
  const m = v.match(/^(\d+):(\d+(?:\.\d+)?)$/);
  if (m) return +m[1] * 60 + +m[2];
  return isFinite(+v) ? +v : null;
}
const kb = n => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* WebKit without permission */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

export default function ReportDialog({ job, jobLabel, time, category: initialCategory, onClose }) {
  const [category, setCategory] = useState(initialCategory || (job ? "missing" : "startup"));
  const [description, setDescription] = useState("");
  const [at, setAt] = useState(job && time != null ? fmtTime(time) : "");
  const [include, setInclude] = useState({ logs: !!job, text: !!job, app_log: true, audio: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [made, setMade] = useState(null);
  const [note, setNote] = useState("");
  const first = useRef(null);

  useEffect(() => { first.current?.focus(); }, []);
  useEffect(() => {
    const onKey = e => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/report", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, description, time: parseTime(at), job: job || null, include }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { setError(body.error || "Couldn't create the report."); return; }
      setMade(body);
      openEmail(body);
    } catch {
      setError("Couldn't reach the app to create the report.");
    } finally {
      setBusy(false);
    }
  }
  async function openEmail(r = made) {
    const res = await fetch("/api/report/open", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: r.name, open: "email", mailto: r.mailto }),
    }).catch(() => null);
    setNote(res?.ok ? "Your email app opened with the message filled in. Attach the zip, then send it." :
      "Couldn't open your email app — use Copy report text and attach the zip yourself.");
  }
  async function showZip() {
    await fetch("/api/report/open", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: made.name, open: "zip" }),
    }).catch(() => null);
  }
  async function copy() {
    const ok = await copyText(`To: ${made.email.to}\nSubject: ${made.email.subject}\n\n${made.email.body}`);
    setNote(ok ? "Report text copied." : "Couldn't copy — select the text below and copy it.");
  }

  const box = (key, label, hint) => (
    <label className="check report-include">
      <input type="checkbox" checked={include[key]} disabled={!job && key !== "app_log"}
        onChange={e => setInclude(v => ({ ...v, [key]: e.target.checked }))} data-testid={`report-include-${key}`} />
      <span>{label}{hint && <small className="muted"> — {hint}</small>}</span>
    </label>
  );

  return (
    <div className="modal-back" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal report-dialog" role="dialog" aria-modal="true" aria-labelledby="report-title" data-testid="report-dialog">
        <div className="modal-head">
          <h2 id="report-title">Report a problem</h2>
          <button className="modal-x" onClick={onClose} aria-label="Close">×</button>
        </div>
        {!made ? (
          <>
            <p className="muted modal-sub">{job ? <>About <b>{jobLabel || job}</b>.</> : "A general problem, not about one job."}{" "}
              The app makes a zip in your Downloads folder and opens an email for you to send — nothing is sent automatically.</p>
            <label className="form-row">
              <span className="langpick-label">What's wrong?</span>
              <select ref={first} value={category} onChange={e => setCategory(e.target.value)} data-testid="report-category">
                {CATEGORIES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
              </select>
            </label>
            <label className="form-row">
              <span className="langpick-label">Describe it</span>
              <textarea rows={4} value={description} onChange={e => setDescription(e.target.value)} data-testid="report-description"
                placeholder="What did you expect, and what happened instead?" />
            </label>
            {job && (
              <label className="form-row report-time">
                <span className="langpick-label">Where in the video <span className="muted">(optional, m:ss)</span></span>
                <input value={at} onChange={e => setAt(e.target.value)} placeholder="e.g. 1:23.4" data-testid="report-time" />
              </label>
            )}
            <fieldset className="report-includes">
              <legend className="langpick-label">Include</legend>
              {box("logs", "The full job log and job.json")}
              {box("text", "Captions, transcript and script text")}
              {box("app_log", "The app log from the last 24 hours")}
              {box("audio", "The audio", "it contains the recording, so it's off unless you tick it")}
              <p className="muted report-never">API keys are never included. Your home folder's name is replaced with “~”.</p>
            </fieldset>
            {error && <div className="warnbox">{error}</div>}
            <div className="modal-actions">
              <button className="loadtxt" onClick={onClose}>Cancel</button>
              <button className="go small" onClick={create} disabled={busy} data-testid="report-create">
                {busy ? "Creating…" : "Create report"}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="modal-sub"><b>{made.name}</b> is in your Downloads folder ({kb(made.bytes)}). It contains exactly:</p>
            <ul className="report-files" data-testid="report-files">
              {made.files.map(f => <li key={f.name}><span className="mono">{f.name}</span><span className="muted">{kb(f.bytes)}</span></li>)}
            </ul>
            <pre className="report-text" data-testid="report-text">{`To: ${made.email.to}\nSubject: ${made.email.subject}\n\n${made.email.body}`}</pre>
            {note && <p className="stage-note" data-testid="report-note">{note}</p>}
            <div className="modal-actions">
              <button className="loadtxt" onClick={copy} data-testid="report-copy">Copy report text</button>
              <button className="loadtxt" onClick={showZip} data-testid="report-show">Show zip in {navigator.platform?.startsWith("Win") ? "Explorer" : "Finder"}</button>
              <button className="loadtxt" onClick={() => openEmail()} data-testid="report-email">Open the email again</button>
              <button className="go small" onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

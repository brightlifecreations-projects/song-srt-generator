import { useEffect, useState } from "react";

const post = (url, body, method = "POST") =>
  fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });

/** Release notes from our own appcast come as HTML; keep the formatting, drop anything active. */
function safeHtml(html) {
  return String(html || "")
    .replace(/<(script|style|iframe|object|embed)[\s\S]*?<\/\1>/gi, "")
    .replace(/\son\w+="[^"]*"/gi, "")
    .replace(/href="javascript:[^"]*"/gi, 'href="#"');
}

function ago(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString();
}

/** The Updates page (V4.5). Mac: Sparkle checks, downloads, installs and relaunches; Windows and
    checkouts: the latest GitHub release, downloaded from its page. onChange keeps the sidebar dot right. */
export default function Updates({ active, onChange }) {
  const [state, setState] = useState(null);
  const apply = s => { setState(s); onChange?.(s); };
  const refresh = () => fetch("/api/updates").then(r => r.json()).then(apply).catch(() => {});

  useEffect(() => { if (active) refresh(); }, [active]);
  // Follow a check or an install while it runs.
  useEffect(() => {
    if (!active || !state || !["checking", "downloading", "extracting", "installing"].includes(state.status)) return;
    const t = setTimeout(refresh, 700);
    return () => clearTimeout(t);
  }, [active, state]);

  if (!state) return <div className="updatespage" hidden={!active} />;
  const busy = ["checking", "downloading", "extracting", "installing"].includes(state.status);
  const mac = state.mode === "sparkle";
  const native = mac || state.mode === "winsparkle";  // the apps: Sparkle (Mac) or WinSparkle (Windows)
  const a = state.available;

  return (
    <div className="updatespage" hidden={!active} data-testid="updates">
      <section className="card upd-hero">
        <div>
          <span className="langpick-label">This version</span>
          <div className="upd-version" data-testid="current-version">{state.version}</div>
          <p className="card-hint">Last checked: {ago(state.last_checked)}</p>
        </div>
        <button className="go upd-check" disabled={busy} data-testid="check-updates"
          onClick={async () => apply(await (await post("/api/updates/check")).json())}>
          {state.status === "checking" ? "Checking…" : "Check for updates"}
        </button>
      </section>

      {state.status === "uptodate" && (
        <p className="upd-status ok" data-testid="up-to-date">You’re up to date ({state.version}).</p>
      )}
      {state.status === "error" && <div className="errbox" data-testid="update-error">{state.error}</div>}
      {a && ["available", "downloading", "extracting", "installing", "ready"].includes(state.status) && (
        <section className="card upd-available" data-testid="update-available">
          <div className="card-row">
            <div>
              <h2 className="card-title">Version {a.version} is available</h2>
              <p className="card-hint">You have {state.version}.</p>
            </div>
            {native ? (
              <button className="go small" disabled={busy} data-testid="update-now"
                onClick={async () => apply(await (await post("/api/updates/install")).json())}>
                {state.status === "downloading" ? "Downloading…" : state.status === "extracting" ? "Preparing…"
                  : state.status === "installing" ? "Installing…" : "Update now"}
              </button>
            ) : (
              <a className="go small" href={a.url} target="_blank" rel="noreferrer" data-testid="download-update">Download update</a>
            )}
          </div>
          {state.status === "downloading" && state.progress != null && (
            <div className="proc-progress"><i style={{ width: `${Math.round(state.progress * 100)}%` }} /></div>
          )}
          {state.status === "installing" && <p className="card-hint">Installing — the app will quit and reopen on its own.</p>}
          {native
            ? <div className="upd-notes" dangerouslySetInnerHTML={{ __html: safeHtml(a.notes_html) }} />
            : <pre className="upd-notes plain">{a.notes}</pre>}
        </section>
      )}

      <section className="card">
        <div className="card-row">
          <div>
            <b>Check automatically</b>
            <p className="card-hint">Once a day. A dot appears on Updates when a new version is out.</p>
          </div>
          <label className="switch">
            <input type="checkbox" checked={!!state.auto_check} aria-label="Check automatically"
              onChange={async e => apply(await (await post("/api/updates/settings", { auto_check: e.target.checked }, "PUT")).json())} />
            <span className="switch-track" /> {state.auto_check ? "On" : "Off"}
          </label>
        </div>
        <div className="card-row sub">
          <div>
            <b>Download and install automatically</b>
            <p className="card-hint">{mac ? "New versions download in the background and install the next time the app quits."
              : native ? "When the daily check finds a new version, it downloads, installs and reopens the app on its own."
              : "In the installed app only — this copy updates from the release page."}</p>
          </div>
          <label className="switch">
            <input type="checkbox" checked={!!state.auto_install} disabled={!native} aria-label="Download and install automatically"
              onChange={async e => apply(await (await post("/api/updates/settings", { auto_install: e.target.checked }, "PUT")).json())} />
            <span className="switch-track" /> {state.auto_install ? "On" : "Off"}
          </label>
        </div>
      </section>
    </div>
  );
}

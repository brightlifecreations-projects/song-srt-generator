import { useEffect, useRef, useState } from "react";

const today = () => new Date().toISOString().slice(0, 10);

/** The Corrections tab: everything in corrections.md (Whisper's mistakes and their fixes, one
    table per script). Search, edit, disable, delete, add by hand; upload teammates' files and
    merge them (choosing between conflicting fixes); download the merged file; reset with a backup. */
export default function Corrections({ active }) {
  const [state, setState] = useState(null);   // {tables, path, backups, suggested_tables}
  const [loadError, setLoadError] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [saved, setSaved] = useState("");
  const [query, setQuery] = useState("");
  const [upload, setUpload] = useState(null); // {files, plan, choices}
  const [drag, setDrag] = useState(false);
  const [adding, setAdding] = useState({ table: "Hinglish", wrong: "", correct: "" });
  const [notice, setNotice] = useState("");
  const fileInput = useRef(null);
  const saveTimer = useRef(null);

  async function load() {
    const res = await fetch("/api/corrections");
    const body = await res.json();
    if (!res.ok) { setLoadError(body); setState(null); return; }
    setLoadError(null);
    setState(body);
  }
  useEffect(() => { if (active) load(); }, [active]);

  /** Save the whole table (debounced while typing). */
  function persist(next, immediate = false) {
    setState(s => ({ ...s, tables: next }));
    clearTimeout(saveTimer.current);
    const run = async () => {
      const res = await fetch("/api/corrections", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tables: next }),
      });
      const body = await res.json();
      if (!res.ok) { setSaveError(body.errors || [body.error]); return; }
      setSaveError(null);
      setSaved(`Saved ${new Date().toLocaleTimeString()}`);
    };
    if (immediate) run(); else saveTimer.current = setTimeout(run, 700);
  }

  function editRow(tableName, key, patch, immediate) {
    const next = state.tables.map(t => t.name !== tableName ? t : {
      ...t, rows: t.rows.map(r => r.key !== key ? r : { ...r, ...patch }),
    });
    persist(next, immediate);
  }

  function deleteRow(tableName, key) {
    const next = state.tables
      .map(t => t.name !== tableName ? t : { ...t, rows: t.rows.filter(r => r.key !== key) })
      .filter(t => t.rows.length);
    persist(next, true);
  }

  function addRow(e) {
    e.preventDefault();
    const table = adding.table.trim();
    if (!table || !adding.wrong.trim() || !adding.correct.trim()) return;
    const row = { key: `new-${Date.now()}`, wrong: adding.wrong.trim(), correct: adding.correct.trim(),
                  count: 1, last_seen: today(), notes: "added by hand", disabled: false };
    const exists = state.tables.some(t => t.name === table);
    const next = exists
      ? state.tables.map(t => t.name === table ? { ...t, rows: [row, ...t.rows] } : t)
      : [...state.tables, { name: table, rows: [row] }];
    persist(next, true);
    setAdding(a => ({ ...a, wrong: "", correct: "" }));
    setTimeout(load, 400); // pick up the server's keys
  }

  async function checkFiles(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    const form = new FormData();
    files.forEach(f => form.append("files", f));
    const res = await fetch("/api/corrections/upload", { method: "POST", body: form });
    const plan = await res.json();
    if (!res.ok) { setUpload({ files, plan: { errors: { "corrections.md": plan.errors || [plan.error] }, conflicts: [], files: [] } }); return; }
    setUpload({ files, plan, choices: Object.fromEntries(plan.conflicts.map(c => [c.id, c.default])) });
  }

  async function merge() {
    const form = new FormData();
    upload.files.forEach(f => form.append("files", f));
    form.append("apply", "true");
    form.append("choices", JSON.stringify(upload.choices));
    const res = await fetch("/api/corrections/upload", { method: "POST", body: form });
    const body = await res.json();
    if (res.ok && body.merged) {
      setNotice(`Merged ${body.files.join(", ")}: ${body.added} new, ${body.same} already here, ` +
                `${body.conflicts.length} conflict${body.conflicts.length === 1 ? "" : "s"} resolved. ` +
                "A backup of your previous file was kept.");
      setUpload(null);
      setState(body.state);
    }
  }

  async function reset() {
    if (!window.confirm("Clear all corrections? A backup copy of the current file is kept first.")) return;
    const res = await fetch("/api/corrections/reset", { method: "POST" });
    const body = await res.json();
    setNotice(body.backup ? `Cleared. Backup saved as ${body.backup}` : "Cleared.");
    setState(body.state);
    setLoadError(null);
  }

  const q = query.trim().toLowerCase();
  const tables = (state?.tables || []).map(t => ({
    ...t,
    rows: t.rows.filter(r => !q || [t.name, r.wrong, r.correct, r.notes].some(v => String(v).toLowerCase().includes(q))),
  })).filter(t => t.rows.length);
  const total = (state?.tables || []).reduce((n, t) => n + t.rows.length, 0);
  const tableNames = [...new Set([...(state?.suggested_tables || []), ...(state?.tables || []).map(t => t.name)])];

  return (
    <div className="workspace corrections" hidden={!active}
      onDragOver={e => { e.preventDefault(); setDrag(true); }}
      onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget)) setDrag(false); }}
      onDrop={e => { e.preventDefault(); setDrag(false); checkFiles(e.dataTransfer.files); }}>
      <div className="corr-head">
        <div>
          <h2 className="section-title">Your corrections</h2>
          <p className="section-hint">
            Words Whisper got wrong and what they should be, learnt every time you approve a transcript.
            They're fixed automatically in every new draft (all tabs), and the most frequent ones are given
            to Whisper as hints. Saved as <code>{state?.path || "corrections.md"}</code> — readable and
            editable in any text editor, and shareable with your team.
          </p>
        </div>
        <div className="corr-actions">
          <button className="loadtxt" onClick={() => fileInput.current.click()}>Upload .md</button>
          <a className="loadtxt" href="/api/corrections/download" download="corrections.md">Download corrections.md</a>
          <button className="loadtxt danger" onClick={reset} disabled={!total}>Reset</button>
          <input ref={fileInput} type="file" accept=".md,text/markdown" multiple hidden
            onChange={e => { checkFiles(e.target.files); e.target.value = ""; }} />
        </div>
      </div>

      <div className={`corr-drop${drag ? " drag" : ""}`} onClick={() => fileInput.current.click()}>
        Drop one or more <b>corrections .md</b> files here (from teammates) to merge them with yours
      </div>

      {notice && <div className="stage-note corr-notice">{notice}</div>}
      {loadError && (
        <div className="errbox">
          {loadError.error} Fix it in a text editor ({loadError.path}) or use Reset (a backup is kept).
          <ul>{(loadError.errors || []).map(e => <li key={e}>{e}</li>)}</ul>
        </div>
      )}

      {upload && (
        <div className="corr-upload" data-testid="merge-panel">
          <h3>Merge {upload.plan.files.length ? upload.plan.files.join(", ") : "upload"}</h3>
          {Object.entries(upload.plan.errors || {}).map(([name, errs]) => (
            <div key={name} className="errbox corr-file-errors">
              <b>{name} can't be merged:</b>
              <ul>{errs.map(e => <li key={e}>{e}</li>)}</ul>
            </div>
          ))}
          {upload.plan.files.length > 0 && (
            <>
              <p className="section-hint">
                {upload.plan.added} new correction{upload.plan.added === 1 ? "" : "s"},{" "}
                {upload.plan.same} you already have,{" "}
                {upload.plan.conflicts.length} conflict{upload.plan.conflicts.length === 1 ? "" : "s"}.
                {upload.plan.conflicts.length > 0 && " Pick the right correction for each (the one seen more often is preselected):"}
              </p>
              {upload.plan.conflicts.map(c => (
                <fieldset key={c.id} className="corr-conflict">
                  <legend>{c.table}: Whisper writes <b>“{c.wrong}”</b></legend>
                  {c.options.map((o, i) => (
                    <label key={i} className="check">
                      <input type="radio" name={c.id} checked={upload.choices[c.id] === i}
                        onChange={() => setUpload(u => ({ ...u, choices: { ...u.choices, [c.id]: i } }))} />
                      <span>“{o.correct}” <span className="muted">— {o.source}, seen {o.count}×, last {o.last_seen}</span></span>
                    </label>
                  ))}
                </fieldset>
              ))}
              <div className="corr-upload-actions">
                <button className="go small" onClick={merge}>Merge into my corrections</button>
                <button className="loadtxt" onClick={() => setUpload(null)}>Cancel</button>
              </div>
            </>
          )}
          {!upload.plan.files.length && <button className="loadtxt" onClick={() => setUpload(null)}>Close</button>}
        </div>
      )}

      <form className="corr-add" onSubmit={addRow}>
        <span className="langpick-label">Add a correction</span>
        <input list="corr-tables" value={adding.table} onChange={e => setAdding(a => ({ ...a, table: e.target.value }))}
          placeholder="Script" aria-label="Script" />
        <datalist id="corr-tables">{tableNames.map(n => <option key={n} value={n} />)}</datalist>
        <input value={adding.wrong} onChange={e => setAdding(a => ({ ...a, wrong: e.target.value }))} placeholder="Wrong (as Whisper writes it)" aria-label="Wrong" />
        <input value={adding.correct} onChange={e => setAdding(a => ({ ...a, correct: e.target.value }))} placeholder="Correct" aria-label="Correct" />
        <button className="go small" type="submit">Add</button>
      </form>

      <div className="corr-toolbar">
        <input className="corr-search" type="search" placeholder="Search corrections…" value={query}
          onChange={e => setQuery(e.target.value)} />
        <span className="muted">{total} correction{total === 1 ? "" : "s"}{state?.backups ? ` · ${state.backups} backup${state.backups === 1 ? "" : "s"}` : ""}{saved && ` · ${saved}`}</span>
      </div>
      {saveError && <div className="errbox"><b>Not saved:</b><ul>{saveError.map(e => <li key={e}>{e}</li>)}</ul></div>}

      {state && !total && !loadError && (
        <p className="stage-note">No corrections yet. Approve a transcript with fixes in any tab, add one above, or upload a teammate's file.</p>
      )}
      {tables.map(t => (
        <section key={t.name} className="corr-table">
          <h3>{t.name}</h3>
          <table>
            <thead><tr><th>On</th><th>Wrong</th><th>Correct</th><th>Times seen</th><th>Last seen</th><th>Notes</th><th /></tr></thead>
            <tbody>
              {t.rows.map(r => (
                <tr key={r.key} className={r.disabled ? "off" : ""}>
                  <td><input type="checkbox" checked={!r.disabled} aria-label="Enabled"
                    onChange={e => editRow(t.name, r.key, { disabled: !e.target.checked }, true)} /></td>
                  <td><input value={r.wrong} onChange={e => editRow(t.name, r.key, { wrong: e.target.value })} /></td>
                  <td><input value={r.correct} onChange={e => editRow(t.name, r.key, { correct: e.target.value })} /></td>
                  <td><input className="num" type="number" min="1" value={r.count}
                    onChange={e => editRow(t.name, r.key, { count: e.target.value })} /></td>
                  <td className="mono">{r.last_seen}</td>
                  <td className="muted">{r.notes}</td>
                  <td><button className="corr-del" onClick={() => deleteRow(t.name, r.key)} aria-label={`Delete ${r.wrong}`}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

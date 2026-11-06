import { useEffect, useRef, useState } from "react";

const today = () => new Date().toISOString().slice(0, 10);

/** The Hinglish house spellings (hinglish-spellings.md): how Hinglish words are spelt in every
    Hinglish draft, before the corrections file. Built in; edits are saved to the data folder. */
function HouseSpellings({ active }) {
  const [state, setState] = useState(null); // {rows, edited, path}
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState({ wrong: "", correct: "" });
  const [saved, setSaved] = useState("");
  const saveTimer = useRef(null);

  useEffect(() => {
    if (!active) return;
    fetch("/api/spellings").then(r => r.json().then(b => {
      if (r.ok) { setState(b); setError(null); } else setError(b.errors || [b.error]);
    })).catch(() => {});
  }, [active]);

  function persist(rows, immediate = false) {
    setState(s => ({ ...s, rows }));
    clearTimeout(saveTimer.current);
    const run = async () => {
      const res = await fetch("/api/spellings", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows }),
      });
      const body = await res.json();
      if (!res.ok) { setError(body.errors || [body.error]); return; }
      setError(null);
      setState(body);
      setSaved(`Saved ${new Date().toLocaleTimeString()}`);
    };
    if (immediate) run(); else saveTimer.current = setTimeout(run, 700);
  }
  const edit = (key, patch, now) => persist(state.rows.map(r => r.key === key ? { ...r, ...patch } : r), now);

  function add(e) {
    e.preventDefault();
    if (!adding.wrong.trim() || !adding.correct.trim()) return;
    persist([{ key: `new-${Date.now()}`, wrong: adding.wrong.trim(), correct: adding.correct.trim(),
               disabled: false, notes: "" }, ...state.rows], true);
    setAdding({ wrong: "", correct: "" });
  }

  async function restore() {
    if (!window.confirm("Go back to the built-in house spellings? Your edits to them are removed.")) return;
    const body = await (await fetch("/api/spellings/reset", { method: "POST" })).json();
    setState(body);
    setError(null);
  }

  if (!state && !error) return null;
  return (
    <section className="corr-table spellings" data-testid="house-spellings">
      <div className="corr-head">
        <div>
          <h3>Hinglish house spellings <span className="muted">· built in</span></h3>
          <p className="section-hint">
            How Hinglish words are spelt in your subtitles. Every Hinglish draft is rewritten with these
            (whole words) before your corrections above are applied. Switch a row off, edit it, or add your
            own. {state?.edited ? <>Your edits are saved in <code>{state.path}</code>.</> : "These are the built-in spellings."}
          </p>
        </div>
        <div className="corr-actions">
          <button className="loadtxt" onClick={restore} disabled={!state?.edited}>Restore defaults</button>
        </div>
      </div>
      {error && <div className="errbox"><b>Not saved:</b><ul>{error.map(e => <li key={e}>{e}</li>)}</ul></div>}
      <form className="corr-add" onSubmit={add}>
        <span className="langpick-label">Add a spelling</span>
        <input value={adding.wrong} onChange={e => setAdding(a => ({ ...a, wrong: e.target.value }))}
          placeholder="Written (e.g. nahin)" aria-label="Written" />
        <input value={adding.correct} onChange={e => setAdding(a => ({ ...a, correct: e.target.value }))}
          placeholder="House spelling (e.g. nahi)" aria-label="House spelling" />
        <button className="go small" type="submit">Add</button>
        {saved && <span className="muted">{saved}</span>}
      </form>
      {state && (
        <table>
          <thead><tr><th>On</th><th>Written</th><th>House spelling</th><th>Notes</th><th /></tr></thead>
          <tbody>
            {state.rows.map(r => (
              <tr key={r.key} className={r.disabled ? "off" : ""}>
                <td><input type="checkbox" checked={!r.disabled} aria-label="Enabled"
                  onChange={e => edit(r.key, { disabled: !e.target.checked }, true)} /></td>
                <td><input value={r.wrong} onChange={e => edit(r.key, { wrong: e.target.value })} /></td>
                <td><input value={r.correct} onChange={e => edit(r.key, { correct: e.target.value })} /></td>
                <td className="muted">{r.notes}</td>
                <td><button className="corr-del" aria-label={`Delete ${r.wrong}`}
                  onClick={() => persist(state.rows.filter(x => x.key !== r.key), true)}>Delete</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Numbers kept in words (number-exceptions.txt, V4.6): every other number in a subtitle is written
    as digits ("twelve years" → "12 years"); these expressions stay as they are. */
function NumberExceptions({ active }) {
  const [state, setState] = useState(null); // {phrases, edited, path, defaults}
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    if (!active) return;
    fetch("/api/number-exceptions").then(r => r.json()).then(b => { setState(b); setText(b.phrases.join("\n")); })
      .catch(() => {});
  }, [active]);

  async function put(body) {
    const res = await fetch("/api/number-exceptions", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const b = await res.json();
    setState(b);
    setText(b.phrases.join("\n"));
    setSaved(`Saved ${new Date().toLocaleTimeString()}`);
  }
  const save = () => put({ phrases: text.split("\n") });
  const restore = () => {
    if (window.confirm("Go back to the built-in list of number expressions? Your edits are removed.")) put({ reset: true });
  };

  if (!state) return null;
  const dirty = text.split("\n").map(s => s.trim()).filter(Boolean).join("\n") !== state.phrases.join("\n");
  return (
    <section className="corr-table numbers" data-testid="number-exceptions">
      <div className="corr-head">
        <div>
          <h3>Numbers kept in words <span className="muted">· {state.edited ? "edited" : "built in"}</span></h3>
          <p className="section-hint">
            Every subtitle writes numbers as digits — “12 years”, “Week 1”, “7 a.m.”, “70%”, “size 4”, and
            Hinglish counts like “do saal” → “2 saal”. Expressions listed here stay in words (“not one inch”,
            “one time”, “you two”). One per line; a number inside one of them is left alone.
          </p>
        </div>
        <div className="corr-actions">
          <button className="loadtxt" onClick={restore} disabled={!state.edited}>Restore defaults</button>
          <button className="loadtxt" onClick={save} disabled={!dirty} data-testid="numbers-save">Save</button>
        </div>
      </div>
      <textarea className="numbers-list" value={text} onChange={e => setText(e.target.value)} rows={8}
        spellCheck={false} aria-label="Number expressions kept in words" />
      {saved && <p className="muted section-hint">{saved}{state.edited && <> · <code>{state.path}</code></>}</p>}
    </section>
  );
}

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
  const [sourceFilter, setSourceFilter] = useState("all");
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
                  count: 1, last_seen: today(), source: "User", notes: "added by hand", disabled: false };
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
    rows: t.rows.filter(r => (sourceFilter === "all" || (r.source || "User") === sourceFilter) &&
      (!q || [t.name, r.wrong, r.correct, r.notes].some(v => String(v).toLowerCase().includes(q)))),
  })).filter(t => t.rows.length);
  const total = (state?.tables || []).reduce((n, t) => n + t.rows.length, 0);
  const aiOnRows = (state?.tables || []).reduce((n, t) => n + t.rows.filter(r => r.source === "AI" && !r.disabled).length, 0);

  /** Switch off every row whose fix came from AI polish alone (Source "AI"). */
  function disableAiRows() {
    if (!window.confirm(`Switch off all ${aiOnRows} corrections that came from AI polish? You can switch them back on one by one.`)) return;
    persist(state.tables.map(t => ({ ...t, rows: t.rows.map(r => r.source === "AI" ? { ...r, disabled: true } : r) })), true);
  }
  const tableNames = [...new Set([...(state?.suggested_tables || []), ...(state?.tables || []).map(t => t.name)])];

  return (
    <div className="workspace corrections" hidden={!active}
      onDragOver={e => { e.preventDefault(); setDrag(true); }}
      onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget)) setDrag(false); }}
      onDrop={e => { e.preventDefault(); setDrag(false); checkFiles(e.dataTransfer.files); }}>
      <div className="corr-head">
        <p className="section-hint">
          Saved as <code>{state?.path || "corrections.md"}</code> — plain Markdown you can edit or share. Fixes apply
          to every new draft, and the most frequent are given to Whisper as hints.
        </p>
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
                      <span>“{o.correct}” <span className="muted">— {o.source}, seen {o.count}×, last {o.last_seen}{o.source_kind ? `, ${o.source_kind}` : ""}</span></span>
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
        <select className="corr-filter" value={sourceFilter} onChange={e => setSourceFilter(e.target.value)} aria-label="Source">
          <option value="all">All sources</option>
          <option value="User">Source: User</option>
          <option value="AI">Source: AI</option>
          <option value="AI+User">Source: AI+User</option>
        </select>
        <button className="loadtxt" onClick={disableAiRows} disabled={!aiOnRows} data-testid="disable-ai">
          Switch off AI rows{aiOnRows ? ` (${aiOnRows})` : ""}
        </button>
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
            <thead><tr><th>On</th><th>Wrong</th><th>Correct</th><th>Times seen</th><th>Last seen</th><th>Source</th><th>Notes</th><th /></tr></thead>
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
                  <td className="mono">{r.source || "User"}</td>
                  <td className="muted">{r.notes}</td>
                  <td><button className="corr-del" onClick={() => deleteRow(t.name, r.key)} aria-label={`Delete ${r.wrong}`}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      <HouseSpellings active={active} />
      <NumberExceptions active={active} />
    </div>
  );
}

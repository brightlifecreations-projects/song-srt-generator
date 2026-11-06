import { useEffect, useState } from "react";

/** The model dropdown (V4.4.1): the provider's own model list (display name, API id in small
    text, newest first), a refresh button, a built-in list when the live one can't load, the saved
    model kept as "(saved)" if it isn't listed, and "Custom model id…" for anything else.
    A stored name that looks like a label ("Haiku 5.5": spaces or capitals) and isn't listed is
    replaced by the first model; onCleanup saves that. */
function ModelPicker({ provider, value, onChange, onCleanup }) {
  const [list, setList] = useState(null); // {models, source, error, loading}
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(false);
  const [filter, setFilter] = useState("");

  async function load(refresh) {
    setList(l => ({ models: [], ...(l || {}), loading: true }));
    try {
      const r = await (await fetch(`/api/ai/models?provider=${provider}${refresh ? "&refresh=true" : ""}`)).json();
      setList({ ...r, loading: false });
    } catch {
      setList({ models: [], source: "builtin", error: "Couldn't load the model list", loading: false });
    }
  }
  useEffect(() => { setList(null); setCustom(false); setOpen(false); load(false); }, [provider]);

  const models = list?.models || [];
  const listed = models.some(m => m.id === value);
  useEffect(() => {
    if (!list || list.loading || !models.length) return;
    if (!value || (!listed && /\s|[A-Z]/.test(value))) {
      onChange(models[0].id);
      if (value) onCleanup?.(models[0].id);
    }
  }, [list]);

  const current = models.find(m => m.id === value);
  const shown = filter ? models.filter(m => `${m.name} ${m.id}`.toLowerCase().includes(filter.toLowerCase())) : models;
  const pick = id => { onChange(id); setOpen(false); setCustom(false); setFilter(""); };

  return (
    <div className="modelpick">
      <div className="modelpick-row">
        <button type="button" className="modelpick-button" onClick={() => setOpen(o => !o)} aria-haspopup="listbox"
          aria-expanded={open} aria-label="Model">
          {current ? <><span>{current.name}</span><small>{current.id}</small></>
            : value ? <><span>{value} <i>(saved)</i></span><small>not in the provider's list</small></>
            : <span className="muted">{list?.loading ? "Loading models…" : "Choose a model"}</span>}
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M3 4.5 6 7.5l3-3" /></svg>
        </button>
        <button type="button" className="loadtxt" onClick={() => load(true)} disabled={list?.loading}
          aria-label="Refresh the model list" title="Refresh the model list">{list?.loading ? "…" : "↻"}</button>
      </div>
      {list && !list.loading && list.source === "builtin" && (
        <small className="muted">Built-in list — the provider's list couldn't load{list.error ? ` (${list.error})` : ""}.</small>
      )}
      {open && (
        <div className="modelpick-list" role="listbox">
          {models.length > 15 && (
            <input className="modelpick-filter" value={filter} onChange={e => setFilter(e.target.value)}
              placeholder={`Filter ${models.length} models…`} autoFocus />
          )}
          {value && !listed && (
            <button type="button" role="option" aria-selected className="on" onClick={() => pick(value)}>
              <span>{value} <i>(saved)</i></span><small>{value}</small>
            </button>
          )}
          {shown.map(m => (
            <button type="button" role="option" key={m.id} aria-selected={m.id === value}
              className={m.id === value ? "on" : ""} onClick={() => pick(m.id)}>
              <span>{m.name}</span><small>{m.id}</small>
            </button>
          ))}
          <button type="button" role="option" aria-selected={false} onClick={() => { setCustom(true); setOpen(false); }}>
            <span>Custom model id…</span><small>type any model the provider offers</small>
          </button>
        </div>
      )}
      {custom && (
        <input className="modelpick-custom" autoFocus placeholder="Model id, e.g. claude-opus-5-5" aria-label="Custom model id"
          defaultValue={listed ? "" : value} onChange={e => onChange(e.target.value.trim())} />
      )}
    </div>
  );
}

const post = (url, body, method = "POST") =>
  fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** The AI & API keys page: AI polish on/off, auto-polish, the active provider, and each provider's
    key (saved in the system key store, never shown again), model, Custom base URL and Anthropic
    workspace ID, with Save, Test connection and Remove key. onChange lets the sidebar's status follow. */
export default function AiKeys({ active, onChange }) {
  const [state, setState] = useState(null);
  const [editing, setEditing] = useState(null);   // the provider whose form is shown
  const [form, setForm] = useState({ key: "", model: "", url: "", workspace: "" });
  const [showKey, setShowKey] = useState(false);
  const [test, setTest] = useState(null);         // {ok, message} | "running"
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  function apply(s) {
    setState(s);
    onChange?.(s);
  }
  useEffect(() => {
    if (!active) return;
    fetch("/api/ai").then(r => r.json()).then(s => { apply(s); setEditing(e => e || s.provider); }).catch(() => {});
  }, [active]);

  // Fill the form from the saved settings whenever another provider is opened.
  useEffect(() => {
    if (!state || !editing) return;
    setForm({ key: "", model: state.models[editing] || "", url: state.custom_base_url || "",
              workspace: state.anthropic_workspace_id || "" });
    setShowKey(false);
    setTest(null);
    setError("");
  }, [editing, state?.provider]);

  if (!state) return null;
  const provider = state.providers.find(p => p.code === editing) || state.providers[0];
  const saved = state.providers.filter(p => p.has_key || (p.code === "custom" && state.custom_base_url));

  async function save(patch) {
    const body = await (await post("/api/ai", { ...state, ...patch, providers: undefined }, "PUT")).json();
    apply(body);
    return body;
  }

  /** Save: the provider's settings, and its key if one was typed. */
  async function saveProvider() {
    setError(""); setNotice("");
    const models = { ...state.models, [editing]: form.model.trim() || provider.default_model };
    let body = await save({ models, custom_base_url: editing === "custom" ? form.url.trim() : state.custom_base_url,
                            anthropic_workspace_id: editing === "anthropic" ? form.workspace.trim() : state.anthropic_workspace_id });
    if (form.key.trim()) {
      const res = await post("/api/ai/key", { provider: editing, key: form.key });
      body = await res.json();
      if (!res.ok) { setError(body.error); return; }
      apply(body);
      setForm(f => ({ ...f, key: "" }));
    }
    setNotice(`Saved ${provider.name}${form.key.trim() ? " — key stored in the key store" : ""}.`);
  }

  async function removeKey() {
    if (!window.confirm(`Remove the ${provider.name} key from this computer's key store?`)) return;
    const res = await post("/api/ai/key", { provider: editing, key: "" });
    const body = await res.json();
    if (!res.ok) { setError(body.error); return; }
    apply(body);
    setNotice(`${provider.name} key removed.`);
  }

  async function runTest() {
    if (form.key.trim()) await saveProvider();  // test what was just typed
    setNotice("");
    setTest("running");
    const res = await post("/api/ai/test", {
      provider: editing, model: form.model.trim() || provider.default_model,
      custom_base_url: editing === "custom" ? form.url.trim() : state.custom_base_url,
      anthropic_workspace_id: editing === "anthropic" ? form.workspace.trim() : state.anthropic_workspace_id,
    });
    setTest(await res.json());
  }

  const activeProvider = state.providers.find(p => p.code === state.provider);
  return (
    <div className="aipage" hidden={!active} data-testid="ai-settings">
      <section className="card">
        <div className="card-row">
          <div>
            <h2 className="card-title">AI polish</h2>
            <p className="card-hint">After the offline draft, an AI model fixes misheard and misspelled words —
              same lines, nothing added or removed. Its changes are highlighted for you to check.</p>
          </div>
          <label className="switch" title={state.usable ? "" : state.why_not}>
            <input type="checkbox" checked={state.enabled} disabled={!state.usable && !state.enabled}
              onChange={e => save({ enabled: e.target.checked })} aria-label="AI polish on" />
            <span className="switch-track" /> {state.enabled ? "On" : "Off"}
          </label>
        </div>
        <p className="ai-privacy"><b>Only the transcript text is sent to the provider you choose. Audio and video never leave this computer.</b></p>
        {!state.usable && <p className="muted ai-status">{state.why_not} to switch AI polish on.</p>}
        <div className="card-row sub">
          <div>
            <b>Auto-polish after every transcription</b>
            <p className="card-hint">Off: drafts stay offline until you click <i>Polish</i> on the draft.</p>
          </div>
          <label className="switch">
            <input type="checkbox" checked={state.auto} onChange={e => save({ auto: e.target.checked })}
              aria-label="Auto-polish after every transcription" />
            <span className="switch-track" /> {state.auto ? "On" : "Off"}
          </label>
        </div>
      </section>

      <section className="card" data-testid="final-check">
        <div className="card-row">
          <div>
            <h2 className="card-title">Final AI check before output</h2>
            <p className="card-hint">Every SRT, on every page and in both caption modes: once the subtitles are
              built, the provider below checks them — stray quotes and apostrophes, capitals, punctuation,
              numbers as digits, misspellings, and line cuts that break the caption rules. It can only fix a
              word's spelling or move a cut between words, never add or remove a spoken word, so the timing
              can't drift. Its changes are highlighted in the Review panel, with the original and <i>Undo</i>.</p>
            <p className="muted ai-status" data-testid="final-check-status">{state.usable
              ? (state.final_check !== false ? `Runs with ${activeProvider?.name || "the active provider"} after every SRT.`
                                             : "Off: only the offline cleanup runs (stray quotes, sentence case, numbers).")
              : "No provider set up yet, so the offline cleanup runs instead (stray quotes, sentence case, numbers)."}</p>
          </div>
          <label className="switch">
            <input type="checkbox" checked={state.final_check !== false}
              onChange={e => save({ final_check: e.target.checked })} aria-label="Final AI check on" />
            <span className="switch-track" /> {state.final_check !== false ? "On" : "Off"}
          </label>
        </div>
      </section>

      <section className="card">
        <h2 className="card-title">Provider</h2>
        <div className="ai-row">
          <span className="langpick-label">Use</span>
          <select value={state.provider} aria-label="Provider"
            onChange={e => { save({ provider: e.target.value }); setEditing(e.target.value); }}>
            {state.providers.map(p => <option key={p.code} value={p.code}>{p.name}{p.has_key ? " · key saved" : ""}</option>)}
          </select>
          {activeProvider?.key_url && <a className="loadtxt" href={activeProvider.key_url} target="_blank" rel="noreferrer">Get a key</a>}
        </div>
        <div className="saved-keys" data-testid="saved-keys">
          <span className="langpick-label">Saved keys</span>
          {saved.length ? (
            <ul>
              {saved.map(p => (
                <li key={p.code} className={p.code === state.provider ? "on" : ""}>
                  <span>{p.name}</span>
                  {p.code === state.provider
                    ? <span className="badge">Active</span>
                    : <button className="loadtxt" onClick={() => { save({ provider: p.code }); setEditing(p.code); }}>Make active</button>}
                  <button className="loadtxt" onClick={() => setEditing(p.code)}>Edit</button>
                </li>
              ))}
            </ul>
          ) : <span className="muted">None yet — add one below.</span>}
        </div>
      </section>

      <section className="card" data-testid="provider-form">
        <div className="card-row">
          <h2 className="card-title">{provider.name}</h2>
          <span className="langpick-field small-select">
            <select value={editing} onChange={e => setEditing(e.target.value)} aria-label="Edit provider">
              {state.providers.map(p => <option key={p.code} value={p.code}>Edit: {p.name}</option>)}
            </select>
          </span>
        </div>
        <div className="form-grid">
          <label>
            <span className="langpick-label">API key {provider.key_url && <a href={provider.key_url} target="_blank" rel="noreferrer">Get a key</a>}</span>
            <span className="keyfield">
              <input type={showKey ? "text" : "password"} value={form.key} autoComplete="off" aria-label="API key"
                onChange={e => setForm(f => ({ ...f, key: e.target.value }))}
                placeholder={provider.has_key ? "A key is saved — paste a new one to replace it"
                  : editing === "custom" ? "Optional for a local server" : "Paste your API key"} />
              <button type="button" className="loadtxt" onClick={() => setShowKey(v => !v)} aria-label={showKey ? "Hide key" : "Show key"}>
                {showKey ? "Hide" : "Show"}
              </button>
            </span>
          </label>
          {editing === "custom" && (
            <label>
              <span className="langpick-label">Base URL</span>
              <input value={form.url} onChange={e => setForm(f => ({ ...f, url: e.target.value }))} aria-label="Base URL"
                placeholder="e.g. http://localhost:11434/v1 (Ollama) or https://api.deepseek.com/v1" />
            </label>
          )}
          {editing === "anthropic" && (
            <label>
              <span className="langpick-label">Workspace ID <span className="muted">(optional)</span></span>
              <input value={form.workspace} onChange={e => setForm(f => ({ ...f, workspace: e.target.value }))} aria-label="Workspace ID"
                placeholder="Only if your key isn't scoped to a workspace (wrkspc_…)" />
            </label>
          )}
          <div className="form-field">
            <span className="langpick-label">Model</span>
            <ModelPicker provider={editing} value={form.model}
              onChange={id => setForm(f => ({ ...f, model: id }))}
              onCleanup={id => save({ models: { ...state.models, [editing]: id } })} />
          </div>
        </div>
        <div className="ai-row">
          <button className="go small" onClick={saveProvider} data-testid="ai-save">Save</button>
          <button className="loadtxt" onClick={runTest} disabled={test === "running"} data-testid="ai-test">
            {test === "running" ? "Testing…" : "Test connection"}
          </button>
          {provider.has_key && <button className="loadtxt danger" onClick={removeKey} data-testid="ai-remove">Remove key</button>}
        </div>
        {test && test !== "running" && <p className={`ai-status ${test.ok ? "ok" : "bad"}`} data-testid="ai-test-result">{test.message}</p>}
        {notice && <p className="ai-status ok">{notice}</p>}
        {error && <div className="errbox">{error}</div>}
        <p className="card-hint">Keys are stored in this computer's {navigator.platform.startsWith("Win") ? "Credential Manager" : "Keychain"}, one per provider, and never shown again.</p>
        {!state.key_store && <div className="errbox">This computer has no secure key store, so AI polish can't be used here.</div>}
      </section>
    </div>
  );
}

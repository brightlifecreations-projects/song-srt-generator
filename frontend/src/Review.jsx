import { useEffect, useRef, useState } from "react";

/* The Review panel (V4.5): check and edit the generated subtitles before downloading.
   Cues are {start, end, text}. Every change goes through commit(), which keeps undo/redo history,
   tells the page (so the player draws the edited cue) and autosaves to the job folder
   (edited.srt; original.srt is kept). */

import { MIN_CUE, autoFix, fmt, problems, r3 } from "./reviewFix.js";

export default function Review({ jobId, initialCues, edited: initiallyEdited, cap, player, time, duration,
                                 playing, active, onCues, srtName, hasVocals, vocalsLabel }) {
  const [hist, setHist] = useState({ past: [], present: initialCues, future: [] });
  const [sel, setSel] = useState(0);
  const [editing, setEditing] = useState(false);
  const [loop, setLoop] = useState(false);
  const [edited, setEdited] = useState(initiallyEdited);
  const [saveNote, setSaveNote] = useState("");  // autosave status
  const [notice, setNotice] = useState("");      // what an action did (re-time, …)
  const [retiming, setRetiming] = useState(false);
  const rowRefs = useRef([]);
  const inputRef = useRef(null);
  const saveTimer = useRef(null);
  const cues = hist.present;
  const issues = problems(cues, cap);
  const playingIdx = cues.findIndex(c => time >= c.start && time < c.end);

  useEffect(() => { onCues(cues); }, [cues]);

  function commit(next, keepSel) {
    const sorted = [...next].sort((a, b) => a.start - b.start);
    setHist(h => ({ past: [...h.past.slice(-99), h.present], present: sorted, future: [] }));
    if (keepSel !== undefined) setSel(Math.max(0, Math.min(keepSel, sorted.length - 1)));
  }
  function undo() {
    setHist(h => h.past.length ? ({ past: h.past.slice(0, -1), present: h.past.at(-1), future: [h.present, ...h.future] }) : h);
  }
  function redo() {
    setHist(h => h.future.length ? ({ past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) }) : h);
  }
  useEffect(() => { if (hist.past.length || hist.future.length) scheduleSave(cues); }, [hist.present]);

  function scheduleSave(next) {
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      const res = await fetch(`/api/jobs/${jobId}/cues`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cues: next }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { setSaveNote(body.error || "Couldn't save the edits."); return; }
      setEdited(true);
      setSaveNote(body.recorded?.length
        ? `Saved · added to your corrections: ${body.recorded.map(r => `${r.wrong} → ${r.correct}`).join(", ")}`
        : `Saved ${new Date().toLocaleTimeString()}`);
    }, 700);
  }

  const update = (i, patch) => commit(cues.map((c, k) => (k === i ? { ...c, ...patch } : c)), i);
  const nudge = (i, field, d) => {
    const c = cues[i];
    const v = r3(Math.max(0, c[field] + d));
    if (field === "start" ? v >= c.end : v <= c.start) return;
    update(i, { [field]: v });
  };
  const fromPlayhead = (i, field) => {
    const c = cues[i];
    if (field === "start" ? time >= c.end : time <= c.start) return;
    update(i, { [field]: r3(time) });
  };
  function split(i) {
    const c = cues[i];
    const pos = editing && inputRef.current ? inputRef.current.selectionStart : Math.floor(c.text.length / 2);
    const a = c.text.slice(0, pos).trim(), b = c.text.slice(pos).trim();
    if (!a || !b) return;
    const t = r3(c.start + (c.end - c.start) * pos / c.text.length);
    commit([...cues.slice(0, i), { ...c, end: t, text: a }, { start: t, end: c.end, text: b }, ...cues.slice(i + 1)], i);
    setEditing(false);
  }
  function merge(i) {
    if (i >= cues.length - 1) return;
    const a = cues[i], b = cues[i + 1];
    commit([...cues.slice(0, i), { start: a.start, end: b.end, text: `${a.text} ${b.text}` }, ...cues.slice(i + 2)], i);
  }
  function remove(i) {
    commit(cues.filter((_, k) => k !== i), i);
  }
  function addAtPlayhead() {
    const next = cues.find(c => c.start > time);
    const end = r3(Math.min(time + 1.5, next ? next.start - 0.01 : time + 1.5, duration || Infinity));
    const fresh = { start: r3(time), end: Math.max(end, r3(time + MIN_CUE)), text: "New subtitle" };
    const list = [...cues, fresh].sort((a, b) => a.start - b.start);
    commit(list, list.indexOf(fresh));
    setEditing(true);
  }
  async function retime(i) {
    const c = cues[i];
    setRetiming(true);
    setNotice("Re-timing the line…");
    try {
      await fetch(`/api/jobs/${jobId}/retime`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start: c.start, end: c.end, text: c.text,
                               low: cues[i - 1]?.end ?? null, high: cues[i + 1]?.start ?? null }),
      });
      let s;
      for (let n = 0; n < 240; n++) {
        await new Promise(r => setTimeout(r, 500));
        s = await (await fetch(`/api/jobs/${jobId}`)).json();
        if (s.status === "error" || (s.status !== "running" && s.status !== "queued" && s.retime_result)) break;
      }
      if (s?.retime_result?.error || !s?.retime_result) {
        setNotice(s?.retime_result?.error || s?.message || "Re-timing failed.");
        return;
      }
      update(i, { start: s.retime_result.start, end: s.retime_result.end });
      setNotice(`Re-timed line ${i + 1}: ${fmt(c.start)}–${fmt(c.end)} → ${fmt(s.retime_result.start)}–${fmt(s.retime_result.end)}`);
    } finally {
      setRetiming(false);
    }
  }

  function go(i, play = true) {
    if (i < 0 || i >= cues.length) return;
    setSel(i);
    player.current?.seek(cues[i].start + 0.01);
    if (play) player.current?.play();
  }

  // Loop this cue: back to its start when the playhead reaches its end.
  useEffect(() => {
    const c = cues[sel];
    if (loop && playing && c && time >= c.end - 0.04) player.current?.seek(c.start + 0.01);
  }, [time]);

  // The playing cue scrolls into view (not while typing in another one).
  useEffect(() => {
    if (playingIdx >= 0 && !editing) rowRefs.current[playingIdx]?.scrollIntoView({ block: "nearest" });
  }, [playingIdx]);

  // Space play/pause, ↑/↓ move between cues, Enter edits, Escape stops editing; ⌘Z / ⇧⌘Z undo/redo.
  useEffect(() => {
    if (!active) return;
    const onKey = e => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !typing) {
        e.preventDefault(); (e.shiftKey ? redo : undo)(); return;
      }
      if (typing) return;
      if (e.key === " ") { e.preventDefault(); player.current?.toggle(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); go(Math.min(sel + 1, cues.length - 1), false); }
      else if (e.key === "ArrowUp") { e.preventDefault(); go(Math.max(sel - 1, 0), false); }
      else if (e.key === "Enter") { e.preventDefault(); setEditing(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing, sel]);

  const counts = issues.reduce((n, p) => ({ ...n, [p.kind]: (n[p.kind] || 0) + 1 }), {});
  return (
    <div className="review" data-testid="review">
      <div className="review-transport">
        <button className="playbtn" onClick={() => player.current?.toggle()} aria-label={playing ? "Pause" : "Play"}>
          {playing
            ? <svg width="13" height="13" viewBox="0 0 12 12" fill="currentColor"><rect x="1.5" width="3.2" height="12" rx="1"/><rect x="7.3" width="3.2" height="12" rx="1"/></svg>
            : <svg width="13" height="13" viewBox="0 0 12 12" fill="currentColor"><path d="M2 1.2c0-.8.9-1.3 1.6-.9l8 4.8c.7.4.7 1.4 0 1.8l-8 4.8c-.7.4-1.6-.1-1.6-.9V1.2z"/></svg>}
        </button>
        <input type="range" className="scrubber" min={0} max={duration || 0} step={0.01} value={Math.min(time, duration || 0)}
          onChange={e => player.current?.seek(Number(e.target.value))} aria-label="Scrubber" />
        <span className="mono">{fmt(time)} / {fmt(duration)}</span>
      </div>

      <div className="review-tools">
        <button className="loadtxt" onClick={addAtPlayhead} data-testid="add-cue">+ Cue at playhead</button>
        <label className="check loop"><input type="checkbox" checked={loop} onChange={e => setLoop(e.target.checked)} data-testid="loop" /> Loop this cue</label>
        <span className="spacer" />
        <button className="loadtxt" onClick={undo} disabled={!hist.past.length} data-testid="undo" title="Undo (⌘Z)">Undo</button>
        <button className="loadtxt" onClick={redo} disabled={!hist.future.length} data-testid="redo" title="Redo (⇧⌘Z)">Redo</button>
      </div>

      {issues.length > 0 && (
        <div className="warnbox review-issues" data-testid="issues">
          <span>
            {[counts.overlap && `${counts.overlap} overlapping`, counts.short && `${counts.short} shorter than ${MIN_CUE} s`,
              counts.long && `${counts.long} over ${cap} characters`].filter(Boolean).join(" · ")}
          </span>
          <button className="loadtxt" onClick={() => commit(autoFix(cues, cap), sel)} data-testid="autofix">Fix automatically</button>
        </div>
      )}

      <div className="cuecard">
        <div className="cuecard-head">
          <span className="cuecard-title">Subtitles · {cues.length}</span>
          <span className="cuecard-hint">Click to jump · Enter to edit · Space play/pause · ↑↓ move</span>
        </div>
        <div className="cuelist review-list">
          {cues.map((c, i) => {
            const bad = issues.filter(p => p.i === i);
            const on = i === sel;
            return (
              <div key={`${i}-${c.start}`} ref={el => (rowRefs.current[i] = el)}
                className={`rcue${i === playingIdx ? " playing" : ""}${on ? " on" : ""}${bad.length ? " bad" : ""}`}
                onClick={() => { if (!on) { go(i); setEditing(false); } }} data-testid="rcue">
                <span className="cue-num">{String(i + 1).padStart(2, "0")}</span>
                <span className="cue-time">{fmt(c.start)}<br />{fmt(c.end)}</span>
                <span className="rcue-text">
                  {on && editing ? (
                    <input ref={inputRef} defaultValue={c.text} aria-label="Cue text"
                      onKeyDown={e => {
                        if (e.key === "Enter") { e.preventDefault(); if (e.currentTarget.value.trim() !== c.text) update(i, { text: e.currentTarget.value.trim() }); setEditing(false); }
                        if (e.key === "Escape") setEditing(false);
                      }}
                      onBlur={e => { const v = e.currentTarget.value.trim(); if (v && v !== c.text) update(i, { text: v }); setEditing(false); }} />
                  ) : (
                    <span onDoubleClick={() => { setSel(i); setEditing(true); }}>{c.text}</span>
                  )}
                  {bad.length > 0 && <small className="rcue-bad">{bad.map(p => p.text).join(" · ")}</small>}
                </span>
                {on && (
                  <div className="rcue-tools" onClick={e => e.stopPropagation()}>
                    <span className="nudge">Start
                      <button onClick={() => nudge(i, "start", -0.1)} aria-label="Start −0.1 s">−.1</button>
                      <button onClick={() => nudge(i, "start", 0.1)} aria-label="Start +0.1 s">+.1</button>
                      <button onClick={() => fromPlayhead(i, "start")} title="Set start from the playhead">⇤ playhead</button>
                    </span>
                    <span className="nudge">End
                      <button onClick={() => nudge(i, "end", -0.1)} aria-label="End −0.1 s">−.1</button>
                      <button onClick={() => nudge(i, "end", 0.1)} aria-label="End +0.1 s">+.1</button>
                      <button onClick={() => fromPlayhead(i, "end")} title="Set end from the playhead">playhead ⇥</button>
                    </span>
                    <span className="nudge">
                      {!editing && <button onClick={() => setEditing(true)}>Edit</button>}
                      <button onMouseDown={e => e.preventDefault()} onClick={() => split(i)} title="Split at the cursor (or the middle)">Split</button>
                      <button onClick={() => merge(i)} disabled={i >= cues.length - 1}>Merge next</button>
                      <button onClick={() => retime(i)} disabled={retiming} title="Re-align this line to its audio">{retiming ? "Re-timing…" : "Re-time line"}</button>
                      <button className="danger" onClick={() => remove(i)}>Delete</button>
                    </span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="download-row">
        <a className="dl-primary" href={`/api/jobs/${jobId}/srt`} data-testid="dl-srt">
          <span>
            <b>Download SRT {edited && <span className="edited-badge" data-testid="edited-badge">Edited</span>}</b>
            <span>{srtName} · {cues.length} cues</span>
          </span>
          <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M8 2v9M4.5 7.5 8 11l3.5-3.5" /><path d="M2.5 13.5h11" /></svg>
        </a>
        <div className="dl-side">
          <a className="dl-secondary" href={`/api/jobs/${jobId}/srt?original=1`} data-testid="dl-original">Download original</a>
          {hasVocals && <a className="dl-secondary" href={`/api/jobs/${jobId}/vocals`}>{vocalsLabel}</a>}
        </div>
      </div>
      {notice && <p className="stage-note review-save" data-testid="notice">{notice}</p>}
      {saveNote && <p className="stage-note review-save muted" data-testid="save-note">{saveNote}</p>}
    </div>
  );
}

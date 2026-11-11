import { useEffect, useRef, useState } from "react";

/* The Review panel (V4.5): check and edit the generated subtitles before downloading.
   Cues are {start, end, text, ai?}. Every change goes through commit(), which keeps undo/redo
   history, tells the page (so the player draws the edited cue) and autosaves to the job folder
   (edited.srt; original.srt is kept).

   V4.6, preview = export: the list shows times to the millisecond, exactly as the SRT has them;
   cue text is kept single-spaced and never empty, the way the server writes it; and Download first
   commits a line still being typed and saves any pending edit, so the file always matches the list.
   The final AI check's changes (cue.ai: [{i: word position, from, to}]) are highlighted with the
   original word and Undo. The Line by line / Word by word switch rebuilds the cues on the server.

   V4.8: cue text follows the clean-caption rules as it's typed (cleanCaption, the server's rules).
   The listening check's verdict (cue.listen: {status "ok" | "fixed" | "flag", reasons, heard}) shows
   as an orange "Check this" with the reason, a Listen button (jump there and play) and "Looks right";
   editing a flagged cue's text or timing counts as checked. Words the final AI check wasn't sure of
   (cue.unsure: word positions) carry a [?] (never in the SRT). Above Download: what the check found,
   and a Verified badge once nothing is left to check.

   V4.8.1: following playback scrolls only the subtitle list (its own scrollTop), never the page or
   the column the player sits in, so the video stays in view. It pauses while a cue is being edited
   and for FOLLOW_PAUSE_MS after the user scrolls the list; "Follow playback" switches it off. */

import { MIN_CUE, autoFix, cleanCaption, fmt, fmtMs, problems, r3 } from "./reviewFix.js";

const clean = cleanCaption;
const FOLLOW_PAUSE_MS = 3000;
const FOLLOW_KEY = "srt-follow-playback";
const savedFollow = () => { try { return localStorage.getItem(FOLLOW_KEY) !== "off"; } catch { return true; } };
const flagged = c => c.listen?.status === "flag" && !c.listen.resolved;

/** The AI's changes that are still in the text (the word at that position is the AI's word). */
const liveAi = c => {
  const words = c.text.split(" ");
  return (c.ai || []).filter(a => words[a.i] === a.to);
};

export default function Review({ jobId, initialCues, edited: initiallyEdited, cap, player, time, duration,
                                 playing, active, onCues, srtName, hasVocals, vocalsLabel,
                                 captionMode, onCaptionMode, switching, checkWith, checkMessage, listenSummary }) {
  const [hist, setHist] = useState({ past: [], present: initialCues, future: [] });
  const [sel, setSel] = useState(0);
  const [editing, setEditing] = useState(false);
  const [loop, setLoop] = useState(false);
  const [edited, setEdited] = useState(initiallyEdited);
  const [saveNote, setSaveNote] = useState("");  // autosave status
  const [notice, setNotice] = useState("");      // what an action did (re-time, …)
  const [retiming, setRetiming] = useState(false);
  const rowRefs = useRef([]);
  const listRef = useRef(null);
  const [follow, setFollow] = useState(savedFollow);
  const userScrolled = useRef(0);      // when the user last scrolled the list themselves
  const ourTop = useRef(null);         // the scrollTop we set last (its scroll event isn't the user's)
  const inputRef = useRef(null);
  const saveTimer = useRef(null);
  const pending = useRef(null);       // cues waiting to be saved
  const saving = useRef(Promise.resolve(true));
  const downloading = useRef(false);
  const applied = useRef(true);       // the line being typed was already applied (Enter, then blur)
  const cues = hist.present;
  const issues = problems(cues, cap);
  const playingIdx = cues.findIndex(c => time >= c.start && time < c.end);
  const aiCount = cues.reduce((n, c) => n + liveAi(c).length, 0);

  useEffect(() => { onCues(cues); }, [cues]);

  function commit(next, keepSel) {
    const sorted = next.map(c => ({ ...c, start: r3(c.start), end: r3(c.end), text: clean(c.text) }))
      .filter(c => c.text)
      .sort((a, b) => a.start - b.start);
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
    pending.current = next;
    saveTimer.current = setTimeout(flushSave, 700);
  }

  /** Save the pending edits now (Download waits for this). Saves run one after another. */
  function flushSave() {
    clearTimeout(saveTimer.current);
    const next = pending.current;
    if (!next) return saving.current;
    pending.current = null;
    saving.current = saving.current.then(async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}/cues`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cues: next }),
          keepalive: true,
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) { setSaveNote(body.error || "Couldn't save the edits."); return false; }
        setEdited(true);
        setSaveNote(body.recorded?.length
          ? `Saved · added to your corrections: ${body.recorded.map(r => `${r.wrong} → ${r.correct}`).join(", ")}`
          : `Saved ${new Date().toLocaleTimeString()}`);
        return true;
      } catch {
        setSaveNote("Couldn't reach the app to save the edits.");
        return false;
      }
    });
    return saving.current;
  }

  /** Download SRT: commit a line still being typed, save, then download what the list shows. */
  async function download(e) {
    if (downloading.current) { downloading.current = false; return; } // our own click, below
    e.preventDefault();
    const link = e.currentTarget;
    if (inputRef.current) applyInput(sel, inputRef.current.value);
    await new Promise(r => setTimeout(r, 30)); // let the commit render and schedule its save
    if (await flushSave() === false) return;
    downloading.current = true;
    link.click();
  }

  function applyInput(i, value) {
    if (applied.current) return;
    applied.current = true;
    const v = clean(value);
    setEditing(false);
    if (!v) { setNotice("A subtitle can't be empty — use Delete to remove it."); return; }
    if (v !== cues[i].text) update(i, { text: v });
  }

  /** A change to a cue's text or timing counts as the user having checked it (V4.8). */
  const update = (i, patch) => commit(cues.map((c, k) => {
    if (k !== i) return c;
    const next = { ...c, ...patch };
    if (c.listen && ("text" in patch || "start" in patch || "end" in patch)) next.listen = { ...c.listen, resolved: true };
    if ("text" in patch && clean(patch.text) !== c.text) delete next.unsure;
    return next;
  }), i);
  const looksRight = i => update(i, { listen: { ...cues[i].listen, resolved: true } });
  const dropUnsure = (i, k) => update(i, { unsure: (cues[i].unsure || []).filter(x => x !== k) });
  /** Jump to a cue a little before it starts, and play (the listening check's Listen button). */
  function listen(i) {
    setSel(i);
    setEditing(false);
    player.current?.seek(Math.max(0, cues[i].start - 0.5));
    player.current?.play();
  }
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
    const a = clean(c.text.slice(0, pos)), b = clean(c.text.slice(pos));
    if (!a || !b) return;
    const t = r3(c.start + (c.end - c.start) * pos / c.text.length);
    const n = a.split(" ").length;
    const ai = liveAi(c);
    const unsure = c.unsure || [];
    commit([...cues.slice(0, i),
      { ...c, end: t, text: a, ai: ai.filter(x => x.i < n), unsure: unsure.filter(x => x < n) },
      { start: t, end: c.end, text: b, ai: ai.filter(x => x.i >= n).map(x => ({ ...x, i: x.i - n })),
        unsure: unsure.filter(x => x >= n).map(x => x - n), ...(c.listen ? { listen: c.listen } : {}) },
      ...cues.slice(i + 1)], i);
    setEditing(false);
  }
  function merge(i) {
    if (i >= cues.length - 1) return;
    const a = cues[i], b = cues[i + 1];
    const n = a.text.split(" ").length;
    const keep = [a, b].find(flagged) || a.listen || b.listen;
    commit([...cues.slice(0, i), { start: a.start, end: b.end, text: `${a.text} ${b.text}`,
      ai: [...liveAi(a), ...liveAi(b).map(x => ({ ...x, i: x.i + n }))],
      unsure: [...(a.unsure || []), ...(b.unsure || []).map(x => x + n)],
      ...(keep ? { listen: keep.listen || keep } : {}) }, ...cues.slice(i + 2)], i);
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
  /** Put the word the AI changed back the way it was. */
  function undoAi(i, change) {
    const c = cues[i];
    const words = c.text.split(" ");
    words[change.i] = change.from;
    update(i, { text: words.join(" "), ai: (c.ai || []).filter(a => a !== change) });
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

  function switchMode(mode) {
    if (mode === captionMode || switching) return;
    if ((edited || hist.past.length) && !window.confirm(
      "Rebuild the subtitles " + (mode === "word" ? "word by word" : "line by line") +
      " from the same timings? Your edits in this panel are replaced.")) return;
    clearTimeout(saveTimer.current);
    pending.current = null;
    onCaptionMode(mode);
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

  /** Bring row i into view by scrolling the list itself (never scrollIntoView, which also scrolls
      the page and the player's column). "follow": the playing cue sits about a third of the way down. */
  function showRow(i, how = "nearest") {
    const list = listRef.current, row = rowRefs.current[i];
    if (!list || !row) return;
    const box = list.getBoundingClientRect();
    // The part of the list actually on screen (in a very short window the panel below the player
    // scrolls, and the list may run past its bottom).
    const area = list.closest(".results")?.getBoundingClientRect();
    const height = Math.max(40, Math.min(list.clientHeight, (area ? area.bottom : innerHeight) - box.top, innerHeight - box.top));
    const top = row.getBoundingClientRect().top - box.top + list.scrollTop;
    const bottom = top + row.offsetHeight;
    let next = list.scrollTop;
    if (top < list.scrollTop || bottom > list.scrollTop + height) {
      next = how === "follow" ? top - height / 3 : top < list.scrollTop ? top : bottom - height;
    }
    next = Math.max(0, Math.min(Math.round(next), list.scrollHeight - list.clientHeight));
    if (Math.abs(next - list.scrollTop) < 1) return;
    list.scrollTop = next;
    ourTop.current = list.scrollTop;  // as the browser rounded it, so its scroll event isn't taken for the user's
  }
  function onListScroll() {
    const list = listRef.current;
    if (ourTop.current !== null && Math.abs(list.scrollTop - ourTop.current) <= 2) { ourTop.current = null; return; }
    ourTop.current = null;
    userScrolled.current = Date.now();
  }
  function toggleFollow(on) {
    setFollow(on);
    try { localStorage.setItem(FOLLOW_KEY, on ? "on" : "off"); } catch { /* private window */ }
    if (on) { userScrolled.current = 0; if (playingIdx >= 0) showRow(playingIdx, "follow"); }
  }

  // Follow playback: the playing cue stays in view in the list — not while a cue is being edited,
  // not for a few seconds after the user scrolled the list, and not when switched off.
  useEffect(() => {
    if (playingIdx < 0 || editing || !follow || Date.now() - userScrolled.current < FOLLOW_PAUSE_MS) return;
    showRow(playingIdx, "follow");
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
      else if (e.key === "ArrowDown") { e.preventDefault(); const k = Math.min(sel + 1, cues.length - 1); go(k, false); showRow(k); }
      else if (e.key === "ArrowUp") { e.preventDefault(); const k = Math.max(sel - 1, 0); go(k, false); showRow(k); }
      else if (e.key === "Enter") { e.preventDefault(); setEditing(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Leaving the page, or this panel going away (a new SRT), with an edit not saved yet: save it now.
  useEffect(() => {
    window.addEventListener("pagehide", flushSave);
    return () => { window.removeEventListener("pagehide", flushSave); flushSave(); };
  }, [jobId]);

  useEffect(() => {
    if (editing) { applied.current = false; inputRef.current?.focus({ preventScroll: true }); showRow(sel); }
  }, [editing, sel]);

  function cueText(c, i) {
    const ai = liveAi(c);
    const unsure = c.unsure || [];
    if (!ai.length && !unsure.length) return c.text;
    const words = c.text.split(" ");
    return words.map((w, k) => {
      const change = ai.find(a => a.i === k);
      const gap = k < words.length - 1 ? " " : "";
      const mark = unsure.includes(k)
        ? <button className="unsure-mark" data-testid="unsure" onClick={e => { e.stopPropagation(); dropUnsure(i, k); }}
            title="The AI check wasn't sure of this word. Click if it's right (or edit it).">[?]</button> : null;
      if (!change) return <span key={k}>{w}{mark}{gap}</span>;
      return (
        <span key={k}>
          <span className="ai-fix" data-testid="ai-fix" tabIndex={0} title={`AI changed “${change.from}” to “${change.to}”`}>
            {w}
            <span className="ai-fix-pop" onClick={e => e.stopPropagation()}>
              was “{change.from}”
              <button onClick={() => undoAi(i, change)} data-testid="ai-undo">Undo</button>
            </span>
          </span>{mark}{gap}
        </span>
      );
    });
  }

  // What the listening check found, as it stands now (a checked or edited cue no longer counts).
  const heardAny = cues.some(c => c.listen);
  const toCheck = cues.filter(flagged).length;
  const fixedCount = cues.filter(c => c.listen?.status === "fixed").length;
  const unsureCount = cues.reduce((n, c) => n + (c.unsure || []).length, 0);
  const okCount = cues.filter(c => c.listen && !flagged(c) && c.listen.status !== "fixed").length;
  const nextToCheck = () => {
    const after = cues.findIndex((c, k) => k > sel && flagged(c));
    const k = after >= 0 ? after : cues.findIndex(flagged);
    if (k >= 0) { listen(k); showRow(k); }
  };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

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

      <div className="review-mode">
        <div className="segmented small capmode" role="radiogroup" aria-label="Captions" data-testid="review-capmode">
          {[["line", "Line by line"], ["word", "Word by word"]].map(([code, title]) => (
            <button key={code} role="radio" aria-checked={captionMode === code} disabled={switching}
              className={`segment${captionMode === code ? " on" : ""}`} onClick={() => switchMode(code)}>
              <span><b>{title}</b></span>
            </button>
          ))}
        </div>
        {switching && <span className="muted">Rebuilding from the same timings…</span>}
        {!switching && aiCount > 0 && (
          <span className="ai-count" data-testid="ai-count-review">
            <b>{aiCount} AI {aiCount === 1 ? "fix" : "fixes"}</b>{checkWith ? ` by ${checkWith}` : ""} · hover one for the original
          </span>
        )}
      </div>
      {checkMessage && <p className="stage-note muted" data-testid="check-message">{checkMessage}</p>}

      <div className="review-tools">
        <button className="loadtxt" onClick={addAtPlayhead} data-testid="add-cue">+ Cue at playhead</button>
        <label className="check loop"><input type="checkbox" checked={loop} onChange={e => setLoop(e.target.checked)} data-testid="loop" /> Loop this cue</label>
        <label className="check loop" title="Keep the playing subtitle in view in the list (pauses while you edit or scroll)">
          <input type="checkbox" checked={follow} onChange={e => toggleFollow(e.target.checked)} data-testid="follow" /> Follow playback</label>
        <span className="spacer" />
        <button className="loadtxt" onClick={undo} disabled={!hist.past.length} data-testid="undo" title="Undo (Ctrl+Z / ⌘Z)">Undo</button>
        <button className="loadtxt" onClick={redo} disabled={!hist.future.length} data-testid="redo" title="Redo (Ctrl+Shift+Z / ⇧⌘Z)">Redo</button>
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
        <div className="cuelist review-list" ref={listRef} onScroll={onListScroll} data-testid="review-list">
          {cues.map((c, i) => {
            const bad = issues.filter(p => p.i === i);
            const on = i === sel;
            const check = flagged(c);
            return (
              <div key={`${i}-${c.start}`} ref={el => (rowRefs.current[i] = el)}
                className={`rcue${i === playingIdx ? " playing" : ""}${on ? " on" : ""}${bad.length ? " bad" : ""}${check ? " check" : ""}`}
                onClick={() => { if (!on) { go(i); setEditing(false); } }} data-testid="rcue">
                <span className="cue-num">{String(i + 1).padStart(2, "0")}</span>
                <span className="cue-time" title="Start and end, exactly as in the SRT">{fmtMs(c.start)}<br />{fmtMs(c.end)}</span>
                <span className="rcue-text">
                  {on && editing ? (
                    <input ref={inputRef} defaultValue={c.text} aria-label="Cue text"
                      onKeyDown={e => {
                        if (e.key === "Enter") { e.preventDefault(); applyInput(i, e.currentTarget.value); }
                        if (e.key === "Escape") setEditing(false);
                      }}
                      onBlur={e => applyInput(i, e.currentTarget.value)} />
                  ) : (
                    <span onDoubleClick={() => { setSel(i); setEditing(true); }}>{cueText(c, i)}</span>
                  )}
                  {bad.length > 0 && <small className="rcue-bad">{bad.map(p => p.text).join(" · ")}</small>}
                  {check && (
                    <small className="rcue-check" data-testid="listen-flag" onClick={e => e.stopPropagation()}>
                      <b>Check this</b> · {c.listen.reasons.join(" · ")}
                      <button onClick={() => listen(i)} data-testid="listen-jump" title="Jump here and listen">▶ Listen</button>
                      <button onClick={() => looksRight(i)} data-testid="listen-ok">Looks right</button>
                    </small>
                  )}
                  {c.listen?.status === "fixed" && (
                    <small className="rcue-fixed" data-testid="listen-fixed">Fixed by the listening check · {c.listen.reasons.join(" · ")}</small>
                  )}
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

      {heardAny && (
        <div className={`listen-summary${toCheck ? "" : " clear"}`} data-testid="listen-summary"
          title={listenSummary?.seconds != null ? `Listened with ${listenSummary.model} in ${listenSummary.seconds} s` : undefined}>
          <span>
            Checked against the audio: {plural(okCount, "caption", "captions")} OK, {fixedCount} fixed,{" "}
            {toCheck === 1 ? "1 needs" : `${toCheck} need`} your check
          </span>
          {toCheck > 0
            ? <button className="loadtxt" onClick={nextToCheck} data-testid="listen-next">Next to check</button>
            : !unsureCount && <span className="verified-badge" data-testid="verified">Verified</span>}
        </div>
      )}
      {unsureCount > 0 && (
        <p className="stage-note listen-unsure" data-testid="unsure-count">
          {unsureCount === 1 ? "1 word" : `${unsureCount} words`} marked [?]: the AI check wasn't sure. Click a [?] once the word is right.
        </p>
      )}
      {!heardAny && listenSummary?.skipped && (
        <p className="stage-note muted" data-testid="listen-skipped">Listening check skipped — {listenSummary.skipped}.</p>
      )}

      <div className="download-row">
        <a className="dl-primary" href={`/api/jobs/${jobId}/srt`} onClick={download} data-testid="dl-srt">
          <span>
            <b>Download SRT {edited && <span className="edited-badge" data-testid="edited-badge">Edited</span>}
              {heardAny && !toCheck && !unsureCount && <span className="edited-badge verified" data-testid="verified-badge">Verified</span>}</b>
            <span>{srtName} · {cues.length} cues · {captionMode === "word" ? "word by word" : "line by line"}</span>
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

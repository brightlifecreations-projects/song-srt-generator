import { useEffect, useLayoutEffect, useRef, useState } from "react";

/* A letter of any script, including combining vowel signs (Devanagari, Urdu…). */
const WORDCHAR = /[\p{L}\p{M}\p{N}'’]/u;

/** Where a correction is in the current text, or null once the user has edited it away.
    Looks in its own line for the replacement as a whole word, nearest to where it was put. */
function locate(text, mark) {
  const lines = text.split("\n");
  if (mark.line >= lines.length) return null;
  const offset = lines.slice(0, mark.line).reduce((n, l) => n + l.length + 1, 0);
  const line = lines[mark.line];
  let best = null;
  for (let i = line.indexOf(mark.replacement); i !== -1; i = line.indexOf(mark.replacement, i + 1)) {
    const end = i + mark.replacement.length;
    if ((i > 0 && WORDCHAR.test(line[i - 1])) || (end < line.length && WORDCHAR.test(line[end]))) continue;
    if (best === null || Math.abs(i - mark.start) < Math.abs(best - mark.start)) best = i;
  }
  return best === null ? null : { from: offset + best, to: offset + best + mark.replacement.length };
}

/** Live positions of the corrections still in the text, in order. */
export function liveMarks(text, marks) {
  return marks
    .map(m => ({ ...m, at: locate(text, m) }))
    .filter(m => m.at)
    .sort((a, b) => a.at.from - b.at.from)
    .filter((m, i, all) => i === 0 || m.at.from >= all[i - 1].at.to);
}

/** The draft box: a textarea with the auto-corrected words highlighted behind it.
    Hovering a highlight shows the original word with Undo. */
export default function DraftEditor({ value, onChange, marks, onUndo, readOnly, placeholder }) {
  const areaRef = useRef(null);
  const backRef = useRef(null);
  const wrapRef = useRef(null);
  const hideTimer = useRef(null);
  const [tip, setTip] = useState(null); // {mark, x, y}
  const [gutter, setGutter] = useState(0);
  const live = liveMarks(value, marks);

  // The textarea's scrollbar narrows its text; the backdrop must wrap at the same width.
  useLayoutEffect(() => {
    const area = areaRef.current;
    if (!area) return;
    const measure = () => setGutter(area.offsetWidth - area.clientWidth - 2);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(area);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (backRef.current && areaRef.current) backRef.current.scrollTop = areaRef.current.scrollTop;
  });

  function segments() {
    const out = [];
    let pos = 0;
    live.forEach(m => {
      if (m.at.from > pos) out.push(value.slice(pos, m.at.from));
      out.push(<mark key={m.id} data-id={m.id}>{value.slice(m.at.from, m.at.to)}</mark>);
      pos = m.at.to;
    });
    out.push(value.slice(pos) + "\n");
    return out;
  }

  function onMove(e) {
    if (!live.length || !backRef.current) return;
    for (const el of backRef.current.querySelectorAll("mark")) {
      for (const r of el.getClientRects()) {
        if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
          const box = wrapRef.current.getBoundingClientRect();
          clearTimeout(hideTimer.current);
          setTip({ mark: live.find(m => m.id === el.dataset.id), x: r.left - box.left, y: r.bottom - box.top + 4 });
          return;
        }
      }
    }
    scheduleHide();
  }

  function scheduleHide() {
    clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => setTip(null), 350);
  }

  return (
    <div className={`drafteditor${readOnly ? " readonly" : ""}`} ref={wrapRef}>
      <div className="drafteditor-back lyrics" ref={backRef} aria-hidden="true"
        style={{ paddingRight: 18 + gutter }}>{segments()}</div>
      <textarea
        ref={areaRef}
        className="lyrics"
        value={value}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        onScroll={() => { if (backRef.current) backRef.current.scrollTop = areaRef.current.scrollTop; }}
        onMouseMove={onMove}
        onMouseLeave={scheduleHide}
        readOnly={readOnly}
        spellCheck={false}
      />
      {tip?.mark && (
        <div className="drafttip" role="tooltip" style={{ left: tip.x, top: tip.y }}
          onMouseEnter={() => clearTimeout(hideTimer.current)} onMouseLeave={scheduleHide}>
          Auto-corrected from <b>“{tip.mark.original}”</b>
          <button onClick={() => { onUndo(tip.mark); setTip(null); }}>Undo</button>
        </div>
      )}
    </div>
  );
}

/* Review panel checks and automatic fixes (V4.5), kept apart so they can be tested on their own. */

export const MIN_CUE = 0.3;
export const fmt = t => {
  if (!isFinite(t)) return "0:00.0";
  const m = Math.floor(t / 60);
  return `${m}:${(t % 60).toFixed(1).padStart(4, "0")}`;
};
export const r3 = t => Math.round(t * 1000) / 1000;

/** Everything that would look wrong on screen: overlaps, very short cues, over-long lines. */
export function problems(cues, cap) {
  const out = [];
  cues.forEach((c, i) => {
    const next = cues[i + 1];
    if (next && c.end > next.start + 0.001) out.push({ i, kind: "overlap", text: `overlaps cue ${i + 2}` });
    if (c.end - c.start < MIN_CUE) out.push({ i, kind: "short", text: `shorter than ${MIN_CUE} s` });
    if (cap && c.text.length > cap) out.push({ i, kind: "long", text: `${c.text.length} characters (max ${cap})` });
  });
  return out;
}

/** Fix automatically, in an order that never reorders text: pull back overlapping ends, split
    over-long lines at the most balanced word boundary within the cue's own time (time shared by
    length), then lengthen very short cues to MIN_CUE, nudging the following cues later where
    they'd overlap (each stays at least MIN_CUE long). */
export function autoFix(cues, cap) {
  const sorted = cues.map(c => ({ ...c })).sort((a, b) => a.start - b.start);
  sorted.forEach((c, i) => {
    const next = sorted[i + 1];
    if (next && c.end > next.start) c.end = r3(Math.max(c.start + 0.05, next.start - 0.01));
  });
  const out = [];
  for (const c of sorted) {
    if (!cap || c.text.length <= cap) { out.push(c); continue; }
    const pieces = [];
    let cur = "";
    for (const w of c.text.split(" ")) {
      if (cur && (cur + " " + w).length > cap) { pieces.push(cur); cur = w; } else cur = cur ? cur + " " + w : w;
    }
    if (cur) pieces.push(cur);
    const total = pieces.reduce((n, p) => n + p.length, 0) || 1;
    let t = c.start;
    pieces.forEach((p, k) => {
      const end = k === pieces.length - 1 ? c.end : r3(t + (c.end - c.start) * p.length / total);
      out.push({ start: r3(t), end, text: p });
      t = r3(end + (k === pieces.length - 1 ? 0 : 0.01));
    });
  }
  for (let i = 0; i < out.length; i++) {
    if (out[i].end - out[i].start >= MIN_CUE) continue;
    out[i].end = r3(out[i].start + MIN_CUE);
    for (let j = i + 1; j < out.length; j++) {
      const prev = out[j - 1], c = out[j];
      if (c.start >= prev.end + 0.01) break;
      c.start = r3(prev.end + 0.01);
      if (c.end - c.start < MIN_CUE) c.end = r3(c.start + MIN_CUE);
    }
  }
  return out;
}

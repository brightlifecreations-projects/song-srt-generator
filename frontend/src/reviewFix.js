/* Review panel checks and automatic fixes (V4.5), kept apart so they can be tested on their own. */

/* V4.6: short captions are allowed (word by word especially); only a cue too short to see is flagged. */
export const MIN_CUE = 0.08;  // = captions.MIN_VISIBLE: what the generator itself guarantees
export const fmt = t => {
  if (!isFinite(t)) return "0:00.0";
  const m = Math.floor(t / 60);
  return `${m}:${(t % 60).toFixed(1).padStart(4, "0")}`;
};
/** m:ss.mmm — to the millisecond, as the SRT has it (the list used to round to 0.1 s). */
export const fmtMs = t => {
  const ms = Math.max(0, Math.round((isFinite(t) ? t : 0) * 1000));
  const m = Math.floor(ms / 60000), s = Math.floor(ms / 1000) % 60;
  return `${m}:${String(s).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
};
export const r3 = t => Math.round(t * 1000) / 1000;
/** Characters on screen: combining marks (Devanagari vowel signs…) and joiners don't count. */
export const displayLen = text => text.replace(/[\p{M}\u200c\u200d]/gu, "").length;
/** Shorter than MIN_CUE — to the millisecond, so a cue Fix automatically made 0.3 s long doesn't
    read as 0.29999… (floating point) and stay flagged. */
const tooShort = c => c.end - c.start < MIN_CUE - 0.0005;

/** Everything that would look wrong on screen: overlaps, very short cues, over-long lines. */
export function problems(cues, cap) {
  const out = [];
  cues.forEach((c, i) => {
    const next = cues[i + 1];
    if (next && c.end > next.start + 0.001) out.push({ i, kind: "overlap", text: `overlaps cue ${i + 2}` });
    if (tooShort(c)) out.push({ i, kind: "short", text: `shorter than ${MIN_CUE} s` });
    if (cap && displayLen(c.text) > cap) out.push({ i, kind: "long", text: `${displayLen(c.text)} characters (max ${cap})` });
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
    if (!cap || displayLen(c.text) <= cap) { out.push(c); continue; }
    const pieces = [];
    let cur = "";
    for (const w of c.text.split(" ")) {
      if (cur && displayLen(cur + " " + w) > cap) { pieces.push(cur); cur = w; } else cur = cur ? cur + " " + w : w;
    }
    if (cur) pieces.push(cur);
    const total = pieces.reduce((n, p) => n + p.length, 0) || 1;
    let t = c.start;
    pieces.forEach((p, k) => {
      const end = k === pieces.length - 1 ? c.end : r3(t + (c.end - c.start) * p.length / total);
      out.push({ start: r3(t), end, text: p });  // (a split line drops its AI highlights)
      t = r3(end + (k === pieces.length - 1 ? 0 : 0.01));
    });
  }
  for (let i = 0; i < out.length; i++) {
    if (!tooShort(out[i])) continue;
    out[i].end = r3(out[i].start + MIN_CUE);
    for (let j = i + 1; j < out.length; j++) {
      const prev = out[j - 1], c = out[j];
      if (c.start >= prev.end + 0.01) break;
      c.start = r3(prev.end + 0.01);
      if (tooShort(c)) c.end = r3(c.start + MIN_CUE);
    }
  }
  return out;
}

/* V4.8 clean captions: letters, digits, spaces, full stops, apostrophes inside words, % after a
   number and a time's colon; every other symbol goes. The same rules as captions.clean_caption()
   on the server, character for character, so what the panel shows is what the SRT gets. */
const STOPS = new Set([".", "\u0964"]);           // the full stop, and Hindi's (the danda)
const APOSTROPHES = new Set(["'", "\u2019"]);
const DASHES = new Set([..."-\u2010\u2011\u2012\u2013\u2014\u2015\u2212/\\|_~"]);
const JOINERS = new Set(["\u200c", "\u200d"]);
const wordy = ch => !!ch && /[\p{L}\p{M}\p{N}]/u.test(ch);
const digit = ch => !!ch && /\p{Nd}/u.test(ch);
export function cleanCaption(text) {
  const chars = Array.from(String(text));
  let out = "";
  chars.forEach((ch, k) => {
    const prev = chars[k - 1] || "", next = chars[k + 1] || "";
    if (ch >= "\ufe00" && ch <= "\ufe0f") return;  // emoji variation selectors
    if (wordy(ch)) out += ch;
    else if (/\s/u.test(ch)) out += " ";
    else if (STOPS.has(ch)) {
      if (ch === "." && (prev === "." || next === "." || prev === "\u2026" || next === "\u2026")) return;
      out += ch;
    } else if (APOSTROPHES.has(ch)) { if (wordy(prev) && wordy(next)) out += ch; }
    else if (ch === "%") { if (digit(prev)) out += ch; }
    else if (ch === ":") { if (digit(prev) && digit(next)) out += ch; }
    else if (JOINERS.has(ch)) { if (wordy(prev) && wordy(next)) out += ch; }
    else if (DASHES.has(ch)) { if (wordy(prev) && wordy(next)) out += " "; }
  });
  const tidy = [];
  for (const w of out.split(" ").filter(Boolean)) {
    if (Array.from(w).every(c => STOPS.has(c))) {
      if (tidy.length && !STOPS.has(tidy[tidy.length - 1].slice(-1))) tidy[tidy.length - 1] += w[0];
      continue;
    }
    tidy.push(w.replace(/^[.\u0964]+/u, "") || w);
  }
  return tidy.join(" ");
}

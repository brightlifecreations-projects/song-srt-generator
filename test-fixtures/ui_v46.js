// v4.6 end-to-end UI checks (Windows: run by ui_edge.py; files, in order:
//   sample/clip.wav, drama.mp4, a reel video with a Hindi file name, hinglish.mp3).
// Every page and mode; Line by line and Word by word; the Review panel against the file the
// browser actually downloaded (count, start, end, text, CRLF, no BOM); unsaved edits; the final AI
// check against test-fixtures/mock_ai.py (args.mock = its base URL) and without it; Corrections,
// number exceptions, AI & API keys (Credential Manager), Updates.
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const navTo = async label => { $$(".navitem").find(b => b.textContent.trim() === label || b.title === label).click(); await sleep(400); };
const ws = () => document.querySelector(".workspace:not([hidden])");
const rows = () => $$("[data-testid=rcue]", ws());
const setVal = (el, value) => {
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
};
const key = (k, el = window) => el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
const drop = f => {
  const dt = new DataTransfer(); dt.items.add(f);
  ws().querySelector(".workbench").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
};
const pickMode = label => $$(".mode", ws()).find(b => b.textContent.includes(label)).click();
const goBtn = () => ws().querySelector("button.go");
const [song, dramaVideo, reelVideo, hinglishAudio] = files;
const MOCK = window.__mock;
const timing = {};
const t0 = () => performance.now();
const secs = t => Math.round((performance.now() - t) / 100) / 10;
const results = [];
const check = (name, ok, detail = "") => { results.push([name, !!ok]); log((ok ? "PASS " : "FAIL ") + name + (detail ? " — " + detail : "")); };

/** The Review list as shown: [ms start, ms end, text] (AI popovers left out). */
function shown() {
  return rows().map(r => {
    const [a, b] = r.querySelector(".cue-time").innerText.split("\n").map(s => {
      const m = s.trim().match(/^(\d+):(\d\d)\.(\d{3})$/);
      return (+m[1] * 60 + +m[2]) * 1000 + +m[3];
    });
    const span = r.querySelector(".rcue-text > span")?.cloneNode(true);
    span?.querySelectorAll(".ai-fix-pop").forEach(x => x.remove());
    const text = (span?.textContent ?? r.querySelector("input")?.value ?? "").split(/\s+/).filter(Boolean).join(" ");
    return [a, b, text];
  });
}
function parseSrt(text) {
  if (!text.endsWith("\r\n")) throw new Error("SRT doesn't end with CRLF");
  return text.slice(0, -2).split("\r\n\r\n").filter(b => b.trim()).map((b, n) => {
    const [num, times, ...rest] = b.split("\r\n");
    if (num !== String(n + 1) || rest.length !== 1) throw new Error("bad SRT block " + JSON.stringify(b));
    const ms = s => { const m = s.match(/(\d\d):(\d\d):(\d\d),(\d{3})/); return ((+m[1] * 60 + +m[2]) * 60 + +m[3]) * 1000 + +m[4]; };
    const [a, c] = times.split(" --> ");
    return [ms(a), ms(c), rest[0]];
  });
}
/** Download SRT through the real link; the saved file must equal the list, cue for cue. */
async function compareDownload(label) {
  const before = shown();
  const file = await download(() => ws().querySelector("[data-testid=dl-srt]").click());
  if (!file) { check(`${label}: downloaded`, false, "no file"); return null; }
  const after = shown();
  const srt = parseSrt(file.text);
  const same = JSON.stringify(after) === JSON.stringify(srt);
  check(`${label}: download = Review list (${srt.length} cues)`, same,
    same ? "" : `list ${JSON.stringify(after.slice(0, 3))}… file ${JSON.stringify(srt.slice(0, 3))}…`);
  check(`${label}: CRLF, UTF-8, no BOM`, file.crlf === file.lf && !file.bom && file.crlf > 0, `${file.name} crlf=${file.crlf} lf=${file.lf}`);
  return { file, srt, before, after };
}
async function waitDone(what, ms = 600000) {
  await until(() => ws().querySelector("[data-testid=review]") && !ws().querySelector(".proc") && rows().length
                   || ws().querySelector(".errbox"), ms, what);
  const err = ws().querySelector(".errbox");
  if (err) throw new Error(`${what}: ${err.textContent}`);
}
const warnings = () => $$(".results .warnbox", ws()).map(w => w.textContent);
async function switchTo(mode) {
  const label = mode === "word" ? "Word by word" : "Line by line";
  const before = ws().querySelector("[data-testid=review]");
  $$("[data-testid=review-capmode] button", ws()).find(b => b.textContent.includes(label)).click();
  await until(() => { const r = ws().querySelector("[data-testid=review]"); return r && r !== before && rows().length; }, 120000, "rebuild " + mode);
  await sleep(300);
}
const lineRules = (list, maxWords = 5, maxChars) => list.every(([, , t]) => t.split(" ").length <= maxWords && (!maxChars || t.length <= maxChars));
const noOverlap = list => list.every((c, i) => i === 0 || list[i - 1][1] <= c[0]);
const WEAK = new Set("a an the to of and but or for from with by at in on is are was were just my your his her their this that".split(" "));
const weakEnds = list => list.slice(0, -1).filter(([, , t]) => WEAK.has(t.split(" ").at(-1).toLowerCase().replace(/[^\w']/g, "")));

await until(() => $$(".navitem").length, 20000, "sidebar");
await fetch("/api/ai", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ final_check: false }) });

// ---------------------------------------------------------------- Song: audio, lyrics, both modes
let t = t0();
await navTo("Song");
pickMode("I have the lyrics"); await sleep(200);
drop(song); await until(() => ws().querySelector(".filechip-name"), 5000, "file");
setVal(ws().querySelector("textarea.lyrics"), "My ex-wife left me for a guy twenty two years younger\nAnd then I ran into them both at Walmart");
await sleep(100);
goBtn().click();
await waitDone("song lyrics");
timing.song_lyrics_s = secs(t);
let list = shown();
log("Song cues:", list.map(c => c[2]));
check("Song: 5 words max, 30 chars, no overlaps", lineRules(list, 5, 30) && noOverlap(list));
check("Song: numbers as digits", list.some(c => c[2].includes("22 years")), list.map(c => c[2]).join(" | "));
check("Song: no weak-word endings", !weakEnds(list).length, JSON.stringify(weakEnds(list)));
await compareDownload("Song line");
// An edit still being typed, then Download at once (the 4.5 panel downloaded the old file here).
rows()[1].click(); await sleep(150);
key("Enter"); await until(() => ws().querySelector(".rcue.on input"), 3000, "edit box");
setVal(ws().querySelector(".rcue.on input"), "me for  a GUY");
let res = await compareDownload("Song unsaved edit");
check("Song: the typed edit is in the downloaded file", res && res.srt.some(c => c[2] === "me for a GUY"), JSON.stringify(res?.srt.map(c => c[2])));
check("Song: Edited badge", !!ws().querySelector("[data-testid=edited-badge]"));
// Nudge + immediately download (inside the old 700 ms autosave window)
rows()[2].click(); await sleep(100);
$$("button", ws().querySelector(".rcue.on")).find(b => b.getAttribute("aria-label") === "End −0.1 s").click();
res = await compareDownload("Song nudge then download");
const original = await download(() => ws().querySelector("[data-testid=dl-original]").click());
check("Song: original is unchanged", original && !original.text.includes("GUY"));
t = t0();
await switchTo("word");
timing.song_switch_to_word_s = secs(t);
list = shown();
check("Song word by word: one word per cue", list.every(c => !c[2].includes(" ")), list.length + " cues");
check("Song word by word: no overlaps, no tiny gaps", noOverlap(list) &&
  list.every((c, i) => i === 0 || c[0] - list[i - 1][1] === 0 || c[0] - list[i - 1][1] >= 900));
await compareDownload("Song word");
await snap("song-word");
await switchTo("line");
check("Song: back to line by line", shown().every(c => c[2].split(" ").length <= 5) && shown().length < list.length);
await compareDownload("Song line again");

// ---------------------------------------------------------------- Drama: video, transcribe
t = t0();
await navTo("Drama");
pickMode("Transcribe for me"); await sleep(200);
drop(dramaVideo); await until(() => ws().querySelector(".filechip-name"), 5000, "video");
await until(() => !goBtn().disabled, 5000, "ready");
goBtn().click();
await until(() => ws().querySelector("[data-testid=review]") || (goBtn().textContent.includes("Approve") && !ws().querySelector(".proc")), 600000, "drama draft");
timing.drama_transcribe_s = secs(t);
log("Drama draft:", ws().querySelector("textarea.lyrics").value);
t = t0();
goBtn().click();
await waitDone("drama approve");
timing.drama_align_s = secs(t);
list = shown();
log("Drama cues:", list.map(c => c[2]));
check("Drama: horizontal 42 chars, 5 words, no overlaps", lineRules(list, 5, 42) && noOverlap(list));
check("Drama: new caption at each pause (turns not merged)", !list.some(c => /night\?? I was|that\.? You/i.test(c[2])));
check("Drama: numbers", list.some(c => /\b2 hours/.test(c[2])), list.map(c => c[2]).join(" | "));
await compareDownload("Drama transcribe line");
await switchTo("word");
await compareDownload("Drama transcribe word");
await snap("drama-video-word");

// ---------------------------------------------------------------- Reels: Hindi-named video with music, script with speakers
t = t0();
await navTo("Reels / YouTube");
pickMode("I have the script"); await sleep(200);
drop(reelVideo); await until(() => ws().querySelector(".filechip-name"), 5000, "reel");
const SCRIPT = "Coach: For twelve years I have been waking up at seven a.m. to train.\n" +
  "Maya: In week one we lost seventy percent of our budget on ads that nobody even watched.\n" +
  "Coach: Our sponsors from Berlin and Tokyo will announce the winners next Thursday evening.\n" +
  "Maya: She walked into the store for her son and asked for the shoes in a size four.\n" +
  "Coach: I am not giving up one inch.";
setVal(ws().querySelector("textarea.lyrics"), SCRIPT);
await sleep(100);
goBtn().click();
await waitDone("reels lyrics");
timing.reels_lyrics_s = secs(t);
list = shown();
log("Reels cues:", list.map(c => c[2]));
log("Reels warnings:", warnings());
check("Reels: speaker labels removed", !list.some(c => /Coach|Maya/.test(c[2])));
check("Reels: the unspoken script line is left out and listed",
  !list.some(c => /Berlin|Tokyo|Thursday/.test(c[2])) && !warnings().some(w => w.endsWith(".”.")) && warnings().some(w => w.includes("weren't found in the audio") && w.includes("Berlin")),
  warnings().join(" / "));
check("Reels: digits (12 years, 7 a.m., Week 1/week 1, 70%, size 4)",
  ["12 years", "7 a.m.", "1", "70%", "size 4"].every(s => list.some(c => c[2].includes(s))), list.map(c => c[2]).join(" | "));
check("Reels: 'one inch' stays words", list.some(c => /one inch/.test(c[2])));
check("Reels: 30 chars, 5 words, no overlaps, no weak endings", lineRules(list, 5, 30) && noOverlap(list) && !weakEnds(list).length,
  JSON.stringify(weakEnds(list)));
check("Reels: phrases kept whole", !list.some(c => /\b(for|her|a)$/.test(c[2])) && list.some(c => c[2].includes("size 4")));
check("Reels: a new caption at each speaker", !list.some(c => /train\. In|watched\. She|4\. I am/.test(c[2])));
res = await compareDownload("Reels line (Hindi file name)");
check("Reels: Hindi file name kept", res && res.file.name.includes("गाना"), res?.file.name);
check("Reels: music detected", (ws().querySelector(".music-note")?.textContent || "").includes("Music detected"),
  ws().querySelector(".music-note")?.textContent);
await switchTo("word");
await compareDownload("Reels word");
await snap("reels-word");
await switchTo("line");

// ---------------------------------------------------------------- Hinglish: transcribe, Fast then Accurate (Song page)
for (const quality of ["Fast", "Accurate"]) {
  t = t0();
  await navTo("Song");
  pickMode("Transcribe for me"); await sleep(300);
  drop(hinglishAudio); await until(() => ws().querySelector(".filechip-name"), 5000, "hinglish");
  const scriptSel = $$("select", ws())[1];
  scriptSel.value = "hinglish"; scriptSel.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(200);
  const more = ws().querySelector("[data-testid=more-options]"); if (!more.open) more.querySelector("summary").click();
  await sleep(200);
  $$(".speedpick button", ws()).find(b => b.textContent.includes(quality)).click();
  await sleep(100);
  goBtn().click();
  await until(() => goBtn().textContent.includes("Approve") && !ws().querySelector(".proc") || ws().querySelector(".errbox"), 900000, "hinglish draft");
  if (ws().querySelector(".errbox")) throw new Error(ws().querySelector(".errbox").textContent);
  timing[`hinglish_${quality.toLowerCase()}_transcribe_s`] = secs(t);
  const draft = ws().querySelector("textarea.lyrics").value;
  log(`Hinglish ${quality} draft:`, draft);
  check(`Hinglish ${quality}: draft in Roman letters`, /[a-z]/i.test(draft) && !/[ऀ-ॿ؀-ۿ]/.test(draft));
  t = t0();
  goBtn().click();
  await waitDone("hinglish approve");
  timing[`hinglish_${quality.toLowerCase()}_align_s`] = secs(t);
  list = shown();
  log(`Hinglish ${quality} cues:`, list.map(c => c[2]));
  check(`Hinglish ${quality}: rules`, lineRules(list, 5, 30) && noOverlap(list));
  await compareDownload(`Hinglish ${quality}`);
}

// ---------------------------------------------------------------- Final AI check (mock provider), then a broken answer
if (MOCK) {
  let s = await (await fetch("/api/ai", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: "custom", models: { custom: "mock-model" }, custom_base_url: MOCK + "/ok/v1", final_check: true }) })).json();
  check("AI keys: custom local provider usable without a key", s.usable, s.why_not);
  t = t0();
  await navTo("Reels / YouTube");
  pickMode("Transcribe for me"); await sleep(200); pickMode("I have the script"); await sleep(200);
  drop(reelVideo); await until(() => ws().querySelector(".filechip-name"), 5000, "reel");
  setVal(ws().querySelector("textarea.lyrics"), SCRIPT);
  await sleep(100);
  goBtn().click();
  await waitDone("reels with final check");
  timing.reels_with_ai_check_s = secs(t);
  const fixes = $$("[data-testid=ai-fix]", ws());
  log("AI fixes:", fixes.map(f => f.title));
  check("AI check: changes highlighted with the original", fixes.length === 2 && fixes.every(f => /AI changed “.+” to “.+”/.test(f.title)));
  check("AI check: count shown", (ws().querySelector("[data-testid=ai-count-review]")?.textContent || "").includes("2 AI fixes"));
  res = await compareDownload("AI-checked line");
  // Undo the capital (keep the spelling fix), download again.
  const capFix = fixes.find(f => /“[a-z]+” to “[A-Z]/.test(f.title));
  capFix.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
  capFix.querySelector("[data-testid=ai-undo]").click();
  await sleep(200);
  check("AI check: undo removes one highlight", $$("[data-testid=ai-fix]", ws()).length === 1);
  res = await compareDownload("AI-checked after undo");
  await sleep(500);
  const corr = await (await fetch("/api/corrections")).json();
  const aiRows = corr.tables.flatMap(tb => tb.rows).filter(r => r.source === "AI");
  check("AI check: kept spelling fix learnt in corrections.md with Source AI", aiRows.length >= 1, JSON.stringify(aiRows));
  await switchTo("word");
  check("AI check: word by word keeps the AI fix", $$("[data-testid=ai-fix]", ws()).length >= 1);
  await compareDownload("AI-checked word");
  const fixEl = ws().querySelector("[data-testid=ai-fix]");
  fixEl?.scrollIntoView({ block: "center" }); fixEl?.focus(); await sleep(300);
  await snap("reels-ai-check");
  // A provider answer that isn't JSON: rule-based captions, one line saying so.
  // Broken answers, each on a fresh SRT (a finished job keeps the check it already had):
  // rule-based captions, no highlights, one line saying why.
  for (const [mode, why] of [["badjson", "the AI's answer wasn't valid JSON"], ["wordcount", "the AI changed the number of words"]]) {
    await fetch("/api/ai", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ custom_base_url: `${MOCK}/${mode}/v1` }) });
    const before = ws().querySelector("[data-testid=review]");
    goBtn().click();
    await until(() => { const r = ws().querySelector("[data-testid=review]"); return r && r !== before && rows().length && !ws().querySelector(".proc"); }, 600000, mode);
    await sleep(300);
    check(`AI check, ${mode}: one-line notice`, warnings().some(w => w === `Final AI check skipped — ${why}. The rule-based captions were used.`), warnings().join(" / "));
    check(`AI check, ${mode}: no highlights`, !$$("[data-testid=ai-fix]", ws()).length);
    await compareDownload(`AI check ${mode}`);
  }
  await fetch("/api/ai", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ final_check: false, custom_base_url: "" , provider: "anthropic"}) });
}

// ---------------------------------------------------------------- Corrections, number exceptions
await navTo("Corrections");
await until(() => document.querySelector("[data-testid=number-exceptions]"), 5000, "numbers editor");
const box = document.querySelector("[data-testid=number-exceptions] textarea");
check("Numbers: defaults listed", box.value.includes("not one") && box.value.includes("you two"));
setVal(box, box.value + "\nseven a.m."); await sleep(100);
document.querySelector("[data-testid=numbers-save]").click(); await sleep(500);
let ne = await (await fetch("/api/number-exceptions")).json();
check("Numbers: saved", ne.edited && ne.phrases.includes("seven a.m."), ne.path);
await fetch("/api/number-exceptions", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reset: true }) });
await snap("corrections");

// ---------------------------------------------------------------- AI & API keys: a key in Windows Credential Manager
await navTo("AI & API keys");
await until(() => document.querySelector("[data-testid=final-check]"), 5000, "final check switch");
check("AI keys: final check switch present", !!document.querySelector("[data-testid=final-check] input[type=checkbox]"));
let k = await (await fetch("/api/ai/key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: "openai", key: "sk-test-srt-generator-v46" }) })).json();
check("AI keys: key saved in the system key store", k.providers?.find(p => p.code === "openai")?.has_key && k.key_store, JSON.stringify(k.error || ""));
window.__keySaved = true;
await snap("ai-keys");

// ---------------------------------------------------------------- Updates
await navTo("Updates");
await sleep(1500);
const upd = await (await fetch("/api/updates")).json();
log("Updates:", upd);
check("Updates: GitHub mode (no Sparkle on Windows)", upd.mode === "github");
await snap("updates");

log("TIMINGS", timing);
const failed = results.filter(r => !r[1]).map(r => r[0]);
log(`RESULT ${results.length - failed.length}/${results.length} passed`, failed);
return { timing, failed };

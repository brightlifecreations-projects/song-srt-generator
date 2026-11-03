// v4.5 UI checks (run by ui_check.py with files clip.mp3, lovika.mov and a screenshot dir):
// Review panel on audio (Song, lyrics) and video (Reels, lyrics): edit, nudge, split, merge, undo,
// redo, loop, add, delete, re-time, keyboard, Fix automatically, downloads, edits kept after leaving
// the page; Updates page; screenshots of both in light and dark.
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const navTo = async label => { $$(".navitem").find(b => b.textContent.trim() === label || b.title === label).click(); await sleep(400); };
const ws = () => document.querySelector(".workspace:not([hidden])");
const rows = () => $$("[data-testid=rcue]", ws());
const cueText = () => rows().map(r => r.querySelector(".rcue-text > span")?.textContent || r.querySelector("input")?.value);
const sel = () => ws().querySelector(".rcue.on");
const btn = (label, root = sel()) => $$("button", root).find(b => b.textContent.trim() === label || b.getAttribute("aria-label") === label);
const setVal = (el, value) => {
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
};
const key = k => window.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
const drop = f => {
  const dt = new DataTransfer(); dt.items.add(f);
  ws().querySelector(".workbench").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
};
const times = () => rows().map(r => r.querySelector(".cue-time").innerText.replace("\n", "–"));
const [mp3, mov] = files;

// ---------------------------------------------------------------- Song: audio, lyrics mode
await until(() => $$(".navitem").length, 20000, "sidebar");
log("sidebar has Updates:", $$(".navitem").some(b => b.textContent.trim() === "Updates"));
await navTo("Song");
$$(".mode", ws()).find(b => b.textContent.includes("I have the lyrics")).click(); await sleep(200);
drop(mp3); await until(() => ws().querySelector(".filechip-name"), 5000, "file");
setVal(ws().querySelector("textarea.lyrics"), "If it is meant to be it's gonna come back to you\nIf it's not, then it wasn't meant for you");
$$("button.go", ws())[0].click();
await until(() => ws().querySelector("[data-testid=review]") || ws().querySelector(".errbox"), 300000, "review panel");
await sleep(1500);
log("review panel:", !!ws().querySelector("[data-testid=review]"), "| cues:", rows().length, "| texts:", cueText());
log("waveform subtitle line:", !!ws().querySelector("[data-testid=wave-sub]"), "| transport:", !!ws().querySelector(".review-transport .scrubber"));
const jobId = ws().querySelector("[data-testid=dl-srt]").getAttribute("href").split("/")[3];

// select cue 1, edit text with Enter
rows()[0].click(); await sleep(400);
key("Enter"); await sleep(200);
let inp = sel().querySelector("input");
setVal(inp, "If it is meant to be it is");
inp.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); await sleep(300);
log("after edit:", cueText()[0]);
// nudge start +0.1, end −0.1
const t0 = times()[0];
btn("Start +0.1 s").click(); await sleep(150); btn("End −0.1 s").click(); await sleep(150);
log("nudge:", t0, "→", times()[0]);
// split at the cursor (edit mode, caret after "If it is")
key("Enter"); await sleep(200);
inp = sel().querySelector("input"); inp.setSelectionRange(8, 8);
btn("Split").dispatchEvent(new MouseEvent("mousedown", { bubbles: true })); btn("Split").click(); await sleep(300);
log("after split:", rows().length, "cues |", cueText().slice(0, 3));
// merge it back
btn("Merge next").click(); await sleep(300);
log("after merge:", rows().length, "cues |", cueText()[0]);
// undo merge, redo merge
ws().querySelector("[data-testid=undo]").click(); await sleep(200);
log("undo merge:", rows().length, "cues");
ws().querySelector("[data-testid=redo]").click(); await sleep(200);
log("redo merge:", rows().length, "cues");
// keyboard: ArrowDown moves selection, Space plays/pauses
key("ArrowDown"); await sleep(300);
log("ArrowDown selects cue:", rows().indexOf(sel()) + 1);
key(" "); await sleep(1200);
const playingNow = ws().querySelector(".review-transport .playbtn").getAttribute("aria-label");
key(" "); await sleep(300);
log("Space toggled playback:", playingNow, "→", ws().querySelector(".review-transport .playbtn").getAttribute("aria-label"));
// loop this cue (cue 1, the longest)
rows()[0].click(); await sleep(300);
ws().querySelector("[data-testid=loop]").click();
ws().querySelector(".review-transport .playbtn").click();
const c2 = times()[0];
await sleep(4500);
const tt = ws().querySelector(".review-transport .mono").textContent;
log("loop on cue 1 (", c2, ") — playhead after 4.5 s:", tt);
ws().querySelector("[data-testid=loop]").click();
ws().querySelector(".review-transport .playbtn").click(); await sleep(200);
if (ws().querySelector(".review-transport .playbtn").getAttribute("aria-label") === "Pause") ws().querySelector(".review-transport .playbtn").click();
// add a cue at the playhead, then delete it
const before = rows().length;
ws().querySelector("[data-testid=add-cue]").click(); await sleep(400);
const addedInput = sel()?.querySelector("input");
if (addedInput) { setVal(addedInput, "Extra line"); addedInput.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); }
await sleep(300);
log("add cue:", before, "→", rows().length, "| new text present:", cueText().includes("Extra line"));
const extra = rows().find(r => r.textContent.includes("Extra line")); extra.click(); await sleep(300);
btn("Delete").click(); await sleep(300);
log("delete:", rows().length, "cues");
// validation: make an overlap with End +0.1 repeated, then Fix automatically
rows()[0].click(); await sleep(300);
for (let k = 0; k < 40; k++) { btn("End +0.1 s").click(); await sleep(20); }
await sleep(300);
log("issues shown:", ws().querySelector("[data-testid=issues]")?.textContent);
ws().querySelector("[data-testid=autofix]").click(); await sleep(400);
log("after Fix automatically:", ws().querySelector("[data-testid=issues]")?.textContent || "no issues", "|", times().slice(0, 2));
// re-time a line after a big text change
const gi = cueText().findIndex(x => x.startsWith("gonna"));
rows()[gi].click(); await sleep(300);
key("Enter"); await sleep(200);
inp = sel().querySelector("input"); setVal(inp, "gonna come back");
inp.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); await sleep(400);
rows()[gi].click(); await sleep(300);
const rt0 = times()[gi];
btn("Re-time line").click();
setVal; // text was changed earlier in this session
await until(() => /Re-timed|failed|Couldn/.test(ws().querySelector("[data-testid=notice]")?.textContent || ""), 120000, "re-time");
log("re-time:", rt0, "→", times()[gi], "|", ws().querySelector("[data-testid=notice]").textContent);
await sleep(1500);  // autosave
// downloads
log("Edited badge:", !!ws().querySelector("[data-testid=edited-badge]"));
const edited = await (await fetch(ws().querySelector("[data-testid=dl-srt]").getAttribute("href"))).text();
const original = await (await fetch(ws().querySelector("[data-testid=dl-original]").getAttribute("href"))).text();
const valid = srt => srt.trim().split(/\n\n+/).every((b, i) => { const l = b.split("\n"); return l[0] === String(i + 1) && /^\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}$/.test(l[1]) && l[2]; });
log("edited SRT valid:", valid(edited), "| has edits:", edited.includes("If it is meant to be it") && edited.includes("gonna come back\n"), "| original unchanged:", original.includes("If it is meant to be it's"), "| differ:", edited !== original);
const corr = await (await fetch("/api/corrections")).json();
log("corrections learnt from the edits:", corr.tables.flatMap(t => t.rows.filter(r => r.last_seen === new Date().toISOString().slice(0, 10)).map(r => `${r.wrong}→${r.correct} [${r.source}]`)));
// leave the page and come back
const kept = cueText().join(" | ");
await navTo("Drama"); await navTo("AI & API keys"); await navTo("Song");
log("edits kept after leaving the page:", cueText().join(" | ") === kept);
const server = await (await fetch(`/api/jobs/${jobId}`)).json();
log("server has the edits:", server.edited, (server.edited_cues || []).length, "cues");

// ---------------------------------------------------------------- Reels: video, lyrics mode
await navTo("Reels / YouTube");
$$(".mode", ws()).find(b => b.textContent.includes("I have the script")).click(); await sleep(200);
drop(mov); await until(() => ws().querySelector(".filechip-name"), 5000, "file");
setVal(ws().querySelector("textarea.lyrics"), "Kal ke board exam ke liye agar bleaching powder ka yeh naya wala reaction revise nahi kiya toh bahut badi problem hai.\nNCERT ne is saal bleaching powder ka reaction badal diya tha.\nThis is the new reaction.");
$$("button.go", ws())[0].click();
await until(() => ws().querySelector("[data-testid=review]") || ws().querySelector(".errbox"), 300000, "video review");
await sleep(2500);
rows()[1].click(); await sleep(1200);
key("Enter"); await sleep(200);
inp = sel().querySelector("input"); setVal(inp, "BOARD EXAM KE LIYE");
inp.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); await sleep(400);
rows()[1].click(); await sleep(1200);
log("video review: cues", rows().length, "| overlay shows the edited text:", ws().querySelector("[data-testid=video-sub]")?.textContent);
await snap("review-video-light");

// ---------------------------------------------------------------- Updates page
await navTo("Updates");
const up = () => document.querySelector("[data-testid=updates]");
await until(() => up() && !up().hidden && up().querySelector("[data-testid=current-version]"), 10000, "updates page");
log("updates page:", up().querySelector("[data-testid=current-version]").textContent, "|", up().querySelector(".card-hint").textContent);
up().querySelector("[data-testid=check-updates]").click();
await until(() => up().querySelector("[data-testid=up-to-date], [data-testid=update-available], [data-testid=update-error]"), 30000, "check result");
log("check:", up().querySelector("[data-testid=up-to-date], [data-testid=update-available], [data-testid=update-error]").textContent.slice(0, 80));
log("switches:", $$("input[type=checkbox]", up()).map(i => `${i.getAttribute("aria-label")}=${i.checked}${i.disabled ? " (disabled)" : ""}`));

// ---------------------------------------------------------------- screenshots
for (const theme of ["light", "dark"]) {
  if (document.documentElement.getAttribute("data-theme") !== theme) $$(".sidebar-foot .navitem")[0].click();
  await sleep(500);
  await navTo("Updates"); await sleep(400); await snap(`updates-${theme}`);
  await navTo("Song"); await sleep(400); rows()[0].click(); await sleep(600); await snap(`review-audio-${theme}`);
  await navTo("Reels / YouTube"); await sleep(400); rows()[1].click(); await sleep(900); await snap(`review-video-${theme}`);
}
await resize(820, 960); await navTo("Song"); await snap("review-audio-narrow-dark"); await navTo("Updates"); await snap("updates-narrow-dark");
await resize(1440, 960);
$$(".sidebar-foot .navitem")[0].click();
return "ok";

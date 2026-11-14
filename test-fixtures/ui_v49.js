// v4.9 UI checks (Mac: ui_check.py; Windows: ui_edge.py). Files, in order: sample/clip.wav, drama.mp4,
// the reel video with a Hindi file name (test-fixtures/make_ui_media_mac.sh makes them). The server must
// run with SRT_REPORT_DIR (a test folder: the report zip goes there, not to Downloads) and SRT_NO_OPEN=1
// (the email and Finder are logged, not opened).
// Covers: script lines flagged / not found / Add it back; possible missing captions; History (list,
// search, filters, Open restores the Review panel exactly with edits, video jobs with their video,
// Re-run, Delete, View log, Show in Finder); Report a problem (Review, History, sidebar: the zip
// follows the checkboxes, the email opens); the settings (clean-up, log level, Clear logs); and the
// downloaded SRT equal to the Review panel after every change.
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
const [song, dramaVideo, reelVideo] = files;
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
    span?.querySelectorAll(".ai-fix-pop, .unsure-mark").forEach(x => x.remove());
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
const jobId = () => ws().querySelector("[data-testid=dl-srt]").getAttribute("href").split("/")[3];
const page = () => document.querySelector(".history:not([hidden])");
const hrows = () => $$("[data-testid=history-row]", page());
const hrow = name => hrows().find(r => r.dataset.name === name);
const jobDir = async () => (await (await fetch(`/api/jobs/${jobId()}`)).json()).job_dir;
async function historyLoaded(n) {
  await until(() => page() && page().querySelector("[data-testid=history-list]") && /of \d+ jobs/.test(page().textContent)
    && (n === undefined || hrows().length === n), 15000, `history list (${n})`);
  await sleep(200);
}
const dialog = () => document.querySelector("[data-testid=report-dialog]");

await until(() => $$(".navitem").length, 20000, "sidebar");
await fetch("/api/ai", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ final_check: false }) });
await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ log_level: "detailed", job_keep_days: 0 }) });

// ---------------------------------------------------------------- Song: a job with an edit
let t = t0();
await navTo("Song");
pickMode("I have the lyrics"); await sleep(200);
drop(song); await until(() => ws().querySelector(".filechip-name"), 5000, "file");
setVal(ws().querySelector("textarea.lyrics"), "My ex-wife left me for a guy twenty two years younger\nAnd then I ran into them both at Walmart");
await sleep(100);
goBtn().click();
await waitDone("song lyrics");
timing.song_s = secs(t);
rows()[1].click(); await sleep(150);
key("Enter"); await until(() => ws().querySelector(".rcue.on input"), 3000, "edit box");
const box = ws().querySelector(".rcue.on input");
setVal(box, "me for a GUY"); key("Enter", box);
await sleep(200);
let res = await compareDownload("Song edited");
const songShown = shown();
const songDir = await jobDir();
check("Song: edited, Edited badge", !!ws().querySelector("[data-testid=edited-badge]") && songShown.some(c => c[2] === "me for a GUY"));
check("Song: no script-line or missing-caption notes on a clean job", !ws().querySelector("[data-testid=script-summary]")
  && !ws().querySelector("[data-testid=notfound-row]") && !ws().querySelector("[data-testid=missing-row]"));

// ---------------------------------------------------------------- Drama: a flagged line, a line not in the audio, Add it back
t = t0();
await navTo("Drama");
pickMode("I have the script"); await sleep(200);
drop(dramaVideo); await until(() => ws().querySelector(".filechip-name"), 5000, "video");
setVal(ws().querySelector("textarea.lyrics"), [
  "Sarah: Where were you last night?",
  "Tom: The blue umbrella is still in the car.",
  "Tom: I was at the hospital for two hours.",
  "Sarah: Purple elephants dance on quiet Tuesdays.",
  "Tom: You never asked me anything."].join("\n"));
await sleep(100);
goBtn().click();
await waitDone("drama script");
timing.drama_s = secs(t);
const scriptFlags = $$("[data-testid=script-flag]", ws());
const notFoundRows = $$("[data-testid=notfound-row]", ws());
log("Drama:", shown().map(c => c[2]), scriptFlags.map(f => f.textContent), notFoundRows.map(r => r.textContent));
check("Drama: the mismatched line is kept and flagged “Script line not confidently found — check it” (orange)",
  scriptFlags.length >= 1 && scriptFlags[0].textContent.includes("Script line not confidently found — check it")
  && shown().some(c => /Purple elephants/.test(c[2])) && $$(".rcue.check", ws()).length >= 1);
check("Drama: the unspoken line is listed “Not found in the audio”, not in the list",
  notFoundRows.length === 1 && notFoundRows[0].textContent.includes("The blue umbrella is still in the car")
  && !shown().some(c => /umbrella/.test(c[2])), notFoundRows.map(r => r.textContent).join(" | "));
check("Drama: the summary counts them", /1 to check, 1 not found/.test(ws().querySelector("[data-testid=script-summary]")?.textContent || ""),
  ws().querySelector("[data-testid=script-summary]")?.textContent);
res = await compareDownload("Drama before Add it back");
check("Drama: the not-found line isn't in the downloaded SRT", res && !res.file.text.includes("umbrella"));
// Click to jump: the flag's Listen moves the playhead to the caption.
const flagIdx = rows().findIndex(r => r.querySelector("[data-testid=script-flag]"));
const flagStart = shown()[flagIdx][0] / 1000;
scriptFlags[0].querySelector("[data-testid=script-jump]").click(); await sleep(800);
const ph = Number(ws().querySelector(".scrubber").value);
check("Drama: Listen on a script flag jumps there", Math.abs(ph - flagStart) < 1.5, `${ph} vs ${flagStart}`);
ws().querySelector(".playbtn")?.click(); await sleep(100);
await snap("drama-script-lines");
ws().querySelector("[data-testid=addback]").click(); await sleep(400);
check("Drama: Add it back puts the line in the list (in its gap) and clears it from Not found",
  shown().some(c => /^The blue umbrella is still in the car\.?$/.test(c[2])) && !ws().querySelector("[data-testid=notfound-row]"),
  JSON.stringify(shown()));
const addedAt = shown().findIndex(c => /umbrella/.test(c[2]));
const added = shown()[addedAt];
check("Drama: added back between its neighbours, overlapping neither", added && added[0] >= 1000 && added[0] <= 2600
  && shown()[addedAt - 1][1] <= added[0] && added[1] <= shown()[addedAt + 1][0], JSON.stringify(shown()));
res = await compareDownload("Drama after Add it back");
check("Drama: the added line is in the downloaded SRT", res && res.file.text.includes("The blue umbrella is still in the car"));
// Looks right clears the script flag.
for (const f of $$("[data-testid=script-ok]", ws())) { f.click(); await sleep(200); }
check("Drama: Looks right clears the script flags", !$$("[data-testid=script-flag]", ws()).length);
await compareDownload("Drama after Looks right");
const dramaShown = shown();
const dramaDir = await jobDir();

// ---------------------------------------------------------------- Reels: a spoken line left out of the script
t = t0();
await navTo("Reels / YouTube");
pickMode("I have the script"); await sleep(200);
drop(reelVideo); await until(() => ws().querySelector(".filechip-name"), 5000, "reel");
setVal(ws().querySelector("textarea.lyrics"), [
  "Coach: For twelve years I have been waking up at seven a.m. to train.",
  "Maya: In week one we lost seventy percent of our budget on ads that nobody even watched.",
  "Coach: I am not giving up one inch.",
  "Maya: The first show starts at seven thirty."].join("\n"));
await sleep(100);
goBtn().click();
await waitDone("reel script");
timing.reel_s = secs(t);
const missingRows = $$("[data-testid=missing-row]", ws());
log("Reel missing:", missingRows.map(r => r.textContent));
check("Reels: the spoken line with no caption shows “Possible missing caption” (orange)",
  missingRows.length >= 1 && missingRows[0].textContent.includes("Possible missing caption"), missingRows.map(r => r.textContent).join(" | "));
check("Reels: counted in the summary", /possible missing caption/.test(ws().querySelector("[data-testid=script-summary]")?.textContent || ""));
check("Reels: not Verified while one is open", !ws().querySelector("[data-testid=verified-badge]"));
const mStart = (() => { const m = missingRows[0].textContent.match(/(\d+):(\d\d)\.(\d{3})/); return m ? +m[1] * 60 + +m[2] + +m[3] / 1000 : 0; })();
missingRows[0].querySelector("[data-testid=missing-jump]").click(); await sleep(800);
const mph = Number(ws().querySelector(".scrubber").value);
check("Reels: click jumps to it", Math.abs(mph - mStart) < 1.5, `${mph} vs ${mStart}`);
ws().querySelector(".playbtn")?.click(); await sleep(100);
for (const r of $$("[data-testid=missing-dismiss]", ws())) { r.click(); await sleep(150); }
check("Reels: “Nothing missing” dismisses it", !ws().querySelector("[data-testid=missing-row]"));
await compareDownload("Reels");

// ---------------------------------------------------------------- Report a problem from the Review panel
ws().querySelector("[data-testid=review-report]").click();
await until(() => dialog(), 3000, "report dialog");
check("Report: opened from Review, about this job, time prefilled", /About/.test(dialog().textContent)
  && dialog().querySelector("[data-testid=report-time]").value !== "");
dialog().querySelector("[data-testid=report-category]").value = "missing";
dialog().querySelector("[data-testid=report-category]").dispatchEvent(new Event("change", { bubbles: true }));
setVal(dialog().querySelector("[data-testid=report-description]"), "UI test: a line is missing");
check("Report: audio is off by default, the rest on", !dialog().querySelector("[data-testid=report-include-audio]").checked
  && ["logs", "text", "app_log"].every(k => dialog().querySelector(`[data-testid=report-include-${k}]`).checked));
dialog().querySelector("[data-testid=report-create]").click();
await until(() => dialog()?.querySelector("[data-testid=report-files]"), 20000, "report made");
await until(() => dialog().querySelector("[data-testid=report-note]"), 5000, "email note");
let files1 = $$("[data-testid=report-files] li .mono", dialog()).map(x => x.textContent);
log("Report files:", files1);
check("Report: zip built with the job log, job.json, captions, app log — and no audio",
  ["report.json", "job/job.json", "job/log.txt", "job/cues.json", "app.log"].every(n => files1.includes(n))
  && !files1.some(n => /input\.|video\./.test(n)), files1.join(", "));
check("Report: the email opened, pre-filled", /email app opened/.test(dialog().querySelector("[data-testid=report-note]").textContent)
  && /SRT Generator problem: Missing captions/.test(dialog().querySelector("[data-testid=report-text]").textContent)
  && /ramanworks06@gmail\.com/.test(dialog().querySelector("[data-testid=report-text]").textContent));
dialog().querySelector("[data-testid=report-copy]").click(); await sleep(300);
check("Report: Copy report text", /copied|select the text/i.test(dialog().querySelector("[data-testid=report-note]").textContent));
dialog().querySelector("[data-testid=report-show]").click(); await sleep(200);
$$("button", dialog()).find(b => b.textContent === "Done").click(); await sleep(200);

// ---------------------------------------------------------------- History
await navTo("History");
await historyLoaded();
const names = hrows().map(r => r.dataset.name);
check("History: lists every job, newest first", names.length >= 3 && names.indexOf(songDir) > names.indexOf(dramaDir), names.join(", "));
const sRow = hrow(songDir);
check("History: date, file, section, mode, language, duration, captions, status, edited",
  sRow && /clip\.wav/.test(sRow.textContent) && /Song/.test(sRow.textContent) && /Lyrics\/script/.test(sRow.textContent)
  && /English/.test(sRow.textContent) && /0:17/.test(sRow.textContent) && /captions?/.test(sRow.textContent)
  && !!sRow.querySelector("[data-testid=history-edited]") && /Verified|to check|Done/i.test(sRow.querySelector("[data-testid=history-status-badge]").textContent),
  sRow?.textContent);
check("History: video jobs have a thumbnail", !!hrow(dramaDir)?.querySelector(".history-thumb img"));
setVal(page().querySelector("[data-testid=history-search]"), "umbrella");
await until(() => hrows().length === 1 && hrows()[0].dataset.name === dramaDir, 8000, "search by text");
check("History: search finds caption text", true);
setVal(page().querySelector("[data-testid=history-search]"), "clip.wav");
await until(() => hrows().length >= 1 && hrows().every(r => /clip\.wav/.test(r.textContent)), 8000, "search by file name");
check("History: search by file name", true);
setVal(page().querySelector("[data-testid=history-search]"), "");
const sel = (id, v) => { const s = page().querySelector(`[data-testid=${id}]`); s.value = v; s.dispatchEvent(new Event("change", { bubbles: true })); };
sel("history-section", "drama");
await until(() => hrows().length >= 1 && hrows().every(r => /Drama/.test(r.querySelector(".history-meta").textContent)), 8000, "section filter");
check("History: section filter", true);
sel("history-section", "");
sel("history-status", "error");
await sleep(800);
check("History: status filter", hrows().every(r => /Error/.test(r.textContent)), hrows().length + " rows");
sel("history-status", "");
const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
setVal(page().querySelector("[data-testid=history-from]"), "2000-01-01");
setVal(page().querySelector("[data-testid=history-to]"), "2000-01-02");
await until(() => hrows().length === 0, 8000, "date filter (none)");
setVal(page().querySelector("[data-testid=history-to]"), today);
await until(() => hrows().length >= 3, 8000, "date filter (today)");
check("History: date filter", true);
setVal(page().querySelector("[data-testid=history-from]"), "");
setVal(page().querySelector("[data-testid=history-to]"), "");
await historyLoaded();

// View log: readable and searchable in the app.
hrow(dramaDir).querySelector("[data-testid=history-log]").click();
await until(() => document.querySelector("[data-testid=log-text]")?.textContent.includes("Step start"), 8000, "log");
const logLines = () => $$("[data-testid=log-text] > div").length;
const all = logLines();
setVal(document.querySelector("[data-testid=log-search]"), "Script line");
await sleep(300);
check("View log: the job log, searchable", all > 20 && logLines() > 0 && logLines() < all
  && $$("[data-testid=log-text] > div").every(d => d.textContent.toLowerCase().includes("script line")), `${logLines()} of ${all}`);
check("View log: no home folder path", !document.querySelector("[data-testid=log-text]").textContent.includes("/Users/"));
key("Escape"); await sleep(200);
hrow(dramaDir).querySelector("[data-testid=history-reveal]").click(); await sleep(200);

// Open: the song job, exactly as it was (edits included), in the Song page.
hrow(songDir).querySelector("[data-testid=history-open]").click();
await until(() => document.querySelector(".pagehead h1")?.textContent === "Song" && rows().length, 10000, "opened song");
await sleep(500);
check("History Open: Song page, the Review panel exactly as it was (edits included)",
  JSON.stringify(shown()) === JSON.stringify(songShown) && !!ws().querySelector("[data-testid=edited-badge]"),
  JSON.stringify(shown()) + " vs " + JSON.stringify(songShown));
check("History Open: the waveform for an audio job", !!ws().querySelector(".wavecard:not(.videocard)"));
await compareDownload("Song reopened");
const orig = await download(() => ws().querySelector("[data-testid=dl-original]").click());
check("History Open: Download original still works", orig && !orig.text.includes("GUY"));
// Keep editing after Open.
rows()[0].click(); await sleep(150);
$$("button", ws().querySelector(".rcue.on")).find(b => b.getAttribute("aria-label") === "End −0.1 s").click();
await compareDownload("Song reopened, edited again");

// Open the drama job: its video, the added line, the cleared flags.
await navTo("History"); await historyLoaded();
hrow(dramaDir).querySelector("[data-testid=history-open]").click();
await until(() => document.querySelector(".pagehead h1")?.textContent === "Drama" && rows().length, 10000, "opened drama");
await sleep(800);
check("History Open: Drama job with its video", !!ws().querySelector(".videocard video[src*='/video']"));
check("History Open: Drama list as left (added line, no flags left)", JSON.stringify(shown()) === JSON.stringify(dramaShown)
  && !$$("[data-testid=script-flag]", ws()).length && !ws().querySelector("[data-testid=notfound-row]"),
  JSON.stringify(shown()) + " vs " + JSON.stringify(dramaShown));
await compareDownload("Drama reopened");

// Re-run now, with the same settings.
await navTo("History"); await historyLoaded();
const before = hrows().length;
hrow(songDir).querySelector("[data-testid=history-rerun]").click();
await until(() => document.querySelector("[data-testid=rerun-dialog] [data-testid=rerun-now]"), 5000, "rerun dialog");
document.querySelector("[data-testid=rerun-now]").click();
await until(() => document.querySelector(".pagehead h1")?.textContent === "Song", 5000, "song page");
check("Re-run: the earlier job's file is attached", /clip/.test(ws().querySelector("[data-testid=file-name]")?.textContent || ""));
const oldRows = ws().querySelector("[data-testid=review]");
await until(() => { const r = ws().querySelector("[data-testid=review]"); return r && r !== oldRows && rows().length && !ws().querySelector(".proc"); }, 300000, "rerun done");
check("Re-run: a new job with the same captions as generated", shown().length === songShown.length && shown().some(c => /22 years/.test(c[2])));
await compareDownload("Re-run");
await navTo("History"); await historyLoaded(before + 1);
check("Re-run: listed in History", hrows().length === before + 1);

// Re-run with changed settings: opens the Drama page prefilled, nothing starts by itself.
hrow(dramaDir).querySelector("[data-testid=history-rerun]").click();
await until(() => document.querySelector("[data-testid=rerun-change]"), 5000, "rerun dialog");
document.querySelector("[data-testid=rerun-change]").click();
await until(() => document.querySelector(".pagehead h1")?.textContent === "Drama", 5000, "drama page");
await sleep(400);
check("Re-run (change settings first): script and file prefilled, waiting for Generate",
  /Purple elephants/.test(ws().querySelector("textarea.lyrics").value) && /drama/.test(ws().querySelector("[data-testid=file-name]").textContent)
  && !ws().querySelector(".proc") && !goBtn().disabled);

// Report from History and from the sidebar.
await navTo("History"); await historyLoaded();
hrow(dramaDir).querySelector("[data-testid=history-report]").click();
await until(() => dialog(), 3000, "report from history");
dialog().querySelector("[data-testid=report-include-audio]").click(); await sleep(50);
dialog().querySelector("[data-testid=report-create]").click();
await until(() => dialog()?.querySelector("[data-testid=report-files]"), 20000, "report 2");
files1 = $$("[data-testid=report-files] li .mono", dialog()).map(x => x.textContent);
check("Report from History with “The audio” ticked: the audio is in the zip", files1.some(n => /^job\/input\./.test(n)), files1.join(", "));
key("Escape"); await sleep(200);
document.querySelector("[data-testid=report-general]").click();
await until(() => dialog(), 3000, "general report");
check("Report (sidebar): a general problem, app-won't-start category first, job boxes off",
  /general problem/.test(dialog().textContent) && dialog().querySelector("[data-testid=report-category]").value === "startup"
  && dialog().querySelector("[data-testid=report-include-logs]").disabled);
dialog().querySelector("[data-testid=report-create]").click();
await until(() => dialog()?.querySelector("[data-testid=report-files]"), 20000, "report 3");
files1 = $$("[data-testid=report-files] li .mono", dialog()).map(x => x.textContent);
check("Report (sidebar): only report.json and the app log", JSON.stringify(files1.sort()) === JSON.stringify(["app.log", "report.json"]), files1.join(", "));
key("Escape"); await sleep(200);

// Settings on the History page.
sel("keep-days", "30"); await sleep(400);
let s = await (await fetch("/api/settings")).json();
check("Clean-up setting: changed on the page", s.job_keep_days === 30 && /JOB_KEEP_DAYS = 30/.test(page().textContent));
sel("keep-days", "0"); await sleep(400);
s = await (await fetch("/api/settings")).json();
check("Clean-up setting: never delete", s.job_keep_days === 0 && /until you delete them/.test(page().textContent));
page().querySelector("[data-testid=log-normal]").click(); await sleep(300);
s = await (await fetch("/api/settings")).json();
check("Log level: Normal", s.log_level === "normal");
page().querySelector("[data-testid=log-detailed]").click(); await sleep(300);
s = await (await fetch("/api/settings")).json();
check("Log level: Detailed", s.log_level === "detailed");
page().querySelector("[data-testid=clear-logs]").click(); await sleep(400);
check("Clear logs", /App log cleared/.test(page().textContent));

// Delete (confirm() is answered OK by the harness).
await historyLoaded();
const n0 = hrows().length;
const victim = hrows()[0].dataset.name;
hrows()[0].querySelector("[data-testid=history-delete]").click();
await until(() => hrows().length === n0 - 1 && !hrow(victim), 8000, "deleted");
check("Delete: the job is gone from the list", true);
const gone = await fetch(`/api/history/${victim}/log`);
check("Delete: and from disk", gone.status === 404);

await snap("history");
const failed = results.filter(r => !r[1]).map(r => r[0]);
log(`RESULT ${results.length - failed.length}/${results.length} passed`, JSON.stringify(timing));
if (failed.length) throw new Error("FAILED: " + failed.join("; "));
return { passed: results.length, timing };

// v4.2 UI checks (run by ui_check.py). Reels tab → Transcribe → Hinglish Accurate draft with
// review highlights and tooltip actions → house spellings in the Corrections tab.
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const tab = name => $$(".sectiontab").find(b => b.textContent.includes(name));
const ws = () => document.querySelector(".workspace:not([hidden])");
const setInput = (el, value) => {
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
};

await until(() => tab("Reels"), 20000, "tabs");
tab("Reels").click();
await sleep(300);
const modeBtn = $$(".mode", ws()).find(b => b.textContent.includes("Transcribe for me"));
modeBtn.click();
await sleep(300);
const lang = ws().querySelector(".langpick select");
const setSelect = (el, v) => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(el, v); el.dispatchEvent(new Event("change", { bubbles: true })); };
setSelect(lang, "auto");
await sleep(200);
const speed = await until(() => ws().querySelector(".speedpick"), 5000, "speed switch");
log("speed switch:", $$(".segment", speed).map(b => b.textContent + (b.classList.contains("on") ? " [on]" : "")));
$$(".segment", speed).find(b => b.textContent.startsWith("Fast")).click();
await sleep(200);
log("after clicking Fast:", $$(".segment.on", speed).map(b => b.textContent));
$$(".segment", speed).find(b => b.textContent.startsWith("Accurate")).click();
log("model note shown (should be no, model installed):", !!ws().querySelector("[data-testid=hinglish-model]"));
const topic = ws().querySelector(".topicwords input");
setInput(topic, "NCERT, bleaching powder, board exam");
log("topic words field:", topic.value);

// drop the clip onto the workbench
const dt = new DataTransfer();
dt.items.add(file);
ws().querySelector(".workbench").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
await until(() => ws().querySelector(".filechip-name"), 5000, "file chip");
log("file:", ws().querySelector(".filechip-name").textContent);
$$("button.go", ws())[0].click();
const t0 = Date.now();
let sawDetail = new Set();
await until(() => {
  const d = ws().querySelector(".proc-detail");
  if (d) sawDetail.add(d.textContent);
  return ws().querySelector("[data-testid=review-count]") || ws().querySelector(".errbox");
}, 600000, "draft");
log("progress messages seen:", [...sawDetail]);
log("draft in", Math.round((Date.now() - t0) / 1000), "s");
const area = ws().querySelector("textarea.lyrics");
log("draft text:", area.value.replace(/\n/g, " / "));
log("review note:", ws().querySelector("[data-testid=review-count]").textContent);
const marks = $$(".drafteditor-back mark", ws());
log("highlights:", marks.map(m => `${m.textContent}(${m.dataset.kind})`));

// hover each kind and use the tooltip
async function hover(mark) {
  const r = mark.getClientRects()[0];
  area.dispatchEvent(new MouseEvent("mousemove", { clientX: r.left + 2, clientY: r.top + r.height / 2, bubbles: true }));
  return until(() => ws().querySelector(".drafttip"), 3000, "tooltip");
}
const disagree = marks.find(m => m.dataset.kind === "disagree" && m.textContent === "naya") || marks.find(m => m.dataset.kind === "disagree");
let tip = await hover(disagree);
log("tooltip:", tip.textContent);
const before = area.value;
$$("button", tip).find(b => b.textContent.startsWith("Use")).click();
await sleep(300);
log("after Use:", area.value !== before ? "text changed" : "unchanged", "|", area.value.split("\n")[0]);
const keepTarget = $$(".drafteditor-back mark", ws())[0];
const n0 = $$(".drafteditor-back mark", ws()).length;
tip = await hover(keepTarget);
log("tooltip 2:", tip.textContent);
$$("button", tip).find(b => b.textContent === "Keep").click();
await sleep(300);
log("highlights before/after Keep:", n0, $$(".drafteditor-back mark", ws()).length);
log("review note now:", ws().querySelector("[data-testid=review-count]").textContent);

// house spellings
tab("Corrections").click();
const panel = await until(() => document.querySelector("[data-testid=house-spellings]"), 10000, "house spellings");
const rows = $$("tbody tr", panel);
log("house spellings rows:", rows.length, "| first:", rows[0].textContent);
const toRow = rows.find(r => r.querySelectorAll("input")[1].value === "to");
log("'to' row on?", toRow.querySelector("input[type=checkbox]").checked);
toRow.querySelector("input[type=checkbox]").click();
await sleep(800);
let server = await (await fetch("/api/spellings")).json();
log("after switching 'to' on — server says disabled:", server.rows.find(r => r.wrong === "to").disabled, "edited:", server.edited);
window.confirm = () => true;
$$("button", panel).find(b => b.textContent === "Restore defaults").click();
await sleep(800);
server = await (await fetch("/api/spellings")).json();
log("after Restore defaults — 'to' disabled:", server.rows.find(r => r.wrong === "to").disabled, "edited:", server.edited);
return "ok";

// v4.3 UI checks (run by ui_check.py with window.MOCK_URL set): AI polish settings → Reels
// transcribe with automatic polish → AI highlights (undo / keep) → Polish again → approve →
// corrections.md Source → Source filter and "Switch off AI rows" → a failing polish.
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const tab = name => $$(".sectiontab").find(b => b.textContent.includes(name));
const ws = () => document.querySelector(".workspace:not([hidden])");
const setVal = (el, value) => {
  const proto = el.tagName === "SELECT" ? HTMLSelectElement.prototype : el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
};
const MOCK = "http://127.0.0.1:7899/v1";

await until(() => tab("Corrections"), 20000, "tabs");
tab("Corrections").click();
let panel = await until(() => document.querySelector("[data-testid=ai-settings]"), 10000, "AI panel");
log("privacy note:", panel.querySelector(".ai-privacy").textContent);
log("switch enabled before any key?", !panel.querySelector(".switch input").disabled, "| status:", panel.querySelector(".ai-status")?.textContent);
log("providers:", $$("select[aria-label=Provider] option", panel).map(o => o.textContent));
setVal(panel.querySelector("select[aria-label=Provider]"), "custom");
await until(() => panel.querySelector("input[aria-label='Base URL']"), 5000, "base URL field");
const url = panel.querySelector("input[aria-label='Base URL']");
setVal(url, MOCK);
url.dispatchEvent(new FocusEvent("blur"));
url.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
await sleep(800);
const model = panel.querySelector("input[aria-label=Model]");
setVal(model, "mock-model");
model.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
await sleep(800);
log("after Custom + local URL + model — switch usable?", !panel.querySelector(".switch input").disabled);
panel.querySelector("[data-testid=ai-test]").click();
await until(() => panel.querySelector(".ai-status.ok, .ai-status.bad"), 20000, "test result");
log("Test connection:", panel.querySelector(".ai-status.ok, .ai-status.bad").textContent);
panel.querySelector(".switch input").click();
await sleep(800);
log("switch now:", panel.querySelector(".switch").textContent);

// Reels → transcribe (Fast) with automatic polish
tab("Reels").click();
await sleep(500);
$$(".mode", ws()).find(b => b.textContent.includes("Transcribe for me")).click();
await sleep(300);
const speed = ws().querySelector(".speedpick");
if (speed) $$(".segment", speed).find(b => b.textContent.startsWith("Fast")).click();
const dt = new DataTransfer();
dt.items.add(file);
ws().querySelector(".workbench").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
await until(() => ws().querySelector(".filechip-name"), 5000, "file chip");
$$("button.go", ws())[0].click();
const seen = new Set(), titles = new Set();
await until(() => {
  const d = ws().querySelector(".proc-detail"); if (d) seen.add(d.textContent);
  const t = ws().querySelector(".proc-title"); if (t) titles.add(t.textContent);
  return ws().querySelector("[data-testid=ai-count]") || ws().querySelector(".errbox");
}, 600000, "polished draft");
log("step titles seen:", [...titles]);
log("progress messages seen:", [...seen].filter(m => /Polish|Hinglish/.test(m)));
const area = ws().querySelector("textarea.lyrics");
log("draft:", area.value.replace(/\n/g, " / "));
log("counts:", ws().querySelector("[data-testid=ai-count]").textContent);
const marks = () => $$(".drafteditor-back mark", ws());
log("highlights:", marks().map(m => `${m.textContent}(${m.dataset.kind})`));
log("AI colour differs from corrections colour:",
  getComputedStyle(marks().find(m => m.dataset.kind === "ai")).backgroundColor);

async function hover(mark) {
  const r = mark.getClientRects()[0];
  area.dispatchEvent(new MouseEvent("mousemove", { clientX: r.left + 2, clientY: r.top + r.height / 2, bubbles: true }));
  return until(() => ws().querySelector(".drafttip"), 3000, "tooltip");
}
// undo the first AI fix ("to" → "toh"), keep the [boss?] guess without brackets
let tip = await hover(marks().find(m => m.dataset.kind === "ai"));
log("AI tooltip:", tip.textContent);
$$("button", tip).find(b => b.textContent === "Undo").click();
await sleep(300);
tip = await hover(marks().find(m => m.dataset.kind === "ai-unsure"));
log("AI-unsure tooltip:", tip.textContent);
$$("button", tip).find(b => b.textContent === "Keep").click();
await sleep(300);
log("after Undo + Keep:", area.value.replace(/\n/g, " / "));
log("counts now:", ws().querySelector("[data-testid=ai-count]").textContent);

// Polish again (polishes the box's current text)
const again = ws().querySelector("[data-testid=polish-again]");
log("Polish again button:", again?.textContent);
again.click();
await until(() => ws().querySelector(".proc"), 10000, "polish again to start");
await until(() => !ws().querySelector(".proc"), 120000, "polish again to finish");
await sleep(500);
log("after Polish again:", area.value.replace(/\n/g, " / "), "|", ws().querySelector("[data-testid=ai-count]").textContent);
// undo the re-applied "toh" again, so it ends up rejected; keep everything else
for (const m of marks().filter(m => m.dataset.kind === "ai")) {
  tip = await hover(m); $$("button", tip).find(b => b.textContent === "Undo").click(); await sleep(200);
}
// the user's own fix
setVal(area, area.value.replace("NCERT ne", "NCERT ne bhi"));
await sleep(200);

// approve
$$("button.go", ws())[0].click();
await until(() => ws().querySelector("[data-testid=learned]") || ws().querySelector(".errbox"), 300000, "approval");
log("learned note:", ws().querySelector("[data-testid=learned]")?.textContent);
const corr = await (await fetch("/api/corrections")).json();
const rows = corr.tables.flatMap(t => t.rows.map(r => `${r.wrong}→${r.correct} [${r.source}]`));
log("corrections now:", rows);

// Source filter + bulk switch-off
tab("Corrections").click();
await sleep(800);
const filter = document.querySelector("select[aria-label=Source]");
setVal(filter, "AI");
await sleep(300);
log("rows shown with Source=AI:", $$(".corr-table:not(.spellings) tbody tr").map(r => r.children[1].querySelector("input").value + ":" + r.children[5].textContent));
window.confirm = () => true;
document.querySelector("[data-testid=disable-ai]").click();
await sleep(1200);
const after = await (await fetch("/api/corrections")).json();
log("AI rows disabled:", after.tables.flatMap(t => t.rows.filter(r => r.source === "AI").map(r => `${r.wrong}:${r.disabled}`)));
setVal(filter, "all");

// a failing polish keeps the draft and says why in one line
await fetch("http://127.0.0.1:7899/mode/mismatch").catch(() => {});
tab("Reels").click();
await sleep(400);
$$("button.retranscribe", ws()).find(b => b.textContent.startsWith("Discard"))?.click();
await until(() => ws().querySelector("[data-testid=ai-message]") || ws().querySelector(".errbox"), 600000, "failed polish");
log("failure message:", ws().querySelector("[data-testid=ai-message]")?.textContent);
log("draft kept (offline):", area.value.split("\n")[0]);
await fetch("http://127.0.0.1:7899/mode/ok").catch(() => {});
log("footer:", document.querySelector("footer")?.textContent.includes("Made by Aman using Claude"));
return "ok";

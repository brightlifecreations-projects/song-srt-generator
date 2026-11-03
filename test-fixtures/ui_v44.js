// v4.4 UI checks (run by ui_check.py with files: clip.mp3, lovika.mov, Lovika-Hospital.mp4 and a
// screenshot dir). Sidebar → AI & API keys page → Song lyrics → Drama transcribe (auto-polish) →
// Reels transcribe (manual Polish, then AI off) → Corrections upload/merge/filter/export →
// screenshots of every page at full and narrow width, light and dark, with layout checks.
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const navTo = async label => { $$(".navitem").find(b => b.textContent.trim() === label || b.title === label).click(); await sleep(400); };
const ws = () => document.querySelector(".workspace:not([hidden])");
const setVal = (el, value) => {
  const proto = el.tagName === "SELECT" ? HTMLSelectElement.prototype : el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
};
const drop = f => {
  const dt = new DataTransfer(); dt.items.add(f);
  ws().querySelector(".workbench").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
};
const [mp3, mov, mp4] = files;
const MOCK = "http://127.0.0.1:7899/v1";

// ---------------------------------------------------------------- sidebar
await until(() => $$(".navitem").length, 20000, "sidebar");
log("sidebar items:", $$(".navlist .navitem").map(b => b.textContent.trim()), "| divider:", !!document.querySelector(".navlist hr"));
log("brand:", document.querySelector(".brand").textContent.trim(), "| icon:", !!document.querySelector(".brand img"));
log("foot:", document.querySelector(".sidebar-foot").innerText.replace(/\n/g, " | "));
await navTo("Drama");
log("active item:", document.querySelector(".navitem.on").textContent.trim(), "| remembered:", localStorage.getItem("srt-section"));
log("page header:", document.querySelector(".pagehead").innerText.replace(/\n/g, " — "));

// ---------------------------------------------------------------- AI & API keys
await navTo("AI & API keys");
const page = () => document.querySelector("[data-testid=ai-settings]");
await until(() => page() && !page().hidden, 5000, "AI page");
log("AI switch usable with nothing set up?", !page().querySelector("input[aria-label='AI polish on']").disabled);
const edit = page().querySelector("select[aria-label='Edit provider']");
setVal(edit, "custom"); await sleep(300);
const form = () => page().querySelector("[data-testid=provider-form]");
setVal(form().querySelector("input[aria-label='Base URL']"), MOCK);
// v4.4.1+: the Model field is a dropdown; pick "Custom model id…" and type the id
form().querySelector("button[aria-label=Model]").click(); await sleep(300);
[...form().querySelectorAll(".modelpick-list button")].find(b => b.textContent.startsWith("Custom model id")).click(); await sleep(200);
setVal(form().querySelector("input[aria-label='Custom model id']"), "mock-model");
form().querySelector("[data-testid=ai-save]").click(); await sleep(800);
log("after saving Custom:", form().querySelector(".ai-status")?.textContent, "| saved list:", page().querySelector("[data-testid=saved-keys]").innerText.replace(/\n/g, " | "));
// a dummy key for OpenRouter: masked, show/hide, save, remove
setVal(edit, "openrouter"); await sleep(300);
const key = form().querySelector("input[aria-label='API key']");
setVal(key, "dummy-key-123");
log("key field masked:", key.type);
form().querySelector("button[aria-label='Show key']").click(); await sleep(100);
log("after Show:", form().querySelector("input[aria-label='API key']").type);
form().querySelector("[data-testid=ai-save]").click(); await sleep(900);
log("OpenRouter saved list:", page().querySelector("[data-testid=saved-keys]").innerText.replace(/\n/g, " | "));
form().querySelector("[data-testid=ai-test]").click();
await until(() => form().querySelector("[data-testid=ai-test-result]"), 30000, "test");
log("OpenRouter test with dummy key:", form().querySelector("[data-testid=ai-test-result]").textContent);
window.confirm = () => true;
form().querySelector("[data-testid=ai-remove]").click(); await sleep(900);
log("after Remove key:", page().querySelector("[data-testid=saved-keys]").innerText.replace(/\n/g, " | "));
// make Custom active, test, switch on
const make = $$("[data-testid=saved-keys] li", page()).find(li => li.textContent.includes("Custom"))?.querySelector("button");
if (make && make.textContent === "Make active") { make.click(); await sleep(800); }
log("active provider:", page().querySelector("select[aria-label=Provider]").value, "|", page().querySelector("[data-testid=saved-keys] li.on")?.textContent);
setVal(edit, "custom"); await sleep(300);
form().querySelector("[data-testid=ai-test]").click();
await until(() => form().querySelector("[data-testid=ai-test-result]"), 30000, "test");
log("Custom test:", form().querySelector("[data-testid=ai-test-result]").textContent);
page().querySelector("input[aria-label='AI polish on']").click(); await sleep(800);
log("AI polish on:", page().querySelector("input[aria-label='AI polish on']").checked, "| auto:", page().querySelector("input[aria-label='Auto-polish after every transcription']").checked,
  "| sidebar:", document.querySelector("[data-testid=ai-status]").textContent);
log("switching providers: Anthropic → Custom");
setVal(page().querySelector("select[aria-label=Provider]"), "anthropic"); await sleep(600);
log("  active:", page().querySelector("select[aria-label=Provider]").value, "| switch disabled now (no key):", page().querySelector("input[aria-label='AI polish on']").disabled, "| sidebar:", document.querySelector("[data-testid=ai-status]").textContent);
setVal(page().querySelector("select[aria-label=Provider]"), "custom"); await sleep(600);
page().querySelector("input[aria-label='AI polish on']").checked || page().querySelector("input[aria-label='AI polish on']").click(); await sleep(600);
log("  back to:", page().querySelector("select[aria-label=Provider]").value, "| on:", page().querySelector("input[aria-label='AI polish on']").checked);

// ---------------------------------------------------------------- Song: lyrics mode
await navTo("Song");
$$(".mode", ws()).find(b => b.textContent.includes("I have the lyrics")).click(); await sleep(200);
drop(mp3); await until(() => ws().querySelector(".filechip-name"), 5000, "file");
setVal(ws().querySelector("textarea.lyrics"), "If it is meant to be it's gonna come back to you\nIf it's not, then it wasn't meant for you");
const more = ws().querySelector("[data-testid=more-options]");
log("More options open?", more.open, "| contents:", more.innerText.replace(/\n/g, " | ").slice(0, 160));
$$("button.go", ws())[0].click();
await until(() => ws().querySelector(".results") || ws().querySelector(".errbox"), 300000, "song result");
log("song result:", ws().querySelector(".doneline")?.textContent, "| cues:", $$("[data-testid=rcue]", ws()).length, "| waveform:", !!ws().querySelector(".wavecard canvas, .wavecard wave, .wavecard [part]") || !!ws().querySelector(".wavecard"));
const srtLink = ws().querySelector(".dl-primary");
let srt = await (await fetch(srtLink.getAttribute("href"))).text();
log("SRT download:", srt.split("\n").slice(0, 3).join(" / "), "| vocals link:", !!ws().querySelector(".dl-secondary"));
const vox = await fetch($$(".dl-secondary", ws()).find(a => a.textContent.includes("Isolated")).getAttribute("href"));
log("vocals download:", vox.status, vox.headers.get("content-type"));

// ---------------------------------------------------------------- Drama: transcribe → auto-polish → edit → approve (video)
await navTo("Drama");
$$(".mode", ws()).find(b => b.textContent.includes("Transcribe")).click(); await sleep(200);
drop(mov); await until(() => ws().querySelector(".filechip-name"), 5000, "file");
await until(() => ws().querySelector("video, .video-wait"), 15000, "video preview");
log("video preview:", ws().querySelector("video") ? "playing in the page" : ws().querySelector(".video-wait").textContent);
const dmore = ws().querySelector("[data-testid=more-options]");
if (!dmore.open) dmore.querySelector("summary").click(); await sleep(200);
log("More options remembered as:", localStorage.getItem("srt-more-options"));
const speed = ws().querySelector(".speedpick");
if (speed) $$(".segment", speed).find(b => b.textContent.startsWith("Fast")).click();
$$("button.go", ws())[0].click();
const titles = new Set();
await until(() => { const t = ws().querySelector(".proc-title"); if (t) titles.add(t.textContent); return ws().querySelector("[data-testid=ai-count]") || ws().querySelector(".errbox"); }, 600000, "drama draft");
log("drama steps seen:", [...titles], "| AI counts:", ws().querySelector("[data-testid=ai-count]")?.textContent.slice(0, 60));
log("drama highlights:", $$(".drafteditor-back mark", ws()).map(m => `${m.textContent}(${m.dataset.kind})`));
const darea = ws().querySelector("textarea.lyrics");
setVal(darea, darea.value.replace("[boss?]", "boss"));
$$("button.go", ws())[0].click();
await until(() => ws().querySelector(".results") || ws().querySelector(".errbox"), 300000, "drama result");
log("drama result:", ws().querySelector(".doneline")?.textContent, "| cues:", $$("[data-testid=rcue]", ws()).length, "| subtitle overlay:", !!ws().querySelector(".video-sub"));
srt = await (await fetch(ws().querySelector(".dl-primary").getAttribute("href"))).text();
log("drama SRT:", srt.split("\n").slice(0, 3).join(" / "));
$$("[data-testid=rcue]", ws())[2].click(); await sleep(500);
log("cue click seeks video to:", ws().querySelector("video").currentTime.toFixed(2), "| overlay text:", ws().querySelector(".video-sub")?.textContent);

// ---------------------------------------------------------------- Reels: auto-polish off → manual Polish; then AI off
await fetch("/api/ai", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ auto: false }) });
await navTo("Reels / YouTube");
$$(".mode", ws()).find(b => b.textContent.includes("Transcribe")).click(); await sleep(200);
drop(mp4); await until(() => ws().querySelector(".filechip-name"), 5000, "file");
const rspeed = ws().querySelector(".speedpick");
if (rspeed) $$(".segment", rspeed).find(b => b.textContent.startsWith("Fast")).click();
$$("button.go", ws())[0].click();
await until(() => ws().querySelector("[data-testid=polish-again]") || ws().querySelector(".errbox"), 600000, "reels draft");
log("reels draft without auto-polish — AI marks:", $$(".drafteditor-back mark[data-kind^=ai]", ws()).length, "| button:", ws().querySelector("[data-testid=polish-again]").textContent);
ws().querySelector("[data-testid=polish-again]").click();
await until(() => ws().querySelector(".proc"), 10000, "polish start");
await until(() => !ws().querySelector(".proc"), 120000, "polish end"); await sleep(400);
log("after Polish — AI marks:", $$(".drafteditor-back mark[data-kind^=ai]", ws()).length, "|", ws().querySelector("[data-testid=ai-count]")?.textContent.slice(0, 40), "| button now:", ws().querySelector("[data-testid=polish-again]")?.textContent);
$$("button.go", ws())[0].click();
await until(() => ws().querySelector(".results") || ws().querySelector(".errbox"), 300000, "reels result");
log("reels result:", ws().querySelector(".doneline")?.textContent, "| cues:", $$("[data-testid=rcue]", ws()).length);
await fetch("/api/ai", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: false, auto: true }) });
await navTo("Song"); await navTo("Reels / YouTube");
log("AI off — sidebar:", document.querySelector("[data-testid=ai-status]").textContent, "| Polish button:", !!ws().querySelector("[data-testid=polish-again]"));

// ---------------------------------------------------------------- Corrections
await navTo("Corrections");
const corr = document.querySelector(".workspace.corrections");
log("AI settings on Corrections page:", !!corr.querySelector("[data-testid=ai-settings], .ai-panel"));
const teammate = new File([`## Hinglish\n| Wrong | Correct | Times seen | Last seen | Notes |\n|---|---|---|---|---|\n| bostum | boss tum | 4 | 2026-11-01 | old-format file |\n`], "teammate.md");
const dt2 = new DataTransfer(); dt2.items.add(teammate);
corr.dispatchEvent(new DragEvent("drop", { dataTransfer: dt2, bubbles: true, cancelable: true }));
await until(() => corr.querySelector("[data-testid=merge-panel]"), 10000, "merge plan");
log("merge plan:", corr.querySelector("[data-testid=merge-panel] .section-hint")?.textContent.trim());
$$("[data-testid=merge-panel] button", corr).find(b => b.textContent.startsWith("Merge")).click();
await until(() => corr.querySelector(".corr-notice"), 10000, "merged");
log("merged:", corr.querySelector(".corr-notice").textContent.slice(0, 80));
const filter = corr.querySelector("select[aria-label=Source]");
setVal(filter, "User"); await sleep(300);
log("Source=User rows:", $$(".corr-table:not(.spellings) tbody tr", corr).map(r => r.children[1].querySelector("input").value));
setVal(filter, "all");
setVal(corr.querySelector(".corr-search"), "bostum"); await sleep(300);
log("search 'bostum':", $$(".corr-table:not(.spellings) tbody tr", corr).length, "row(s)");
setVal(corr.querySelector(".corr-search"), "");
const exported = await (await fetch(corr.querySelector("a[download='corrections.md']").getAttribute("href"))).text();
log("export has Source column:", exported.includes("| Source |"), "| has bostum:", exported.includes("bostum"));
log("house spellings table:", !!corr.querySelector("[data-testid=house-spellings]"));

// ---------------------------------------------------------------- screenshots + layout checks
function layout(tag) {
  const doc = document.documentElement;
  const overflowX = doc.scrollWidth - doc.clientWidth;
  const cut = $$(".page *").filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.right > doc.clientWidth + 1 && getComputedStyle(el).position !== "fixed"
      && !el.closest(".corr-table, .cuelist, .video-frame");
  }).slice(0, 3).map(el => el.className || el.tagName);
  const inp = ws()?.querySelector(".col-inputs")?.getBoundingClientRect();
  const out = ws()?.querySelector(".stagecol")?.getBoundingClientRect();
  const overlap = inp && out && inp.width && out.width && !(inp.right <= out.left || out.right <= inp.left || inp.bottom <= out.top || out.bottom <= inp.top);
  const side = document.querySelector(".sidebar").getBoundingClientRect().width;
  return `${tag}: sidebar ${Math.round(side)}px, horizontal overflow ${overflowX}px, cut off ${cut.length ? cut.join(",") : "none"}, columns overlap ${overlap ? "YES" : "no"}${inp && out && inp.width ? `, output ${out.top >= inp.bottom - 1 ? "below" : "beside"} inputs` : ""}`;
}
const PAGES = ["Song", "Drama", "Reels / YouTube", "Corrections", "AI & API keys"];
for (const [size, w] of [["full", 1440], ["narrow", 820]]) {
  await resize(w, 960);
  for (const theme of ["light", "dark"]) {
    if (document.documentElement.getAttribute("data-theme") !== theme)
      $$(".sidebar-foot .navitem")[0].click();
    await sleep(500);
    for (const p of PAGES) {
      await navTo(p);
      await sleep(500);
      const slug = p.toLowerCase().replace(/[^a-z]+/g, "-").replace(/-$/, "");
      await snap(`${slug}-${size}-${theme}`);
      log(layout(`${slug}-${size}-${theme}`));
    }
  }
}
// collapse by hand at full width, remembered
await resize(1440, 960);
$$(".sidebar-foot .navitem").find(b => b.getAttribute("aria-label") === "Collapse sidebar").click(); await sleep(400);
await snap("collapsed-full-dark");
log("collapsed by hand:", document.querySelector(".app").classList.contains("collapsed"), "| remembered:", localStorage.getItem("srt-sidebar"));
$$(".sidebar-foot .navitem").find(b => b.getAttribute("aria-label") === "Expand sidebar").click();
$$(".sidebar-foot .navitem")[0].click();  // back to light
return "ok";

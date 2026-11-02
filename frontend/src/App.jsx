import { useEffect, useState } from "react";
import Workspace from "./Workspace.jsx";
import Corrections from "./Corrections.jsx";
import Footer from "./Footer.jsx";

/* The three sections. Presets come from /api/meta (config.SECTIONS); these are the fallback
   if the server's list hasn't arrived yet. */
const SECTION_TABS = [
  { code: "song", name: "Song", hint: "Music & lyrics · vocals isolated", format: "vertical", isolate: "on" },
  { code: "drama", name: "Drama", hint: "Dialogue over music & effects", format: "horizontal", isolate: "on" },
  { code: "reels", name: "Reels / YouTube", hint: "Speech · short, punchy captions", format: "vertical", isolate: "auto" },
];
const CORRECTIONS_TAB = { code: "corrections", name: "Corrections", hint: "Your fixes for Whisper · shareable" };

function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem("srt-theme") || "light");
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("srt-theme", theme);
  }, [theme]);
  return [theme, () => setTheme(t => (t === "light" ? "dark" : "light"))];
}

/** /api/meta (languages, scripts, section presets, version) plus a newer GitHub release if the
    server found one (Windows and checkouts only; the Mac app updates through Sparkle). */
function useMeta() {
  const [meta, setMeta] = useState(null);
  const [update, setUpdate] = useState(null);
  useEffect(() => {
    fetch("/api/meta").then(r => r.json()).then(setMeta).catch(() => {});
    fetch("/api/update")
      .then(r => r.json())
      .then(u => { if (u.available) setUpdate(u); })
      .catch(() => { /* offline: no banner */ });
  }, []);
  return [meta, update];
}

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [meta, update] = useMeta();
  const [section, setSectionState] = useState(() => {
    try {
      const saved = localStorage.getItem("srt-section");
      return [...SECTION_TABS, CORRECTIONS_TAB].some(t => t.code === saved) ? saved : "song";
    } catch { return "song"; }
  });

  // A file dropped outside a drop target must not replace the app with the file itself.
  useEffect(() => {
    const block = e => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); };
    window.addEventListener("dragover", block);
    window.addEventListener("drop", block);
    return () => { window.removeEventListener("dragover", block); window.removeEventListener("drop", block); };
  }, []);

  function setSection(code) {
    setSectionState(code);
    try { localStorage.setItem("srt-section", code); } catch { /* private mode */ }
  }

  const tabs = SECTION_TABS.map(t => ({ ...t, ...(meta?.sections || []).find(s => s.code === t.code) }));

  return (
    <div className="shell">
      {update && (
        <a className="update-banner" href={update.url} target="_blank" rel="noreferrer">
          <span className="update-dot" />
          Update available — version {update.latest}. <b>Download it from GitHub →</b>
        </a>
      )}
      <header className="masthead">
        <div className="masthead-left">
          <span className="kicker">Local subtitle tool — offline</span>
          <h1><em>SRT</em> Generator</h1>
          <p className="masthead-sub">
            Songs, dramas, reels and YouTube videos — subtitles timed to every word. Bring your
            lyrics or script, or let it transcribe a draft for you to correct.
          </p>
        </div>
        <div className="masthead-right">
          <button className="theme-toggle" onClick={toggleTheme}>
            <span className="theme-dot" />{theme === "light" ? "Dark" : "Light"}
          </button>
          <p className="privacy"><b>Everything runs on this machine.</b><br />Files never leave your computer.</p>
        </div>
      </header>

      <nav className="sectiontabs" role="tablist" aria-label="Section">
        {[...tabs, CORRECTIONS_TAB].map(t => (
          <button key={t.code} role="tab" aria-selected={section === t.code}
            className={`sectiontab${section === t.code ? " on" : ""}`} onClick={() => setSection(t.code)}>
            <b>{t.name}</b>
            <span>{t.hint}</span>
          </button>
        ))}
      </nav>

      {tabs.map(t => (
        <Workspace key={t.code} section={t.code} preset={t} meta={meta} active={section === t.code} />
      ))}
      <Corrections active={section === "corrections"} />

      <Footer version={meta?.version || ""} />
    </div>
  );
}

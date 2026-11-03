import { useEffect, useState } from "react";
import Workspace from "./Workspace.jsx";
import Corrections from "./Corrections.jsx";
import AiKeys from "./AiKeys.jsx";

/* The three sections. Presets come from /api/meta (config.SECTIONS); these are the fallback
   if the server's list hasn't arrived yet. */
const SECTION_TABS = [
  { code: "song", name: "Song", format: "vertical", isolate: "on" },
  { code: "drama", name: "Drama", format: "horizontal", isolate: "on" },
  { code: "reels", name: "Reels / YouTube", format: "vertical", isolate: "auto" },
];

/* Each page's title and one short line (V4.4: replaces the masthead). */
const PAGES = {
  song: { title: "Song", line: "Lyrics timed to the vocals — one subtitle per phrase." },
  drama: { title: "Drama", line: "Dialogue subtitles — 7 seconds at most, a new one at every pause." },
  reels: { title: "Reels / YouTube", line: "Short, punchy captions — four words or less, 2.5 seconds at most." },
  corrections: { title: "Corrections", line: "Whisper's mistakes and your fixes, learnt from every approval." },
  ai: { title: "AI & API keys", line: "Optional AI polish. Only transcript text is sent; audio and video stay on this computer." },
};

const ICON = {
  song: <path d="M9 18V5l11-2v13M9 18a3 3 0 1 1-3-3 3 3 0 0 1 3 3Zm11-2a3 3 0 1 1-3-3 3 3 0 0 1 3 3Z" />,
  drama: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 15h4M13 15h4M7 11h10" /></>,
  reels: <><rect x="3" y="5" width="18" height="14" rx="3" /><path d="m10 9 5 3-5 3V9Z" /></>,
  corrections: <><path d="M4 7h10M4 12h7M4 17h6" /><path d="m14 16 2.5 2.5L21 13" /></>,
  ai: <><circle cx="8" cy="15" r="4" /><path d="m11 12 9-9M17 6l3 3M15 8l2 2" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />,
  collapse: <path d="M15 6l-6 6 6 6" />,
  expand: <path d="M9 6l6 6-6 6" />,
};
const Icon = ({ name }) => (
  <svg className="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICON[name]}</svg>
);

function stored(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode */ }
}

function useTheme() {
  const [theme, setTheme] = useState(() => stored("srt-theme", "light"));
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    store("srt-theme", theme);
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

function useNarrow(query = "(max-width: 900px)") {
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return narrow;
}

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [meta, update] = useMeta();
  const [page, setPageState] = useState(() => {
    const saved = stored("srt-section", "song");
    return saved in PAGES ? saved : "song";
  });
  const [userCollapsed, setUserCollapsed] = useState(() => stored("srt-sidebar", "open") === "collapsed");
  const narrow = useNarrow();
  const collapsed = narrow || userCollapsed;
  const [ai, setAi] = useState(null); // /api/ai, for the sidebar's status

  useEffect(() => {
    fetch("/api/ai").then(r => r.json()).then(setAi).catch(() => {});
  }, [page]);

  // A file dropped outside a drop target must not replace the app with the file itself.
  useEffect(() => {
    const block = e => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); };
    window.addEventListener("dragover", block);
    window.addEventListener("drop", block);
    return () => { window.removeEventListener("dragover", block); window.removeEventListener("drop", block); };
  }, []);

  function setPage(code) {
    setPageState(code);
    store("srt-section", code);
    window.scrollTo(0, 0);
  }
  function toggleCollapsed() {
    setUserCollapsed(c => { store("srt-sidebar", c ? "open" : "collapsed"); return !c; });
  }

  const tabs = SECTION_TABS.map(t => ({ ...t, ...(meta?.sections || []).find(s => s.code === t.code) }));
  const aiOn = ai && ai.enabled && ai.usable;
  const nav = (code, label) => (
    <button key={code} className={`navitem${page === code ? " on" : ""}`} onClick={() => setPage(code)}
      aria-current={page === code ? "page" : undefined} title={collapsed ? label : undefined}>
      <Icon name={code} /><span className="navlabel">{label}</span>
    </button>
  );

  return (
    <div className={`app${collapsed ? " collapsed" : ""}`}>
      <aside className="sidebar" aria-label="Navigation">
        <div className="brand">
          <img src="/favicon.png" alt="" width="28" height="28" />
          <span className="navlabel">SRT Generator</span>
        </div>
        <nav className="navlist">
          {tabs.map(t => nav(t.code, t.name))}
          <hr />
          {nav("corrections", "Corrections")}
          {nav("ai", "AI & API keys")}
        </nav>
        <div className="sidebar-foot">
          <button className="aistatus" onClick={() => setPage("ai")} data-testid="ai-status"
            title={collapsed ? `AI polish: ${aiOn ? "on" : "off"}` : undefined}>
            <span className={`dot${aiOn ? " on" : ""}`} />
            <span className="navlabel">AI polish: <b>{aiOn ? "on" : "off"}</b></span>
          </button>
          <button className="navitem" onClick={toggleTheme} title={collapsed ? (theme === "light" ? "Dark mode" : "Light mode") : undefined}>
            <Icon name={theme === "light" ? "moon" : "sun"} />
            <span className="navlabel">{theme === "light" ? "Dark mode" : "Light mode"}</span>
          </button>
          {!narrow && (
            <button className="navitem" onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              title={collapsed ? "Expand sidebar" : undefined}>
              <Icon name={collapsed ? "expand" : "collapse"} /><span className="navlabel">Collapse</span>
            </button>
          )}
          <span className="version" data-testid="version">v{meta?.version || ""}</span>
        </div>
      </aside>

      <main className="page">
        {update && (
          <a className="update-banner" href={update.url} target="_blank" rel="noreferrer">
            <span className="update-dot" />
            Update available — version {update.latest}. <b>Download it from GitHub →</b>
          </a>
        )}
        <header className="pagehead">
          <h1>{PAGES[page].title}</h1>
          <p>{PAGES[page].line}</p>
        </header>
        {tabs.map(t => (
          <Workspace key={t.code} section={t.code} preset={t} meta={meta} active={page === t.code} />
        ))}
        <Corrections active={page === "corrections"} />
        <AiKeys active={page === "ai"} onChange={setAi} />
      </main>
    </div>
  );
}

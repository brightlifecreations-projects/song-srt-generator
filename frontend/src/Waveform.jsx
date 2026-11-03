import { useEffect, useImperativeHandle, useRef, forwardRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";

function fmt(t) {
  if (!isFinite(t)) return "0:00";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

/** Scrubbable waveform. source: the picked File, or a URL (a video's extracted audio).
    subtitle: the cue to draw under the waveform (Review panel). Exposes seekTo(seconds) (seek and
    play), seek(seconds), play(), pause() and toggle() via ref; onDuration/onPlaying report back. */
const Waveform = forwardRef(function Waveform({ source, name, scanning, onTime, subtitle, onDuration, onPlaying }, ref) {
  const holderRef = useRef(null);
  const wsRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    if (!source || !holderRef.current) return;
    const styles = getComputedStyle(document.documentElement);
    const ws = WaveSurfer.create({
      container: holderRef.current,
      height: 96,
      waveColor: styles.getPropertyValue("--wave").trim(),
      progressColor: styles.getPropertyValue("--wave-progress").trim(),
      cursorColor: styles.getPropertyValue("--ink").trim(),
      cursorWidth: 1.5,
      barWidth: 2.5,
      barGap: 2,
      barRadius: 2,
      normalize: true,
    });
    const isFile = typeof source !== "string";
    const url = isFile ? URL.createObjectURL(source) : source;
    ws.load(url);
    ws.on("ready", () => { setDuration(ws.getDuration()); onDuration?.(ws.getDuration()); });
    ws.on("play", () => { setPlaying(true); onPlaying?.(true); });
    ws.on("pause", () => { setPlaying(false); onPlaying?.(false); });
    ws.on("seeking", (t) => { setTime(t); onTime && onTime(t); });
    ws.on("timeupdate", (t) => { setTime(t); onTime && onTime(t); });
    wsRef.current = ws;
    return () => { ws.destroy(); if (isFile) URL.revokeObjectURL(url); wsRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  // Recolor on theme change without reloading audio
  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => {
      const ws = wsRef.current;
      if (!ws) return;
      const styles = getComputedStyle(root);
      ws.setOptions({
        waveColor: styles.getPropertyValue("--wave").trim(),
        progressColor: styles.getPropertyValue("--wave-progress").trim(),
        cursorColor: styles.getPropertyValue("--ink").trim(),
      });
    });
    observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  useImperativeHandle(ref, () => ({
    seekTo(seconds) {
      const ws = wsRef.current;
      if (!ws || !ws.getDuration()) return;
      ws.setTime(Math.max(0, Math.min(seconds, ws.getDuration())));
      ws.play();
    },
    seek(seconds) {
      const ws = wsRef.current;
      if (ws && ws.getDuration()) ws.setTime(Math.max(0, Math.min(seconds, ws.getDuration())));
    },
    play() { wsRef.current?.play(); },
    pause() { wsRef.current?.pause(); },
    toggle() { wsRef.current?.playPause(); },
  }));

  return (
    <div className="wavecard">
      <div className="wavecard-head">
        <span className="wavecard-title">{scanning ? "Isolating vocals" : "Waveform"}</span>
        <span className="wavecard-time">{fmt(time)} / {fmt(duration)}</span>
      </div>
      <div className="wave-holder">
        <div ref={holderRef} />
        {scanning && <div className="wave-scan" />}
      </div>
      {subtitle !== undefined && (
        <div className="wave-sub" data-testid="wave-sub">{subtitle || <span className="muted">—</span>}</div>
      )}
      <div className="player-row">
        <button
          className="playbtn"
          onClick={() => wsRef.current && wsRef.current.playPause()}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? (
            <svg width="13" height="13" viewBox="0 0 12 12" fill="currentColor"><rect x="1.5" width="3.2" height="12" rx="1"/><rect x="7.3" width="3.2" height="12" rx="1"/></svg>
          ) : (
            <svg width="13" height="13" viewBox="0 0 12 12" fill="currentColor"><path d="M2 1.2c0-.8.9-1.3 1.6-.9l8 4.8c.7.4.7 1.4 0 1.8l-8 4.8c-.7.4-1.6-.1-1.6-.9V1.2z"/></svg>
          )}
        </button>
        <span className="player-name">{name}</span>
      </div>
    </div>
  );
});

export default Waveform;

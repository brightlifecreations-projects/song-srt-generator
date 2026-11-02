import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

function fmt(t) {
  if (!isFinite(t)) return "0:00";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

/** The picked video with the subtitles drawn over it, as they'll look on screen.
    Plays the file itself; when the web view can't decode it (MKV, some codecs) it reports
    onPlayable(false) and plays fallbackSrc, the server's preview copy, once that exists.
    Exposes seekTo(seconds) via ref, like Waveform. */
const VideoPreview = forwardRef(function VideoPreview(
  { file, fallbackSrc, cues, format, onTime, onPlayable, processing }, ref) {
  const videoRef = useRef(null);
  const [fileUrl, setFileUrl] = useState(null);
  const [playable, setPlayable] = useState(null);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setFileUrl(url);
    setPlayable(null);
    setTime(0);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function report(ok) {
    setPlayable(ok);
    onPlayable?.(ok);
  }

  const src = playable === false ? fallbackSrc : fileUrl;
  const active = cues.find(c => time >= c.start && time < c.end);

  useImperativeHandle(ref, () => ({
    seekTo(seconds) {
      const v = videoRef.current;
      if (!v || !v.duration) return;
      v.currentTime = Math.max(0, Math.min(seconds, v.duration));
      v.play();
    },
  }));

  return (
    <div className="wavecard videocard">
      <div className="wavecard-head">
        <span className="wavecard-title">Video preview</span>
        <span className="wavecard-time">{fmt(time)} / {fmt(duration)}</span>
      </div>
      <div className={`video-frame ${format}`}>
        {src ? (
          <video
            key={src}
            ref={videoRef}
            src={src}
            controls
            playsInline
            preload="metadata"
            onLoadedMetadata={e => {
              setDuration(e.currentTarget.duration);
              // Audio-only decoding (an unsupported video codec) reports a 0×0 picture.
              if (src === fileUrl) report(e.currentTarget.videoWidth > 0);
            }}
            onError={() => { if (src === fileUrl) report(false); }}
            onTimeUpdate={e => { const t = e.currentTarget.currentTime; setTime(t); onTime?.(t); }}
            onSeeked={e => { const t = e.currentTarget.currentTime; setTime(t); onTime?.(t); }}
          />
        ) : (
          <div className="video-wait">
            {processing
              ? "This video can't be played here directly, so a preview copy is being made…"
              : "This video can't be played here directly. A preview copy is made when you transcribe or generate."}
          </div>
        )}
        {active && (
          <div className="video-sub" data-testid="video-sub"><span>{active.text}</span></div>
        )}
      </div>
      <div className="player-row">
        <span className="player-name">{file.name}</span>
        {!cues.length && <span className="video-hint">Subtitles appear on the video once they're generated</span>}
      </div>
    </div>
  );
});

export default VideoPreview;

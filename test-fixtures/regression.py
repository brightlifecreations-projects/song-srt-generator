"""API regression run for the three tabs and both modes (test fixtures, not shipped).

    python test-fixtures/regression.py <base-url> <case>... | all

Each case uploads a file through the app's HTTP API exactly like the UI does (lyrics mode, or
transcribe → approve), waits for the job, and checks the result: SRT well-formed, cues in
order, no overlaps, the section's character/word/time caps held, section recorded in cues.json.
Prints one line per case and a JSON summary; exit 1 if any case fails.

Media: sample/clip.wav (English, in the repo), test-fixtures/Lovika-Hospital.mp4 and the
"Kabhi Kabhi Aditi" song from an earlier job (local only; cases are skipped without them).
"""

import json
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "test-fixtures"
WORK = FIX / ".work"
SONG_JOB = ROOT / "jobs" / "20261029-160741-kabhi-kabhi-aditi-zindagi-jaane-tu-2008"
FFMPEG = str(ROOT / "bin" / "ffmpeg")
CAPS = {"song": (None, None), "drama": (7.0, None), "reels": (2.5, 4)}  # (max seconds, max words)


def media(name: str) -> Path | None:
    """Test inputs, made on first use from the fixtures (other containers, a 60 s song excerpt)."""
    WORK.mkdir(exist_ok=True)
    video = FIX / "Lovika-Hospital.mp4"
    song = SONG_JOB / "input.mp3"
    made = {
        "clip.wav": ROOT / "sample" / "clip.wav",
        "lovika.mp4": video,
        "song.mp3": song,
    }
    if name in made:
        return made[name] if made[name].exists() else None
    src, args = {
        "lovika.mov": (video, ["-c", "copy"]),
        "lovika.mkv": (video, ["-c", "copy"]),
        "lovika.m4a": (video, ["-vn", "-c:a", "copy"]),
        "song-60s.mp3": (song, ["-ss", "0", "-t", "60", "-c", "copy"]),
        "clip.mp3": (ROOT / "sample" / "clip.wav", ["-b:a", "192k"]),
    }[name]
    out = WORK / name
    if not out.exists() and src.exists():
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), *args, str(out)], check=True)
    return out if out.exists() else None


def song_lyrics() -> str:
    cues = json.loads((SONG_JOB / "cues.json").read_text(encoding="utf-8"))["cues"]
    return "\n".join(c["text"] for c in cues)


def lovika_text() -> str:
    text = (FIX / "Bleaching-Powder-provisional-reference.txt").read_text(encoding="utf-8")
    text = re.sub(r"\[([^\]]*)\?\]", r"\1", text)
    return "\n".join(s.strip() for s in re.split(r"(?<=[.?!])\s+", text) if s.strip())


# name → (section, mode, file, settings)
CASES = {
    "song-lyrics-mp3": ("song", "lyrics", "song.mp3", {"language": "hinglish", "lyrics": song_lyrics}),
    "song-lyrics-wav": ("song", "lyrics", "clip.wav", {"language": "en", "lyrics": "sample"}),
    "song-transcribe-hinglish": ("song", "transcribe", "song-60s.mp3", {"language": "hinglish", "script": "hinglish", "quality": "accurate"}),
    "drama-lyrics-mov": ("drama", "lyrics", "lovika.mov", {"language": "hinglish", "lyrics": lovika_text}),
    "drama-transcribe-mkv-fast": ("drama", "transcribe", "lovika.mkv", {"language": "hinglish", "script": "hinglish", "quality": "fast"}),
    "reels-transcribe-mp4": ("reels", "transcribe", "lovika.mp4", {"language": "auto", "script": "default", "quality": "accurate", "topic_words": "NCERT, bleaching powder, board exam"}),
    "reels-lyrics-m4a": ("reels", "lyrics", "lovika.m4a", {"language": "hinglish", "lyrics": lovika_text}),
    "reels-transcribe-english": ("reels", "transcribe", "clip.mp3", {"language": "auto", "script": "default"}),
}


def post(base: str, path: str, fields: dict, file: Path | None = None) -> dict:
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    if file:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{file.name}\"\r\n"
                 "Content-Type: application/octet-stream\r\n\r\n").encode() + file.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(base + path, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def wait(base: str, job_id: str, statuses=("done", "error", "draft")) -> dict:
    while True:
        with urllib.request.urlopen(f"{base}/api/jobs/{job_id}") as r:
            s = json.load(r)
        if s["status"] in statuses:
            return s
        time.sleep(1)


def check_srt(srt: str, section: str, max_chars: int) -> list[str]:
    problems, last_end = [], -1.0
    blocks = [b for b in srt.strip().split("\n\n") if b.strip()]
    max_seconds, max_words = CAPS[section]

    def secs(t):
        h, m, rest = t.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    for n, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) != 3 or lines[0] != str(n) or " --> " not in lines[1]:
            problems.append(f"cue {n} malformed")
            continue
        start, end = (secs(x) for x in lines[1].split(" --> "))
        if start < last_end:
            problems.append(f"cue {n} overlaps the previous one")
        if end < start:
            problems.append(f"cue {n} ends before it starts")
        if len(lines[2]) > max_chars:
            problems.append(f"cue {n} has {len(lines[2])} chars > {max_chars}")
        if max_words and len(lines[2].split()) > max_words:
            problems.append(f"cue {n} has more than {max_words} words")
        if max_seconds and end - start > max_seconds + 0.25:  # + early-appear offset
            problems.append(f"cue {n} lasts {end - start:.2f}s > {max_seconds}")
        last_end = end
    if not blocks:
        problems.append("no cues")
    return problems


def run(base: str, name: str) -> dict:
    section, mode, fname, opts = CASES[name]
    path = media(fname)
    if path is None:
        return {"case": name, "result": "skipped", "why": f"{fname} not available"}
    fmt = "horizontal" if section == "drama" else "vertical"
    t0 = time.time()
    if mode == "lyrics":
        lyrics = opts["lyrics"]
        lyrics = (ROOT / "sample" / "lyrics.txt").read_text(encoding="utf-8") if lyrics == "sample" else lyrics()
        job = post(base, "/api/jobs", {"lyrics": lyrics, "video_format": fmt, "language": opts["language"],
                                       "section": section}, path)
        state = wait(base, job["job_id"])
        draft = None
    else:
        fields = {"language": opts["language"], "section": section, "script": opts["script"]}
        for k in ("quality", "topic_words"):
            if k in opts:
                fields[k] = opts[k]
        job = post(base, "/api/transcribe", fields, path)
        draft = wait(base, job["job_id"])
        if draft["status"] != "draft":
            return {"case": name, "result": "FAIL", "why": draft.get("message")}
        post(base, f"/api/jobs/{job['job_id']}/approve",
             {"lyrics": draft["draft_text"], "video_format": fmt, "language": opts["language"]})
        state = wait(base, job["job_id"], ("done", "error"))
    seconds = time.time() - t0
    if state["status"] != "done":
        return {"case": name, "result": "FAIL", "why": state.get("message")}
    with urllib.request.urlopen(f"{base}/api/jobs/{job['job_id']}/srt") as r:
        srt = r.read().decode("utf-8")
    job_dir = ROOT / "jobs" / state["job_dir"]
    data_dir = job_dir if job_dir.exists() else None
    problems = check_srt(srt, section, 42 if fmt == "horizontal" else 25)
    cues_json = json.loads((data_dir / "cues.json").read_text(encoding="utf-8")) if data_dir else {}
    if data_dir and cues_json.get("section") != section:
        problems.append("section not recorded in cues.json")
    out = {"case": name, "result": "FAIL" if problems else "pass", "cues": len(state["cues"]),
           "first_cue": round(state["cues"][0]["start"], 2) if state["cues"] else None,
           "seconds": round(seconds, 1), "timings": state.get("timings"), "job_dir": state["job_dir"]}
    if draft:
        out.update(engine=draft.get("engine"), quality=draft.get("quality"), script=draft.get("script"),
                   review_marks=len(draft.get("review_marks") or []), draft=draft["draft_text"][:120])
    if problems:
        out["problems"] = problems[:5]
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    base, names = argv[0].rstrip("/"), argv[1:]
    names = list(CASES) if names == ["all"] else names
    results = []
    for name in names:
        r = run(base, name)
        results.append(r)
        print(f"{r['result']:7} {name:28} {r.get('cues', '')} cues, first {r.get('first_cue')} s, "
              f"{r.get('seconds')} s {r.get('why') or r.get('problems') or ''}", flush=True)
    print(json.dumps(results, indent=1, ensure_ascii=False))
    return 1 if any(r["result"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

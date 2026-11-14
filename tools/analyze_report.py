"""Read a user's problem report (V4.9) — a developer tool, not shipped with the app.

    python tools/analyze_report.py <SRT-Generator-report-….zip> [--no-rerun] [--keep]

Unpacks and validates the zip, then prints a readable summary: the report, the settings, every step
with its timing, script lines recovered / flagged / not found, AI changes, flags, possible missing
captions, failed external commands, warnings and errors. When the audio is included it runs the job
again here with the same settings (offline: the final AI check is skipped) and lists how the new
captions differ from the reported ones.

Run it first whenever you're given a report zip (CLAUDE.md, "Debugging a user report").
"""

import argparse
import difflib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("report_id", "category", "app_version", "os", "included")


class ReportError(Exception):
    pass


def unpack(zip_path: Path, into: Path) -> Path:
    """Extract safely (no absolute paths, no ".."), and check report.json is there."""
    if not zipfile.is_zipfile(zip_path):
        raise ReportError(f"{zip_path} isn't a zip file.")
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
                raise ReportError(f"Refusing an unsafe path in the zip: {name}")
            if info.file_size > 2 * 1024 ** 3:
                raise ReportError(f"{name} is too large ({info.file_size} bytes).")
        z.extractall(into)
    if not (into / "report.json").exists():
        raise ReportError("No report.json: this isn't an SRT Generator problem report.")
    return into


def load(folder: Path) -> dict:
    def j(name):
        p = folder / name
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except ValueError:
            raise ReportError(f"{name} isn't valid JSON.")
    info = j("report.json")
    missing = [k for k in REQUIRED if k not in info]
    if missing:
        raise ReportError(f"report.json is missing {', '.join(missing)}.")
    listed = set(info.get("files") or [])
    present = {str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*") if p.is_file()} - {"report.json"}
    problems = [f"listed but missing: {n}" for n in sorted(listed - present)]
    return {"info": info, "job": j("job/job.json"), "cues": j("job/cues.json"), "edited": j("job/edited-cues.json"),
            "timeline": j("job/timeline.json"), "folder": folder, "problems": problems,
            "log": (folder / "job/log.txt").read_text(encoding="utf-8", errors="replace") if (folder / "job/log.txt").exists() else "",
            "app_log": (folder / "app.log").read_text(encoding="utf-8", errors="replace") if (folder / "app.log").exists() else ""}


def clock(t) -> str:
    if not isinstance(t, (int, float)):
        return "?"
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:06.3f}"


def section(title: str):
    print(f"\n== {title} " + "=" * max(0, 70 - len(title)))


def summarize(r: dict):
    info, job, cues = r["info"], r["job"] or {}, r["cues"] or {}
    section("Report")
    print(f"Category:     {info['category']}")
    print(f"Description:  {info.get('description') or '(none)'}")
    print(f"Time in video: {clock(info['time_in_video']) if info.get('time_in_video') is not None else 'not given'}")
    print(f"App {info['app_version']} on {info['os']} — created {info.get('created')}")
    print(f"Job:          {info.get('job') or '(general problem)'}")
    print(f"Included:     {', '.join(k for k, v in info['included'].items() if v) or 'nothing'}")
    for p in r["problems"]:
        print(f"WARNING: {p}")
    if not job:
        print("(no job.json in the report)")
    else:
        env = job.get("environment") or {}
        section("Environment")
        print(f"{env.get('os')} · {env.get('cpu')} · GPU {env.get('gpu')} · Python {env.get('python')}")
        print(f"Free disk {env.get('disk_free_mb')} MB · memory {env.get('memory')}")
        print("Packages: " + ", ".join(f"{k} {v}" for k, v in (env.get("packages") or {}).items()))
        for m in env.get("models") or []:
            print(f"Model {m['name']} ({m.get('version') or '-'}): {'installed' if m.get('installed') else 'MISSING'} at {m.get('path')}")
        section("Settings and input")
        print("Settings: " + json.dumps(job.get("settings") or {}, ensure_ascii=False))
        inp = job.get("input") or {}
        print(f"Input: {inp.get('name')} · {inp.get('size_bytes')} bytes · {inp.get('duration')} s · "
              f"{inp.get('container')} · video {inp.get('has_video')} · "
              + ", ".join(f"{s.get('codec_type')} {s.get('codec_name')}" for s in inp.get("streams") or []))
        section(f"Steps (status: {job.get('status')})")
        for s in job.get("steps") or []:
            out = json.dumps(s.get("outputs") or {}, ensure_ascii=False)
            print(f"{s.get('start', '')[11:23]}  {s['name']:<26} {s.get('seconds', 0):>8.3f} s  "
                  f"{'FAILED: ' + s['error'] if s.get('error') else out[:110]}")
        for rnd in job.get("reanchor") or []:
            section(f"Re-anchoring round {rnd['round']}: lost at line {rnd['lost_at']} ({rnd['why']})")
            print(f"Anchor line {rnd['anchor']}, searched {rnd['searched']}, {rnd['anchored']} lines heard by Whisper, "
                  f"{rnd['chunks']} chunks, {rnd['seconds']} s")
            for x in rnd["lines"]:
                print(f"Line {x['line']:>3}: {x['before']} -> {x['after']}"
                      f"{'  heard at ' + str(x['heard_at']) if x['heard_at'] else '  (not heard)'}")
        lines = job.get("script_lines") or []
        odd = [x for x in lines if x.get("status") != "kept"]
        section(f"Script lines ({len(lines)}; {len(odd)} not simply kept)")
        for x in odd:
            print(f"Line {x['line']:>3} {x['status'].upper():<10} confidence {x.get('confidence')} "
                  f"searched {x.get('searched')} heard {x.get('heard_share')} ‘{x.get('heard', '')}’: {x['text']}"
                  f" — {x.get('reason', '')}")
        section("AI")
        for a in job.get("ai") or []:
            print(f"{a['kind']} by {a.get('provider')} in {a.get('seconds')} s, tokens {a.get('usage')}: "
                  f"{len(a.get('accepted') or [])} accepted, {len(a.get('refused') or a.get('refused_lines') or [])} refused")
            for c in a.get("accepted") or []:
                print(f"   accepted: {c.get('from', c.get('original'))} -> {c.get('to', c.get('replacement'))}")
            for c in a.get("refused") or []:
                print(f"   refused:  {c.get('from')} -> {c.get('to')} ({c.get('why')})")
        bad = [c for c in job.get("commands") or [] if c.get("exit") not in (0, None) or c.get("error")]
        section(f"External commands ({len(job.get('commands') or [])}, {len(bad)} failed)")
        for c in bad:
            print(f"FAILED exit {c.get('exit')} ({c.get('what')}): {c['cmd']}\n   {(c.get('stderr') or c.get('error') or '').strip()[:500]}")
        section("Edits in Review")
        for e in job.get("edits") or []:
            for ch in e["changes"]:
                print(f"{e['time'][11:19]} {ch['edit']}: {ch.get('from', '')} {('-> ' + ch['to']) if ch.get('to') else ''}")
    shown = r["edited"] if isinstance(r["edited"], list) else cues.get("cues") or []
    flags = [(i + 1, c) for i, c in enumerate(shown) if any((c.get(k) or {}).get("status") == "flag" for k in ("listen", "script"))]
    section(f"Captions: {len(cues.get('cues') or [])} generated, {len(shown)} as reported; {len(flags)} flagged")
    if cues.get("listen_check"):
        print(f"Listening check: {json.dumps(cues['listen_check'], ensure_ascii=False)}")
    for n, c in flags:
        why = (c.get("listen") or {}).get("reasons") or [(c.get("script") or {}).get("reason")]
        resolved = any((c.get(k) or {}).get("resolved") for k in ("listen", "script"))
        print(f"#{n:<3} {clock(c['start'])}–{clock(c['end'])} “{c['text']}”: {'; '.join(filter(None, why))}"
              f"{' (marked checked)' if resolved else ''}")
    missing = cues.get("missing_captions") or (job.get("missing_captions") if job else []) or []
    section(f"Possible missing captions: {len(missing)}")
    for m in missing:
        print(f"{clock(m['start'])}–{clock(m['end'])} ({m['seconds']} s of speech with no caption)")
    nf = cues.get("not_found") or []
    if nf:
        section(f"Not found in the audio: {len(nf)}")
        for x in nf:
            print(f"Line {x['line']}: {x['text']} (gap {x.get('gap')})")
    section("Warnings and errors")
    for w in (job.get("warnings") if job else None) or []:
        print(f"WARNING: {w['message']}")
    for e in (job.get("errors") if job else None) or []:
        print(f"ERROR: {e['message']}")
    for source, text in (("log.txt", r["log"]), ("app.log", r["app_log"])):
        block = []
        for line in text.splitlines():
            if " ERROR " in line or " CRITICAL " in line or " WARNING " in line or line.startswith(("Traceback", "  File", "    ")) and block:
                block.append(line)
        if block:
            print(f"--- {source} ---")
            print("\n".join(block[-60:]))


def rerun(r: dict, work: Path) -> list[dict] | None:
    """Run the job again here with the same settings; returns the new cues (or None if it can't)."""
    folder, job, cues = r["folder"], r["job"] or {}, r["cues"] or {}
    audio = next(iter(sorted((folder / "job").glob("input.*"))), None)
    if audio is None:
        print("\n(No audio in the report: not re-run. Ask for a report with “The audio” ticked.)")
        return None
    text = None
    for name in ("lyrics.txt", "approved-text.txt"):
        if (folder / "job" / name).exists():
            text = (folder / "job" / name).read_text(encoding="utf-8")
            break
    if not text:
        print("\n(No script or approved transcript in the report: not re-run.)")
        return None
    data = work / "data"
    data.mkdir(parents=True, exist_ok=True)
    models = ROOT / "models" if (ROOT / "models").is_dir() else Path.home() / "Library/Application Support/SRT Generator/models"
    if models.is_dir() and not (data / "models").exists():
        (data / "models").symlink_to(models, target_is_directory=True)
    os.environ["SRT_DATA_DIR"] = str(data)
    sys.path.insert(0, str(ROOT))
    import pipeline  # noqa: E402 — after SRT_DATA_DIR, so the re-run uses the scratch folder

    s = job.get("settings") or {}
    section("Re-run with the same settings (offline: no AI check)")
    report = lambda key, msg, fraction=None: print(f"  [{key}] {msg}", flush=True)  # noqa: E731
    ctx = pipeline.start_job(audio, s.get("section") or cues.get("section") or "song", report,
                             s.get("isolate") or cues.get("isolate"))
    result = pipeline.align_and_write(ctx, text, strip_tags=s.get("strip_tags", True),
                                      video_format=s.get("format") or cues.get("video_format"),
                                      language=s.get("language") or cues.get("language"), report=report,
                                      caption_mode=s.get("caption_mode") or cues.get("caption_mode"),
                                      max_words=s.get("max_words"), max_chars=s.get("max_chars"),
                                      merge_short=s.get("merge_short"))
    print(f"Re-run job folder: {result['job_dir']}")
    return result["cues"]


def diff(old: list[dict], new: list[dict]):
    section(f"Re-run vs reported captions ({len(old)} reported, {len(new)} now)")
    a = [c["text"] for c in old]
    b = [c["text"] for c in new]
    same = shifted = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            for o, n in zip(old[i1:i2], new[j1:j2]):
                if abs(o["start"] - n["start"]) > 0.2 or abs(o["end"] - n["end"]) > 0.2:
                    shifted += 1
                    print(f"timing  “{o['text']}”: {clock(o['start'])}–{clock(o['end'])} -> {clock(n['start'])}–{clock(n['end'])}")
                else:
                    same += 1
            continue
        for o in old[i1:i2]:
            print(f"- {clock(o['start'])} {o['text']}")
        for n in new[j1:j2]:
            print(f"+ {clock(n['start'])} {n['text']}")
    print(f"{same} captions the same, {shifted} moved by more than 0.2 s.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("zip", type=Path)
    ap.add_argument("--no-rerun", action="store_true", help="only summarize, even when the audio is included")
    ap.add_argument("--keep", action="store_true", help="keep the unpacked report and the re-run's job folder")
    args = ap.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="srt-report-"))
    try:
        r = load(unpack(args.zip, work / "report"))
        summarize(r)
        if not args.no_rerun:
            new = rerun(r, work)
            if new is not None:
                old = r["cues"].get("cues") if r["cues"] else []
                diff(old or [], new)
        return 0
    except ReportError as e:
        print(f"Not a valid report: {e}")
        return 2
    finally:
        if args.keep:
            print(f"\nKept in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

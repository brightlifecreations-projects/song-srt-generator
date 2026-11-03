"""Word-timing check for the test fixtures (not shipped).

Reference timings come from an aligner independent of Whisper: torchaudio's MMS_FA, a
wav2vec2 CTC forced aligner trained on romanised text in 1,100+ languages, which suits
Hinglish. It needs torchaudio, which the app doesn't install, so run this file with a
separate venv that has torch + torchaudio:

    python timing.py reference <audio> <text.txt> <out.json>       → [{"word", "start", "end"}]
    python timing.py compare <reference.json> <cues.json | words.json> [...]

compare matches words in order (same normalisation as score.py) and reports the start-time
error of every matched word: mean, median, worst, and the share within 0.3 s.
"""

import difflib
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from score import words  # noqa: E402


def reference(audio: str, text: str) -> list[dict]:
    import soundfile
    import torch
    import torchaudio
    from torchaudio.pipelines import MMS_FA as bundle

    data, rate = soundfile.read(audio, dtype="float32", always_2d=True)  # a WAV
    wave = torch.from_numpy(data.T)
    wave = torchaudio.functional.resample(wave.mean(0, keepdim=True), rate, bundle.sample_rate)
    model, tokenizer, aligner = bundle.get_model(), bundle.get_tokenizer(), bundle.get_aligner()
    transcript = [re.sub(r"[^a-z']", "", w) for w in words(text.replace("[", " ").replace("?]", " "))]
    transcript = [w for w in transcript if w]
    with torch.inference_mode():
        emission, _ = model(wave)
        spans = aligner(emission[0], tokenizer(transcript))
    frame = wave.size(1) / emission.size(1) / bundle.sample_rate
    return [{"word": w, "start": round(s[0].start * frame, 3), "end": round(s[-1].end * frame, 3)}
            for w, s in zip(transcript, spans)]


def load_words(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):  # cues.json
        data = [w for cue in data["cues"] for w in cue["words"]]
    out = []
    for w in data:
        for part in words(w["word"]):  # "jaan-pehchaan," → one normalised word
            out.append({"word": part, "start": w["start"], "end": w["end"]})
    return out


def compare(ref: list[dict], hyp: list[dict], early: float = 0.0) -> dict:
    sm = difflib.SequenceMatcher(a=[w["word"] for w in ref], b=[w["word"] for w in hyp], autojunk=False)
    errs = []
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            errs.append(abs(hyp[block.b + k]["start"] - ref[block.a + k]["start"]))
    if not errs:
        return {"matched": 0}
    return {"matched": len(errs), "ref_words": len(ref), "mean": statistics.mean(errs),
            "median": statistics.median(errs), "max": max(errs),
            "within_0.3": sum(e <= 0.3 for e in errs) / len(errs)}


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[0] == "reference":
        out = reference(argv[1], Path(argv[2]).read_text(encoding="utf-8"))
        Path(argv[3]).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"{len(out)} words → {argv[3]}")
        return 0
    if len(argv) >= 3 and argv[0] == "compare":
        ref = load_words(argv[1])
        for path in argv[2:]:
            r = compare(ref, load_words(path))
            if not r["matched"]:
                print(f"{path}: no words matched")
                continue
            print(f"{path}: {r['matched']}/{r['ref_words']} words matched · start error mean "
                  f"{r['mean']:.3f} s, median {r['median']:.3f} s, worst {r['max']:.3f} s, "
                  f"{r['within_0.3'] * 100:.0f}% within 0.3 s")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Word error rate of a transcript against a reference (test fixtures, not shipped).

    python test-fixtures/score.py <reference.txt> <hypothesis.txt | job folder> [--show]

A job folder is scored on the draft the user would see (transcript-autocorrected.txt if
present, else transcript-draft.txt). Text is compared lower-cased, without punctuation
(apostrophes and hyphens inside words are dropped, so "jaan-pehchaan" = "jaanpehchaan").

Phrases the reference marks as unsure, written "[words?]", are excluded from scoring: each
is a wildcard that absorbs 0–3 hypothesis words at no cost, and its words don't count
towards the reference length.

WER = (substitutions + deletions + insertions) / scored reference words.
"""

import re
import sys
from pathlib import Path

UNSURE = re.compile(r"\[([^\]]*)\?\]")
WILDCARD = None  # a reference slot that matches anything
MAX_WILDCARD_WORDS = 3


def words(text: str) -> list[str]:
    text = text.lower().replace("’", "'")
    text = re.sub(r"(?<=\w)['\-](?=\w)", "", text)
    return re.findall(r"\w+", text)


def reference_tokens(text: str) -> tuple[list, int]:
    """Reference words with each unsure phrase as one WILDCARD slot; also the unsure word count."""
    out, unsure, pos = [], 0, 0
    for m in UNSURE.finditer(text):
        out += words(text[pos:m.start()])
        out.append(WILDCARD)
        unsure += len(words(m.group(1)))
        pos = m.end()
    out += words(text[pos:])
    return out, unsure


def align(ref: list, hyp: list[str]):
    """Levenshtein alignment; a WILDCARD absorbs 0..MAX_WILDCARD_WORDS hyp words for free.
    Returns (S, D, I, ops) with ops = [(kind, ref_word, hyp_words)]."""
    n, m = len(ref), len(hyp)
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == inf:
                continue
            if i < n and ref[i] is WILDCARD:
                for k in range(0, min(MAX_WILDCARD_WORDS, m - j) + 1):
                    if c < cost[i + 1][j + k]:
                        cost[i + 1][j + k], back[i + 1][j + k] = c, ("skip", i, j)
                continue
            if i < n and j < m:
                step = 0 if ref[i] == hyp[j] else 1
                if c + step < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], back[i + 1][j + 1] = c + step, ("ok" if step == 0 else "sub", i, j)
            if i < n and c + 1 < cost[i + 1][j]:
                cost[i + 1][j], back[i + 1][j] = c + 1, ("del", i, j)
            if j < m and c + 1 < cost[i][j + 1]:
                cost[i][j + 1], back[i][j + 1] = c + 1, ("ins", i, j)
    ops, i, j = [], n, m
    while (i, j) != (0, 0):
        kind, pi, pj = back[i][j]
        ops.append((kind, ref[pi] if pi < i else None, hyp[pj:j]))
        i, j = pi, pj
    ops.reverse()
    counts = {k: sum(1 for o in ops if o[0] == k) for k in ("sub", "del", "ins")}
    return counts["sub"], counts["del"], counts["ins"], ops


def score(reference: str, hypothesis: str) -> dict:
    ref, unsure = reference_tokens(reference)
    hyp = words(hypothesis)
    s, d, i, ops = align(ref, hyp)
    n = sum(1 for r in ref if r is not WILDCARD)
    return {"wer": (s + d + i) / n if n else 0.0, "sub": s, "del": d, "ins": i,
            "ref_words": n, "unsure_words": unsure, "hyp_words": len(hyp), "ops": ops}


def hypothesis_text(path: Path) -> str:
    if path.is_dir():
        for name in ("transcript-autocorrected.txt", "transcript-draft.txt", "approved-text.txt"):
            if (path / name).exists():
                return (path / name).read_text(encoding="utf-8")
        raise SystemExit(f"No transcript in {path}")
    return path.read_text(encoding="utf-8")


def errors(ops) -> list[str]:
    out = []
    for kind, r, h in ops:
        if kind == "sub":
            out.append(f"  {r} → {h[0]}")
        elif kind == "del":
            out.append(f"  {r} → (missing)")
        elif kind == "ins":
            out.append(f"  (extra) → {h[0]}")
    return out


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 2
    result = score(Path(args[0]).read_text(encoding="utf-8"), hypothesis_text(Path(args[1])))
    print(f"WER {result['wer'] * 100:.1f}%  ({result['sub']} sub, {result['del']} del, {result['ins']} ins "
          f"/ {result['ref_words']} words; {result['unsure_words']} unsure words excluded)")
    if "--show" in argv:
        print("\n".join(errors(result["ops"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

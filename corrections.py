"""The corrections file: words Whisper got wrong and what they should be (DECISIONS V4.1).

corrections.md lives in the data folder (DATA_DIR) and is plain Markdown that people can
read and edit in any text editor, and share with teammates:

    ## Hinglish
    | Wrong | Correct | Times seen | Last seen | Notes |
    |---|---|---|---|---|
    | adti | Aditi | 3 | 2026-11-02 | |
    | ~~main~~ | mein | 2 | 2026-11-02 | disabled: strikethrough |

One table per script (Hinglish, Hindi (Devanagari), Urdu, English, …). Every approved
transcription adds its fixes (Whisper's draft vs the approved text, word by word per line);
every new draft has the enabled fixes for its script applied before the user sees it, and
the most frequent correct words are passed to Whisper as hotwords.
"""

import difflib
import re
import shutil
import threading
import unicodedata
from datetime import date, datetime
from pathlib import Path

FILE_NAME = "corrections.md"
BACKUP_DIR = "corrections-backups"
COLUMNS = ["Wrong", "Correct", "Times seen", "Last seen", "Notes"]
MAX_PHRASE_WORDS = 4  # longer rewrites are kept in the job's diff but not learned
HOTWORD_TOKENS = 200  # Whisper's prompt holds 223 tokens (half its 448-token context)

HEADER = """# SRT Generator — corrections

Words Whisper got wrong, and what they should be. SRT Generator adds a row every time you
approve a transcript with a fix, applies these fixes to every new draft (whole words and
phrases, matching upper/lower case), and passes the most frequent correct words to Whisper
as hints.

How to edit this file by hand:

- One table per script, under a `## Script name` heading (Hinglish, Hindi (Devanagari), Urdu, English, …).
- Columns: **Wrong** (what Whisper writes; can be a few words) · **Correct** · **Times seen**
  (a whole number) · **Last seen** (YYYY-MM-DD) · **Notes** (optional).
- To switch a row off without deleting it, strike the wrong word through: `~~adti~~`.
- Write a `|` inside a cell as `\\|`.
- Share it with your team: they upload it in the app's Corrections tab and it's merged with theirs.
"""

_lock = threading.Lock()


class CorrectionsError(Exception):
    """A file that isn't a valid corrections file; .errors lists the bad rows."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---------------------------------------------------------------------------
# Which table a transcription belongs to

SCRIPT_TABLES = {"hinglish": "Hinglish", "devanagari": "Hindi (Devanagari)", "urdu": "Urdu",
                 "english": "English"}
LANGUAGE_TABLES = {"hi": "Hindi (Devanagari)", "ur": "Urdu", "en": "English"}


def table_for(script: str | None, language: str | None) -> str:
    """The table a draft's corrections go in: by script, or for "same" by its spoken language."""
    if script in SCRIPT_TABLES:
        return SCRIPT_TABLES[script]
    if language in LANGUAGE_TABLES:
        return LANGUAGE_TABLES[language]
    try:
        from whisper.tokenizer import LANGUAGES
        return LANGUAGES.get(language, language or "Other").title()
    except ImportError:
        return (language or "Other").title()


# ---------------------------------------------------------------------------
# Words

def _core(token: str) -> tuple[str, str, str]:
    """Split a token into (leading punctuation, word, trailing punctuation)."""
    start, end = 0, len(token)
    while start < end and _is_punct(token[start]):
        start += 1
    while end > start and _is_punct(token[end - 1]):
        end -= 1
    return token[:start], token[start:end], token[end:]


def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch).startswith(("P", "S")) and ch not in "'’"


def key_of(phrase: str) -> str:
    """Lookup key: the words without surrounding punctuation, lower-cased, single-spaced."""
    return " ".join(_core(t)[1].casefold() for t in phrase.split() if _core(t)[1])


def stored_form(wrong: str, correct: str) -> str:
    """The correction as stored: lower case when the wrong word's own capitalisation explains
    it ("Kabi" → "Kabhi" is stored as "kabhi"), as typed otherwise (names: "adti" → "Aditi")."""
    lower = correct.lower()
    return lower if lower != correct and match_case(wrong, lower) == correct else correct


def match_case(original: str, replacement: str) -> str:
    """Carry the original's capitalisation over (case-aware replacement)."""
    if len(original) > 1 and original.isupper() and replacement.islower():
        return replacement.upper()
    if original[:1].isupper() and replacement[:1].islower():
        return replacement[:1].upper() + replacement[1:]
    return replacement


# ---------------------------------------------------------------------------
# The Markdown file

def _cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    parts = re.split(r"(?<!\\)\|", body)
    return [p.strip().replace("\\|", "|") for p in parts]


def parse(text: str, source: str = "corrections.md") -> dict:
    """Markdown → {table: {key: row}}. Raises CorrectionsError listing every bad row."""
    tables: dict[str, dict] = {}
    errors: list[str] = []
    current = None
    for n, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            if not current:
                errors.append(f"{source} line {n}: a table heading needs a script name")
                current = None
                continue
            tables.setdefault(current, {})
            continue
        if not stripped.startswith("|"):
            continue
        cells = _cells(stripped)
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue  # the |---| separator row
        if [c.lower() for c in cells[:4]] == [c.lower() for c in COLUMNS[:4]]:
            continue  # header row
        where = f"{source} line {n}"
        if current is None:
            errors.append(f"{where}: this row isn't under a '## Script' heading")
            continue
        if len(cells) < 4:
            errors.append(f"{where}: needs at least 4 columns (Wrong | Correct | Times seen | Last seen), found {len(cells)}")
            continue
        wrong, correct, count, seen = cells[:4]
        notes = cells[4] if len(cells) > 4 else ""
        disabled = bool(re.fullmatch(r"~~.+~~", wrong))
        if disabled:
            wrong = wrong[2:-2].strip()
        bad = []
        if not key_of(wrong):
            bad.append("'Wrong' is empty")
        if not correct.strip():
            bad.append("'Correct' is empty")
        if not re.fullmatch(r"\d+", count) or int(count) < 1:
            bad.append(f"'Times seen' must be a whole number of 1 or more (got '{count}')")
        try:
            datetime.strptime(seen, "%Y-%m-%d")
        except ValueError:
            bad.append(f"'Last seen' must be a date like 2026-11-02 (got '{seen}')")
        key = key_of(wrong)
        if not bad and key in tables[current]:
            bad.append(f"'{wrong}' is already in the {current} table (line "
                       f"{tables[current][key]['line']})")
        if bad:
            errors.append(f"{where}: " + "; ".join(bad))
            continue
        tables[current][key] = {"wrong": wrong, "correct": correct.strip(), "count": int(count),
                                "last_seen": seen, "notes": notes, "disabled": disabled, "line": n}
    if errors:
        raise CorrectionsError(errors)
    for rows in tables.values():
        for row in rows.values():
            row.pop("line", None)
    return {name: rows for name, rows in tables.items()}


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render(tables: dict) -> str:
    out = [HEADER]
    for name in sorted(tables, key=str.casefold):
        rows = tables[name]
        if not rows:
            continue
        out.append(f"## {name}\n")
        out.append("| " + " | ".join(COLUMNS) + " |")
        out.append("|" + "---|" * len(COLUMNS))
        for row in sorted(rows.values(), key=lambda r: (-r["count"], r["wrong"].casefold())):
            wrong = _cell(row["wrong"])
            out.append(f"| {'~~' + wrong + '~~' if row['disabled'] else wrong} | {_cell(row['correct'])} "
                       f"| {row['count']} | {row['last_seen']} | {_cell(row.get('notes', ''))} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def path(data_dir: Path) -> Path:
    return Path(data_dir) / FILE_NAME


def load(data_dir: Path) -> dict:
    p = path(data_dir)
    return parse(p.read_text(encoding="utf-8"), p.name) if p.exists() else {}


def save(data_dir: Path, tables: dict):
    p = path(data_dir)
    tmp = p.with_suffix(".md.tmp")
    tmp.write_text(render(tables), encoding="utf-8")
    tmp.replace(p)


def backup(data_dir: Path) -> Path | None:
    p = path(data_dir)
    if not p.exists():
        return None
    folder = Path(data_dir) / BACKUP_DIR
    folder.mkdir(exist_ok=True)
    target = folder / f"corrections-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    shutil.copy2(p, target)
    return target


def reset(data_dir: Path) -> Path | None:
    """Back the file up, then clear it."""
    with _lock:
        saved = backup(data_dir)
        save(data_dir, {})
        return saved


# ---------------------------------------------------------------------------
# Diff of an approved transcript

def diff_lines(draft: str, approved: str) -> list[dict]:
    """Word-by-word changes, line by line. Lines are first matched to each other (so an added
    or removed line doesn't shift everything after it), then each changed pair is compared word
    by word. Returns [{"draft_line", "approved_line", "draft", "approved", "changes": [
    {"type": "replace"|"insert"|"delete", "wrong", "correct"}]}]."""
    a = [line.strip() for line in draft.splitlines() if line.strip()]
    b = [line.strip() for line in approved.splitlines() if line.strip()]
    out = []
    matcher = difflib.SequenceMatcher(a=[key_of(x) for x in a], b=[key_of(x) for x in b], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if a[i] != b[j]:  # same words, different punctuation/case: still compare
                    out.append(_diff_pair(a[i], b[j], i, j))
            continue
        pairs = list(zip(range(i1, i2), range(j1, j2)))
        for i, j in pairs:
            out.append(_diff_pair(a[i], b[j], i, j))
        for i in range(i1 + len(pairs), i2):
            out.append({"draft_line": i + 1, "approved_line": None, "draft": a[i], "approved": "",
                        "changes": [{"type": "delete", "wrong": a[i], "correct": ""}]})
        for j in range(j1 + len(pairs), j2):
            out.append({"draft_line": None, "approved_line": j + 1, "draft": "", "approved": b[j],
                        "changes": [{"type": "insert", "wrong": "", "correct": b[j]}]})
    return [d for d in out if d["changes"]]


def _diff_pair(a_line: str, b_line: str, i: int, j: int) -> dict:
    aw, bw = a_line.split(), b_line.split()
    ak = [_core(w)[1] for w in aw]
    bk = [_core(w)[1] for w in bw]
    changes = []
    matcher = difflib.SequenceMatcher(a=[k.casefold() for k in ak], b=[k.casefold() for k in bk],
                                      autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for x, y in zip(range(i1, i2), range(j1, j2)):
                if ak[x] != bk[y] and ak[x] and bk[y]:  # a case-only fix
                    changes.append({"type": "replace", "wrong": ak[x], "correct": bk[y]})
            continue
        if op == "replace" and i2 - i1 == j2 - j1 and all(
                _similar(ak[x], bk[y]) for x, y in zip(range(i1, i2), range(j1, j2))):
            # Same number of words, each one a respelling of the other: learn each word on its
            # own (reusable). A re-split run ("bichadhja i" → "bichhad jaaye") stays one phrase,
            # so no rule like "i" → "jaaye" is ever learnt.
            for x, y in zip(range(i1, i2), range(j1, j2)):
                if ak[x] and bk[y] and ak[x] != bk[y]:
                    changes.append({"type": "replace", "wrong": ak[x], "correct": bk[y]})
            continue
        wrong = " ".join(k for k in ak[i1:i2] if k)
        correct = " ".join(k for k in bk[j1:j2] if k)
        kind = "replace" if wrong and correct else ("insert" if correct else "delete")
        if wrong or correct:
            changes.append({"type": kind, "wrong": wrong, "correct": correct})
    return {"draft_line": i + 1, "approved_line": j + 1, "draft": a_line, "approved": b_line,
            "changes": changes}


def _similar(a: str, b: str) -> bool:
    """Spelled alike (a respelling, not a different word), or a number written out ("3" → "Three")."""
    if a.isdigit() or b.isdigit():
        return True
    return difflib.SequenceMatcher(a=a.casefold(), b=b.casefold()).ratio() >= 0.5


def learnable(diff: list[dict]) -> list[tuple[str, str]]:
    """The (wrong, correct) pairs worth keeping: replacements of up to MAX_PHRASE_WORDS words."""
    pairs = []
    for line in diff:
        for c in line["changes"]:
            if (c["type"] == "replace" and len(c["wrong"].split()) <= MAX_PHRASE_WORDS
                    and len(c["correct"].split()) <= MAX_PHRASE_WORDS):
                pairs.append((c["wrong"], c["correct"]))
    return pairs


def diff_markdown(diff: list[dict]) -> str:
    out = []
    for d in diff:
        where = f"draft line {d['draft_line']}" if d["draft_line"] else "new line"
        if d["approved_line"]:
            where += f" → approved line {d['approved_line']}"
        out.append(f"- **{where}**  \n  Whisper: {d['draft'] or '—'}  \n  Approved: {d['approved'] or '—'}")
        for c in d["changes"]:
            if c["type"] == "replace":
                out.append(f"  - replaced “{c['wrong']}” → “{c['correct']}”")
            elif c["type"] == "insert":
                out.append(f"  - inserted “{c['correct']}”")
            else:
                out.append(f"  - deleted “{c['wrong']}”")
    return "\n".join(out) + "\n" if out else "No changes: the draft was approved as Whisper wrote it.\n"


def record(data_dir: Path, table: str, pairs: list[tuple[str, str]], today: str | None = None) -> list[dict]:
    """Add approved fixes to corrections.md. Same fix again → Times seen + 1; a different fix for
    the same wrong word → the newest wins, the old one is noted. Returns what changed."""
    today = today or date.today().isoformat()
    changed = []
    with _lock:
        tables = load(data_dir)
        rows = tables.setdefault(table, {})
        for wrong, correct in pairs:
            key = key_of(wrong)
            if not key or wrong == correct:
                continue
            correct = stored_form(wrong, correct)
            wrong = wrong if wrong != wrong.capitalize() or " " in wrong else wrong.lower()
            row = rows.get(key)
            if row and row["correct"] == correct:
                row["count"] += 1
                row["last_seen"] = today
                changed.append({"wrong": wrong, "correct": correct, "count": row["count"], "change": "seen again"})
            elif row:
                note = f"was “{row['correct']}” ({row['count']}×, last {row['last_seen']})"
                rows[key] = {"wrong": row["wrong"], "correct": correct, "count": 1, "last_seen": today,
                             "notes": note, "disabled": row["disabled"]}
                changed.append({"wrong": wrong, "correct": correct, "count": 1, "change": note})
            else:
                rows[key] = {"wrong": wrong, "correct": correct, "count": 1, "last_seen": today,
                             "notes": "", "disabled": False}
                changed.append({"wrong": wrong, "correct": correct, "count": 1, "change": "new"})
        save(data_dir, tables)
    return changed


# ---------------------------------------------------------------------------
# Applying the corrections to a new draft

def apply(text: str, rows: dict) -> tuple[str, list[dict]]:
    """Replace every whole word or phrase that matches an enabled row (case-insensitive match,
    case-aware replacement; punctuation around the words is kept). Longest phrases win.
    Returns (new text, [{"line", "start", "end", "original", "replacement"}]) with character
    offsets into the new line, for highlighting."""
    active = {k: r for k, r in rows.items() if not r["disabled"]}
    if not active:
        return text, []
    longest = max(len(k.split()) for k in active)
    out_lines, hits = [], []
    for n, line in enumerate(text.split("\n")):
        tokens = line.split(" ")
        cores = [_core(t) for t in tokens]
        result, pieces_pos, i = [], 0, 0
        while i < len(tokens):
            match = None
            for size in range(min(longest, len(tokens) - i), 0, -1):
                span = cores[i:i + size]
                if not all(c[1] for c in span):
                    continue
                key = " ".join(c[1].casefold() for c in span)
                if key in active:
                    match = (size, active[key])
                    break
            if match:
                size, row = match
                span = cores[i:i + size]
                original = " ".join(c[1] for c in span)
                replacement = match_case(span[0][1], row["correct"])
                if replacement != original:
                    lead, trail = span[0][0], span[-1][2]
                    piece = lead + replacement + trail
                    start = pieces_pos + len(lead)
                    hits.append({"line": n, "start": start, "end": start + len(replacement),
                                 "original": original, "replacement": replacement})
                    result.append(piece)
                    pieces_pos += len(piece) + 1
                    i += size
                    continue
            result.append(tokens[i])
            pieces_pos += len(tokens[i]) + 1
            i += 1
        out_lines.append(" ".join(result))
    return "\n".join(out_lines), hits


def hotwords(rows: dict, count_tokens=None, limit: int = HOTWORD_TOKENS) -> str:
    """The most frequent correct words of a table, comma-separated, within Whisper's prompt limit.
    count_tokens(text) → token count (the model's own tokenizer); without it ~4 chars a token."""
    count_tokens = count_tokens or (lambda s: len(s) // 3 + 1)
    words, seen = [], set()
    for row in sorted((r for r in rows.values() if not r["disabled"]), key=lambda r: -r["count"]):
        word = row["correct"].strip()
        if word.casefold() in seen:
            continue
        candidate = ", ".join(words + [word])
        if count_tokens(" " + candidate) > limit:
            break
        words.append(word)
        seen.add(word.casefold())
    return ", ".join(words)


# ---------------------------------------------------------------------------
# Merging uploaded files

def merge_plan(mine: dict, uploads: list[tuple[str, dict]]) -> dict:
    """What merging the uploaded files into mine would do. Returns {"added", "same", "conflicts"},
    each conflict {"table", "key", "wrong", "options": [{"source", "correct", "count", ...}],
    "default": index of the option with the highest Times seen (mine on a tie)}."""
    options: dict[tuple, list] = {}
    for table, rows in mine.items():
        for key, row in rows.items():
            options.setdefault((table, key), []).append({"source": "Your corrections", **row})
    for name, tables in uploads:
        for table, rows in tables.items():
            for key, row in rows.items():
                options.setdefault((table, key), []).append({"source": name, **row})
    added, same, conflicts = 0, 0, []
    for (table, key), opts in options.items():
        distinct = {o["correct"] for o in opts}
        in_mine = opts[0]["source"] == "Your corrections"
        if len(distinct) == 1:
            if not in_mine:
                added += 1
            elif len(opts) > 1:
                same += 1
            continue
        best = max(range(len(opts)), key=lambda i: (opts[i]["count"], opts[i]["source"] == "Your corrections"))
        conflicts.append({"table": table, "key": key, "wrong": opts[0]["wrong"], "options": opts,
                          "default": best})
    return {"added": added, "same": same, "conflicts": conflicts}


def merge(mine: dict, uploads: list[tuple[str, dict]], choices: dict | None = None) -> dict:
    """Merge uploaded tables into mine. choices: {"<table>\\t<key>": option index} for conflicts
    (default: highest Times seen). The same fix in several files keeps the highest Times seen
    and the latest Last seen, so uploading a file twice changes nothing."""
    choices = choices or {}
    plan = merge_plan(mine, uploads)
    merged = {t: dict(rows) for t, rows in mine.items()}
    conflict_keys = {(c["table"], c["key"]): c for c in plan["conflicts"]}
    for name, tables in uploads:
        for table, rows in tables.items():
            target = merged.setdefault(table, {})
            for key, row in rows.items():
                if (table, key) in conflict_keys:
                    continue
                if key in target:
                    cur = target[key]
                    target[key] = {**cur, "count": max(cur["count"], row["count"]),
                                   "last_seen": max(cur["last_seen"], row["last_seen"])}
                else:
                    target[key] = dict(row)
    for (table, key), c in conflict_keys.items():
        pick = c["options"][int(choices.get(f"{table}\t{key}", c["default"]))]
        others = [o for o in c["options"] if o["correct"] != pick["correct"]]
        note = "; ".join(f"{o['source']}: “{o['correct']}” ({o['count']}×)" for o in others)
        merged.setdefault(table, {})[key] = {
            "wrong": pick["wrong"], "correct": pick["correct"], "count": pick["count"],
            "last_seen": pick["last_seen"], "disabled": pick["disabled"],
            "notes": ("chosen over " + note) if note else pick.get("notes", "")}
    return merged

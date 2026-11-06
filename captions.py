"""Caption rules (DECISIONS.md V4.6): from aligned words to on-screen captions.

Everything here is pure Python on plain dicts, so it is unit-tested without any model
(tests/test_captions.py). The pipeline calls it after alignment:

    words   = words_from_segments(segments, speakers)   # the aligner's words, never changed
    tokens  = make_tokens(words, language)              # display units: numbers as digits,
                                                         # "twenty five" -> one "25" token
    tokens  = offline_cleanup(tokens, words, language)  # stray quotes, sentence case, "I"
    cues    = build_cues(tokens, words, rules)          # "line" (TikTok rules) or "word" mode

Timing always comes from the word timestamps: a caption starts on its first spoken word and
ends just after its last, so converting display text (numbers, an AI's spelling fix) or moving
a cut point can never make the timing drift.

Words:  {"word": raw aligned text, "start", "end", "line": script line, "speaker": str|None}
Tokens: {"text": display text, "w0", "w1": first/last word index (inclusive), "orig": the text
         before the final AI check changed it (only when it did)}
Cues:   {"index", "start", "end", "text", "tokens": [token indices], "words": [...], "ai": [...]}
"""

import math
import re
import unicodedata

# ---------------------------------------------------------------------------
# Defaults (config.py can override every one through rules_for())

MAX_WORDS = 5
MAX_CHARS_VERTICAL = 30
MAX_CHARS_HORIZONTAL = 42
END_TAIL = 0.1           # a caption stays this long after its last word ends (never into the next)
WORD_GAP_FILL = 1.0      # word by word: a gap shorter than this is bridged (the word stays up)
MERGE_SHORT_SECONDS = 0.5
MIN_VISIBLE = 0.08      # no caption is shorter than this (two frames); see _min_visible()
TRANSCRIBE_PAUSE = 0.7   # transcribe mode: a pause at least this long starts a new caption

# Words a caption must not end on (articles, prepositions, conjunctions, auxiliaries,
# possessives) — English, and Hinglish / Hindi postpositions and particles.
WEAK_END_ENGLISH = set("""
a an the to of and but or nor so for from with by at in on into onto upon about as than that
just is are was were am be been being has have had do does did will would shall should can could
may might must my your his her its our their this these those very really if because when while
whose which who whom where like not no every each some any i'm you're we're they're he's she's
it's i'll you'll we'll i'd i've you've we've they've let's gonna wanna gotta
""".split())
WEAK_END_HINDI = set("""
ka ki ke ko se mein me ne par pe tak aur toh to bhi hi jo ya ek yeh ye woh wo is us un in
का की के को से में ने पर तक और तो भी ही जो या
""".split())
# English and Hindi together: what Hinglish captions use (English words appear in them too).
WEAK_END = WEAK_END_ENGLISH | WEAK_END_HINDI


def weak_end(language: str | None) -> set:
    """The weak words for a caption's language: English alone, or with the Hindi ones for Hindi,
    Hinglish and Urdu ("me" is "in" in Hinglish, but a fine last word in English)."""
    return WEAK_END if language in ("hinglish", "hi", "ur", "pa", None) else WEAK_END_ENGLISH
# Words that lean on the word before them: don't start a caption with them (Hindi postpositions).
WEAK_START = set("ka ki ke ko se mein ne tak wala wali wale waala का की के को से में ने तक".split())
DETERMINERS = set("the a an my your his her our their its this that these those some any every no".split())
PARTICLES = set("up out down off over away back".split())
NEGATIONS = set("not never no don't won't can't didn't isn't wasn't ain't nahi nahin na mat".split())
SUBJECTS = set("i you he she we they it main mai hum tum aap".split())
CONJ_START = set("and but or so because that which who when where if while then after before until "
                 "unless though although aur lekin magar kyunki kyonki jab agar phir".split())
# Words a number belongs to: "size 4", "Week 1", "7 a.m.", "12 years", "70%".
LABELS = set("size week day chapter level step part number no no. round season episode page room gate "
             "class grade rank top round verse act scene stage phase volume vol vol. track floor "
             "row seat line".split())
UNITS = set("year years yrs month months week weeks day days hour hours hrs minute minutes min mins "
            "second seconds secs times percent % kg kgs kilo kilos km kms miles mile pounds lbs inch "
            "inches feet foot cm mm meters metres litres liters dollars dollar rupees rupee rs bucks "
            "a.m. p.m. am pm o'clock people kids children steps reps sets calories grams g ml "
            "saal saalon mahine mahina mahino hafte hafta din dino ghante ghanta minat baje log logon "
            "bachche bacche rupaye rupay rupye kilo guna pratishat".split())

_PUNCT_END = re.compile(r"[,.;:!?…।॥۔،؟؛]['\"”’\)\]]*$")
OPENING_PUNCT = re.compile(r"^['\"“‘\(\[]*[¿¡]")
_SENTENCE_END = re.compile(r"[.!?…।॥۔؟]['\"”’\)\]]*$")
# Abbreviations whose full stop doesn't end a phrase: a.m., p.m., e.g., U.S., Mr., Dr., etc.
ABBREVIATIONS = set("mr. mrs. ms. dr. st. vs. etc. jr. sr. prof. no. mt. ft. approx. dept. inc. ltd. co.".split())


def _abbreviation(text: str) -> bool:
    body = text.strip().strip("'\"”’)]").casefold()
    return body in ABBREVIATIONS or bool(re.fullmatch(r"(?:\w\.){2,}", body))


class _Ends:
    """PUNCT_END / SENTENCE_END: like a regex .search(), but an abbreviation's dot doesn't count."""

    def __init__(self, pattern):
        self.pattern = pattern

    def search(self, text: str):
        if _abbreviation(text) and not re.search(r"[,;:!?…]", text):
            return None
        return self.pattern.search(text)


PUNCT_END = _Ends(_PUNCT_END)
SENTENCE_END = _Ends(_SENTENCE_END)
_EDGE = re.compile(r"^([^\w]*)(.*?)([^\w]*)$", re.UNICODE)
_ZERO_WIDTH = {"Mn", "Mc", "Me", "Cf"}


def display_len(text: str) -> int:
    """Characters on screen, not counting combining marks or joiners (Devanagari vowel signs…)."""
    return sum(1 for ch in text if unicodedata.category(ch) not in _ZERO_WIDTH)


def core(text: str) -> str:
    """A token without its surrounding punctuation, lower-cased: "“Hello," -> "hello"."""
    m = _EDGE.match(text.strip())
    return (m.group(2) if m else text).casefold().replace("’", "'")


def _split_edges(text: str) -> tuple[str, str, str]:
    m = _EDGE.match(text)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", text, "")


# ---------------------------------------------------------------------------
# Words from the aligner

def words_from_segments(segments, lines: list[str] | None = None, speakers: list | None = None) -> list[dict]:
    """stable-ts segments -> flat word list, each word tagged with its script line and speaker.

    With `lines`, words are matched to the script lines by counting words (the aligner may cut a
    line into several segments); without, each segment counts as a line."""
    raw = [(si, w) for si, seg in enumerate(segments) for w in seg.words if w.word.strip()]
    line_of = [si for si, _ in raw]
    if lines is not None:
        counts = [len(line.split()) for line in lines]
        if sum(counts) == len(raw):
            line_of = [li for li, n in enumerate(counts) for _ in range(n)]
    out = []
    for (_, w), line in zip(raw, line_of):
        out.append({"word": w.word.strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3),
                    "line": line, "speaker": speakers[line] if speakers and line < len(speakers) else None,
                    "probability": round(float(getattr(w, "probability", None) or 0.0), 3)
                    if getattr(w, "probability", None) is not None else None})
    return out


# ---------------------------------------------------------------------------
# Speaker labels (script mode)

_LABEL = re.compile(r"^\s*([^\W\d_][\w.'’\- ]{0,24}?)\s*:\s*(.*)$", re.UNICODE)


def split_speakers(lines: list[str]) -> tuple[list[str], list, list[str]]:
    """Script lines like "Sarah: Hi there" -> (["Hi there"], ["Sarah"], labels found).

    A label is at most three words before a colon. Labels count only when at least two lines
    carry one, so a single "Note: …" or "Listen: …" stays text. A label on a line of its own
    applies to the lines after it. Lines left empty are dropped."""
    found = [_LABEL.match(line) for line in lines]
    candidates = [m for m in found if m and len(m.group(1).split()) <= 3
                  and not re.match(r"^(https?|www)$", m.group(1), re.I)]
    if len(candidates) < 2:
        return list(lines), [None] * len(lines), []
    texts, speakers, current, labels = [], [], None, []
    for line, m in zip(lines, found):
        if m and len(m.group(1).split()) <= 3:
            current = m.group(1).strip()
            if current not in labels:
                labels.append(current)
            line = m.group(2).strip()
        if line:
            texts.append(line)
            speakers.append(current)
    return texts, speakers, labels


# ---------------------------------------------------------------------------
# Numbers -> digits (display only; the words keep their timings)

_UNITS = {"zero": 0, "oh": None, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
          "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
          "nineteen": 19}
_UNITS.pop("oh")
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
_HINGLISH = {"ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
             "chhah": 6, "chheh": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11,
             "barah": 12, "baarah": 12, "terah": 13, "chaudah": 14, "pandrah": 15, "solah": 16,
             "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20, "tees": 30, "chalis": 40,
             "chaalis": 40, "pachas": 50, "pachaas": 50, "sau": 100, "hazaar": 1000, "hazar": 1000}
_HINGLISH_SCALES = {"sau": 100, "hazaar": 1000, "hazar": 1000, "lakh": 100_000, "crore": 10_000_000}

# Expressions that stay in words. Editable: number-exceptions.txt in the data folder.
DEFAULT_EXCEPTIONS = [
    "not one", "no one", "one time", "one day", "one of", "one another", "one by one", "the one",
    "this one", "that one", "which one", "every one", "anyone", "someone", "one more", "one thing",
    "one last", "one and only", "you two", "us two", "the two of", "one or two", "a thousand times",
    "one step at a time", "one love", "ek baar", "ek din", "ek dum", "ekdum", "do pal",
]


def load_exceptions(text: str | None) -> list[str]:
    """number-exceptions.txt -> phrases (one per line, # comments)."""
    if text is None:
        return list(DEFAULT_EXCEPTIONS)
    return [" ".join(line.split()).casefold() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _english_number(words: list[str]) -> tuple[int, int] | None:
    """The longest English number at the start of words: (value, words used), or None."""
    total, current, used, last = 0, 0, 0, None
    i = 0
    while i < len(words):
        w = words[i]
        parts = w.split("-")
        if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS and 0 < _UNITS[parts[1]] < 10:
            if last in ("unit", "tens", "teen"):
                break
            current += _TENS[parts[0]] + _UNITS[parts[1]]
            last = "unit"
        elif w in _UNITS:
            if last in ("unit", "teen") or (last == "tens" and _UNITS[w] >= 10) or (last == "tens" and _UNITS[w] == 0):
                break
            current += _UNITS[w]
            last = "teen" if _UNITS[w] >= 10 else "unit"
        elif w in _TENS:
            if last in ("unit", "tens", "teen"):
                break
            current += _TENS[w]
            last = "tens"
        elif w in _SCALES and last is not None:
            if _SCALES[w] == 100:
                if current == 0 or current >= 100:
                    break
                current *= 100
            else:
                total += max(current, 1) * _SCALES[w]
                current = 0
            last = "scale"
        elif w == "and" and last == "scale" and i + 1 < len(words) and (words[i + 1] in _UNITS or words[i + 1] in _TENS):
            last = "and"
        else:
            break
        i += 1
        if last != "and":
            used = i
    if used == 0:
        return None
    # A trailing "and" isn't part of the number.
    return total + current, used


def _hinglish_number(words: list[str]) -> tuple[int, int] | None:
    total, current, used = 0, 0, 0
    for i, w in enumerate(words):
        if w in _HINGLISH_SCALES and used:
            current = max(current, 1) * _HINGLISH_SCALES[w]
            if _HINGLISH_SCALES[w] >= 1000:
                total += current
                current = 0
        elif w in _HINGLISH and w not in _HINGLISH_SCALES and (used == 0 or current % 100 == 0 and current):
            current += _HINGLISH[w]
        elif w in _HINGLISH_SCALES and not used:
            current = _HINGLISH_SCALES[w]
        else:
            break
        used = i + 1
    return (total + current, used) if used else None


def _is_digits(text: str) -> bool:
    return bool(re.fullmatch(r"\d[\d,.:]*%?", core(text)))


def make_tokens(words: list[dict], language: str | None = None, exceptions: list[str] | None = None,
                numbers: bool = True) -> list[dict]:
    """Display tokens over the words. Number words become digits ("twenty five" -> "25", spanning
    both words); "seventy percent" -> "70%"; expressions in the exceptions list stay words.
    Hinglish counts (ek, do, teen…) become digits only when a count word follows ("do saal")."""
    exceptions = [e.casefold() for e in (DEFAULT_EXCEPTIONS if exceptions is None else exceptions)]
    hinglish = language in ("hinglish", "hi")
    cores = [core(w["word"]) for w in words]
    tokens, i = [], 0
    while i < len(words):
        found = None
        if numbers and not _excepted(cores, i, exceptions):
            found = _number_at(words, cores, i, hinglish)
        if found:
            text, n = found
            tokens.append({"text": text, "w0": i, "w1": i + n - 1})
            i += n
        else:
            tokens.append({"text": words[i]["word"], "w0": i, "w1": i})
            i += 1
    return tokens


def _excepted(cores: list[str], i: int, exceptions: list[str]) -> bool:
    """Is the word at i inside one of the exception phrases?"""
    for phrase in exceptions:
        parts = phrase.split()
        for k in range(len(parts)):
            if i - k >= 0 and cores[i - k:i - k + len(parts)] == parts:
                return True
    return False


def _number_at(words, cores, i, hinglish) -> tuple[str, int] | None:
    """(display text, words used) when a number starts at word i."""
    # Never run a number across a punctuation break or a script line.
    span = []
    for k in range(i, min(len(words), i + 8)):
        if k > i and words[k]["line"] != words[i]["line"]:
            break
        span.append(cores[k])
        if PUNCT_END.search(words[k]["word"]):
            break
    english = _english_number(span)
    value = used = None
    if english:
        value, used = english
        single = used == 1 and span[0] in _UNITS and _UNITS[span[0]] <= 10
        if span[0] == "one" and used == 1 and not _counted(cores, words, i, 1):
            return None  # "one" alone is usually a pronoun: "the one I love"
        if span[0] == "one" and used == 1 and NEGATIONS & set(cores[max(0, i - 3):i]):
            return None  # "not giving up one inch": an expression, not a count
        if single and span[0] in ("two", "ten") and not _counted(cores, words, i, 1) and i > 0 \
                and cores[i - 1] in ("you", "us", "we", "them"):
            return None
    elif hinglish:
        h = _hinglish_number(span)
        if h and (_counted(cores, words, i, h[1]) or h[1] > 1 or span[0] in _HINGLISH_SCALES):
            value, used = h
        if h and h[1] == 1 and span[0] in ("do", "saat", "das", "sau", "nau") and not _counted(cores, words, i, 1):
            value = used = None  # do = give/two, saat = with/seven… only as a count
    if value is None:
        return None
    first, last = words[i]["word"], words[i + used - 1]["word"]
    lead = _split_edges(first)[0]
    trail = _split_edges(last)[2]
    text = f"{value:,}" if value >= 10000 else str(value)
    # "seventy percent" -> "70%"
    nxt = i + used
    if nxt < len(words) and cores[nxt] in ("percent", "per cent") and words[nxt]["line"] == words[i]["line"] \
            and not PUNCT_END.search(last):
        trail = _split_edges(words[nxt]["word"])[2]
        return f"{lead}{text}%{trail}", used + 1
    return f"{lead}{text}{trail}", used


def _counted(cores, words, i, used) -> bool:
    """Is the number at i a count or a label's number? ("12 years", "size 4", "7 a.m.")"""
    nxt = i + used
    after = cores[nxt] if nxt < len(cores) and not PUNCT_END.search(words[nxt - 1]["word"]) else ""
    before = cores[i - 1] if i > 0 and not PUNCT_END.search(words[i - 1]["word"]) else ""
    return after in UNITS or after in ("percent", "per") or before in LABELS


# ---------------------------------------------------------------------------
# Offline cleanup (always runs; the final AI check, when set up, comes after it)

_DQ = '"“”„'
_SQ_OPEN = "'‘"
_SQ_CLOSE = "'’"


def _latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum("LATIN" in unicodedata.name(c, "") for c in letters) / len(letters) >= 0.5


def offline_cleanup(tokens: list[dict], words: list[dict]) -> list[dict]:
    """Stray quotation marks and apostrophes removed (per script line), sentence case at the start
    of every sentence, and a lone "i" written "I". Never lower-cases anything (CAPS for emphasis
    stay). Returns new tokens."""
    tokens = [dict(t) for t in tokens]
    by_line: dict[int, list[int]] = {}
    for k, t in enumerate(tokens):
        by_line.setdefault(words[t["w0"]]["line"], []).append(k)
    for ks in by_line.values():
        _fix_quotes(tokens, ks)
    sentence_start = True
    for k, t in enumerate(tokens):
        text = t["text"]
        if _latin(text):
            lead, body, trail = _split_edges(text)
            if body.casefold() in ("i", "i'm", "i'll", "i've", "i'd", "i’m", "i’ll", "i’ve", "i’d"):
                body = "I" + body[1:]
            if sentence_start and body and body[0].islower():
                body = body[0].upper() + body[1:]
            t["text"] = lead + body + trail
        if any(c.isalnum() for c in text):
            sentence_start = bool(SENTENCE_END.search(text))
    return tokens


def _fix_quotes(tokens: list[dict], ks: list[int]):
    """Remove double quotes that don't pair up, and single quotes used as quotation marks that
    don't pair up (apostrophes inside or at the end of words — don't, players' — stay)."""
    opens = []
    stray = []  # (token k, side, char index)
    for k in ks:
        text = tokens[k]["text"]
        lead, body, trail = _split_edges(text)
        for ch in lead:
            if ch in _DQ:
                if opens and opens[-1][2] == "d" and ch in '"”':
                    opens.pop()
                else:
                    opens.append((k, "lead", "d", ch))
            elif ch in _SQ_OPEN:
                opens.append((k, "lead", "s", ch))
        for ch in trail:
            if ch in _DQ:
                if opens and opens[-1][2] == "d":
                    opens.pop()
                else:
                    stray.append((k, "trail", ch))
            elif ch in _SQ_CLOSE:
                if opens and opens[-1][2] == "s":
                    opens.pop()
                elif not body.casefold().endswith("s") and ch != "’" or ch == "'" and not body.casefold().endswith("s"):
                    stray.append((k, "trail", ch))
    stray += [(k, side, ch) for k, side, _, ch in opens]
    for k, side, ch in stray:
        lead, body, trail = _split_edges(tokens[k]["text"])
        if side == "lead":
            lead = lead.replace(ch, "", 1)
        else:
            idx = trail.rfind(ch)
            trail = trail[:idx] + trail[idx + 1:]
        if body or lead or trail:
            tokens[k]["text"] = lead + body + trail


# ---------------------------------------------------------------------------
# Line breaking (TikTok rules)

def rules_for(section_rules: dict, video_format: str, *, mode: str = "line", transcribe: bool = False,
              language: str | None = None,
              max_words: int = MAX_WORDS, max_chars_vertical: int = MAX_CHARS_VERTICAL,
              max_chars_horizontal: int = MAX_CHARS_HORIZONTAL, merge_short: bool = False,
              merge_short_seconds: float = MERGE_SHORT_SECONDS, end_tail: float = END_TAIL,
              word_gap_fill: float = WORD_GAP_FILL, transcribe_pause: float = TRANSCRIBE_PAUSE) -> dict:
    pause = section_rules.get("pause_split_seconds")
    if transcribe:
        pause = min(pause or transcribe_pause, transcribe_pause)
    section_words = section_rules.get("max_words")
    return {"mode": mode if mode in ("line", "word") else "line", "language": language,
            "max_words": min(max_words, section_words) if section_words else max_words,
            "max_chars": max_chars_vertical if video_format == "vertical" else max_chars_horizontal,
            "max_seconds": section_rules.get("max_cue_seconds"),
            "pause_split": pause, "merge_short": merge_short, "merge_short_seconds": merge_short_seconds,
            "end_tail": end_tail, "word_gap_fill": word_gap_fill}


def _tstart(tokens, words, k):
    return words[tokens[k]["w0"]]["start"]


def _tend(tokens, words, k):
    return words[tokens[k]["w1"]]["end"]


def runs(tokens: list[dict], words: list[dict], rules: dict) -> list[list[int]]:
    """Token indices cut at the hard boundaries: a script line, a speaker change, punctuation that
    ends a phrase, an opening ¿/¡, and a long pause (not right after a weak word)."""
    out, cur = [], []
    for k, t in enumerate(tokens):
        if cur:
            prev = tokens[cur[-1]]
            pw, w = words[prev["w1"]], words[t["w0"]]
            gap = w["start"] - pw["end"]
            pause = rules.get("pause_split")
            if (w["line"] != pw["line"] or w["speaker"] != pw["speaker"] or OPENING_PUNCT.search(t["text"])
                    or (pause and gap >= pause
                        and (core(prev["text"]) not in weak_end(rules.get("language")) or gap >= 2.0))):
                out.append(cur)
                cur = []
        cur.append(k)
        if PUNCT_END.search(t["text"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _break_cost(tokens, words, a: int, b: int, weak: set = WEAK_END) -> float:
    """Cost of ending a caption after token a, the next one starting at token b."""
    last, nxt = core(tokens[a]["text"]), core(tokens[b]["text"])
    cost = 0.0
    if last in weak:
        cost += 10
    if nxt in WEAK_START:
        cost += 6
    if last in SUBJECTS and nxt not in CONJ_START:
        cost += 5  # keep the subject with its verb
    if _is_digits(tokens[b]["text"]) and last in LABELS:
        cost += 8  # "size | 4"
    if _is_digits(tokens[a]["text"]) and (nxt in UNITS or nxt.startswith("%")):
        cost += 8  # "12 | years"
    if a > 0 and core(tokens[a - 1]["text"]) in DETERMINERS and last not in weak and nxt not in CONJ_START \
            and nxt not in WEAK_END_ENGLISH and not _is_digits(tokens[b]["text"]) \
            and not PUNCT_END.search(tokens[a]["text"]):
        cost += 3  # "the old | ones": don't split a noun phrase after its first adjective
    if nxt in ("me", "him", "us", "them") and last not in weak:
        cost += 3  # "told | me": keep the verb with its object
    if nxt in PARTICLES and last not in weak:
        cost += 2.5  # "waking | up"
    if nxt in CONJ_START:
        cost -= 1.5
    gap = _tstart(tokens, words, b) - _tend(tokens, words, a)
    cost -= min(max(gap, 0.0), 1.0) * 3
    return cost


_SIZE_COST = {1: 4.0, 2: 1.5, 3: 0.4}


def _chunk_ok(tokens, words, ks, rules) -> bool:
    if len(ks) == 1:
        return True
    if len(ks) > rules["max_words"]:
        return False
    if display_len(" ".join(tokens[k]["text"] for k in ks)) > rules["max_chars"]:
        return False
    if rules.get("max_seconds") and _tend(tokens, words, ks[-1]) - _tstart(tokens, words, ks[0]) > rules["max_seconds"]:
        return False
    return True


def break_run(tokens, words, run: list[int], rules: dict) -> list[list[int]]:
    """The cheapest way to cut one run into captions that fit (dynamic programming)."""
    n = len(run)
    best = [math.inf] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(max(0, j - max(rules["max_words"], 1)), j):
            chunk = run[i:j]
            if not _chunk_ok(tokens, words, chunk, rules) or best[i] == math.inf:
                continue
            cost = best[i] + (_SIZE_COST.get(len(chunk), 0.0) if n > len(chunk) else 0.0)
            if i > 0:
                cost += _break_cost(tokens, words, run[i - 1], run[i], weak_end(rules.get("language")))
            if cost < best[j]:
                best[j], back[j] = cost, i
    pieces, j = [], n
    while j > 0:
        i = back[j]
        pieces.append(run[i:j])
        j = i
    return pieces[::-1]


def check_chunks(tokens, words, chunks: list[list[int]], rules: dict) -> bool:
    """Do these captions (e.g. the AI's cut points) keep the hard rules? Every caption fits, no
    caption crosses a hard boundary, and the order and coverage of tokens is exact."""
    if [k for c in chunks for k in c] != list(range(len(tokens))) or any(not c for c in chunks):
        return False
    hard = {r[-1] for r in runs(tokens, words, rules)}
    for c in chunks:
        if not _chunk_ok(tokens, words, c, rules) or any(k in hard for k in c[:-1]):
            return False
        if core(tokens[c[-1]]["text"]) in weak_end(rules.get("language")) and c[-1] not in hard:
            return False
    return True


def line_chunks(tokens, words, rules: dict) -> list[list[int]]:
    chunks = [p for run in runs(tokens, words, rules) for p in break_run(tokens, words, run, rules)]
    if rules.get("merge_short"):
        chunks = _merge_short(tokens, words, chunks, rules)
    return chunks


def _merge_short(tokens, words, chunks, rules) -> list[list[int]]:
    """"Merge very short captions": one shorter than merge_short_seconds joins its neighbour in
    the same run when the result still fits (never across punctuation, a line or a speaker)."""
    run_of = {}
    for r, run in enumerate(runs(tokens, words, rules)):
        for k in run:
            run_of[k] = r
    out = [list(c) for c in chunks]
    changed = True
    while changed:
        changed = False
        for i, c in enumerate(out):
            if _tend(tokens, words, c[-1]) - _tstart(tokens, words, c[0]) >= rules["merge_short_seconds"]:
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(out) and run_of[out[j][0]] == run_of[c[0]]:
                    merged = sorted(out[j] + c)
                    if _chunk_ok(tokens, words, merged, {**rules, "max_seconds": None}):
                        out[min(i, j)] = merged
                        del out[max(i, j)]
                        changed = True
                        break
            if changed:
                break
    return out


# ---------------------------------------------------------------------------
# Cues

def build_cues(tokens: list[dict], words: list[dict], rules: dict,
               chunks: list[list[int]] | None = None) -> list[dict]:
    """Captions in either mode, timed from the words: each starts on its first word and ends
    END_TAIL after its last, never past the next caption's start (no overlaps). Word by word,
    a short gap to the next word is bridged so the words don't flicker."""
    if rules["mode"] == "word":
        chunks = [[k] for k in range(len(tokens))]
    elif chunks is None:
        chunks = line_chunks(tokens, words, rules)
    cues = []
    for c in chunks:
        ws = [w for k in c for w in words[tokens[k]["w0"]:tokens[k]["w1"] + 1]]
        cue = {"start": ws[0]["start"], "end": ws[-1]["end"],
               "text": " ".join(tokens[k]["text"] for k in c), "tokens": list(c),
               "speaker": ws[0]["speaker"],
               "words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in ws]}
        ai = [{"i": pos, "from": tokens[k]["orig"], "to": tokens[k]["text"]}
              for pos, k in enumerate(c) if tokens[k].get("orig") is not None]
        if ai:
            cue["ai"] = ai
        cues.append(cue)
    for i, cue in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else None
        end = cue["end"] + rules.get("end_tail", END_TAIL)
        if rules["mode"] == "word" and nxt is not None and nxt - cue["end"] < rules.get("word_gap_fill", WORD_GAP_FILL):
            end = nxt
        if nxt is not None:
            end = min(end, nxt)
        cue["start"] = round(max(0.0, cue["start"]), 3)
        cue["end"] = round(max(end, cue["start"] + 0.001), 3)
        if nxt is not None and cue["end"] > nxt:  # the aligner gave two words the same instant
            cue["end"] = round(nxt, 3)
    for i, cue in enumerate(cues):
        if i + 1 < len(cues) and cues[i + 1]["start"] <= cue["start"]:
            cues[i + 1]["start"] = round(cue["start"] + 0.001, 3)
            cue["end"] = cues[i + 1]["start"]
    _min_visible(cues, rules.get("min_visible", MIN_VISIBLE))
    for i, cue in enumerate(cues):
        cue["index"] = i + 1
    return cues


def _min_visible(cues: list[dict], minimum: float):
    """The aligner sometimes squeezes a word into no time at all ("My" at 0.360–0.360), which would
    make a caption that flashes for a millisecond. Give such a caption `minimum` seconds: into the
    gap after it, else from the start of the next caption when that one stays at least as long.
    Never an overlap; the words keep their own timestamps."""
    for i, cue in enumerate(cues):
        if cue["end"] - cue["start"] >= minimum:
            continue
        want = round(cue["start"] + minimum, 3)
        nxt = cues[i + 1] if i + 1 < len(cues) else None
        if nxt is None or want <= nxt["start"]:
            cue["end"] = want
        elif nxt["end"] - want >= minimum:
            nxt["start"] = cue["end"] = want
        else:
            cue["end"] = max(cue["end"], nxt["start"])


# ---------------------------------------------------------------------------
# Script lines the aligner couldn't find in the audio

def unmatched_lines(words: list[dict], min_probability: float = 0.15) -> list[int]:
    """Script lines that probably aren't in the audio: the aligner squeezed their words into almost
    no time, or heard them with very low confidence. Returns line indices."""
    by_line: dict[int, list[dict]] = {}
    for w in words:
        by_line.setdefault(w["line"], []).append(w)
    out = []
    for line, ws in sorted(by_line.items()):
        squeezed = sum(1 for w in ws if w["end"] - w["start"] <= 0.021) / len(ws)
        probs = [w["probability"] for w in ws if w.get("probability") is not None]
        mean = sum(probs) / len(probs) if probs else 1.0
        span = ws[-1]["end"] - ws[0]["start"]
        if (squeezed >= 0.6 and len(ws) > 1) or mean < min_probability or (len(ws) > 2 and span < 0.05 * len(ws)):
            out.append(line)
    return out

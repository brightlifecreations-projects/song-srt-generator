"""Final AI check before output (DECISIONS.md V4.6).

After the captions are built, and when an AI provider is set up on the AI & API keys page (with
"Final AI check" on, the default), the cue texts go to that provider once more, with their word
lists, the script (if any), the section, the language and the caption rules. The AI may only:

- change the text of a word (token): stray quotes and apostrophes, capitalisation, punctuation,
  numbers as digits, misspellings;
- move cut points between existing words, where a line breaks the caption rules.

It answers with word indices and corrected text plus where each caption starts — never a new
transcript — so it can't add or remove a spoken word, and the app rebuilds every time from the
word timestamps: timing can't drift. Anything invalid (not JSON, an unknown index, a word split
in two or deleted, a timeout…) keeps the rule-based captions and says so in one line. Without a
provider, captions.offline_cleanup() has already done the offline part.
"""

import difflib
import json
import re
import time

import ai_polish
import captions
import config

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "tokens": {"type": "array", "items": {
            "type": "object", "properties": {"i": {"type": "integer"}, "t": {"type": "string"}},
            "required": ["i", "t"], "additionalProperties": False}},
        "cue_starts": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["tokens", "cue_starts"], "additionalProperties": False,
}

SYSTEM_PROMPT = """You do the final check on subtitles (SRT captions) before they are exported. \
The words were already timed to the audio; your job is the text and the line cuts only.

You get every word ("tokens", each with an index i and its text t) and the current captions (as the \
index of the first token of each caption, "cue_starts"), plus context: the section, the language and \
script, the user's own script when there is one, and the caption rules.

You may ONLY:
1. Change the text of a token: fix stray or unmatched quotation marks and apostrophes, capitalisation \
(sentence case, proper nouns, "I"; a word the user's script writes in CAPS for emphasis stays in caps), \
punctuation, numbers written as digits ("12 years", "Week 1", "7 a.m.", "70%", "size 4"; keep \
expressions like "not one inch", "one time", "you two" in words), and misspellings.
2. Move the cut points between captions (cue_starts) where a caption breaks the rules below.

You must NEVER add, remove, merge, split or reorder words. Each token stays one token: a token's \
new text has exactly as many words (space-separated parts) as before, and is never empty. Don't \
rephrase or translate. When the user's script spells something a certain way, follow the script.

Caption rules: one line per caption; at most {max_words} words and about {max_chars} characters; cut \
at natural reading points; keep phrases whole (prepositional phrases like "for her son", "in a size \
4"); keep subjects with their verbs; never end a caption on a weak word (the, a, an, to, of, and, \
but, just, is, ka, ki, ke, aur, toh…); punctuation that ends a phrase always ends a caption; one \
speaker per caption; a caption always ends after every token listed in "must_end_after" (a pause, \
the end of a script line, a speaker change or phrase-ending punctuation), so never put one of \
those inside a caption. {mode_note}

Answer with JSON only: {{"tokens": [{{"i": 3, "t": "corrected"}}, ...], "cue_starts": [0, 5, 9, ...]}}. \
List only the tokens you changed. cue_starts must start with 0 and cover every caption, in order."""

WORD_MODE_NOTE = ("These are word-by-word captions (one word per caption): return cue_starts "
                  "unchanged; fix the text only.")


class ReviewError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def active() -> dict | None:
    """The AI settings to check with, or None: no provider set up, or the switch is off."""
    s = ai_polish.load_settings()
    if not s.get("final_check", True) or not ai_polish.usable(s)[0]:
        return None
    return s


def build_request(tokens: list[dict], chunks: list[list[int]], *, words: list[dict], section: str,
                  language: str | None, script: str | None, script_text: str | None, rules: dict) -> str:
    payload = {
        "section": {"song": "song lyrics", "drama": "drama dialogue",
                    "reels": "reel / YouTube speech"}.get(section, section),
        "language": language,
        "script": ai_polish.SCRIPT_DESCRIPTIONS.get(script or "same", script),
        "caption_mode": "word by word" if rules["mode"] == "word" else "line by line",
        "rules": {"max_words": rules["max_words"], "max_chars": rules["max_chars"]},
        "users_script": script_text or None,
        "tokens": [{"i": k, "t": t["text"],
                    **({"speaker": words[t["w0"]]["speaker"]} if words[t["w0"]].get("speaker") else {})}
                   for k, t in enumerate(tokens)],
        "cue_starts": [c[0] for c in chunks],
        # V4.6.1: the hard breaks the cuts are checked against (they used to be checked, not sent).
        **({"must_end_after": [r[-1] for r in captions.runs(tokens, words, rules)[:-1]]}
           if rules["mode"] == "line" else {}),
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _letters(text: str) -> str:
    return re.sub(r"[\W_]", "", text.casefold())


def plausible(original: str, new: str) -> bool:
    """A spelling, case, punctuation or number fix — not a different word."""
    a, b = _letters(original), _letters(new)
    if a == b or not a or not b:
        return bool(b) or not a
    if b.isdigit() or a.isdigit():
        return True  # "five" -> "5"
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.5


def validate(answer: str, tokens: list[dict]) -> tuple[dict[int, str], list[int] | None, int]:
    """The AI's answer -> ({token index: new text}, cue_starts or None, changes dropped as
    implausible). Raises ReviewError when the answer can't be used at all."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", (answer or "").strip())
    try:
        data = json.loads(body)
    except ValueError:
        raise ReviewError("invalid", "the AI's answer wasn't valid JSON")
    if not isinstance(data, dict) or not isinstance(data.get("tokens"), list):
        raise ReviewError("invalid", "the AI's answer didn't have the expected fields")
    if len(data["tokens"]) > len(tokens):
        raise ReviewError("mismatch", "the AI returned more words than there are")
    edits, dropped = {}, 0
    for item in data["tokens"]:
        if not isinstance(item, dict) or not isinstance(item.get("i"), int) or not isinstance(item.get("t"), str):
            raise ReviewError("invalid", "the AI's answer didn't have the expected fields")
        k, text = item["i"], " ".join(item["t"].split())
        if not 0 <= k < len(tokens) or k in edits:
            raise ReviewError("invalid", "the AI referred to a word that doesn't exist")
        original = tokens[k]["text"]
        if len(text.split()) != len(original.split()):
            raise ReviewError("mismatch", "the AI changed the number of words")
        if text == original:
            continue
        if not plausible(original, text):
            dropped += 1
            continue
        edits[k] = text
    starts = data.get("cue_starts")
    if starts is not None:
        if not isinstance(starts, list) or not all(isinstance(x, int) for x in starts) or not starts \
                or starts[0] != 0 or starts != sorted(set(starts)) or starts[-1] >= len(tokens):
            starts = None
    return edits, starts, dropped


def review(tokens: list[dict], words: list[dict], chunks: list[list[int]], settings: dict, *,
           section: str, language: str | None, script: str | None, script_text: str | None,
           rules: dict, timeout: float | None = None, adapter=None) -> dict:
    """Run the check. Returns {"tokens", "chunks" (None = rule-based cuts), "changes", "message",
    "seconds", "sent", "returned"}. Never raises: any problem keeps the rule-based result."""
    t0 = time.perf_counter()
    name = ai_polish.provider_name(settings["provider"])
    mode_note = WORD_MODE_NOTE if rules["mode"] == "word" else ""
    system = SYSTEM_PROMPT.format(max_words=rules["max_words"], max_chars=rules["max_chars"], mode_note=mode_note)
    request = build_request(tokens, chunks, words=words, section=section, language=language, script=script,
                            script_text=script_text, rules=rules)
    result = {"tokens": tokens, "chunks": None, "changes": 0, "message": None, "sent": request,
              "returned": None, "provider": name}
    try:
        adapter = adapter or ai_polish.adapter_for(settings)
        adapter.timeout = timeout or config.AI_REVIEW_TIMEOUT_SECONDS
        answer = adapter.complete(system, request, schema=RESPONSE_SCHEMA)
        result["returned"] = answer
        edits, starts, dropped = validate(answer, tokens)
    except (ReviewError, ai_polish.PolishError) as e:
        result["message"] = f"Final AI check skipped — {e}. The rule-based captions were used."
        result["seconds"] = round(time.perf_counter() - t0, 2)
        return result
    except Exception as e:  # noqa: BLE001 — never fail the job over the check
        result["message"] = f"Final AI check skipped — something went wrong ({type(e).__name__}). " \
                            "The rule-based captions were used."
        result["seconds"] = round(time.perf_counter() - t0, 2)
        return result
    new_tokens = []
    for k, t in enumerate(tokens):
        t = dict(t)
        if k in edits:
            t["orig"] = t.get("orig", t["text"])
            t["text"] = edits[k]
            if t["text"] == t["orig"]:  # a later check put the word back: no change left to show
                del t["orig"]
        new_tokens.append(t)
    notes = []
    new_chunks = None
    if rules["mode"] == "line" and starts is not None:
        bounds = starts + [len(tokens)]
        proposed = [list(range(a, b)) for a, b in zip(bounds, bounds[1:])]
        merged, refused = captions.merge_cuts(new_tokens, words, proposed, rules)
        if merged != captions.line_chunks(new_tokens, words, rules):
            new_chunks = merged
        if refused and starts != [c[0] for c in chunks]:  # unchanged cuts its own text fix made wrong: recut quietly
            where = "" if new_chunks is None else f" in {refused} phrase{'s' if refused > 1 else ''}"
            notes.append(f"its line cuts broke the caption rules{where}, so the rule-based cuts were kept"
                         + (" there" if where else ""))
    if dropped:
        notes.append(f"{dropped} change(s) that replaced a word with a different one were ignored")
    result.update(tokens=new_tokens, chunks=new_chunks, changes=len(edits),
                  seconds=round(time.perf_counter() - t0, 2))
    if notes:
        result["message"] = f"Final AI check by {name}: " + "; ".join(notes) + "."
    return result

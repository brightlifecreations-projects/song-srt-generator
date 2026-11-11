"""Final AI check before output (DECISIONS.md V4.6; a full editorial pass since V4.8).

After the captions are built, and when an AI provider is set up on the AI & API keys page (with
"Final AI check" on, the default), the cue texts go to that provider once more, with their word
lists, the script (if any), the section, the language and the caption rules. The AI may only:

- change the text of a word (token): misheard or misspelled words, capitalisation and proper
  nouns, numbers as digits, punctuation (the clean-caption symbol rules are applied afterwards);
- move cut points between existing words, to break lines by meaning;
- mark words it isn't sure of ("unsure"), shown as [?] in the Review panel (never in the SRT).

It answers with word indices and corrected text plus where each caption starts — never a new
transcript — so it can't add or remove a spoken word, and the app rebuilds every time from the
word timestamps: timing can't drift. An answer that can't be read at all (not JSON, an unknown
index, a timeout…) keeps the rule-based captions and says so in one line; otherwise each change
is judged on its own, and only a change that breaks a rule (adds or drops a word, empties a
word, swaps in an unrelated word) is refused, with the reason in the job log. Without a provider,
captions.offline_cleanup() has already done the offline part.
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
        "unsure": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["tokens", "cue_starts", "unsure"], "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the editor doing the final pass on subtitles (SRT captions) before they \
are exported. The words were already timed to the audio; your job is the text and the line cuts.

You get every word ("tokens", each with an index i and its text t) and the current captions (as the \
index of the first token of each caption, "cue_starts"), plus context: the section, the language and \
script, the user's own script when there is one, the caption rules, and number_exceptions.

Do a full editorial pass:
1. Misheard or misspelled words: fix a word that is clearly a mishearing or misspelling of the right \
word in context (sound-alikes, names, brands, Hinglish spellings). When the user's script spells \
something a certain way, follow the script.
2. Capitalisation and proper nouns: sentence case, names and places capitalised, "I"; a word the user's \
script writes in CAPS for emphasis stays in caps.
3. Numbers as digits: the app has already turned most number words into digits (a two-word number \
is one token, "25"); write any it missed as digits in that token: "four" -> "4", "first" -> "1st", \
"seventy percent" -> "70%", Hinglish counts (ek, do, teen -> 1, 2, 3) when they are numbers (not "do" \
meaning "give"). A token stays one token, so a number spread over two tokens keeps its words. Words in \
number_exceptions stay words ("no one", "someone", "the one").
4. Clean-caption symbols: the app keeps only letters, digits, spaces, full stops, apostrophes inside \
words, % after a number and the colon of a time; it removes every other symbol itself after the line \
breaks are set. So never add quotation marks, dashes, brackets, slashes or emoji; you may keep or add \
a comma or full stop where a phrase really ends, because punctuation guides the line breaks.
5. Line breaks by meaning (cue_starts): aim for 4-5 words and at most {max_words} words and about \
{max_chars} characters per caption; keep phrases whole (prepositional phrases like "for her son", "in \
a size 4", a verb with its object, a subject with its verb); never end a caption on a weak word (the, \
a, an, to, of, and, but, just, is, my, ka, ki, ke, aur, toh…); one speaker per caption; a caption \
always ends after every token listed in "must_end_after" (a pause, the end of a script line, a speaker \
change or phrase-ending punctuation), so never put one of those inside a caption. {mode_note}
6. Anything you are unsure of (a word that may be misheard, a name you can't confirm): list its \
token index in "unsure"; the user sees it marked [?]. Don't guess wildly: mark it instead.

You must NEVER add, remove, merge, split or reorder spoken words. Each token stays one token: a \
token's new text has exactly as many words (space-separated parts) as before, and is never empty. \
Don't rephrase or translate.

Answer with JSON only: {{"tokens": [{{"i": 3, "t": "corrected"}}, ...], "cue_starts": [0, 5, 9, ...], \
"unsure": [12, ...]}}. List only the tokens you changed. cue_starts must start with 0 and cover every \
caption, in order. "unsure" may be empty."""

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
                  language: str | None, script: str | None, script_text: str | None, rules: dict,
                  exceptions: list[str] | None = None) -> str:
    payload = {
        "section": {"song": "song lyrics", "drama": "drama dialogue",
                    "reels": "reel / YouTube speech"}.get(section, section),
        "language": language,
        "script": ai_polish.SCRIPT_DESCRIPTIONS.get(script or "same", script),
        "caption_mode": "word by word" if rules["mode"] == "word" else "line by line",
        "rules": {"max_words": rules["max_words"], "max_chars": rules["max_chars"]},
        "users_script": script_text or None,
        "number_exceptions": exceptions if exceptions is not None else captions.DEFAULT_EXCEPTIONS,
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
    """A spelling, case, punctuation, number or sound-alike fix — not a different word."""
    a, b = _letters(original), _letters(new)
    if a == b or not a or not b:
        return bool(b) or not a
    if any(ch.isdigit() for ch in a + b):
        return True  # "five" -> "5", "first" -> "1st", "seven" -> "7:30"
    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.5:
        return True
    import hinglish  # sounds alike, spelt differently: "Cindy" -> "Sydney", "rasta" -> "raasta"
    return hinglish.similarity(a, b) >= 0.6 and len(hinglish.skeleton(a)) >= 3


def validate(answer: str, tokens: list[dict]) -> tuple[dict[int, str], list[int] | None, list[dict], list[int]]:
    """The AI's answer -> ({token index: new text}, cue_starts or None, refused changes
    [{"i", "from", "to", "why"}], unsure token indices). Raises ReviewError when the answer can't be
    used at all; a single change that breaks a rule is refused on its own."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", (answer or "").strip())
    try:
        data = json.loads(body)
    except ValueError:
        raise ReviewError("invalid", "the AI's answer wasn't valid JSON")
    if not isinstance(data, dict) or not isinstance(data.get("tokens"), list):
        raise ReviewError("invalid", "the AI's answer didn't have the expected fields")
    if len(data["tokens"]) > len(tokens):
        raise ReviewError("mismatch", "the AI returned more words than there are")
    edits, refused = {}, []
    for item in data["tokens"]:
        if not isinstance(item, dict) or not isinstance(item.get("i"), int) or not isinstance(item.get("t"), str):
            raise ReviewError("invalid", "the AI's answer didn't have the expected fields")
        k, text = item["i"], " ".join(item["t"].split())
        if not 0 <= k < len(tokens) or k in edits:
            raise ReviewError("invalid", "the AI referred to a word that doesn't exist")
        original = tokens[k]["text"]
        if text == original:
            continue
        why = None
        if not text:
            why = "it emptied the word (the number of words would change)"
        elif len(text.split()) != len(original.split()):
            why = "it changed the number of words (a spoken word would be added or dropped)"
        elif not plausible(original, text):
            why = "it replaced the word with a different one, not a spelling or sound-alike fix"
        elif not captions.clean_caption(text):
            why = "only symbols would be left"
        if why:
            refused.append({"i": k, "from": original, "to": text, "why": why})
            continue
        edits[k] = text
    starts = data.get("cue_starts")
    if starts is not None:
        if not isinstance(starts, list) or not all(isinstance(x, int) for x in starts) or not starts \
                or starts[0] != 0 or starts != sorted(set(starts)) or starts[-1] >= len(tokens):
            starts = None
    unsure = data.get("unsure") or []
    unsure = sorted({x for x in unsure if isinstance(x, int) and 0 <= x < len(tokens)}) \
        if isinstance(unsure, list) else []
    return edits, starts, refused, unsure


def review(tokens: list[dict], words: list[dict], chunks: list[list[int]], settings: dict, *,
           section: str, language: str | None, script: str | None, script_text: str | None,
           rules: dict, timeout: float | None = None, adapter=None, exceptions: list[str] | None = None) -> dict:
    """Run the check. Returns {"tokens", "chunks" (None = rule-based cuts), "changes", "message",
    "seconds", "sent", "returned", "refused" (changes refused, with why), "unsure"}. Never raises:
    any problem keeps the rule-based result."""
    t0 = time.perf_counter()
    name = ai_polish.provider_name(settings["provider"])
    mode_note = WORD_MODE_NOTE if rules["mode"] == "word" else ""
    system = SYSTEM_PROMPT.format(max_words=rules["max_words"], max_chars=rules["max_chars"], mode_note=mode_note)
    request = build_request(tokens, chunks, words=words, section=section, language=language, script=script,
                            script_text=script_text, rules=rules, exceptions=exceptions)
    result = {"tokens": tokens, "chunks": None, "changes": 0, "message": None, "sent": request,
              "returned": None, "provider": name, "refused": [], "unsure": []}
    try:
        adapter = adapter or ai_polish.adapter_for(settings)
        adapter.timeout = timeout or config.AI_REVIEW_TIMEOUT_SECONDS
        answer = adapter.complete(system, request, schema=RESPONSE_SCHEMA)
        result["returned"] = answer
        edits, starts, rejected, unsure = validate(answer, tokens)
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
        t.pop("unsure", None)
        if k in unsure:
            t["unsure"] = True
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
    if rejected:
        kinds = sorted({r["why"].split(" (")[0] for r in rejected})
        notes.append(f"{len(rejected)} change(s) were refused and ignored ({'; '.join(kinds)}); "
                     "the job log lists each one")
    result.update(tokens=new_tokens, chunks=new_chunks, changes=len(edits), refused=rejected, unsure=unsure,
                  seconds=round(time.perf_counter() - t0, 2))
    if notes:
        result["message"] = f"Final AI check by {name}: " + "; ".join(notes) + "."
    return result

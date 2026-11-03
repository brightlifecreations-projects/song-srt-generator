"""AI polish: an optional, online second pass over a transcript draft (DECISIONS.md V4.3).

An owner-approved, opt-in exception to the offline rule. Off until a key is added. Only the
transcript text (plus the section, script, topic words, relevant corrections and the words the
offline pass doubted) is sent to the provider the user picks; audio and video never leave the
computer. The provider fixes misheard and misspelled words only, line for line; anything that
doesn't validate is thrown away and the offline draft stays.

- Providers: Anthropic (Claude), OpenAI, Google Gemini, OpenRouter, and Custom (any
  OpenAI-compatible server: DeepSeek, Groq, Mistral, a local Ollama or LM Studio, …).
- One adapter per API format (anthropic, openai, gemini) behind complete(system, user) → text.
- API keys live in the macOS Keychain / Windows Credential Manager (the `keyring` package), one
  per provider. Never in config.py, ai-settings.json, logs, cues.json or corrections.md.
"""

import difflib
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

import config
import corrections

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("SRT_DATA_DIR") or ROOT)
SETTINGS_FILE = "ai-settings.json"
KEYCHAIN_SERVICE = "SRT Generator AI polish"

PROVIDERS = {
    "anthropic": {"name": "Anthropic (Claude)", "format": "anthropic", "base_url": "https://api.anthropic.com",
                  "model": "claude-opus-5-5", "key_url": "https://console.anthropic.com/settings/keys"},
    "openai": {"name": "OpenAI", "format": "openai", "base_url": "https://api.openai.com/v1",
               "model": "gpt-5.4-mini", "key_url": "https://platform.openai.com/api-keys"},
    "gemini": {"name": "Google Gemini", "format": "gemini",
               "base_url": "https://generativelanguage.googleapis.com/v1beta",
               "model": "gemini-flash-latest", "key_url": "https://aistudio.google.com/apikey"},
    "openrouter": {"name": "OpenRouter", "format": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "model": "openrouter/auto", "key_url": "https://openrouter.ai/keys"},
    "custom": {"name": "Custom (OpenAI-compatible)", "format": "openai", "base_url": "",
               "model": "", "key_url": ""},
}
# Claude models that take the server-side refusal fallback ("default" picks the substitute model).
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")


class PolishError(Exception):
    """A one-line, user-safe reason the polish didn't happen. kind: auth, rate_limit, timeout,
    offline, invalid, mismatch, refusal, server, config."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Settings (ai-settings.json in the data folder) and keys (Keychain / Credential Manager)

def _settings_path() -> Path:
    return DATA_DIR / SETTINGS_FILE


def load_settings() -> dict:
    try:
        saved = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    provider = saved.get("provider") if saved.get("provider") in PROVIDERS else "anthropic"
    models = {code: (saved.get("models") or {}).get(code) or p["model"] for code, p in PROVIDERS.items()}
    return {"enabled": bool(saved.get("enabled")), "provider": provider, "models": models,
            "custom_base_url": saved.get("custom_base_url", ""),
            "anthropic_workspace_id": saved.get("anthropic_workspace_id", "")}


def save_settings(settings: dict):
    clean = {"enabled": bool(settings.get("enabled")), "provider": settings.get("provider", "anthropic"),
             "models": {k: str(v).strip() for k, v in (settings.get("models") or {}).items() if k in PROVIDERS},
             "custom_base_url": str(settings.get("custom_base_url", "")).strip(),
             # Only for Anthropic keys not scoped to a workspace; an ID, not a secret.
             "anthropic_workspace_id": str(settings.get("anthropic_workspace_id", "")).strip()}
    p = _settings_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    tmp.replace(p)


def _keyring():
    import keyring
    from keyring.backends import fail

    if isinstance(keyring.get_keyring(), fail.Keyring):
        raise PolishError("config", "This computer has no secure key store, so API keys can't be saved.")
    return keyring


def get_key(provider: str) -> str | None:
    try:
        return _keyring().get_password(KEYCHAIN_SERVICE, provider)
    except PolishError:
        return None
    except Exception:
        return None


def set_key(provider: str, key: str):
    """Store (or, with an empty key, delete) a provider's API key in the system key store."""
    kr = _keyring()
    key = (key or "").strip()
    if key:
        kr.set_password(KEYCHAIN_SERVICE, provider, key)
    else:
        try:
            kr.delete_password(KEYCHAIN_SERVICE, provider)
        except Exception:
            pass


def _local(url: str) -> bool:
    return bool(re.match(r"https?://(localhost|127\.0\.0\.1|\[::1\])(:|/|$)", url or ""))


def usable(settings: dict | None = None) -> tuple[bool, str]:
    """Can polish run with these settings? (ok, why not). A local Custom server needs no key."""
    s = settings or load_settings()
    provider = s["provider"]
    if provider == "custom" and not s["custom_base_url"]:
        return False, "Custom needs a base URL"
    if not s["models"].get(provider):
        return False, "Choose a model name"
    if not get_key(provider) and not (provider == "custom" and _local(s["custom_base_url"])):
        return False, "Add an API key first"
    return True, ""


def active() -> dict | None:
    """The settings to polish with, or None when AI polish is off or can't run."""
    s = load_settings()
    if not s["enabled"] or not usable(s)[0]:
        return None
    return s


def public_state() -> dict:
    """Everything the settings panel shows. Never includes a key: only whether one is saved."""
    s = load_settings()
    ok, why = usable(s)
    try:
        _keyring()
        store = True
    except PolishError:
        store = False
    return {**s, "usable": ok, "why_not": why, "key_store": store,
            "providers": [{"code": c, "name": p["name"], "default_model": p["model"], "key_url": p["key_url"],
                           "has_key": bool(get_key(c))} for c, p in PROVIDERS.items()]}


def provider_name(code: str) -> str:
    return PROVIDERS.get(code, {}).get("name", code)


# ---------------------------------------------------------------------------
# Adapters: complete(system, user) → the model's text

def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _provider_message(body: str) -> str:
    """The provider's own error text, if its reply has one: {"error": {"message": …}} or similar."""
    try:
        data = json.loads(body)
    except ValueError:
        match = re.search(r"'message': '([^']+)'", body)  # an SDK error's repr
        return match.group(1) if match else ""
    err = data.get("error", data) if isinstance(data, dict) else {}
    message = err.get("message", "") if isinstance(err, dict) else str(err)
    return " ".join(str(message).split())[:220]


def _http_error(status: int, body: str) -> PolishError:
    low = body.lower()
    if status in (401, 403) or "api_key_invalid" in low or "invalid api key" in low or "incorrect api key" in low:
        return PolishError("auth", "The API key was refused — check it in the Corrections tab")
    if status == 429:
        return PolishError("rate_limit", "The provider is rate-limiting this key — try again in a minute")
    if status == 404:
        return PolishError("config", "The provider doesn't know that model name")
    if status >= 500:
        return PolishError("server", f"The provider had a problem (HTTP {status}) — try again later")
    detail = _provider_message(body)
    return PolishError("invalid", f"The provider rejected the request (HTTP {status}"
                                  + (f": {detail})" if detail else ")"))


class Adapter:
    def __init__(self, base_url: str, key: str | None, model: str, timeout: float):
        self.base_url, self.key, self.model, self.timeout = base_url.rstrip("/"), key, model, timeout

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "srt-generator", **headers})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_ssl_context()) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise _http_error(e.code, e.read().decode("utf-8", "replace")[:2000])
        except (TimeoutError, ssl.SSLError) as e:
            if isinstance(e, TimeoutError) or "timed out" in str(e):
                raise PolishError("timeout", f"The provider didn't answer within {self.timeout:.0f} seconds")
            raise PolishError("offline", "Couldn't make a secure connection to the provider")
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError) or "timed out" in str(e.reason):
                raise PolishError("timeout", f"The provider didn't answer within {self.timeout:.0f} seconds")
            raise PolishError("offline", "Couldn't reach the provider — no internet connection?")
        except ValueError:
            raise PolishError("invalid", "The provider's reply wasn't valid JSON")


class AnthropicAdapter(Adapter):
    """Claude, through the official anthropic SDK."""

    workspace_id = ""  # for keys not scoped to a workspace (anthropic-workspace-id header)

    def complete(self, system: str, user: str) -> str:
        import anthropic

        headers = {"anthropic-workspace-id": self.workspace_id} if self.workspace_id else None
        client = anthropic.Anthropic(api_key=self.key, base_url=self.base_url, timeout=self.timeout,
                                     max_retries=1, default_headers=headers)
        kwargs = {"model": self.model, "max_tokens": 16000, "system": system,
                  "messages": [{"role": "user", "content": user}],
                  "output_config": {"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}}}
        if self.model in FALLBACK_MODELS:  # a declined request is re-run on Anthropic's pick, server-side
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            try:
                response = client.beta.messages.create(**kwargs)
            except anthropic.BadRequestError as e:
                if "output_config" not in str(e) and "fallback" not in str(e):
                    raise
                # An older or unusual model without structured outputs or fallbacks: ask plainly.
                kwargs.pop("output_config")
                kwargs.pop("fallbacks", None)
                kwargs.pop("betas", None)
                response = client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise PolishError("auth", "The API key was refused — check it in the Corrections tab")
        except anthropic.PermissionDeniedError:
            raise PolishError("auth", "This API key isn't allowed to use that model")
        except anthropic.NotFoundError:
            raise PolishError("config", "Anthropic doesn't know that model name")
        except anthropic.RateLimitError:
            raise PolishError("rate_limit", "Anthropic is rate-limiting this key — try again in a minute")
        except anthropic.APITimeoutError:
            raise PolishError("timeout", f"Anthropic didn't answer within {self.timeout:.0f} seconds")
        except anthropic.APIConnectionError:
            raise PolishError("offline", "Couldn't reach Anthropic — no internet connection?")
        except anthropic.APIStatusError as e:
            raise _http_error(e.status_code, str(e))
        if response.stop_reason == "refusal":
            raise PolishError("refusal", "The model declined to polish this transcript")
        return "".join(b.text for b in response.content if b.type == "text")


class OpenAIAdapter(Adapter):
    """OpenAI's Chat Completions format: OpenAI, OpenRouter and every OpenAI-compatible server."""

    def complete(self, system: str, user: str) -> str:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        headers["X-Title"] = "SRT Generator"  # OpenRouter's app label; ignored elsewhere
        payload = {"model": self.model, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            data = self._post(f"{self.base_url}/chat/completions", payload, headers)
        except PolishError as e:
            if e.kind != "invalid":
                raise
            payload.pop("response_format")  # a server without JSON mode: ask plainly
            data = self._post(f"{self.base_url}/chat/completions", payload, headers)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise PolishError("invalid", "The provider's reply had no text in it")


class GeminiAdapter(Adapter):
    """Google's generateContent API. The key goes in a header, never the URL (URLs end up in errors)."""

    def complete(self, system: str, user: str) -> str:
        payload = {"systemInstruction": {"parts": [{"text": system}]},
                   "contents": [{"role": "user", "parts": [{"text": user}]}],
                   "generationConfig": {"responseMimeType": "application/json"}}
        data = self._post(f"{self.base_url}/models/{self.model}:generateContent", payload,
                          {"x-goog-api-key": self.key or ""})
        try:
            candidate = data["candidates"][0]
        except (KeyError, IndexError, TypeError):
            raise PolishError("refusal" if data.get("promptFeedback") else "invalid",
                              "The provider returned no answer for this transcript")
        return "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))


ADAPTERS = {"anthropic": AnthropicAdapter, "openai": OpenAIAdapter, "gemini": GeminiAdapter}


def adapter_for(settings: dict) -> Adapter:
    code = settings["provider"]
    p = PROVIDERS[code]
    base = settings["custom_base_url"] if code == "custom" else p["base_url"]
    adapter = ADAPTERS[p["format"]](base, get_key(code), settings["models"][code], config.AI_TIMEOUT_SECONDS)
    if code == "anthropic":
        adapter.workspace_id = settings.get("anthropic_workspace_id", "")
    return adapter


# ---------------------------------------------------------------------------
# The request

RESPONSE_SCHEMA = {"type": "object", "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
                   "required": ["lines"], "additionalProperties": False}

SYSTEM_PROMPT = """You proofread speech-to-text drafts that will become subtitles. A person approves your \
work afterwards, so be conservative.

You get the draft as numbered lines, plus context. Return the same lines with misheard or misspelled \
words fixed, and nothing else changed.

Rules:
1. Return exactly as many lines as you were given, in the same order: line N of your answer is line N \
of the draft, corrected. Never merge, split, drop or add lines.
2. Fix only words the speech recogniser got wrong (misheard words, misspellings, wrong word boundaries). \
Never add or remove content, and never rephrase, translate, summarise, reorder or "improve" anything. \
Keep fillers, repetitions, slang and the speaker's own grammar. Keep each line's punctuation unless a \
fix needs it changed.
3. Keep the draft's writing system ("script"). Hinglish means Hindi written in everyday Roman letters, \
with English words left in English.
4. Follow the house spellings and the user's corrections exactly; they are the user's own choices.
5. "doubtful_words" are where the offline recogniser was unsure or two recognisers disagreed. Look at \
them closely, but change them only when the context makes the right word clear. "topic_words" are \
names and terms the user says appear in this audio.
6. When you think a word is wrong but you aren't sure what it should be, write your best guess as \
[word?] (the word in square brackets with a question mark) so the user checks it. Use this sparingly.

Answer with JSON only, in this shape: {"lines": ["first line", "second line", ...]}"""

SCRIPT_DESCRIPTIONS = {
    "hinglish": "Hinglish (Hindi in everyday Roman letters, English words in English)",
    "devanagari": "Hindi in Devanagari script", "urdu": "Urdu script",
    "english": "English (a translation)", "same": "the spoken language's usual script",
}


def relevant_corrections(rows: dict, draft: str, limit: int = 120) -> list[dict]:
    """The user's corrections that matter for this draft: rows whose wrong or correct words appear
    in it first, then the most frequent ones, up to `limit`."""
    text = " " + " ".join(corrections.key_of(w) for w in draft.split()) + " "
    enabled = [r for r in rows.values() if not r["disabled"]]
    present = [r for r in enabled if f" {corrections.key_of(r['wrong'])} " in text
               or f" {corrections.key_of(r['correct'])} " in text]
    rest = sorted((r for r in enabled if r not in present), key=lambda r: -r["count"])
    return [{"wrong": r["wrong"], "correct": r["correct"]} for r in (present + rest)[:limit]]


def build_request(lines: list[str], *, section: str, script: str | None, language: str | None,
                  topic_words: list[str], correction_rows: dict, spellings: dict,
                  doubtful: list[dict]) -> str:
    payload = {
        "section": {"song": "song lyrics", "drama": "drama dialogue", "reels": "reel / YouTube speech"}.get(section, section),
        "script": SCRIPT_DESCRIPTIONS.get(script or "same", script),
        "language": language,
        "topic_words": topic_words,
        "house_spellings": [{"written": r["wrong"], "write_as": r["correct"]}
                            for r in spellings.values() if not r["disabled"]],
        "your_corrections": relevant_corrections(correction_rows, "\n".join(lines)),
        "doubtful_words": doubtful,
        "line_count": len(lines),
        "lines": [{"n": i + 1, "text": line} for i, line in enumerate(lines)],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# Validating the answer and finding what changed

UNSURE = re.compile(r"\[([^\[\]]+?)\?\]")


def parse_lines(text: str, expected: int) -> list[str]:
    body = text.strip()
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body)
    try:
        data = json.loads(body)
    except ValueError:
        raise PolishError("invalid", "The AI's answer wasn't valid JSON")
    lines = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(lines, list) or not all(isinstance(x, (str, dict)) for x in lines):
        raise PolishError("invalid", "The AI's answer didn't have the expected lines")
    lines = [x.get("text", "") if isinstance(x, dict) else x for x in lines]
    if len(lines) != expected:
        raise PolishError("mismatch", f"The AI returned {len(lines)} lines instead of {expected}")
    return [" ".join(str(x).split()) for x in lines]


def _plain(line: str) -> str:
    return UNSURE.sub(r"\1", line)


def line_ok(original: str, polished: str) -> bool:
    """A polished line is only accepted when it's still the same line: no words added or dropped
    beyond a word-boundary fix, and still mostly the same letters."""
    a, b = original.split(), _plain(polished).split()
    if abs(len(a) - len(b)) > max(1, len(a) // 5):
        return False
    # 0.7: real fixes scored 0.75–0.97 in testing (even a garbled line), a rephrased line 0.61.
    return difflib.SequenceMatcher(a=original.casefold(), b=_plain(polished).casefold()).ratio() >= 0.7


def changes(original_lines: list[str], polished_lines: list[str]) -> list[dict]:
    """Word-level changes, as highlight marks into the polished text: {"line", "start", "end",
    "replacement", "original", "kind": "ai" | "ai-unsure"}."""
    marks = []
    for n, (a_line, b_line) in enumerate(zip(original_lines, polished_lines)):
        if a_line == b_line:
            continue
        a_tok, b_tok = a_line.split(" "), b_line.split(" ")
        offsets, pos = [], 0
        for t in b_tok:
            offsets.append(pos)
            pos += len(t) + 1
        sm = difflib.SequenceMatcher(a=[corrections.key_of(_plain(t)) for t in a_tok],
                                     b=[corrections.key_of(_plain(t)) for t in b_tok], autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            unsure = [j for j in range(j1, j2) if UNSURE.search(b_tok[j])] if op == "equal" else []
            if op == "equal" and not unsure:
                continue
            if op == "equal":
                spans = [(j, j + 1, a_tok[i1 + (j - j1)]) for j in unsure]
            else:
                if j1 == j2:
                    continue  # a deletion has nothing left to highlight
                spans = [(j1, j2, " ".join(corrections._core(t)[1] for t in a_tok[i1:i2]))]
            for s, e, original in spans:
                first_lead = corrections._core(b_tok[s])[0]
                if "[" in first_lead and UNSURE.search(b_tok[s]):  # keep "[word?]" whole
                    first_lead = first_lead[:first_lead.index("[")]
                last = corrections._core(b_tok[e - 1])
                start = offsets[s] + len(first_lead)
                end = offsets[e - 1] + len(b_tok[e - 1]) - len(last[2])
                if b_tok[e - 1].endswith("?]"):
                    end = offsets[e - 1] + b_tok[e - 1].rindex("?]") + 2
                text = b_line[start:end]
                marks.append({"line": n, "start": start, "end": end, "replacement": text,
                              "original": original, "kind": "ai-unsure" if UNSURE.search(text) else "ai"})
    return marks


def polish(lines: list[str], settings: dict, **context) -> dict:
    """Send the draft, validate the answer. Returns {"lines", "marks", "skipped_lines",
    "provider", "model", "seconds"}; raises PolishError (the caller keeps the offline draft)."""
    t0 = time.perf_counter()
    if not any(line.strip() for line in lines):
        raise PolishError("invalid", "There's no text to polish")
    adapter = adapter_for(settings)
    text = adapter.complete(SYSTEM_PROMPT, build_request(lines, **context))
    polished = parse_lines(text, len(lines))
    kept, skipped = [], []
    for i, (a, b) in enumerate(zip(lines, polished)):
        if line_ok(a, b):
            kept.append(b)
        else:
            kept.append(a)
            skipped.append(i)
    return {"lines": kept, "marks": changes(lines, kept), "skipped_lines": skipped,
            "provider": settings["provider"], "model": settings["models"][settings["provider"]],
            "seconds": round(time.perf_counter() - t0, 2)}


def test_connection(settings: dict) -> dict:
    """A tiny real request ("Helo world" → one line back) to check the key, model and URL."""
    t0 = time.perf_counter()
    result = adapter_for(settings).complete(
        SYSTEM_PROMPT, build_request(["Helo world, this is a test."], section="reels", script="same",
                                     language="en", topic_words=[], correction_rows={}, spellings={},
                                     doubtful=[]))
    lines = parse_lines(result, 1)
    return {"ok": True, "reply": lines[0], "seconds": round(time.perf_counter() - t0, 2)}

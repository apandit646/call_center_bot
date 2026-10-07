"""The languages a support call can be held in, and how a call follows the caller's language.

A call starts in the company's language and follows the caller - but only into a language the
company has switched on, and only when we are sure. Speech recognition sometimes hears a short
Hindi sentence as another language, so one odd sentence never flips the call:

    English -> Hindi            at once (Hindi script is unmistakable, and it is the common case)
    the caller's first sentence at once, if it is a real sentence
    anything else               only after two sentences in a row in the new language
    "please speak in Tamil"     at once

English and Hindi lines are written by hand (support_brain.VOICE_REPLIES, voice_support.LINES).
Lines for the other languages are translated from the English ones the first time a call needs
them and kept in Redis, so that happens once per language, not once per call.
"""
import asyncio
import hashlib
import json
import logging
import re
from typing import Any

from app.core.redis import get_redis

log = logging.getLogger(__name__)

# Language key -> name. Keys are the first part of the speech codes ("ta" <-> "ta-IN").
NAMES = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam",
    "mr": "Marathi", "od": "Odia", "pa": "Punjabi", "ta": "Tamil", "te": "Telugu",
}
CODES = {f"{key}-IN" for key in NAMES}
DEFAULT_CODES = ["en-IN", "hi-IN"]
HANDWRITTEN = ("en", "hi")

# Which script a sentence is written in tells us its language (Marathi shares Hindi's script).
_SCRIPTS = [
    ("hi", re.compile(r"[ऀ-ॿ]")), ("bn", re.compile(r"[ঀ-৿]")), ("pa", re.compile(r"[਀-੿]")),
    ("gu", re.compile(r"[઀-૿]")), ("od", re.compile(r"[଀-୿]")), ("ta", re.compile(r"[஀-௿]")),
    ("te", re.compile(r"[ఀ-౿]")), ("kn", re.compile(r"[ಀ-೿]")), ("ml", re.compile(r"[ഀ-ൿ]")),
]
_LATIN = re.compile(r"[A-Za-z]")
_LATIN_WORD = re.compile(r"[A-Za-z]+")

# "English mein boliye" / "please speak in Tamil" / "हिंदी में बताइए"
_SPOKEN_NAMES = {
    "en": r"english|angrezi|अंग्रेज़ी|अंग्रेजी|इंग्लिश", "hi": r"hindi|हिंदी|हिन्दी", "bn": r"bengali|bangla|বাংলা",
    "gu": r"gujarati|ગુજરાતી", "kn": r"kannada|ಕನ್ನಡ", "ml": r"malayalam|മലയാളം", "mr": r"marathi|मराठी",
    "od": r"odia|oriya|ଓଡ଼ିଆ", "pa": r"punjabi|panjabi|ਪੰਜਾਬੀ", "ta": r"tamil|தமிழ்", "te": r"telugu|తెలుగు",
}
_REQUEST = {
    key: re.compile(
        rf"\b(?:in|into)\s+(?:{names})\b|(?:{names})\s*(?:mein|me|में|मे)(?![\wऀ-ॿ])|\b(?:speak|talk)\s+(?:{names})\b"
        rf"|(?:{names})\s+(?:please|bol|bolo|boliye|बोल)", re.I)
    for key, names in _SPOKEN_NAMES.items()
}


def key_of(code: str | None) -> str:
    """ "ta-IN" -> "ta". Anything we do not speak -> "en"."""
    key = (code or "en").split("-")[0].lower()
    return key if key in NAMES else "en"


def code_of(key: str) -> str:
    return f"{key}-IN"


def allowed_codes(company: Any) -> list[str]:
    """The languages this company's calls may be held in: what it switched on, plus the one calls start in."""
    chosen = [c for c in (getattr(company, "inbound_languages", None) or DEFAULT_CODES) if c in CODES]
    start = company.inbound_language if company.inbound_language in CODES else "en-IN"
    return list(dict.fromkeys([start, *chosen]))


def recognition_language(company: Any) -> str:
    """What to tell speech-to-text. Hindi mode also returns English words in English, which is what an
    English + Hindi company needs; with more languages on, the recogniser has to detect the language."""
    return "hi-IN" if set(allowed_codes(company)) <= {"en-IN", "hi-IN"} else "unknown"


def spoken_in(text: str, detected: str | None = None) -> str | None:
    """The language key a sentence is in, judged by its script. None = cannot tell (no letters)."""
    counts = {key: len(pattern.findall(text)) for key, pattern in _SCRIPTS}
    key, most = max(counts.items(), key=lambda kv: kv[1])
    latin = len(_LATIN.findall(text))
    if most == 0:
        return "en" if latin else None
    if most < latin / 3:  # a mostly English sentence with a native word or two
        return "en"
    if key == "hi" and key_of(detected) == "mr":
        return "mr"
    return key


def requested(text: str) -> str | None:
    """The language the caller explicitly asked to be spoken to in, if any."""
    return next((key for key, pattern in _REQUEST.items() if pattern.search(text)), None)


def next_language(current: str, speech: str, detected: str | None, allowed: list[str], state: dict[str, Any]) -> str:
    """The language code the call continues in after what the caller just said.

    state: the call's own state; remembers a language heard once ("lang_pending") until it is heard again."""
    asked = requested(speech)
    if asked and code_of(asked) in allowed:
        state.pop("lang_pending", None)
        return code_of(asked)

    heard = spoken_in(speech, detected)
    if heard is None or code_of(heard) not in allowed or code_of(heard) == current:
        state.pop("lang_pending", None)
        return current

    words = len(speech.split())
    if heard == "en":
        # Speakers of every Indian language say "yes", "ok", "payment nahi hua": only a real English
        # sentence, with nothing else in it, counts towards going back to English.
        if any(pattern.search(speech) for _, pattern in _SCRIPTS) or len(_LATIN_WORD.findall(speech)) < 4:
            return current
        sure = False
    else:
        first_sentence = state.get("turn", 0) == 0 and words >= 3
        sure = first_sentence or (heard == "hi" and key_of(current) == "en")
    if sure or state.get("lang_pending") == heard:
        state.pop("lang_pending", None)
        return code_of(heard)
    state["lang_pending"] = heard
    return current


# --------------------------------------------------------------------------- spoken lines per language
_translated: dict[tuple[str, str], dict[str, str]] = {}


def lines(name: str, key: str, written: dict[str, dict[str, str]]) -> dict[str, str]:
    """The table of fixed spoken lines `name` in language `key` (English until a translation is ready)."""
    return written.get(key) or _translated.get((name, key)) or written["en"]


def _version(english: dict[str, str]) -> str:
    return hashlib.sha1(json.dumps(english, sort_keys=True).encode()).hexdigest()[:10]


async def ensure(key: str, tables: dict[str, dict[str, dict[str, str]]]) -> None:
    """Make sure the fixed lines exist in language `key` before a call speaks it. Never raises:
    a line that cannot be translated is spoken in English."""
    if key in HANDWRITTEN or key not in NAMES:
        return
    for name, written in tables.items():
        if (name, key) in _translated or key in written:
            continue
        english = written["en"]
        cache_key = f"call_lines:{name}:{key}:{_version(english)}"
        try:
            cached = await get_redis().hgetall(cache_key)
        except Exception:
            cached = {}
        cached = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in cached.items()}
        if set(english) <= set(cached):
            _translated[(name, key)] = {k: cached[k] for k in english}
            continue
        _translated[(name, key)] = table = await _translate(english, key)
        if any(table[k] != english[k] for k in english):  # something was translated: worth keeping
            try:
                await get_redis().hset(cache_key, mapping=table)
            except Exception:
                log.warning("Could not cache the %s call lines", NAMES[key])


async def _translate(english: dict[str, str], key: str) -> dict[str, str]:
    from app.services.campaign_translation import translate_text  # imports speech + Gemini clients

    async def one(text: str) -> str:
        try:
            return await translate_text(text, "en-IN", code_of(key)) or text
        except Exception:
            return text

    translated = await asyncio.gather(*(one(text) for text in english.values()))
    done = sum(1 for a, b in zip(translated, english.values(), strict=True) if a != b)
    log.info("Call lines translated to %s: %d of %d", NAMES[key], done, len(english))
    return dict(zip(english.keys(), translated, strict=True))

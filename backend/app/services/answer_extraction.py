"""Turn a spoken answer into raw + structured data for analytics."""
import re
from typing import Any

from app.models import CampaignQuestion
from app.models.enums import AnswerType
from app.services.gemini import get_gemini

SYSTEM = """You extract structured answers from a customer's spoken reply on a phone survey in India.
The question and the reply may be in English, Hindi (Devanagari) or Hinglish (mixed); understand all of them.
Return:
- answered: false if the reply does not answer the question (silence, off-topic, "can you repeat").
- needs_repeat: true if the customer asked to repeat or did not understand the question.
- normalized_answer: an object that ALWAYS has a "value" key, typed by answer_type:
    yes_no -> true/false; rating -> integer on the requested scale (default 1-5); number -> number;
    choice -> exactly one of the provided options; text -> short clean summary string IN ENGLISH
    (so analytics are comparable across languages).
  You may add extra well-named keys for other facts in the reply (e.g. "duration_months": 6).
- confidence: 0..1 how sure you are about normalized_answer.
- intent: what the customer did:
    answer   - answered the question (even partly or reluctantly)
    repeat   - asked to repeat / didn't understand
    question - asked something back (about the company, the call, a product, anything)
    wait     - asked to hold on
    busy     - can't talk now / asked to be called later
    stop     - doesn't want to be called / not interested
    other    - anything else (small talk, unrelated statement)
- reply: ONLY for intent question/other: what the agent should say before re-asking, max 25 words, warm and
  natural, in the reply language given. Answer briefly if it is general and harmless, or if the "known facts"
  given answer it (e.g. which company is calling, what the product is). Never invent company facts, prices,
  dates or policies that are not in the known facts - say the team will help with that.
  Empty string for other intents."""

SCHEMA = {
    "type": "object",
    "properties": {
        "answered": {"type": "boolean"},
        "needs_repeat": {"type": "boolean"},
        "normalized_answer": {"type": "object"},
        "confidence": {"type": "number"},
        "intent": {"type": "string", "enum": ["answer", "repeat", "question", "wait", "busy", "stop", "other"]},
        "reply": {"type": "string"},
    },
    "required": ["answered", "needs_repeat", "normalized_answer", "confidence", "intent", "reply"],
}

# Offline fallback vocabulary (English, Hindi, romanised Hindi). Matched on whole tokens because
# regex word boundaries don't work inside Devanagari words (vowel signs aren't "word" characters).
_YES = {"yes", "yeah", "yep", "sure", "definitely", "correct", "haan", "han", "ha", "ji", "bilkul",
        "हाँ", "हां", "हा", "जी", "बिल्कुल", "ज़रूर", "जरूर", "यस", "येस"}
_NO = {"no", "nope", "never", "nahi", "nahin", "nai", "na", "नहीं", "नही", "ना", "मत", "नो"}
_REPEAT = {"repeat", "pardon", "again", "dobara", "phir", "दोबारा", "फिर"}
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
}


# Devanagari letters and vowel signs, but not the danda punctuation (। ॥): "नहीं।" must tokenise to "नहीं".
_TOKEN_RE = re.compile(r"[\w\u0900-\u0963\u0966-\u097F]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


PENDING = "_pending_enrichment"  # marks answers whose Gemini clean-up runs after the call


def answer_leads(question: CampaignQuestion, text: str) -> bool:
    """Does the reply *start* with the answer? ("haan, baad mein call karna" -> yes it does;
    "I'm busy, call me later" / "give me 2 minutes" -> no)."""
    tokens = _tokens(text)
    if not tokens:
        return False
    first = tokens[0]
    if question.answer_type == AnswerType.YES_NO:
        return first in _YES or first in _NO
    if question.answer_type in (AnswerType.RATING, AnswerType.NUMBER):
        return first.isdigit() or first in _WORD_NUMBERS
    if question.answer_type == AnswerType.CHOICE:
        return any(text.strip().lower().startswith(o.lower()) for o in question.options or [])
    return False


def extract_answer_live(question: CampaignQuestion, raw_answer: str) -> dict[str, Any] | None:
    """Instant, no-network answer handling used mid-call; None means "ask Gemini now".

    Every second of processing is dead air on the phone, so clear-cut answers (a yes/no word, a
    number, a listed option) are taken as-is, and free-text answers are saved raw and summarised by
    Gemini after the call (see call_service.enrich_answers). Only ambiguous structured answers
    still go to Gemini during the call.
    """
    quick = _heuristic(question, raw_answer)
    if quick["needs_repeat"]:
        return quick
    if question.answer_type == AnswerType.TEXT:
        return {"answered": True, "needs_repeat": False, "confidence": None, "intent": "answer", "reply": "",
                "normalized_answer": {"value": raw_answer.strip(), PENDING: True}}
    if quick["answered"] and quick["confidence"] >= 0.7:
        return quick
    return None


REPLY_LANGUAGE = {
    "en": "English",
    "hi": "Hinglish - Hindi in Devanagari script, keeping everyday English words (product, service, team) in English",
}


async def extract_answer(
    question: CampaignQuestion, raw_answer: str, reply_language: str = "en-IN", facts: str | None = None
) -> dict[str, Any]:
    """facts: what the agent may state about the company / product if the customer asks (see voice_qa.call_facts)."""
    prompt = (
        f"Question: {question.text}\n"
        f"answer_type: {question.answer_type}\n"
        f"options: {question.options or 'n/a'}\n"
        f"hint: {question.extraction_hint or 'n/a'}\n"
        f"reply language: {REPLY_LANGUAGE.get(reply_language.split('-')[0], 'English')}\n"
        f"known facts:\n{facts or 'n/a'}\n\n"
        f"Customer said: \"{raw_answer}\""
    )
    result = await get_gemini().generate_json(SYSTEM, prompt, _heuristic(question, raw_answer), SCHEMA)
    if result.get("intent") not in {"answer", "repeat", "question", "wait", "busy", "stop", "other"}:
        result["intent"] = "repeat" if result.get("needs_repeat") else "answer"
    result["reply"] = (result.get("reply") or "").strip()[:300]
    normalized = result.get("normalized_answer")
    if not isinstance(normalized, dict) or "value" not in normalized:
        result["normalized_answer"] = {"value": normalized if normalized is not None else raw_answer}
    result["confidence"] = max(0.0, min(1.0, float(result.get("confidence") or 0)))
    return result


def _heuristic(question: CampaignQuestion, text: str) -> dict[str, Any]:
    """Offline fallback, deliberately simple."""
    tokens = _tokens(text)
    if _REPEAT & set(tokens) and len(tokens) <= 6:
        return {"answered": False, "needs_repeat": True, "normalized_answer": {"value": None}, "confidence": 0.0,
                "intent": "repeat", "reply": ""}
    value: Any = text.strip()
    confidence = 0.5
    if question.answer_type == AnswerType.YES_NO:
        # First yes/no word wins: "haan, nahi to problem nahi hai" is a yes.
        first = next((t for t in tokens if t in _YES or t in _NO), None)
        value = None if first is None else first in _YES
        confidence = 0.0 if first is None else 0.7
    elif question.answer_type in (AnswerType.RATING, AnswerType.NUMBER):
        m = re.search(r"-?\d+(\.\d+)?", text)
        word = next((_WORD_NUMBERS[t] for t in tokens if t in _WORD_NUMBERS), None)
        value = float(m.group()) if m else word
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        confidence = 0.7 if value is not None else 0.0
    elif question.answer_type == AnswerType.CHOICE:
        value = next((o for o in question.options or [] if o.lower() in text.lower()), None)
        confidence = 0.7 if value else 0.0
    answered = value not in (None, "")
    return {"answered": answered, "needs_repeat": False, "normalized_answer": {"value": value}, "confidence": confidence,
            "intent": "answer", "reply": ""}

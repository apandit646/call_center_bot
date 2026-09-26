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
- confidence: 0..1 how sure you are about normalized_answer."""

SCHEMA = {
    "type": "object",
    "properties": {
        "answered": {"type": "boolean"},
        "needs_repeat": {"type": "boolean"},
        "normalized_answer": {"type": "object"},
        "confidence": {"type": "number"},
    },
    "required": ["answered", "needs_repeat", "normalized_answer", "confidence"],
}

# Offline fallback vocabulary (English, Hindi, romanised Hindi). Matched on whole tokens because
# regex word boundaries don't work inside Devanagari words (vowel signs aren't "word" characters).
_YES = {"yes", "yeah", "yep", "sure", "definitely", "correct", "right", "haan", "han", "ha", "ji", "bilkul",
        "हाँ", "हां", "हा", "जी", "बिल्कुल", "ज़रूर", "जरूर"}
_NO = {"no", "nope", "never", "nahi", "nahin", "nai", "na", "नहीं", "नही", "ना", "मत"}
_REPEAT = {"repeat", "pardon", "again", "dobara", "phir", "दोबारा", "फिर"}
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\wऀ-ॿ]+", text.lower())  # ऀ-ॿ = Devanagari incl. vowel signs


async def extract_answer(question: CampaignQuestion, raw_answer: str) -> dict[str, Any]:
    prompt = (
        f"Question: {question.text}\n"
        f"answer_type: {question.answer_type}\n"
        f"options: {question.options or 'n/a'}\n"
        f"hint: {question.extraction_hint or 'n/a'}\n\n"
        f"Customer said: \"{raw_answer}\""
    )
    result = await get_gemini().generate_json(SYSTEM, prompt, _heuristic(question, raw_answer), SCHEMA)
    normalized = result.get("normalized_answer")
    if not isinstance(normalized, dict) or "value" not in normalized:
        result["normalized_answer"] = {"value": normalized if normalized is not None else raw_answer}
    result["confidence"] = max(0.0, min(1.0, float(result.get("confidence") or 0)))
    return result


def _heuristic(question: CampaignQuestion, text: str) -> dict[str, Any]:
    """Offline fallback, deliberately simple."""
    tokens = _tokens(text)
    if _REPEAT & set(tokens) and len(tokens) <= 6:
        return {"answered": False, "needs_repeat": True, "normalized_answer": {"value": None}, "confidence": 0.0}
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
    return {"answered": answered, "needs_repeat": False, "normalized_answer": {"value": value}, "confidence": confidence}

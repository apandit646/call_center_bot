"""Provider-agnostic Q&A state machine for one outbound call.

    greeting -> Q1 -> customer speaks -> extract & save -> Q2 -> ... -> closing -> hang up

Telephony webhooks call `start` once and `handle_answer` after each customer turn; the returned
VoiceAction is rendered into provider markup (e.g. TwiML) by the telephony provider.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.models import Call, CallAnswer, CallTranscript, Campaign, CampaignContact, CampaignQuestion
from app.models.enums import AnswerStatus, CallStatus, Speaker
from app.services.answer_extraction import extract_answer

log = logging.getLogger(__name__)
_STATE_TTL = 6 * 3600


@dataclass
class VoiceAction:
    say: list[str] = field(default_factory=list)
    gather_question_index: int | None = None  # listen for an answer to this question
    hangup: bool = False
    language: str = "en-IN"


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


# System prompts kept as separate utterances (not glued to the question) so each one is
# synthesised once and cached. Keyed by language prefix; unknown languages use English.
PHRASES = {
    "en": {
        "not_heard": "Sorry, I didn't catch that.",
        "repeat": "Sure, here is the question again.",
        "clarify": "Sorry, I didn't quite get that. Let me ask again.",
    },
    # Hinglish, the way people actually speak on calls; Bulbul reads the English words naturally.
    "hi": {
        "not_heard": "Sorry, आपकी आवाज़ clear नहीं आई।",
        "repeat": "ज़रूर, question फिर से सुनिए।",
        "clarify": "Sorry, answer समझ नहीं आया। Question फिर से सुनिए।",
    },
}


def phrase(campaign: Campaign, name: str) -> str:
    return PHRASES.get(campaign.language.split("-")[0], PHRASES["en"])[name]


def greeting_text(campaign: Campaign, contact: CampaignContact | None, customer_name: str | None) -> str:
    variables = _SafeDict({**(contact.variables if contact else {}), "name": customer_name or "there"})
    return campaign.greeting_script.format_map(variables).strip()


def closing_text(campaign: Campaign, customer_name: str | None = None) -> str:
    return campaign.closing_script.format_map(_SafeDict(name=customer_name or "")).strip()


def all_utterances(campaign: Campaign, contact: CampaignContact | None, customer_name: str | None) -> list[str]:
    """Everything a call for this contact might say - used to pre-generate TTS audio."""
    fixed = PHRASES.get(campaign.language.split("-")[0], PHRASES["en"]).values()
    return [greeting_text(campaign, contact, customer_name), *(q.text for q in campaign.questions),
            closing_text(campaign, customer_name), *fixed]


async def start(db: AsyncSession, call: Call) -> VoiceAction:
    campaign = await db.get(Campaign, call.campaign_id)
    contact = await db.get(CampaignContact, call.campaign_contact_id)
    now = datetime.now(UTC)
    call.status = CallStatus.IN_PROGRESS
    call.answered_at = call.answered_at or now
    call.current_question_index = 0
    await _set_reprompts(call, 0)

    greeting = greeting_text(campaign, contact, call.customer.name)
    questions = campaign.questions
    if not questions:
        closing = closing_text(campaign, call.customer.name)
        await _log_ai(db, call, [greeting, closing])
        await db.commit()
        return VoiceAction(say=[greeting, closing], hangup=True, language=campaign.language)

    await _log_ai(db, call, [greeting])
    await _log_ai(db, call, [questions[0].text], questions[0])
    await db.commit()
    return VoiceAction(say=[greeting, questions[0].text], gather_question_index=0, language=campaign.language)


async def handle_answer(db: AsyncSession, call: Call, question_index: int, speech: str) -> VoiceAction:
    campaign = await db.get(Campaign, call.campaign_id)
    questions = campaign.questions
    lang = campaign.language

    if call.current_question_index >= len(questions):
        return VoiceAction(say=[closing_text(campaign, call.customer.name)], hangup=True, language=lang)
    if question_index != call.current_question_index:
        # Duplicate / out-of-order webhook: re-ask the current question without changing state.
        q = questions[call.current_question_index]
        return VoiceAction(say=[q.text], gather_question_index=call.current_question_index, language=lang)

    question = questions[question_index]
    speech = (speech or "").strip()
    reprompts = await _get_reprompts(call)

    if not speech:
        if reprompts < settings.VOICE_MAX_REPROMPTS:
            await _set_reprompts(call, reprompts + 1)
            say = [phrase(campaign, "not_heard"), question.text]
            await _log_ai(db, call, say, question)
            await db.commit()
            return VoiceAction(say=say, gather_question_index=question_index, language=lang)
        await _save_answer(db, call, question, None, {"value": None}, None, AnswerStatus.SKIPPED)
        return await _advance(db, call, campaign)

    await _log(db, call, Speaker.CUSTOMER, speech, question)
    result = await extract_answer(question, speech)
    if (not result.get("answered") or result.get("needs_repeat")) and reprompts < settings.VOICE_MAX_REPROMPTS:
        await _set_reprompts(call, reprompts + 1)
        say = [phrase(campaign, "repeat" if result.get("needs_repeat") else "clarify"), question.text]
        await _log_ai(db, call, say, question)
        await db.commit()
        return VoiceAction(say=say, gather_question_index=question_index, language=lang)

    status = AnswerStatus.ANSWERED if result.get("answered") else AnswerStatus.UNCLEAR
    await _save_answer(db, call, question, speech, result["normalized_answer"], result["confidence"], status)
    return await _advance(db, call, campaign)


async def _advance(db: AsyncSession, call: Call, campaign: Campaign) -> VoiceAction:
    questions = campaign.questions
    call.current_question_index += 1
    await _set_reprompts(call, 0)
    idx = call.current_question_index
    if idx < len(questions):
        await _log_ai(db, call, [questions[idx].text], questions[idx])
        await db.commit()
        return VoiceAction(say=[questions[idx].text], gather_question_index=idx, language=campaign.language)

    answered = set(
        (
            await db.scalars(
                select(CallAnswer.question_id).where(
                    CallAnswer.call_id == call.id, CallAnswer.status == AnswerStatus.ANSWERED
                )
            )
        ).all()
    )
    call.all_questions_answered = all(q.id in answered for q in questions if q.is_required)
    closing = closing_text(campaign, call.customer.name)
    await _log_ai(db, call, [closing])
    await db.commit()
    return VoiceAction(say=[closing], hangup=True, language=campaign.language)


async def _save_answer(
    db: AsyncSession,
    call: Call,
    question: CampaignQuestion,
    raw: str | None,
    normalized: dict,
    confidence: float | None,
    status: str,
) -> None:
    values = {
        "raw_answer": raw,
        "normalized_answer": normalized,
        "confidence": confidence,
        "status": status,
        "updated_at": datetime.now(UTC),
    }
    stmt = insert(CallAnswer).values(
        call_id=call.id, question_id=question.id, campaign_id=call.campaign_id, customer_id=call.customer_id, **values
    )
    await db.execute(stmt.on_conflict_do_update(index_elements=["call_id", "question_id"], set_=values))


async def _log(db: AsyncSession, call: Call, speaker: str, text: str, question: CampaignQuestion | None = None) -> None:
    turn = await db.scalar(select(func.count(CallTranscript.id)).where(CallTranscript.call_id == call.id)) or 0
    db.add(CallTranscript(call_id=call.id, turn_index=turn, speaker=speaker, text=text,
                          question_id=question.id if question else None))
    await db.flush()


async def _log_ai(db: AsyncSession, call: Call, texts: list[str], question: CampaignQuestion | None = None) -> None:
    for text in texts:
        await _log(db, call, Speaker.AI, text, question)


# Reprompt counters live in Redis (call:{id}) - temporary per-call state.
async def _get_reprompts(call: Call) -> int:
    raw = await get_redis().get(f"call:{call.id}")
    return int(json.loads(raw).get("reprompts", 0)) if raw else 0


async def _set_reprompts(call: Call, value: int) -> None:
    await get_redis().set(f"call:{call.id}", json.dumps({"reprompts": value}), ex=_STATE_TTL)

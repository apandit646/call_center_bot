"""Provider-agnostic Q&A state machine for one outbound call.

    greeting -> Q1 -> customer speaks -> understand -> acknowledge -> Q2 -> ... -> closing -> hang up

Telephony webhooks call `start` once and `handle_answer` after each customer turn; the returned
VoiceAction is rendered into provider markup (e.g. TwiML) by the telephony provider.

Human touches (voice_persona): short acknowledgements between questions, empathy on low ratings,
"one last question", and handling of off-script turns - who is this / are you a robot / hold on /
call me later / don't call me / any other question (a short Gemini reply, then back to the survey).

Company: the campaign's company is loaded with it, so the greeting can say {company} and off-script
questions ("who is this?", "which product?") are answered from the company profile and the
campaign's product/service details (see call_facts).

Language: every call starts in the campaign's primary language (e.g. English). After each answer
the call follows the customer - reply in Hindi and every later line is spoken in Hindi
(see choose_language).
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.models import Call, CallAnswer, CallTranscript, Campaign, CampaignContact, CampaignQuestion
from app.models.enums import AnswerStatus, AnswerType, CallStatus, ContactStatus, Speaker
from app.services import voice_persona
from app.services.company_service import profile_text
from app.services.answer_extraction import answer_leads, extract_answer, extract_answer_live
from app.services.voice_persona import phrase

log = logging.getLogger(__name__)
_STATE_TTL = 6 * 3600
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN_WORD = re.compile(r"[A-Za-z]+")
# Switching back to the primary language needs a real sentence: Hindi speakers say "yes"/"ok" too.
_MIN_WORDS_TO_SWITCH_BACK = 3
# Off-script turns handled per question before gently moving on (stops endless loops).
_MAX_DETOURS_PER_QUESTION = 2


@dataclass
class VoiceAction:
    say: list[str] = field(default_factory=list)
    gather_question_index: int | None = None  # listen for an answer to this question
    hangup: bool = False
    language: str = "en-IN"


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


# --------------------------------------------------------------------------- language
def call_languages(campaign: Campaign) -> list[str]:
    return list(dict.fromkeys([campaign.language, *(campaign.alt_languages or [])]))


def recognition_language(campaign: Campaign, current: str) -> str:
    """Language to ask the recogniser for.

    Open auto-detection over all Indian languages misfires on short answers ("achha" came back as
    Bengali, "haan" as Tamil), so Hindi+English campaigns always use Hindi in code-mix mode: Hindi
    comes back in Devanagari and English words stay in Latin script, and the script tells us which
    language the customer spoke.
    """
    return "hi-IN" if "hi-IN" in call_languages(campaign) else current


def choose_language(campaign: Campaign, current: str, speech: str, detected: str | None) -> str:
    """Which language to continue the call in, given what the customer just said."""
    allowed = call_languages(campaign)
    words = len(speech.split())
    if "hi-IN" in allowed and _DEVANAGARI.search(speech):
        return "hi-IN"  # Hindi words come back in Devanagari from Sarvam (codemix mode)
    if "en-IN" in allowed and current != "en-IN" and _LATIN_WORD.search(speech) and not _DEVANAGARI.search(speech):
        # All-English answer. A lone "yes"/"ok" is common in Hindi speech too, so need a real sentence.
        return "en-IN" if words >= _MIN_WORDS_TO_SWITCH_BACK else current
    if not detected or detected not in allowed or detected == current:
        return current
    if detected == campaign.language and words < _MIN_WORDS_TO_SWITCH_BACK:
        return current
    return detected


# --------------------------------------------------------------------------- script text
def question_text(campaign: Campaign, question: CampaignQuestion, lang: str) -> str:
    if lang == campaign.language:
        return question.text
    return (question.translations or {}).get(lang) or question.text


def _script(campaign: Campaign, lang: str, field_name: str) -> str:
    primary = campaign.greeting_script if field_name == "greeting" else campaign.closing_script
    if lang == campaign.language:
        return primary
    return ((campaign.translations or {}).get(lang) or {}).get(field_name) or primary


def company_name(campaign: Campaign) -> str:
    return campaign.company.name if campaign.company else "our team"


def greeting_text(
    campaign: Campaign, contact: CampaignContact | None, customer_name: str | None, lang: str | None = None
) -> str:
    variables = _SafeDict({
        **(contact.variables if contact else {}), "name": customer_name or "there", "company": company_name(campaign),
    })
    return _script(campaign, lang or campaign.language, "greeting").format_map(variables).strip()


def closing_text(campaign: Campaign, customer_name: str | None = None, lang: str | None = None) -> str:
    variables = _SafeDict(name=customer_name or "", company=company_name(campaign))
    return _script(campaign, lang or campaign.language, "closing").format_map(variables).strip()


def call_facts(campaign: Campaign) -> str:
    """Everything the AI may state as fact on this call: who the company is and what the campaign is about."""
    kind = campaign.offering_type if campaign.offering_type in ("product", "service") else "topic"
    lines = [
        profile_text(campaign.company),
        f"This call is about the {kind}: {campaign.offering_name.strip()}" if (campaign.offering_name or "").strip() else "",
        f"About the {kind}: {campaign.offering_details.strip()}" if (campaign.offering_details or "").strip() else "",
        f"Purpose of this call: {campaign.description.strip()}" if (campaign.description or "").strip() else "",
    ]
    return "\n".join(line for line in lines if line)


def all_utterances(
    campaign: Campaign, contact: CampaignContact | None, customer_name: str | None
) -> dict[str, list[str]]:
    """Everything a call for this contact might say, per language - used to pre-generate TTS audio."""
    out = {}
    for lang in call_languages(campaign):
        out[lang] = [
            greeting_text(campaign, contact, customer_name, lang),
            *(question_text(campaign, q, lang) for q in campaign.questions),
            closing_text(campaign, customer_name, lang),
            voice_persona.company_intro(lang, company_name(campaign)),
            *voice_persona.all_phrases(lang),
        ]
    return out


# --------------------------------------------------------------------------- call flow
async def start(db: AsyncSession, call: Call) -> VoiceAction:
    campaign = await db.get(Campaign, call.campaign_id)
    contact = await db.get(CampaignContact, call.campaign_contact_id)
    now = datetime.now(UTC)
    call.status = CallStatus.IN_PROGRESS
    call.answered_at = call.answered_at or now
    call.current_question_index = 0
    call.language = campaign.language
    lang = call.language
    await _set_state(call, {})

    greeting = greeting_text(campaign, contact, call.customer.name, lang)
    questions = campaign.questions
    if not questions:
        closing = closing_text(campaign, call.customer.name, lang)
        await _log_ai(db, call, [greeting, closing])
        await db.commit()
        return VoiceAction(say=[greeting, closing], hangup=True, language=lang)

    first = question_text(campaign, questions[0], lang)
    await _log_ai(db, call, [greeting])
    await _log_ai(db, call, [first], questions[0])
    await db.commit()
    return VoiceAction(say=[greeting, first], gather_question_index=0, language=lang)


async def handle_answer(
    db: AsyncSession, call: Call, question_index: int, speech: str, detected_language: str | None = None
) -> VoiceAction:
    campaign = await db.get(Campaign, call.campaign_id)
    questions = campaign.questions
    lang = call.language or campaign.language

    if call.current_question_index >= len(questions):
        return VoiceAction(say=[closing_text(campaign, call.customer.name, lang)], hangup=True, language=lang)
    if question_index != call.current_question_index:
        # Duplicate / out-of-order webhook: re-ask the current question without changing state.
        q = questions[call.current_question_index]
        return VoiceAction(
            say=[question_text(campaign, q, lang)], gather_question_index=call.current_question_index, language=lang
        )

    question = questions[question_index]
    speech = (speech or "").strip()
    state = await _get_state(call)

    if not speech:
        if state.get("reprompts", 0) < settings.VOICE_MAX_REPROMPTS:
            state["reprompts"] = state.get("reprompts", 0) + 1
            return await _ask_again(db, call, campaign, question, lang, state, [phrase(lang, "not_heard")])
        await _save_answer(db, call, question, None, {"value": None}, None, AnswerStatus.SKIPPED)
        return await _advance(db, call, campaign, [phrase(lang, "move_on")])

    await _log(db, call, Speaker.CUSTOMER, speech, question)
    new_lang = choose_language(campaign, lang, speech, detected_language)
    if new_lang != lang:
        log.info("Call %s switching language %s -> %s", call.id, lang, new_lang)
        call.language = lang = new_lang

    # 1) Off-script turns we recognise instantly. "Don't call me" always wins; the others win unless
    #    the reply *starts* with a clear answer ("haan, baad mein call karna" to "want a callback?" is a yes,
    #    but "I'm busy right now, call me later" is busy).
    intent = voice_persona.detect_intent(speech)
    if intent == "stop":
        return await _end_call(db, call, campaign, lang, "stop")
    live = extract_answer_live(question, speech)
    leads_with_answer = bool(live and live["answered"] and answer_leads(question, speech))
    if intent and not leads_with_answer:
        if intent in voice_persona.CALL_ENDING_INTENTS:
            return await _end_call(db, call, campaign, lang, intent)
        return await _detour(db, call, campaign, question, lang, state, speech, intent)

    # 2) Understand the answer - instantly when clear-cut, otherwise via Gemini (which also spots
    #    questions/tangents and drafts a reply in the same request).
    result = live or await extract_answer(question, speech, lang, call_facts(campaign))
    gem_intent = result.get("intent", "answer")
    if gem_intent in voice_persona.CALL_ENDING_INTENTS:
        return await _end_call(db, call, campaign, lang, gem_intent)
    if gem_intent in ("question", "other", "wait") and not result.get("answered"):
        return await _detour(db, call, campaign, question, lang, state, speech, gem_intent, result.get("reply"))

    if (not result.get("answered") or result.get("needs_repeat")) and state.get("reprompts", 0) < settings.VOICE_MAX_REPROMPTS:
        state["reprompts"] = state.get("reprompts", 0) + 1
        return await _ask_again(
            db, call, campaign, question, lang, state,
            [phrase(lang, "repeat" if result.get("needs_repeat") else "clarify")],
        )

    status = AnswerStatus.ANSWERED if result.get("answered") else AnswerStatus.UNCLEAR
    await _save_answer(db, call, question, speech, result["normalized_answer"], result["confidence"], status)
    ack = voice_persona.acknowledgement(question, result["normalized_answer"], lang) if status == AnswerStatus.ANSWERED \
        else phrase(lang, "move_on")
    return await _advance(db, call, campaign, [ack])


async def _ask_again(
    db: AsyncSession, call: Call, campaign: Campaign, question: CampaignQuestion, lang: str,
    state: dict[str, Any], lead: list[str],
) -> VoiceAction:
    say = [*lead, question_text(campaign, question, lang)]
    await _set_state(call, state)
    await _log_ai(db, call, say, question)
    await db.commit()
    return VoiceAction(say=say, gather_question_index=call.current_question_index, language=lang)


async def _detour(
    db: AsyncSession, call: Call, campaign: Campaign, question: CampaignQuestion, lang: str,
    state: dict[str, Any], speech: str, intent: str, reply: str | None = None,
) -> VoiceAction:
    """Customer went off-script: respond like a person would, then come back to the question."""
    state["detours"] = state.get("detours", 0) + 1
    if state["detours"] > _MAX_DETOURS_PER_QUESTION:
        await _save_answer(db, call, question, speech, {"value": None}, None, AnswerStatus.UNCLEAR)
        return await _advance(db, call, campaign, [phrase(lang, "move_on")])

    if intent == "wait":
        lead = [phrase(lang, "wait")]
    elif intent == "purpose":  # "who is this?" - say which company is calling, then why
        lead = [voice_persona.company_intro(lang, company_name(campaign)), phrase(lang, "purpose"), phrase(lang, "continue")]
    elif intent == "robot":
        lead = [phrase(lang, intent), phrase(lang, "continue")]
    else:  # question / other: Gemini's reply, or a polite fixed line if Gemini is unavailable
        lead = [reply or phrase(lang, "offtopic"), phrase(lang, "continue")]
    log.info("Call %s: off-script turn (%s) on Q%d", call.id, intent, call.current_question_index + 1)
    return await _ask_again(db, call, campaign, question, lang, state, [line for line in lead if line])


async def _end_call(db: AsyncSession, call: Call, campaign: Campaign, lang: str, reason: str) -> VoiceAction:
    """Customer is busy (call back later) or doesn't want calls (never call again)."""
    say = [phrase(lang, reason)]
    if reason == "stop":
        contact = await db.get(CampaignContact, call.campaign_contact_id)
        contact.status = ContactStatus.DO_NOT_CALL
    else:
        await _set_state(call, {**await _get_state(call), "callback": True})
    log.info("Call %s ended early by customer (%s)", call.id, reason)
    await _log_ai(db, call, say)
    await db.commit()
    return VoiceAction(say=say, hangup=True, language=lang)


async def _advance(db: AsyncSession, call: Call, campaign: Campaign, lead: list[str] | None = None) -> VoiceAction:
    questions = campaign.questions
    lang = call.language or campaign.language
    call.current_question_index += 1
    await _set_state(call, {k: v for k, v in (await _get_state(call)).items() if k == "callback"})
    idx = call.current_question_index
    lead = list(lead or [])
    if idx < len(questions):
        if idx == len(questions) - 1 and len(questions) > 2:
            lead.append(phrase(lang, "last_question"))
        text = question_text(campaign, questions[idx], lang)
        await _log_ai(db, call, lead)
        await _log_ai(db, call, [text], questions[idx])
        await db.commit()
        return VoiceAction(say=[*lead, text], gather_question_index=idx, language=lang)

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
    closing = closing_text(campaign, call.customer.name, lang)
    await _log_ai(db, call, [*lead, closing])
    await db.commit()
    return VoiceAction(say=[*lead, closing], hangup=True, language=lang)


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


# Per-call conversation state lives in Redis (call:{id}): reprompt/detour counters for the current
# question and whether the customer asked to be called back.
async def _get_state(call: Call) -> dict[str, Any]:
    raw = await get_redis().get(f"call:{call.id}")
    return json.loads(raw) if raw else {}


async def _set_state(call: Call, state: dict[str, Any]) -> None:
    await get_redis().set(f"call:{call.id}", json.dumps(state), ex=_STATE_TTL)


async def callback_requested(call_id: Any) -> bool:
    raw = await get_redis().get(f"call:{call_id}")
    return bool(raw and json.loads(raw).get("callback"))

"""Conversational voice agent: the call is a real conversation, not a script read aloud.

Every customer turn goes to an LLM (Sarvam's voice-agent chat model by default) that sees the
campaign goal, the questions still to cover, what has been collected so far and the recent
conversation. It returns what to say next plus any survey answers it heard - so the agent reacts to
what the customer actually said, asks natural follow-ups, answers side questions from the campaign
description, and still fills in every campaign answer in the right structured form.

Kept deterministic on purpose (never left to the model):
  * the reply language - taken from the script the customer spoke in (see voice_qa.choose_language)
  * "don't call me" -> apology + Do Not Call; "busy" -> callback
  * a hard cap on the number of turns
  * a scripted fallback for any turn where the model is slow or fails, so the call never breaks
"""
import asyncio
import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Call, CallAnswer, CallTranscript, Campaign, CampaignContact, CampaignQuestion
from app.models.enums import AnswerStatus, AnswerType, ContactStatus, Speaker
from app.services import voice_persona, voice_qa
from app.services.answer_extraction import answer_leads, extract_answer_live
from app.services.gemini import get_gemini
from app.services.speech.sarvam import get_sarvam
from app.services.voice_persona import phrase
from app.services.voice_qa import VoiceAction

log = logging.getLogger(__name__)
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_HISTORY_TURNS = 16

# Step 2 - what to say next (answers are already extracted by step 1).
TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "end": {"type": "string", "enum": ["no", "completed", "busy", "stop"]},
    },
    "required": ["reply", "end"],
}

# Step 1 - pull survey answers out of what the customer said. Kept separate from the reply: a single
# request that must both write natural Hindi and extract data reliably drops the extraction.
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "captured": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {}, "summary_en": {"type": "string"}},
                "required": ["key", "value", "summary_en"],
            },
        },
    },
    "required": ["captured"],
}

EXTRACT_SYSTEM = """You extract survey answers from what a customer said on a phone call.
The customer may speak English, Hindi or Hinglish.
Return JSON {"captured": [...]} with one item for every listed question that the customer's LAST message
clearly answers - any question in the list, in any order, even if it was only mentioned in passing.
value by type: yes_no -> true/false; rating -> integer on the scale; number -> number;
choice -> one of the options exactly; text -> short English summary.
summary_en: one-line English summary of what they said for that question.
Rules:
- A complaint or suggestion answers an "improve" / "problems" / feedback question.
- "Yes / go ahead / sure / बोलिए" said in reply to a greeting or "do you have a minute?" is NOT an answer.
- "Haven't thought about it" / "don't know" is NOT an answer.
- Skip questions already marked ANSWERED unless the customer clearly changes that answer.
- If nothing is answered return {"captured": []}."""


LANGUAGE_RULES = {
    "en-IN": "English. Simple, friendly Indian English.",
    "hi-IN": (
        "Hindi written in DEVANAGARI script, the way people really talk (Hinglish): keep common English words "
        "like product, service, app, team, feedback, scale in English letters. NEVER write Hindi in Roman letters. "
        "Example: \"अच्छा, समझ गई। तो 1 से 5 के scale पर आप कितने satisfied हैं?\""
    ),
}


# Default personality & conversation rules (editable per campaign in the admin portal).
DEFAULT_AGENT_PROMPT = """You are an AI voice sales representative having a real-time conversation with a customer.

CONVERSATION RULES:

1. CUSTOMER INTERRUPTION / BARGE-IN
- The customer may interrupt you at any time.
- If the customer starts speaking while you are speaking, immediately stop your current response.
- Do not finish the sentence you were saying.
- Do not repeat the interrupted information unless the customer asks for it.
- Listen carefully to the customer's new statement and respond to that instead.
- Treat the interruption as intentional and important.

2. NATURAL CONVERSATION
- Speak like a real human sales representative.
- Never sound robotic, scripted, or repetitive.
- Keep responses short and conversational.
- Ask only one question at a time.
- Do not give long explanations unless the customer asks for details.
- Use the customer's previous statements to maintain context.

3. WHEN CUSTOMER CHANGES THE TOPIC
- Follow the customer's new topic first.
- Answer their question.
- Then naturally return to the previous conversation only if necessary.

Example:
AI: "Our premium plan includes unlimited support and-"
Customer: "How much does it cost?"
AI: "Sure. It's Rs 999 per month."

Do NOT say:
"Sorry, as I was saying, our premium plan includes..."

4. WHEN CUSTOMER SAYS "WAIT", "STOP", "HOLD ON", ETC.
Immediately pause and listen.

Example:
Customer: "Wait, I have a question."
AI: "Sure, go ahead."

5. WHEN CUSTOMER DISAGREES
Do not argue with the customer.
Acknowledge their point and respond appropriately.

Customer: "That's too expensive."
AI: "I understand. What's your budget range?"

6. BARGAINING / NEGOTIATION
If the customer negotiates the price:
- Never invent a discount.
- Stay within the pricing and discount limits given in "About this call".
- If those limits allow it, make a reasonable counteroffer.
- If they do not, say that you can check with the team for an additional discount.

Example:
Customer: "Can you do Rs 40,000?"
AI: "I may be able to help. The best price I can offer directly is Rs 45,000."

7. SILENCE
If the customer is silent, wait briefly before speaking again.
Do not repeatedly ask "Are you there?"

8. UNKNOWN INFORMATION
Never make up information.
If you don't know something, say:
"Let me check that for you."

9. ENDING THE CALL
If the customer clearly wants to end the conversation, don't continue selling.
Politely close the call.

10. MOST IMPORTANT RULE
The customer's latest spoken message always has priority over your previous unfinished response.

Always respond to what the customer is saying NOW, not what you were planning to say."""


def _system_prompt(campaign: Campaign, lang: str) -> str:
    """The campaign's persona/rules, then the mechanics every call needs (goal, answers, language, ending)."""
    gender = settings.SARVAM_TTS_SPEAKER_GENDER.lower()
    facts = voice_qa.call_facts(campaign) or "A short customer call on behalf of our company."
    persona = (campaign.agent_prompt or "").strip() or DEFAULT_AGENT_PROMPT
    return f"""{persona}

=== About this call ===
Your name is {settings.AGENT_NAME}; you are {gender}, calling on behalf of {voice_qa.company_name(campaign)}.
This is a live PHONE call - everything you write is spoken aloud.
The company and what this call is about (the only facts you may state; use them when the customer asks who is
calling, about the company, or about the product or service):
{facts}
The CURRENT CRM GOAL is to get answers to the campaign questions listed in each message, one at a time, in your own
natural words. Don't read them word-for-word; keep their meaning (always mention the scale for ratings).

=== Voice rules ===
- 1-2 sentences, at most 30 words. No emojis, lists or markdown.
- Never invent prices, dates, offers, policies or facts that are not listed in "About this call" - say our team
  will help with that. If asked whether you are a bot or AI, say honestly that you are an AI assistant.
- Use {gender} verb forms for yourself in Hindi.
- Reply language: {LANGUAGE_RULES.get(lang, LANGUAGE_RULES["en-IN"])}

=== Ending the call ===
- Customer is busy or asks to be called later -> say a friendly goodbye, end="busy".
- Customer is not interested or doesn't want calls -> apologise briefly, end="stop".
- Every REQUIRED question answered -> thank them warmly, say goodbye, end="completed".
- Otherwise end="no".

=== Output: JSON only ===
- reply: exactly what you will say next. Never ask again for anything marked ANSWERED.
- end: "no" | "completed" | "busy" | "stop"."""


def _questions_block(campaign: Campaign, answered: dict[str, Any]) -> str:
    rows = []
    for q in campaign.questions:
        status = f"ANSWERED = {json.dumps(answered[q.key], ensure_ascii=False)}" if q.key in answered else "NOT YET ASKED/ANSWERED"
        opts = f", options: {q.options}" if q.answer_type == AnswerType.CHOICE and q.options else ""
        req = "required" if q.is_required else "optional"
        rows.append(f'- key "{q.key}" ({q.answer_type}{opts}, {req}): "{q.text}" -> {status}')
    return "\n".join(rows)


async def _history(db: AsyncSession, call: Call) -> str:
    rows = (
        await db.scalars(
            select(CallTranscript).where(CallTranscript.call_id == call.id)
            .order_by(CallTranscript.turn_index.desc()).limit(_HISTORY_TURNS)
        )
    ).all()
    return "\n".join(f"{'You' if r.speaker == Speaker.AI else 'Customer'}: {r.text}" for r in reversed(rows))


async def _answered(db: AsyncSession, call: Call, campaign: Campaign) -> dict[str, Any]:
    by_id = {q.id: q.key for q in campaign.questions}
    rows = (await db.scalars(select(CallAnswer).where(CallAnswer.call_id == call.id))).all()
    return {by_id[a.question_id]: (a.normalized_answer or {}).get("value")
            for a in rows if a.question_id in by_id and a.status == AnswerStatus.ANSWERED}


def _coerce(question: CampaignQuestion, value: Any) -> Any:
    """Make the model's value match the question type (it is helpful, not always precise)."""
    t = question.answer_type
    if t == AnswerType.YES_NO:
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        return True if s in {"true", "yes", "haan", "हाँ", "y", "1"} else False if s in {"false", "no", "nahi", "नहीं", "n", "0"} else None
    if t in (AnswerType.RATING, AnswerType.NUMBER):
        m = re.search(r"-?\d+(\.\d+)?", str(value))
        if not m:
            return None
        num = float(m.group())
        num = int(num) if num.is_integer() else num
        return max(1, min(10, int(num))) if t == AnswerType.RATING else num
    if t == AnswerType.CHOICE:
        s = str(value).strip().lower()
        return next((o for o in question.options or [] if o.lower() == s or o.lower() in s), None)
    return str(value).strip() or None


async def _ask_model(system: str, user: str, schema: dict) -> dict:
    timeout = settings.AGENT_LLM_TIMEOUT_SECONDS
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if settings.CONVERSATION_LLM == "gemini" and get_gemini().enabled:
        result = await asyncio.wait_for(
            get_gemini().generate_json(system, user, fallback={}, schema=schema, temperature=0.6), timeout
        )
        if not result:
            raise RuntimeError("Gemini returned nothing")
        return result
    return await get_sarvam().chat_json(messages, schema, timeout=timeout, max_tokens=900)


_SENTENCE = re.compile(r"[^.!?\u0964]+[.!?\u0964]*")


def _goodbye_only(reply: str, lang: str, reason: str) -> str:
    """We are hanging up: never leave the customer with a question they can't answer."""
    kept = [m.group().strip() for m in _SENTENCE.finditer(reply) if "?" not in m.group()]
    text = " ".join(k for k in kept if k)
    return text or phrase(lang, reason)


def _next_step(question: CampaignQuestion | None) -> str:
    if question is None:
        return "ALL questions are answered: thank them warmly, say goodbye, end=\"completed\"."
    kind = {
        AnswerType.YES_NO: "a yes/no question - keep it yes/no",
        AnswerType.RATING: "a rating question - say the scale (e.g. 1 to 5)",
        AnswerType.NUMBER: "a question asking for a number",
        AnswerType.CHOICE: f"a choice between: {', '.join(question.options or [])}",
        AnswerType.TEXT: "an open question",
    }.get(question.answer_type, "a question")
    return (
        f"NEXT: after reacting to what they said (and briefly answering if they asked something), ask this question "
        f"in your own natural words, keeping its meaning: \"{question.text}\" ({kind}). Do not skip it or assume its answer. "
        f"EXCEPTION: if the customer is busy, asks to be called later, or doesn't want to talk, do NOT ask anything - "
        f"just say a short friendly goodbye (end=\"busy\" or \"stop\")."
    )


async def _save_captured(
    db: AsyncSession, call: Call, campaign: Campaign, speech: str, captured: list, answered: dict[str, Any]
) -> None:
    by_key = {q.key: q for q in campaign.questions}
    for item in captured:
        if not isinstance(item, dict):
            continue
        q = by_key.get(str(item.get("key")))
        if q is None:
            continue
        raw_value = item.get("value")
        if q.answer_type == AnswerType.TEXT and item.get("summary_en"):
            raw_value = item["summary_en"]  # free text is stored as an English summary
        value = _coerce(q, raw_value)
        if value is None:
            continue
        normalized = {"value": value}
        if item.get("summary_en") and q.answer_type != AnswerType.TEXT:
            normalized["note"] = str(item["summary_en"])[:300]
        await voice_qa._save_answer(db, call, q, speech, normalized, None, AnswerStatus.ANSWERED)
        answered[q.key] = value


async def start(db: AsyncSession, call: Call) -> VoiceAction:
    """Greeting + first question are pre-recorded (instant); the conversation takes over after that."""
    action = await voice_qa.start(db, call)
    campaign = await db.get(Campaign, call.campaign_id)
    first = campaign.questions[0] if campaign.questions else None
    await voice_qa._set_state(call, {
        "turn": 0,
        "last_say": action.say,
        "last_question": voice_qa.question_text(campaign, first, call.language) if first else "",
        "pending_key": first.key if first else None,
    })
    return action


async def handle_turn(
    db: AsyncSession, call: Call, turn: int, speech: str, detected_language: str | None = None
) -> VoiceAction:
    campaign = await db.get(Campaign, call.campaign_id)
    state = await voice_qa._get_state(call)
    lang = call.language or campaign.language

    # Twilio retries / out-of-order webhooks: repeat what we last said, don't re-process.
    expected = state.get("turn", 0)
    if turn != expected and state.get("last_say"):
        return VoiceAction(say=state["last_say"], gather_question_index=expected, language=lang)

    speech = (speech or "").strip()
    if not speech:
        state["silences"] = state.get("silences", 0) + 1
        if state["silences"] > 2:  # can't hear them at all: try again later
            state["callback"] = True
            return await _finish(db, call, campaign, lang, state, [phrase(lang, "not_heard"), phrase(lang, "busy")], "busy")
        return await _say(db, call, lang, state, [phrase(lang, "not_heard"), state.get("last_question") or ""])
    state["silences"] = 0

    await voice_qa._log(db, call, Speaker.CUSTOMER, speech)
    new_lang = voice_qa.choose_language(campaign, lang, speech, detected_language)
    if new_lang != lang:
        log.info("Call %s switching language %s -> %s", call.id, lang, new_lang)
        call.language = lang = new_lang

    # Hard rules first: "don't call me" is honoured no matter what the model would say.
    if voice_persona.detect_intent(speech) == "stop":
        contact = await _contact(db, call)
        contact.status = ContactStatus.DO_NOT_CALL
        return await _finish(db, call, campaign, lang, state, [phrase(lang, "stop")], "stop")

    max_turns = len(campaign.questions) * 3 + 6
    if state.get("turn", 0) >= max_turns:
        return await _finish(db, call, campaign, lang, state,
                             [voice_qa.closing_text(campaign, call.customer.name, lang)], "completed")

    answered = await _answered(db, call, campaign)
    history = await _history(db, call)

    # Step 1: extract any answers from what they just said.
    try:
        extracted = await _ask_model(
            EXTRACT_SYSTEM,
            f"Questions:\n{_questions_block(campaign, answered)}\n\n"
            f"Recent conversation:\n{history}\n\nCustomer's LAST message: \"{speech}\"",
            EXTRACT_SCHEMA,
        )
    except Exception as exc:
        log.warning("Call %s: answer extraction failed (%r); scripted fallback for this turn", call.id, exc)
        return await _scripted_turn(db, call, campaign, lang, state, speech, answered)
    await _save_captured(db, call, campaign, speech, extracted.get("captured") or [], answered)
    call.current_question_index = len(answered)

    # Step 2: decide what to say. The code picks WHICH question comes next (in order, never skipping);
    # the model decides HOW to say it naturally.
    next_q = next((q for q in campaign.questions if q.key not in answered), None)
    user = (
        f"Survey questions for this call:\n{_questions_block(campaign, answered)}\n\n"
        f"Conversation so far:\n{history}\n\n"
        f"The customer just said: \"{speech}\"\n\n"
        f"{_next_step(next_q)}\n"
        f"Reply language: {lang}"
    )
    try:
        result = await _ask_model(_system_prompt(campaign, lang), user, TURN_SCHEMA)
        reply = str(result.get("reply") or "").strip()
        if not reply:
            raise ValueError("empty reply")
    except Exception as exc:
        log.warning("Call %s: conversational model unavailable (%r); scripted fallback for this turn", call.id, exc)
        await db.commit()  # keep the answers already extracted
        return await _scripted_turn(db, call, campaign, lang, state, speech, answered, already_saved=True)

    # Keep the voice natural: Hindi must be Devanagari for the Hindi voice to pronounce it well.
    if lang == "hi-IN" and not _DEVANAGARI.search(reply):
        try:
            reply = await get_sarvam().to_devanagari(reply)
        except Exception:
            log.warning("Call %s: transliteration failed; speaking the reply as-is", call.id)

    end = result.get("end", "no")
    required_done = all(q.key in answered for q in campaign.questions if q.is_required)
    if end == "busy":
        await voice_qa._set_state(call, {**state, "callback": True})
        state["callback"] = True
        return await _finish(db, call, campaign, lang, state, [_goodbye_only(reply, lang, "busy")], "busy")
    if end == "stop":
        (await _contact(db, call)).status = ContactStatus.DO_NOT_CALL
        return await _finish(db, call, campaign, lang, state, [_goodbye_only(reply, lang, "stop")], "stop")
    if end == "completed" and not required_done and next_q is not None:
        # The model tried to wrap up early: keep going with the next required question.
        log.info("Call %s: model tried to end with questions unanswered; continuing", call.id)
        say = [reply, voice_qa.question_text(campaign, next_q, lang)]
        state["last_question"] = say[-1]
        return await _say(db, call, lang, state, say)
    if end == "completed" or (required_done and len(answered) == len(campaign.questions)):
        say = [reply]
        if end != "completed":  # model forgot to say goodbye
            say.append(voice_qa.closing_text(campaign, call.customer.name, lang))
        return await _finish(db, call, campaign, lang, state, say, "completed")

    state["last_question"] = reply
    return await _say(db, call, lang, state, [reply])


async def _scripted_turn(
    db: AsyncSession, call: Call, campaign: Campaign, lang: str, state: dict, speech: str, answered: dict,
    already_saved: bool = False,
) -> VoiceAction:
    """Model unavailable: handle this turn like the scripted engine - instant, no network."""
    pending = [q for q in campaign.questions if q.key not in answered]
    if not pending:
        return await _finish(db, call, campaign, lang, state, [voice_qa.closing_text(campaign, call.customer.name, lang)], "completed")
    current = next((q for q in pending if q.key == state.get("pending_key")), pending[0])
    live = None if already_saved else extract_answer_live(current, speech)
    intent = voice_persona.detect_intent(speech)
    if intent in voice_persona.CALL_ENDING_INTENTS and not (live and live["answered"] and answer_leads(current, speech)):
        if intent == "busy":
            state["callback"] = True
        return await _finish(db, call, campaign, lang, state, [phrase(lang, intent)], intent)
    say: list[str] = []
    if live and live["answered"]:
        await voice_qa._save_answer(db, call, current, speech, live["normalized_answer"], live["confidence"], AnswerStatus.ANSWERED)
        answered[current.key] = live["normalized_answer"].get("value")
        say.append(voice_persona.acknowledgement(current, live["normalized_answer"], lang))
        pending = [q for q in pending if q.key != current.key]
        if not pending:
            say.append(voice_qa.closing_text(campaign, call.customer.name, lang))
            return await _finish(db, call, campaign, lang, state, say, "completed")
        current = pending[0]
    else:
        say.append(phrase(lang, "clarify"))
    question = voice_qa.question_text(campaign, current, lang)
    say.append(question)
    state["pending_key"] = current.key
    state["last_question"] = question
    call.current_question_index = len(answered)
    return await _say(db, call, lang, state, say)


async def _say(db: AsyncSession, call: Call, lang: str, state: dict, say: list[str]) -> VoiceAction:
    say = [s for s in say if s]
    state["turn"] = state.get("turn", 0) + 1
    state["last_say"] = say
    await voice_qa._set_state(call, state)
    await voice_qa._log_ai(db, call, say)
    await db.commit()
    return VoiceAction(say=say, gather_question_index=state["turn"], language=lang)


async def _finish(
    db: AsyncSession, call: Call, campaign: Campaign, lang: str, state: dict, say: list[str], reason: str
) -> VoiceAction:
    answered = await _answered(db, call, campaign)
    call.all_questions_answered = all(q.key in answered for q in campaign.questions if q.is_required)
    call.current_question_index = len(campaign.questions)  # conversation over
    state["turn"] = state.get("turn", 0) + 1
    state["last_say"] = say
    await voice_qa._set_state(call, state)
    await voice_qa._log_ai(db, call, [s for s in say if s])
    await db.commit()
    log.info("Call %s: conversation ended (%s), %d/%d answers", call.id, reason, len(answered), len(campaign.questions))
    return VoiceAction(say=[s for s in say if s], hangup=True, language=lang)


async def _contact(db: AsyncSession, call: Call) -> CampaignContact:
    return await db.get(CampaignContact, call.campaign_contact_id)

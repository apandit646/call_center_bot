"""Inbound support calls: the phone channel of the support brain.

    customer dials the company's number -> greeting (+ "I see you have an open request...")
        -> [ caller speaks -> speech-to-text -> support brain -> spoken reply ]  until they are done
        -> saved as a conversation (channel "phone") with its transcript, recording and outcome

The thinking is services/support_brain.py, the same code that answers WhatsApp: the company's
knowledge base, domains and ticket rules. This module adds what a call needs:
  * who is calling: the caller's number -> that company's customer -> their existing tickets
  * turn-taking and the per-call state (kept on the SupportCall row)
  * the reply language, which follows the caller (Devanagari Hindi <-> English)
  * hidden caller id: answers still work; a ticket needs their number first
  * hanging up early: a problem the caller described is never lost - it becomes a ticket
"""
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Company, Conversation, Customer, Message, SupportCall, Ticket
from app.models.enums import (
    OPEN_TICKET_STATUSES,
    TERMINAL_CALL_STATUSES,
    CallOutcome,
    CallStatus,
    Channel,
    ConversationStatus,
    MessageDirection,
    MessageRole,
)
from app.services import storage, support_brain
from app.services.answer_extraction import _NO, _YES, _tokens
from app.services.company_service import twilio_credentials
from app.services.phone import normalize_phone
from app.services.speech.sarvam import get_sarvam
from app.services.support_brain import BrainReply, CallerContext
from app.services.telephony import calling_problem, get_provider
from app.services.voice_qa import VoiceAction

log = logging.getLogger(__name__)

# Callers who withhold their number share this placeholder customer until they tell us a number.
UNKNOWN_PHONE = "unknown"
UNKNOWN_NAME = "Unknown caller (hidden number)"
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN_WORD = re.compile(r"[A-Za-z]+")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9",
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4", "paanch": "5", "panch": "5",
    "chhe": "6", "che": "6", "saat": "7", "aath": "8", "nau": "9",
    "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5", "छह": "6", "छः": "6",
    "सात": "7", "आठ": "8", "नौ": "9",
}

# Lines only a call needs (the rest come from support_brain.VOICE_REPLIES).
LINES = {
    "en": {
        "hello": "Hello, thank you for calling {company}. I am the virtual assistant.",
        "open_ticket": "I see you have an open request, number {number}, about {issue}. Are you calling about that?",
        "new_issue": "Okay. Please tell me how I can help you.",
        "anything_else": "Is there anything else I can help you with?",
        "not_heard": "Sorry, I didn't catch that. Could you say it again?",
        "silent_bye": "I can't hear you. Please call again. Goodbye.",
        "phone_retry": "Sorry, I didn't get the number. Please say your mobile number, one digit at a time.",
        "too_long": "I'm sorry, I couldn't sort this out on the call.",
    },
    "hi": {
        "hello": "नमस्ते, {company} को call करने के लिए धन्यवाद। मैं virtual assistant हूँ।",
        "open_ticket": "आपकी एक request open है, number {number}, {issue} के बारे में। क्या आप उसी के लिए call कर रहे हैं?",
        "new_issue": "ठीक है। बताइए, आपकी क्या मदद की जाए?",
        "anything_else": "क्या आपकी और कोई मदद की जाए?",
        "not_heard": "Sorry, आवाज़ clear नहीं आई। कृपया फिर से बताइए।",
        "silent_bye": "आपकी आवाज़ नहीं आ रही। कृपया दोबारा call कीजिए। धन्यवाद।",
        "phone_retry": "Sorry, number समझ नहीं आया। कृपया अपना mobile number एक-एक अंक करके बताइए।",
        "too_long": "माफ़ कीजिए, यह call पर solve नहीं हो पाया।",
    },
}


def _line(lang: str, key: str, **kwargs: Any) -> str:
    """lang: a call language (en-IN / hi-IN) or a brain language (en / hi)."""
    if "number" in kwargs:
        kwargs["number"] = support_brain.spoken_number(kwargs["number"])
    return LINES[_brain_lang(lang)][key].format(**kwargs)


def _brain_lang(tts_lang: str) -> str:
    """Call language (en-IN / hi-IN) -> the brain's language key."""
    return "hi" if tts_lang.startswith("hi") else "en"


# --------------------------------------------------------------------------- is inbound on?
async def company_for_number(db: AsyncSession, number: str | None) -> Company | None:
    """The company that owns the dialled number - whether or not it currently takes calls, so the
    request can still be verified with its Twilio token before we decide to answer."""
    if not number:
        return None
    company = await db.scalar(select(Company).where(Company.twilio_from_number == number))
    return company if company is not None and twilio_credentials(company) is not None else None


def inbound_problem(company: Company) -> str | None:
    """Why this company cannot switch inbound calls on, or None if it can."""
    if not company.inbound_allowed:
        return "Inbound calls are not included in your plan. Ask the platform administrator to enable them."
    return calling_problem(company)


def inbound_active(company: Company) -> bool:
    return bool(company.is_active and company.inbound_enabled and inbound_problem(company) is None)


def webhook_urls() -> tuple[str, str]:
    """(voice URL, status URL) that the company's Twilio number must point at."""
    base = f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/telephony/inbound"
    return base, f"{base}/status"


def turn_url(call_id: uuid.UUID, turn: int) -> str:
    return f"{webhook_urls()[0]}/{call_id}/turn?t={turn}"


def recording_url(call_id: uuid.UUID) -> str:
    return f"{webhook_urls()[0]}/{call_id}/recording"


async def sync_number(company: Company, old_provider: Any = None) -> None:
    """Make the company's Twilio number match its inbound switch. Raises if Twilio refuses.

    old_provider: the provider for the number inbound used before this change, if the number
    changed or inbound was switched off - that number stops receiving calls here."""
    if old_provider is not None:
        try:
            await old_provider.configure_inbound(None, None)
        except Exception as exc:  # the old number may be gone already; never block the change on it
            log.warning("Company %s: could not clear the inbound webhook on the old number: %r", company.id, exc)
    if company.inbound_enabled:
        voice_url, status_url = webhook_urls()
        await get_provider(company).configure_inbound(voice_url, status_url)


# --------------------------------------------------------------------------- the call
async def start(db: AsyncSession, company: Company, form: dict[str, str], provider_name: str) -> tuple[SupportCall, VoiceAction]:
    """A call came in: find the caller, greet them, and mention the request they already have."""
    sid = form.get("CallSid") or None
    if sid:  # the provider retried the webhook: say the same thing again
        existing = await db.scalar(select(SupportCall).where(SupportCall.provider_call_id == sid))
        if existing is not None:
            state = existing.state or {}
            return existing, VoiceAction(
                say=state.get("last_say", []), gather_question_index=state.get("turn", 0),
                language=existing.language or company.inbound_language,
            )

    lang = company.inbound_language or "en-IN"
    customer = await _caller(db, company, form.get("From"))
    conversation = Conversation(company_id=company.id, customer_id=customer.id, channel=Channel.PHONE, customer=customer)
    db.add(conversation)
    await db.flush()
    call = SupportCall(
        company_id=company.id, customer_id=customer.id, conversation_id=conversation.id, provider=provider_name,
        provider_call_id=sid, from_number=customer.phone if customer.phone != UNKNOWN_PHONE else None,
        to_number=form.get("To"), status=CallStatus.IN_PROGRESS, language=lang,
    )
    db.add(call)
    await db.flush()

    hello = (company.inbound_greeting or "").strip() or _line(lang, "hello", company=company.name)
    tickets = await _tickets(db, customer)
    open_ticket = next((t for t in tickets if t.status in OPEN_TICKET_STATUSES), None)
    state: dict[str, Any] = {"turn": 0, "silences": 0, "open_issue": False}
    if open_ticket is not None:
        # Start from what we already know instead of making them explain it again.
        say = [hello, _line(lang, "open_ticket", number=open_ticket.number, issue=support_brain._short(open_ticket.issue))]
        state["ticket_question"] = str(open_ticket.id)
    else:
        say = [hello, support_brain._t(_brain_lang(lang), "greeting", Channel.PHONE)]
    state["last_say"] = say
    call.state = state
    for text in say:
        support_brain.save_message(db, conversation, MessageDirection.OUTBOUND, MessageRole.ASSISTANT, text)
    await db.commit()
    log.info("Inbound call %s for %s from %s (%d existing ticket(s))", call.id, company.name, call.from_number or "hidden number", len(tickets))
    return call, VoiceAction(say=say, gather_question_index=0, language=lang)


async def handle_turn(
    db: AsyncSession, call: SupportCall, turn: int, speech: str, detected_language: str | None = None
) -> VoiceAction:
    """The caller spoke (or stayed silent) after turn `turn`."""
    company = await db.get(Company, call.company_id)
    conversation = await db.get(Conversation, call.conversation_id)
    state = dict(call.state or {})
    lang = call.language or company.inbound_language

    # Provider retries / out-of-order webhooks: repeat what we last said, don't re-process.
    if turn != state.get("turn", 0) or state.get("finalized"):
        return VoiceAction(
            say=state.get("last_say", []), language=lang, hangup=bool(state.get("finalized")),
            gather_question_index=None if state.get("finalized") else state.get("turn", 0),
        )

    speech = (speech or "").strip()
    if not speech:
        state["silences"] = state.get("silences", 0) + 1
        if state["silences"] > 2:
            return await _say(db, call, conversation, state, lang, [_line(lang, "silent_bye")], hangup=True)
        return await _say(db, call, conversation, state, lang, [_line(lang, "not_heard")])
    state["silences"] = 0

    lang = call.language = _next_language(lang, speech)
    brain_lang = _brain_lang(lang)
    support_brain.save_message(db, conversation, MessageDirection.INBOUND, MessageRole.CUSTOMER, speech)
    conversation.last_message_at = datetime.now(UTC)
    await db.commit()

    customer = await db.get(Customer, call.customer_id)
    anonymous = customer.phone == UNKNOWN_PHONE
    tickets = [] if anonymous else await _tickets(db, customer)
    focus = next((t for t in tickets if str(t.id) == state.get("focus_ticket_id")), None)

    if state.get("awaiting_phone"):
        reply = await _take_phone_number(db, call, conversation, company, state, speech, brain_lang)
    elif state.get("turn", 0) >= settings.INBOUND_MAX_TURNS:
        # Going in circles: hand it to a person rather than keep the caller on the line.
        reply = await support_brain.escalate(
            db, conversation, "call_too_long", preface=_line(lang, "too_long"), lang=brain_lang, channel=Channel.PHONE
        )
        reply.end = True
    else:
        reply = await _reply_to(db, conversation, company, state, speech, brain_lang, tickets, focus, anonymous)

    text = await _speakable(reply.text, lang)
    say = [text]
    if reply.kind in ("escalated", "ticket_updated") and not reply.end:
        say.append(_line(lang, "anything_else"))
    _track(call, state, reply)
    return await _say(db, call, conversation, state, lang, say, hangup=reply.end, meta=reply.meta)


async def _reply_to(
    db: AsyncSession, conversation: Conversation, company: Company, state: dict[str, Any], speech: str, lang: str,
    tickets: list[Ticket], focus: Ticket | None, anonymous: bool,
) -> BrainReply:
    caller = CallerContext(tickets=tickets, focus_ticket=focus, can_raise_ticket=not anonymous)
    # We just asked "are you calling about request number ...?": a plain yes / no answers that.
    asked_id = state.pop("ticket_question", None)
    asked = next((t for t in tickets if str(t.id) == asked_id), None)
    if asked is not None:
        tokens = _tokens(speech)
        first = next((t for t in tokens if t in _YES or t in _NO), None)
        if first is not None and len(tokens) <= 4:
            if first in _NO:
                return BrainReply(_line(lang, "new_issue"), "greeting")
            caller.focus_ticket = asked
            return await support_brain._ticket_status(
                caller, asked, speech, await support_brain.history_of(db, conversation), lang
            )
    return await support_brain.respond(db, conversation, company, speech, Channel.PHONE, lang=lang, caller=caller)


async def _take_phone_number(
    db: AsyncSession, call: SupportCall, conversation: Conversation, company: Company, state: dict[str, Any],
    speech: str, lang: str,
) -> BrainReply:
    """Caller id was hidden and a ticket is needed: the caller is telling us their number."""
    pending = state["awaiting_phone"]
    phone = normalize_phone(_spoken_digits(speech))
    if phone is None and pending.get("tries", 0) < 1:
        state["awaiting_phone"] = {**pending, "tries": pending.get("tries", 0) + 1}
        return BrainReply(_line(lang, "phone_retry"), "need_phone")
    if phone is not None:  # from here on this is that customer's call and ticket
        customer = await _customer_by_phone(db, company, phone)
        conversation.customer_id = call.customer_id = customer.id
        conversation.customer = customer
        call.from_number = phone
    # No usable number after two tries: still raise it, under the unknown caller, so nothing is lost.
    state.pop("awaiting_phone")
    return await support_brain.escalate(db, conversation, pending.get("reason", "human_agent"), lang=lang, channel=Channel.PHONE)


def _track(call: SupportCall, state: dict[str, Any], reply: BrainReply) -> None:
    """Remember what this turn changed: the ticket in focus, the outcome, and whether a problem is still open."""
    if reply.kind == "need_phone" and "awaiting_phone" not in state:
        state["awaiting_phone"] = {"reason": reply.meta.get("reason", "human_agent"), "tries": 0}
    if reply.ticket is not None:
        state["focus_ticket_id"] = str(reply.ticket.id)
    elif reply.kind in ("answered", "clarify"):
        state.pop("focus_ticket_id", None)  # they moved on to something new

    if reply.kind == "escalated":
        call.outcome, call.ticket_id = CallOutcome.TICKET_RAISED, reply.ticket.id
    elif reply.kind == "ticket_updated" and call.outcome != CallOutcome.TICKET_RAISED:
        call.outcome, call.ticket_id = CallOutcome.TICKET_UPDATED, reply.ticket.id
    elif reply.kind in ("answered", "ticket_status") and call.outcome is None:
        call.outcome = CallOutcome.ANSWERED

    if reply.kind in ("clarify", "need_phone"):
        state["open_issue"] = True  # they described something we have not dealt with yet
    elif reply.kind in ("answered", "escalated", "ticket_updated", "satisfied"):
        state["open_issue"] = False


async def _say(
    db: AsyncSession, call: SupportCall, conversation: Conversation, state: dict[str, Any], lang: str,
    say: list[str], hangup: bool = False, meta: dict[str, Any] | None = None,
) -> VoiceAction:
    say = [s for s in say if s]
    for i, text in enumerate(say):
        support_brain.save_message(
            db, conversation, MessageDirection.OUTBOUND, MessageRole.ASSISTANT, text, meta=meta if i == 0 else None
        )
    state["turn"] = state.get("turn", 0) + 1
    state["last_say"] = say
    call.state = state
    conversation.last_message_at = datetime.now(UTC)
    if hangup:
        await _close(db, call, conversation)
    await db.commit()
    return VoiceAction(say=say, gather_question_index=None if hangup else state["turn"], hangup=hangup, language=lang)


# --------------------------------------------------------------------------- after the call
async def _close(db: AsyncSession, call: SupportCall, conversation: Conversation) -> None:
    """The conversation is over (we hung up, or the caller did). Runs once per call."""
    state = dict(call.state or {})
    if state.get("finalized"):
        return
    state["finalized"] = True
    spoke = await db.scalar(
        select(func.count(Message.id)).where(Message.conversation_id == conversation.id, Message.role == MessageRole.CUSTOMER)
    )
    if state.get("open_issue") and call.ticket_id is None and spoke:
        # They explained a problem and hung up before we answered it or raised it: raise it now.
        reply = await support_brain.escalate(
            db, conversation, "caller_hung_up", lang=_brain_lang(call.language or "en-IN"), channel=Channel.PHONE
        )
        call.outcome, call.ticket_id = CallOutcome.TICKET_RAISED, reply.ticket.id
        log.info("Inbound call %s: caller hung up with an open problem -> ticket #%s", call.id, reply.ticket.number)
    if call.outcome is None:
        call.outcome = CallOutcome.ABANDONED
    if conversation.status == ConversationStatus.OPEN:
        conversation.status = (
            ConversationStatus.RESOLVED if call.outcome == CallOutcome.ANSWERED else ConversationStatus.CLOSED
        )
    call.state = state


async def finish(
    db: AsyncSession, call_id: uuid.UUID, status: str | None = None, duration_seconds: int | None = None
) -> None:
    """Apply the provider's final call status and close the conversation if it is still open."""
    call = await db.get(SupportCall, call_id, with_for_update=True)
    if call is None:
        return
    if duration_seconds is not None:
        call.duration_seconds = duration_seconds
    if status in set(TERMINAL_CALL_STATUSES) and call.status not in TERMINAL_CALL_STATUSES:
        call.status = status
        call.ended_at = datetime.now(UTC)
        await _close(db, call, await db.get(Conversation, call.conversation_id))
    await db.commit()


async def store_recording(db: AsyncSession, call_id: uuid.UUID, url: str) -> None:
    call = await db.get(SupportCall, call_id)
    if call is None:
        return
    call.recording_url = url
    try:
        data = await get_provider(await db.get(Company, call.company_id)).download_recording(url)
        if data:
            call.recording_path = storage.save_bytes(f"recordings/inbound/{call.company_id}", f"{call.id}.mp3", data)
    except Exception:
        log.exception("Could not download the recording of inbound call %s", call_id)
    await db.commit()


async def handle_event(db: AsyncSession, event: dict[str, Any]) -> None:
    """A queued provider event for an inbound call (final status, recording ready)."""
    call_id = uuid.UUID(event["support_call_id"])
    if event.get("recording_url"):
        await store_recording(db, call_id, event["recording_url"])
    if event.get("status") or event.get("duration_seconds") is not None:
        await finish(db, call_id, event.get("status"), event.get("duration_seconds"))


async def close_stale(db: AsyncSession, now: datetime) -> None:
    """Calls whose final status callback never arrived: close them so nothing stays "in progress"."""
    stale = (
        await db.scalars(
            select(SupportCall.id).where(
                SupportCall.status == CallStatus.IN_PROGRESS,
                SupportCall.started_at < now - timedelta(minutes=settings.CALL_STALE_MINUTES),
            )
        )
    ).all()
    for call_id in stale:
        await finish(db, call_id, CallStatus.COMPLETED)


# --------------------------------------------------------------------------- helpers
async def _caller(db: AsyncSession, company: Company, from_number: str | None) -> Customer:
    phone = normalize_phone(from_number) if from_number else None
    if phone is None:  # withheld / "anonymous" / not a real number
        return await _customer_by_phone(db, company, UNKNOWN_PHONE, UNKNOWN_NAME)
    return await _customer_by_phone(db, company, phone)


async def _customer_by_phone(db: AsyncSession, company: Company, phone: str, name: str | None = None) -> Customer:
    customer = await db.scalar(select(Customer).where(Customer.company_id == company.id, Customer.phone == phone))
    if customer is None:
        customer = Customer(company_id=company.id, phone=phone, name=name)
        db.add(customer)
        await db.flush()
    return customer


async def _tickets(db: AsyncSession, customer: Customer) -> list[Ticket]:
    """The caller's recent tickets in this company, newest first (none for a hidden number)."""
    if customer.phone == UNKNOWN_PHONE:
        return []
    return list(
        (
            await db.scalars(
                select(Ticket)
                .where(Ticket.company_id == customer.company_id, Ticket.customer_id == customer.id)
                .order_by(Ticket.created_at.desc())
                .limit(settings.INBOUND_TICKET_HISTORY)
            )
        ).unique().all()
    )


def _next_language(current: str, speech: str) -> str:
    """The call follows the caller: Hindi words arrive in Devanagari from the recogniser (code-mix mode).
    Going back to English needs a real sentence, because Hindi speakers say "yes" / "ok" too."""
    if _DEVANAGARI.search(speech):
        return "hi-IN"
    if current != "en-IN" and len(_LATIN_WORD.findall(speech)) >= 3:
        return "en-IN"
    return current


def _spoken_digits(speech: str) -> str:
    """ "nine eight, 7 6 5..." / "९८७६..." -> "98765..." """
    text = speech.translate(_DEVANAGARI_DIGITS).lower()
    for word, digit in _DIGIT_WORDS.items():
        text = re.sub(rf"(?<![\wऀ-ॿ]){re.escape(word)}(?![\wऀ-ॿ])", digit, text)
    return re.sub(r"\D", "", text)


async def _speakable(text: str, lang: str) -> str:
    """A Hindi voice needs Devanagari; transliterate a reply the model wrote in Roman letters."""
    text = " ".join(text.split())
    if lang.startswith("hi") and text and not _DEVANAGARI.search(text):
        try:
            return await get_sarvam().to_devanagari(text)
        except Exception:
            log.warning("Transliteration unavailable; speaking the reply as written")
    return text

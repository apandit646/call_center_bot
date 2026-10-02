"""WhatsApp support workflow.

    inbound message -> domain detection -> domain-specific RAG -> Gemini -> answer / clarification
                                                                      +-> not resolved -> ticket

Every step runs inside one company: its customers, its domains, its knowledge base, its WhatsApp
number, and its company profile (so the bot can say who it works for).
"""
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Company, Conversation, Customer, Domain, Message, Ticket
from app.models.enums import (
    OPEN_TICKET_STATUSES,
    ConversationStatus,
    MessageDirection,
    MessageRole,
    TicketPriority,
    TicketSource,
)
from app.services import ticket_service, whatsapp_client
from app.services.company_service import profile_text
from app.services.gemini import get_gemini
from app.services.phone import normalize_phone
from app.services.rag import format_context, retrieve

log = logging.getLogger(__name__)

INTENTS = [
    "question", "provide_info", "company_info", "satisfied", "not_resolved", "human_agent", "greeting", "other",
]

ANALYZE_SYSTEM = """You are the routing layer of a customer-support assistant on WhatsApp.
Classify the customer's LATEST message using the conversation so far.
intent:
- question: asks something or describes a problem
- provide_info: supplies details the assistant asked for (ids, dates, amounts...)
- company_info: asks about the company itself - its name, what it does or sells, address, contact details
- satisfied: the issue is resolved / they are thankful and done
- not_resolved: says the answer did not help or the problem persists
- human_agent: explicitly asks for a human / agent / complaint to be raised
- greeting: only a greeting with no request
- other: anything else
domain_code: the single best matching support domain code from the list, or null if none fits.
Keep the current domain unless the customer clearly switched topic.
language: the language of the latest message - "en" (English), "hi" (Hindi in Devanagari script) or
"hinglish" (Hindi/English mix written in Latin letters, e.g. "mera payment cut gaya but plan active nahi hua")."""

ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "domain_code": {"type": ["string", "null"]},
        "domain_confidence": {"type": "number"},
        "language": {"type": "string", "enum": ["en", "hi", "hinglish"]},
    },
    "required": ["intent", "domain_code", "domain_confidence", "language"],
}

ANSWER_SYSTEM = """You are a friendly, concise customer-support assistant replying on WhatsApp for the company in
the "Company profile". Answer ONLY from the knowledge base context and the company profile provided.
Never invent policies, amounts, dates or links.
Decide a status:
- answered: the context answers the question. End by asking if this resolved their issue.
- clarify: you need specific details from the customer (e.g. transaction id, order number, email) to help. Ask for them.
- escalate: the context cannot resolve it, it needs account-specific action by staff, or the customer already gave
  the details and it still cannot be resolved. Tell them you will raise a support request.
Keep replies under 120 words, plain text, no markdown headings.
Reply in the language given in the prompt; the knowledge base may be in English, so translate as needed.
Keep product names, amounts, ids and technical terms as they are."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "status": {"type": "string", "enum": ["answered", "clarify", "escalate"]},
    },
    "required": ["reply", "status"],
}

COMPANY_SYSTEM = """You are a friendly, concise customer-support assistant replying on WhatsApp for the company in
the "Company profile". The customer is asking about the company itself. Answer ONLY from the company profile.
If the profile does not say, tell them you don't have that detail and offer to help with their issue.
Keep replies under 80 words, plain text, no markdown headings.
Reply in the language given in the prompt; keep names, addresses, numbers and links exactly as written."""

TICKET_SYSTEM = """Summarise a customer-support conversation for a human agent.
Always write in English, translating anything the customer said in Hindi or Hinglish.
issue: one line describing the problem.
summary: 2-5 short lines with all facts the customer gave (ids, amounts, dates) and what was already tried.
priority: low | medium | high | urgent (money lost, service down or security -> high/urgent)."""

TICKET_SCHEMA = {
    "type": "object",
    "properties": {
        "issue": {"type": "string"},
        "summary": {"type": "string"},
        "priority": {"type": "string", "enum": [p.value for p in TicketPriority]},
    },
    "required": ["issue", "summary", "priority"],
}

_SATISFIED = re.compile(
    r"\b(thanks|thank you|thx|resolved|solved|got it|that helps|perfect|great|dhanyavaad|dhanyawad|shukriya"
    r"|ho gaya|ho gya)\b|धन्यवाद|शुक्रिया|हो गया", re.I)
_NOT_RESOLVED = re.compile(
    r"\b(not (resolved|solved|working|helpful)|didn'?t help|still|same issue|useless|nahi hua|abhi bhi|ab bhi"
    r"|kaam nahi)\b|नहीं हुआ|अभी भी|काम नहीं", re.I)
_HUMAN = re.compile(
    r"\b(human|agent|representative|real person|complaint|raise (a )?(ticket|issue)|insaan)\b|शिकायत|एजेंट", re.I)
_COMPANY = re.compile(
    r"\b(company name|your company|which company|what company|about (your|the) company|who are you"
    r"|your (office )?address|where are you located|company ka naam|kaun si company|konsi company|aap kaun)\b"
    r"|कंपनी का नाम|कौन सी कंपनी|आप कौन", re.I)
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|hii+|good (morning|afternoon|evening)|namaste|namaskar|नमस्ते|नमस्कार)[\s!.,?]*$", re.I)


# Fixed bot messages in each supported language. Gemini-written answers follow the same language.
LANGUAGES = ("en", "hi", "hinglish")
LANGUAGE_INSTRUCTION = {
    "en": "English",
    "hi": "Hindi in Devanagari script (keep common English words like payment, refund, app, OTP in English)",
    "hinglish": "Hinglish - Hindi mixed with English, written in Latin letters, the way the customer writes",
}
REPLIES = {
    "en": {
        "greeting": "Hello! I'm the virtual support assistant for {company}. How can I help you today?",
        "satisfied": "Glad I could help! If you need anything else, just send a message here.",
        "which_topic": "Sure, I can help. Could you tell me a bit more? Is this about {topics}?",
        "sorry": "I'm sorry I couldn't resolve this for you here.",
        "ticket_raised": "I've raised support request #{number}. Our team will review it and get back to you.",
        "ticket_update": "Thanks, I've added this to your support request #{number}. Our team will get back to you soon.",
        "found": "Here's what I found:",
        "resolved_q": "Did this resolve your issue?",
        "need_details": "I couldn't find an answer to that. Could you share a few more details "
                        "(for example an order or transaction ID)?",
    },
    "hi": {
        "greeting": "नमस्ते! मैं {company} का virtual support assistant हूँ। बताइए, मैं आपकी क्या मदद कर सकता हूँ?",
        "satisfied": "खुशी हुई कि मदद हो पाई! आगे कुछ भी चाहिए तो यहीं message कीजिए।",
        "which_topic": "ज़रूर, मैं मदद करता हूँ। थोड़ा और बताइए - क्या यह {topics} से जुड़ा है?",
        "sorry": "माफ़ कीजिए, मैं यहाँ यह issue solve नहीं कर पाया।",
        "ticket_raised": "मैंने आपकी support request #{number} raise कर दी है। हमारी team इसे देखकर आपसे संपर्क करेगी।",
        "ticket_update": "धन्यवाद, मैंने यह आपकी support request #{number} में जोड़ दिया है। हमारी team जल्द संपर्क करेगी।",
        "found": "मुझे यह जानकारी मिली:",
        "resolved_q": "क्या इससे आपका issue solve हुआ?",
        "need_details": "मुझे इसका जवाब नहीं मिला। क्या आप थोड़ी और जानकारी दे सकते हैं (जैसे order या transaction ID)?",
    },
    "hinglish": {
        "greeting": "Namaste! Main {company} ka virtual support assistant hoon. Batayiye, main aapki kya madad kar sakta hoon?",
        "satisfied": "Khushi hui ki madad ho payi! Aage kuch bhi chahiye toh yahin message kijiye.",
        "which_topic": "Zaroor, main madad karta hoon. Thoda aur batayiye - kya yeh {topics} se related hai?",
        "sorry": "Sorry, main yahan yeh issue solve nahi kar paya.",
        "ticket_raised": "Maine aapki support request #{number} raise kar di hai. Hamari team ise dekh kar aapse contact karegi.",
        "ticket_update": "Thanks, maine yeh aapki support request #{number} mein add kar diya hai. Team jaldi contact karegi.",
        "found": "Mujhe yeh jaankari mili:",
        "resolved_q": "Kya isse aapka issue solve hua?",
        "need_details": "Mujhe iska jawab nahi mila. Kya aap thodi aur details de sakte hain (jaise order ya transaction ID)?",
    },
}
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_HINGLISH_MARKERS = {
    "hai", "hain", "nahi", "nahin", "kya", "mera", "meri", "mere", "mujhe", "aap", "aapka", "raha", "rahi", "hua",
    "hui", "gaya", "gayi", "abhi", "bhi", "kab", "kyun", "kaise", "paisa", "paise", "kijiye", "karo", "tha", "thi",
    "haan", "yaar", "bhai", "karna", "karni", "chahiye", "gya", "kar", "se", "baat", "ka", "ke", "ki", "mein",
    "hoon", "hu", "toh", "kuch", "liye", "milega", "mila", "namaste", "namaskar", "ji",
}


def detect_language(text: str) -> str:
    """Offline guess used when Gemini is unavailable (and for quick follow-ups)."""
    letters = [ch for ch in text if ch.isalpha()]
    if letters and sum(1 for ch in letters if _DEVANAGARI.match(ch)) / len(letters) > 0.3:
        return "hi"
    tokens = re.findall(r"[a-z]+", text.lower())
    markers = sum(1 for t in tokens if t in _HINGLISH_MARKERS)
    return "hinglish" if markers >= 2 or (markers == 1 and len(tokens) <= 4) else "en"


def _t(lang: str, key: str, **kwargs: Any) -> str:
    return REPLIES.get(lang, REPLIES["en"])[key].format(**kwargs)


@dataclass
class InboundMessage:
    wa_id: str
    wa_message_id: str
    text: str
    profile_name: str | None = None


# --------------------------------------------------------------------------- entry point
async def handle_inbound(db: AsyncSession, msg: InboundMessage, company: Company) -> None:
    """Handle one inbound message for the company whose WhatsApp number received it."""
    if await db.scalar(select(Message.id).where(Message.wa_message_id == msg.wa_message_id)):
        log.info("Duplicate WhatsApp message %s ignored", msg.wa_message_id)
        return

    customer = await _get_or_create_customer(db, msg, company)
    conversation = await _get_or_create_conversation(db, customer)
    _save_message(db, conversation, MessageDirection.INBOUND, MessageRole.CUSTOMER, msg.text, msg.wa_message_id)
    conversation.last_message_at = datetime.now(UTC)
    await db.commit()

    if conversation.status == ConversationStatus.ESCALATED:
        await _handle_escalated_followup(db, conversation, msg.text, detect_language(msg.text))
        return

    domains = await _domains(db, company.id)
    history = await _history(db, conversation)
    analysis = await _analyze(msg.text, history, domains, conversation.domain)
    intent = analysis["intent"]
    lang = analysis.get("language") if analysis.get("language") in LANGUAGES else detect_language(msg.text)
    # "Payment nahi hua" / "still not working" on the first message describes the problem; it can
    # only mean "your answer didn't help" once the bot has actually answered something.
    if intent == "not_resolved" and not await _bot_has_answered(db, conversation):
        intent = "question"

    # Feedback turns ("that didn't help", "thanks") describe the current issue, so they never re-route it.
    can_switch = intent != "company_info" and (
        conversation.domain is None or intent in ("question", "provide_info", "other")
    )
    domain = (_domain_by_code(domains, analysis.get("domain_code")) if can_switch else None) or conversation.domain
    if domain and domain.id != conversation.domain_id:
        conversation.domain_id = domain.id
        conversation.domain = domain

    if intent == "greeting" and not conversation.domain_id:
        await _reply(db, conversation, _t(lang, "greeting", company=company.name))
    elif intent == "company_info":
        await _answer_company_question(db, conversation, company, msg.text, history, lang)
    elif intent == "satisfied":
        conversation.status = ConversationStatus.RESOLVED
        conversation.unresolved_turns = 0
        await _reply(db, conversation, _t(lang, "satisfied"))
    elif intent in ("not_resolved", "human_agent"):
        await escalate(db, conversation, reason=intent, lang=lang)
    else:
        await _answer_with_rag(db, conversation, domains, msg.text, history, lang)


async def _domains(db: AsyncSession, company_id: Any) -> list[Domain]:
    return list(
        (await db.scalars(select(Domain).where(Domain.company_id == company_id, Domain.is_active.is_(True)))).all()
    )


# --------------------------------------------------------------------------- steps
async def _analyze(text: str, history: str, domains: list[Domain], current: Domain | None) -> dict[str, Any]:
    fallback = _heuristic_analysis(text, domains)
    domain_list = "\n".join(f"- {d.code}: {d.name}. {d.description or ''}" for d in domains) or "(none)"
    prompt = (
        f"Support domains:\n{domain_list}\n\n"
        f"Current domain: {current.code if current else 'none'}\n\n"
        f"Conversation so far:\n{history}\n\n"
        f"Latest customer message:\n{text}"
    )
    result = await get_gemini().generate_json(ANALYZE_SYSTEM, prompt, fallback, ANALYZE_SCHEMA)
    if result.get("intent") not in INTENTS:
        result["intent"] = "question"
    return result


async def _answer_with_rag(
    db: AsyncSession, conversation: Conversation, domains: list[Domain], text: str, history: str, lang: str = "en"
) -> None:
    if conversation.domain is None:
        conversation.unresolved_turns += 1
        topics = ", ".join(d.name for d in domains) or "billing, technical or account issues"
        if conversation.unresolved_turns >= settings.MAX_UNRESOLVED_TURNS:
            await escalate(db, conversation, reason="domain_unknown", lang=lang)
            return
        await _reply(db, conversation, _t(lang, "which_topic", topics=topics))
        return

    # Include the previous customer turn so short follow-ups ("TXN12345") still retrieve well.
    chunks = await retrieve(db, f"{history[-600:]}\n{text}", conversation.company_id, conversation.domain.id)
    fallback = _fallback_answer(chunks, lang)
    company = await db.get(Company, conversation.company_id)
    prompt = (
        f"Company profile:\n{profile_text(company) or '(not provided)'}\n\n"
        f"Domain: {conversation.domain.name}\n\n"
        f"Knowledge base context:\n{format_context(chunks)}\n\n"
        f"Conversation so far:\n{history}\n\n"
        f"Customer's latest message:\n{text}\n\n"
        f"Reply language: {LANGUAGE_INSTRUCTION.get(lang, 'English')}"
    )
    result = await get_gemini().generate_json(ANSWER_SYSTEM, prompt, fallback, ANSWER_SCHEMA, temperature=0.3)
    status = result.get("status", "answered")
    reply = (result.get("reply") or fallback["reply"]).strip()

    if status == "escalate":
        await escalate(db, conversation, reason="rag_cannot_resolve", preface=reply, lang=lang)
        return
    if status == "clarify":
        conversation.unresolved_turns += 1
        if conversation.unresolved_turns > settings.MAX_UNRESOLVED_TURNS:
            await escalate(db, conversation, reason="too_many_clarifications", lang=lang)
            return
    else:
        conversation.unresolved_turns = 0
    await _reply(db, conversation, reply, meta={"rag_status": status, "chunks": [str(c.chunk_id) for c in chunks]})


async def _answer_company_question(
    db: AsyncSession, conversation: Conversation, company: Company, text: str, history: str, lang: str
) -> None:
    """ "What is your company's name?" / "Where is your office?" - answered from the company profile,
    with no domain or knowledge-base lookup needed."""
    profile = profile_text(company)
    prompt = (
        f"Company profile:\n{profile or '(not provided)'}\n\n"
        f"Conversation so far:\n{history}\n\n"
        f"Customer's latest message:\n{text}\n\n"
        f"Reply language: {LANGUAGE_INSTRUCTION.get(lang, 'English')}"
    )
    reply = await get_gemini().generate_text(COMPANY_SYSTEM, prompt, fallback=profile[:900] or company.name)
    await _reply(db, conversation, reply, meta={"company_info": True})


async def escalate(
    db: AsyncSession, conversation: Conversation, reason: str, preface: str | None = None, lang: str = "en"
) -> Ticket:
    """Raise a ticket from the conversation and tell the customer."""
    history = await _history(db, conversation, limit=40)
    customer_lines = [line.removeprefix("Customer: ") for line in history.splitlines() if line.startswith("Customer: ")]
    fallback = {
        "issue": (customer_lines[0] if customer_lines else "Customer needs assistance")[:200],
        "summary": "\n".join(customer_lines[-6:]),
        "priority": TicketPriority.MEDIUM.value,
    }
    data = await get_gemini().generate_json(TICKET_SYSTEM, f"Conversation:\n{history}", fallback, TICKET_SCHEMA)
    priority = data.get("priority") if data.get("priority") in set(TicketPriority) else TicketPriority.MEDIUM

    conversation.status = ConversationStatus.ESCALATED
    conversation.summary = data.get("summary") or fallback["summary"]
    ticket = await ticket_service.create_ticket(
        db,
        company_id=conversation.company_id,
        customer_id=conversation.customer_id,
        conversation_id=conversation.id,
        domain_id=conversation.domain_id,
        issue=(data.get("issue") or fallback["issue"])[:1000],
        ai_summary=conversation.summary,
        priority=priority,
        source=TicketSource.WHATSAPP,
    )
    log.info("Conversation %s escalated (%s) -> ticket #%s", conversation.id, reason, ticket.number)

    lead = preface or _t(lang, "sorry")
    await _reply(
        db,
        conversation,
        f"{lead}\n\n{_t(lang, 'ticket_raised', number=ticket.number)}",
        meta={"ticket_id": str(ticket.id), "escalation_reason": reason},
    )
    return ticket


async def _handle_escalated_followup(db: AsyncSession, conversation: Conversation, text: str, lang: str) -> None:
    ticket = await db.scalar(
        select(Ticket).where(Ticket.conversation_id == conversation.id).order_by(Ticket.created_at.desc()).limit(1)
    )
    if ticket and ticket.status in OPEN_TICKET_STATUSES:
        ticket_service.add_event(db, ticket, "customer_message", None, text=text)
        await _reply(db, conversation, _t(lang, "ticket_update", number=ticket.number))
        return
    # The ticket is closed: start fresh on the same conversation.
    conversation.status = ConversationStatus.OPEN
    conversation.unresolved_turns = 0
    await db.commit()
    await _answer_with_rag(
        db, conversation, await _domains(db, conversation.company_id), text, await _history(db, conversation), lang,
    )


# --------------------------------------------------------------------------- helpers
async def _reply(db: AsyncSession, conversation: Conversation, text: str, meta: dict[str, Any] | None = None) -> None:
    customer = conversation.customer
    to = customer.whatsapp_id or customer.phone.lstrip("+")
    wa_id, status = None, "sent"
    try:
        wa_id = await whatsapp_client.send_text(to, text, await db.get(Company, conversation.company_id))
    except Exception as exc:
        log.exception("Failed to send WhatsApp message to %s", to)
        status = "failed"
        meta = {**(meta or {}), "send_error": str(exc)[:500]}
    msg = _save_message(db, conversation, MessageDirection.OUTBOUND, MessageRole.ASSISTANT, text, wa_id, meta)
    msg.delivery_status = status
    conversation.last_message_at = datetime.now(UTC)
    await db.commit()


async def send_agent_message(db: AsyncSession, conversation: Conversation, text: str, agent_id: Any) -> Message:
    customer = conversation.customer
    wa_id = await whatsapp_client.send_text(
        customer.whatsapp_id or customer.phone.lstrip("+"), text, await db.get(Company, conversation.company_id)
    )
    msg = _save_message(
        db, conversation, MessageDirection.OUTBOUND, MessageRole.AGENT, text, wa_id, {"agent_id": str(agent_id)}
    )
    msg.delivery_status = "sent"
    conversation.last_message_at = datetime.now(UTC)
    await db.commit()
    return msg


def _save_message(
    db: AsyncSession,
    conversation: Conversation,
    direction: str,
    role: str,
    content: str,
    wa_message_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Message:
    msg = Message(
        conversation_id=conversation.id, direction=direction, role=role, content=content,
        wa_message_id=wa_message_id, meta=meta or {},
    )
    db.add(msg)
    return msg


async def _get_or_create_customer(db: AsyncSession, msg: InboundMessage, company: Company) -> Customer:
    phone = normalize_phone("+" + msg.wa_id.lstrip("+")) or "+" + msg.wa_id.lstrip("+")
    customer = await db.scalar(select(Customer).where(Customer.company_id == company.id, Customer.phone == phone))
    if customer is None:
        customer = Customer(company_id=company.id, phone=phone, whatsapp_id=msg.wa_id, name=msg.profile_name)
        db.add(customer)
        await db.flush()
    else:
        customer.whatsapp_id = customer.whatsapp_id or msg.wa_id
        customer.name = customer.name or msg.profile_name
    return customer


async def _get_or_create_conversation(db: AsyncSession, customer: Customer) -> Conversation:
    cutoff = datetime.now(UTC) - timedelta(hours=settings.CONVERSATION_IDLE_HOURS)
    conversation = await db.scalar(
        select(Conversation)
        .where(
            Conversation.customer_id == customer.id,
            Conversation.channel == "whatsapp",
            Conversation.status.in_([ConversationStatus.OPEN, ConversationStatus.ESCALATED]),
            Conversation.last_message_at >= cutoff,
        )
        .order_by(Conversation.last_message_at.desc())
        .limit(1)
    )
    if conversation is None:
        conversation = Conversation(
            company_id=customer.company_id, customer_id=customer.id, channel="whatsapp", customer=customer
        )
        db.add(conversation)
        await db.flush()
    return conversation


async def _history(db: AsyncSession, conversation: Conversation, limit: int | None = None) -> str:
    limit = limit or settings.CONVERSATION_HISTORY_TURNS
    rows = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
    ).all()
    label = {MessageRole.CUSTOMER: "Customer", MessageRole.AGENT: "Agent"}
    return "\n".join(f"{label.get(m.role, 'Assistant')}: {m.content}" for m in reversed(rows))


async def _bot_has_answered(db: AsyncSession, conversation: Conversation) -> bool:
    rows = await db.scalars(
        select(Message.meta).where(Message.conversation_id == conversation.id, Message.role == MessageRole.ASSISTANT)
    )
    return any((meta or {}).get("rag_status") for meta in rows)


def _domain_by_code(domains: list[Domain], code: str | None) -> Domain | None:
    return next((d for d in domains if code and d.code == code), None)


def _heuristic_analysis(text: str, domains: list[Domain]) -> dict[str, Any]:
    if _HUMAN.search(text):
        intent = "human_agent"
    elif _COMPANY.search(text):
        intent = "company_info"
    elif _NOT_RESOLVED.search(text):
        intent = "not_resolved"
    elif _SATISFIED.search(text):
        intent = "satisfied"
    elif _GREETING.match(text):
        intent = "greeting"
    else:
        intent = "question"

    lowered = text.lower()
    best, best_score = None, 0
    for d in domains:
        terms = {d.code.lower(), d.name.lower(), *(k.lower() for k in d.keywords or [])}
        score = sum(1 for t in terms if t and t in lowered)
        if score > best_score:
            best, best_score = d, score
    return {
        "intent": intent,
        "domain_code": best.code if best else None,
        "domain_confidence": 0.5 if best else 0.0,
        "language": detect_language(text),
    }


def _fallback_answer(chunks: list[Any], lang: str = "en") -> dict[str, Any]:
    if chunks:
        return {
            "reply": f"{_t(lang, 'found')}\n\n{chunks[0].content[:600]}\n\n{_t(lang, 'resolved_q')}",
            "status": "answered",
        }
    return {"reply": _t(lang, "need_details"), "status": "clarify"}

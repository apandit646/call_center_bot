"""WhatsApp support channel.

    inbound message -> support brain (domain detection -> RAG -> answer / clarification / ticket)
                    -> reply sent from the company's own WhatsApp number

The thinking lives in services/support_brain.py and is shared with inbound phone calls
(services/voice_support.py); this module only receives and sends WhatsApp messages.
"""
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Company, Conversation, Customer, Message, Ticket
from app.models.enums import OPEN_TICKET_STATUSES, Channel, ConversationStatus, MessageDirection, MessageRole
from app.services import billing_service, support_brain, ticket_service, whatsapp_client
from app.services.phone import normalize_phone
from app.services.support_brain import BrainReply, detect_language

log = logging.getLogger(__name__)


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
    support_brain.save_message(
        db, conversation, MessageDirection.INBOUND, MessageRole.CUSTOMER, msg.text, msg.wa_message_id
    )
    conversation.last_message_at = datetime.now(UTC)
    await db.commit()

    if conversation.status == ConversationStatus.ESCALATED:
        reply = await _escalated_followup(db, conversation, msg.text, detect_language(msg.text))
    else:
        reply = await support_brain.respond(db, conversation, company, msg.text, Channel.WHATSAPP)
    await _send(db, conversation, reply)


async def _escalated_followup(db: AsyncSession, conversation: Conversation, text: str, lang: str) -> BrainReply:
    """The chat already has a ticket: more messages are added to it while it is open."""
    ticket = await db.scalar(
        select(Ticket).where(Ticket.conversation_id == conversation.id).order_by(Ticket.created_at.desc()).limit(1)
    )
    if ticket and ticket.status in OPEN_TICKET_STATUSES:
        ticket_service.add_event(db, ticket, "customer_message", None, text=text)
        return BrainReply(
            support_brain._t(lang, "ticket_update", number=ticket.number), "ticket_updated", ticket=ticket
        )
    # The ticket is closed: start fresh on the same conversation.
    conversation.status = ConversationStatus.OPEN
    conversation.unresolved_turns = 0
    await db.commit()
    return await support_brain.answer_with_rag(
        db, conversation, await support_brain.domains_of(db, conversation.company_id), text,
        await support_brain.history_of(db, conversation), lang,
    )


# --------------------------------------------------------------------------- delivery
async def _send(db: AsyncSession, conversation: Conversation, reply: BrainReply) -> None:
    """Send the brain's reply on WhatsApp and record it (a failed send is recorded as failed)."""
    customer = conversation.customer
    to = customer.whatsapp_id or customer.phone.lstrip("+")
    wa_id, status, meta = None, "sent", reply.meta
    try:
        wa_id = await whatsapp_client.send_text(to, reply.text, await db.get(Company, conversation.company_id))
    except Exception as exc:
        log.exception("Failed to send WhatsApp message to %s", to)
        status = "failed"
        meta = {**meta, "send_error": str(exc)[:500]}
    msg = support_brain.save_message(
        db, conversation, MessageDirection.OUTBOUND, MessageRole.ASSISTANT, reply.text, wa_id, meta
    )
    msg.delivery_status = status
    conversation.last_message_at = datetime.now(UTC)
    if status == "sent":
        await db.flush()
        await billing_service.record_message(db, conversation.company_id, billing_service.WHATSAPP_MESSAGE, msg.id)
    await db.commit()


async def send_agent_message(db: AsyncSession, conversation: Conversation, text: str, agent_id: Any) -> Message:
    customer = conversation.customer
    wa_id = await whatsapp_client.send_text(
        customer.whatsapp_id or customer.phone.lstrip("+"), text, await db.get(Company, conversation.company_id)
    )
    msg = support_brain.save_message(
        db, conversation, MessageDirection.OUTBOUND, MessageRole.AGENT, text, wa_id, {"agent_id": str(agent_id)}
    )
    msg.delivery_status = "sent"
    conversation.last_message_at = datetime.now(UTC)
    await db.commit()
    return msg


# --------------------------------------------------------------------------- customer & conversation
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
            Conversation.channel == Channel.WHATSAPP,
            Conversation.status.in_([ConversationStatus.OPEN, ConversationStatus.ESCALATED]),
            Conversation.last_message_at >= cutoff,
        )
        .order_by(Conversation.last_message_at.desc())
        .limit(1)
    )
    if conversation is None:
        conversation = Conversation(
            company_id=customer.company_id, customer_id=customer.id, channel=Channel.WHATSAPP, customer=customer
        )
        db.add(conversation)
        await db.flush()
    return conversation

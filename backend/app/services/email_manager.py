"""Email support channel, through the company's own SendGrid account.

    customer emails the company's support address
        -> SendGrid Inbound Parse posts it to /webhooks/email/{token}
        -> support brain (the same one as WhatsApp and phone calls) -> answer / clarification / ticket
        -> the reply is sent from the company's support address with the company's SendGrid API key

Sending has two switches and both must be on: EMAIL_ENABLED in the server settings, and "email
support" in the company's settings. While either is off, incoming email is still recorded (so
nothing is lost) but no email is sent; the reply is saved as "not sent".
"""
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Company, Conversation, Customer, Message
from app.models.enums import Channel, ConversationStatus, MessageDirection, MessageRole
from app.services import billing_service, company_service, conversation_manager, support_brain
from app.services.support_brain import BrainReply, detect_language

log = logging.getLogger(__name__)

MAX_BODY = 4000
# Where the customer's own words end and the quoted earlier email begins.
_QUOTE_START = re.compile(
    r"^\s*(On .{5,200} wrote:|-{2,}\s*Original Message\s*-{2,}|From:\s.+|_{10,}|Sent from my .+)\s*$", re.I | re.M)
# Mail no person wrote: answering it could start an endless loop between two auto-responders.
_AUTO_SENDER = re.compile(r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounce[s]?|notifications?)@", re.I)
_AUTO_HEADER = re.compile(
    r"^(auto-submitted:\s*(?!no)\S+|precedence:\s*(bulk|junk|list|auto_reply)|x-autoreply:|x-auto-response-suppress:|list-unsubscribe:)",
    re.I | re.M)


class EmailNotSent(RuntimeError):
    """Email is switched off or not connected; the message says which."""


@dataclass
class InboundEmail:
    message_id: str  # the email's Message-ID, or a digest of it when it has none
    from_address: str
    from_name: str | None
    subject: str
    text: str
    references: str = ""
    automatic: bool = False


def parse_inbound(form: dict[str, str]) -> InboundEmail | None:
    """A SendGrid Inbound Parse post -> the email. None if it has no usable sender."""
    name, address = parseaddr(form.get("from", ""))
    address = address.strip().lower()
    if "@" not in address:
        return None
    headers = form.get("headers", "")
    found = re.search(r"^message-id:\s*(<[^>\r\n]+>)", headers, re.I | re.M)
    subject = " ".join((form.get("subject") or "").split())[:300]
    body = form.get("text") or re.sub(r"<[^>]+>", " ", form.get("html") or "")
    text = clean_body(body)
    message_id = found.group(1) if found else "<" + hashlib.sha1(f"{address}|{subject}|{text}".encode()).hexdigest() + "@no-id>"
    references = re.search(r"^references:\s*(.+(?:\r?\n[ \t].+)*)", headers, re.I | re.M)
    return InboundEmail(
        message_id=message_id, from_address=address, from_name=name.strip() or None, subject=subject, text=text,
        references=" ".join(references.group(1).split()) if references else "",
        automatic=bool(_AUTO_SENDER.match(address) or _AUTO_HEADER.search(headers)),
    )


def clean_body(body: str) -> str:
    """The new part of an email: without the quoted earlier messages below it."""
    body = body.replace("\r\n", "\n")
    if m := _QUOTE_START.search(body):
        body = body[: m.start()]
    lines = [line for line in body.split("\n") if not line.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:MAX_BODY]


def _external_id(message_id: str) -> str:
    """Message-IDs can be long; the message table's id column holds 128 characters."""
    return "email:" + hashlib.sha1(message_id.encode()).hexdigest()


# --------------------------------------------------------------------------- entry point
async def handle_inbound(db: AsyncSession, email: InboundEmail, company: Company) -> None:
    """Handle one incoming email for the company whose support address received it."""
    external_id = _external_id(email.message_id)
    if await db.scalar(select(Message.id).where(Message.wa_message_id == external_id)):
        log.info("Duplicate email %s ignored", email.message_id)
        return
    if email.from_address == (company.email_address or "").lower():
        log.info("Email from the company's own support address ignored")
        return

    customer = await _get_or_create_customer(db, email, company)
    conversation, is_new = await _get_or_create_conversation(db, customer)
    # The first email's subject is part of the question ("Refund not received"); later ones just repeat "Re: ...".
    said = f"{email.subject}\n\n{email.text}".strip() if is_new and email.subject else (email.text or email.subject)
    if not said:
        return
    support_brain.save_message(
        db, conversation, MessageDirection.INBOUND, MessageRole.CUSTOMER, said, external_id,
        {"subject": email.subject, "message_id": email.message_id, "references": email.references},
    )
    conversation.last_message_at = datetime.now(UTC)
    await db.commit()
    if email.automatic:
        log.info("Automatic email from %s recorded, not answered", email.from_address)
        return

    if conversation.status == ConversationStatus.ESCALATED:
        reply = await conversation_manager._escalated_followup(db, conversation, said, detect_language(said))
    else:
        reply = await support_brain.respond(db, conversation, company, said, Channel.EMAIL)
    await _send(db, conversation, company, reply)


# --------------------------------------------------------------------------- delivery
async def send(company: Company, to: str, subject: str, body: str, in_reply_to: str = "", references: str = "") -> str:
    """Send one email through the company's SendGrid account. Raises EmailNotSent while email is off."""
    if problem := company_service.email_problem(company):
        raise EmailNotSent(problem)
    creds = company_service.email_credentials(company)
    headers = {}
    if in_reply_to:  # keeps the reply in the customer's existing thread
        headers = {"In-Reply-To": in_reply_to, "References": f"{references} {in_reply_to}".strip()}
    payload: dict[str, Any] = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": creds.address, "name": creds.from_name or company.name},
        "reply_to": {"email": creds.address},
        "subject": subject[:300],
        "content": [{"type": "text/plain", "value": body}],
    }
    if headers:
        payload["headers"] = headers
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{settings.SENDGRID_API_BASE.rstrip('/')}/v3/mail/send", json=payload,
            headers={"Authorization": f"Bearer {creds.api_key}"},
        )
    if resp.is_error:
        try:
            detail = "; ".join(e.get("message", "") for e in resp.json().get("errors", [])) or resp.text
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"SendGrid rejected the email ({resp.status_code}): {detail[:300]}")
    return resp.headers.get("X-Message-Id", "")


async def _thread(db: AsyncSession, conversation: Conversation) -> tuple[str, str, str]:
    """(subject, message id to reply to, references) of the customer's latest email in the conversation."""
    meta = await db.scalar(
        select(Message.meta)
        .where(Message.conversation_id == conversation.id, Message.role == MessageRole.CUSTOMER)
        .order_by(Message.created_at.desc()).limit(1)
    ) or {}
    subject = meta.get("subject") or "Your support request"
    return (subject if subject.lower().startswith("re:") else f"Re: {subject}"), meta.get("message_id", ""), meta.get("references", "")


async def _deliver(db: AsyncSession, conversation: Conversation, company: Company, text: str) -> tuple[str, dict[str, Any]]:
    """Send `text` to the conversation's customer. Returns (delivery status, details to keep on the message)."""
    subject, in_reply_to, references = await _thread(db, conversation)
    signature = f"\n\n{company.email_from_name or company.name}"
    try:
        await send(company, conversation.customer.email, subject, text + signature, in_reply_to, references)
        return "sent", {}
    except EmailNotSent as exc:
        log.info("Email to %s not sent: %s", conversation.customer.email, exc)
        return "not_sent", {"send_error": str(exc)}
    except Exception as exc:
        log.exception("Failed to send email to %s", conversation.customer.email)
        return "failed", {"send_error": str(exc)[:500]}


async def _send(db: AsyncSession, conversation: Conversation, company: Company, reply: BrainReply) -> None:
    status, details = await _deliver(db, conversation, company, reply.text)
    msg = support_brain.save_message(
        db, conversation, MessageDirection.OUTBOUND, MessageRole.ASSISTANT, reply.text, None, {**reply.meta, **details}
    )
    msg.delivery_status = status
    conversation.last_message_at = datetime.now(UTC)
    if status == "sent":
        await db.flush()
        await billing_service.record_message(db, conversation.company_id, billing_service.EMAIL_MESSAGE, msg.id)
    await db.commit()


async def send_agent_message(db: AsyncSession, conversation: Conversation, text: str, agent_id: Any) -> Message:
    """A person on the team replies by email. Raises (and saves nothing) if the email cannot be sent."""
    company = await db.get(Company, conversation.company_id)
    subject, in_reply_to, references = await _thread(db, conversation)
    await send(company, conversation.customer.email, subject, f"{text}\n\n{company.email_from_name or company.name}", in_reply_to, references)
    msg = support_brain.save_message(
        db, conversation, MessageDirection.OUTBOUND, MessageRole.AGENT, text, None, {"agent_id": str(agent_id)}
    )
    msg.delivery_status = "sent"
    conversation.last_message_at = datetime.now(UTC)
    await db.commit()
    return msg


# --------------------------------------------------------------------------- customer & conversation
async def _get_or_create_customer(db: AsyncSession, email: InboundEmail, company: Company) -> Customer:
    customer = await db.scalar(
        select(Customer)
        .where(Customer.company_id == company.id, func.lower(Customer.email) == email.from_address)
        .order_by(Customer.created_at).limit(1)
    )
    if customer is None:
        customer = Customer(company_id=company.id, email=email.from_address, name=email.from_name, phone=None)
        db.add(customer)
        await db.flush()
    else:
        customer.name = customer.name or email.from_name
    return customer


async def _get_or_create_conversation(db: AsyncSession, customer: Customer) -> tuple[Conversation, bool]:
    cutoff = datetime.now(UTC) - timedelta(hours=settings.CONVERSATION_IDLE_HOURS)
    conversation = await db.scalar(
        select(Conversation)
        .where(
            Conversation.customer_id == customer.id,
            Conversation.channel == Channel.EMAIL,
            Conversation.status.in_([ConversationStatus.OPEN, ConversationStatus.ESCALATED]),
            Conversation.last_message_at >= cutoff,
        )
        .order_by(Conversation.last_message_at.desc()).limit(1)
    )
    if conversation is not None:
        return conversation, False
    conversation = Conversation(company_id=customer.company_id, customer_id=customer.id, channel=Channel.EMAIL, customer=customer)
    db.add(conversation)
    await db.flush()
    return conversation, True

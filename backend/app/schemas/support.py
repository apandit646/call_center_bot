import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import ConversationStatus, TicketPriority, TicketSource, TicketStatus
from app.schemas.common import ORMModel


# ---------- customers ----------
class CustomerOut(ORMModel):
    id: uuid.UUID
    name: str | None
    phone: str
    email: str | None
    city: str | None
    whatsapp_id: str | None
    attributes: dict[str, Any]
    created_at: datetime


class CustomerCreate(BaseModel):
    name: str | None = None
    phone: str
    email: str | None = None
    city: str | None = None
    attributes: dict[str, Any] = {}


class CustomerUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    city: str | None = None
    attributes: dict[str, Any] | None = None


# ---------- domains ----------
class DomainOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    keywords: list[str]
    is_active: bool


class DomainCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    name: str
    description: str | None = None
    keywords: list[str] = []


class DomainUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    keywords: list[str] | None = None
    is_active: bool | None = None


class DomainBrief(ORMModel):
    id: uuid.UUID
    code: str
    name: str


# ---------- conversations ----------
class MessageOut(ORMModel):
    id: uuid.UUID
    direction: str
    role: str
    content: str
    delivery_status: str | None
    meta: dict[str, Any]
    created_at: datetime


class CustomerBrief(ORMModel):
    id: uuid.UUID
    name: str | None
    phone: str


class ConversationOut(ORMModel):
    id: uuid.UUID
    channel: str
    status: ConversationStatus
    customer: CustomerBrief
    domain: DomainBrief | None
    unresolved_turns: int
    summary: str | None
    last_message_at: datetime
    created_at: datetime


class SupportCallOut(ORMModel):
    """The phone call behind a conversation whose channel is "phone"."""

    id: uuid.UUID
    from_number: str | None  # None = the caller hid their number
    status: str
    outcome: str | None
    language: str | None
    ticket_id: uuid.UUID | None
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    has_recording: bool = False


class InboundCallOut(SupportCallOut):
    """A row of the inbound calls list: the call plus who called and the ticket it led to."""

    conversation_id: uuid.UUID
    customer: CustomerBrief | None = None
    ticket_number: int | None = None


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
    ticket_ids: list[uuid.UUID] = []
    call: SupportCallOut | None = None


class AgentMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


# ---------- tickets ----------
class UserBrief(ORMModel):
    id: uuid.UUID
    full_name: str
    email: str


class TicketOut(ORMModel):
    id: uuid.UUID
    number: int
    customer: CustomerBrief
    domain: DomainBrief | None
    conversation_id: uuid.UUID | None
    issue: str
    ai_summary: str | None
    priority: TicketPriority
    status: TicketStatus
    source: TicketSource
    assigned_to: UserBrief | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TicketEventOut(ORMModel):
    id: uuid.UUID
    event_type: str
    data: dict[str, Any]
    actor: UserBrief | None
    created_at: datetime


class TicketDetail(TicketOut):
    events: list[TicketEventOut]


class TicketCreate(BaseModel):
    customer_id: uuid.UUID
    domain_id: uuid.UUID | None = None
    issue: str = Field(min_length=3)
    priority: TicketPriority = TicketPriority.MEDIUM


class TicketUpdate(BaseModel):
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    domain_id: uuid.UUID | None = None


class TicketAssign(BaseModel):
    agent_id: uuid.UUID | None


class TicketComment(BaseModel):
    text: str = Field(min_length=1, max_length=5000)

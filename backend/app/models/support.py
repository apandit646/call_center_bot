import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Identity, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyOwned, Timestamps, UUIDPk
from app.models.customers import Customer
from app.models.enums import ConversationStatus, TicketPriority, TicketSource, TicketStatus
from app.models.identity import User


class Domain(UUIDPk, CompanyOwned, Timestamps, Base):
    __tablename__ = "domains"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_domains_company_code"),)

    code: Mapped[str] = mapped_column(String(64))  # e.g. "billing"
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Conversation(UUIDPk, CompanyOwned, Timestamps, Base):
    __tablename__ = "conversations"

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="whatsapp")
    status: Mapped[str] = mapped_column(String(32), default=ConversationStatus.OPEN, index=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("domains.id"))
    unresolved_turns: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    customer: Mapped[Customer] = relationship(lazy="joined")
    domain: Mapped[Domain | None] = relationship(lazy="joined")


class Message(UUIDPk, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    wa_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    delivery_status: Mapped[str | None] = mapped_column(String(32))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SupportCall(UUIDPk, CompanyOwned, Timestamps, Base):
    """One inbound phone call answered by the voice agent. What was said is the linked conversation
    (channel "phone"); this row holds the call itself and the per-call turn state."""

    __tablename__ = "support_calls"

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), unique=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    provider_call_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    from_number: Mapped[str | None] = mapped_column(String(32))  # None = caller id withheld
    to_number: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="in_progress", index=True)  # CallStatus values
    language: Mapped[str | None] = mapped_column(String(16))  # follows the caller, like outbound calls
    outcome: Mapped[str | None] = mapped_column(String(32))  # CallOutcome
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    recording_url: Mapped[str | None] = mapped_column(String(1024))
    recording_path: Mapped[str | None] = mapped_column(String(512))
    # Turn state (turn number, silences, which ticket is being discussed...). Kept on the row, which
    # every webhook locks, so a call never depends on Redis.
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Ticket(UUIDPk, CompanyOwned, Timestamps, Base):
    __tablename__ = "tickets"

    number: Mapped[int] = mapped_column(Integer, Identity(start=10001), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"))
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("domains.id"))
    issue: Mapped[str] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(16), default=TicketPriority.MEDIUM, index=True)
    status: Mapped[str] = mapped_column(String(32), default=TicketStatus.OPEN, index=True)
    source: Mapped[str] = mapped_column(String(32), default=TicketSource.WHATSAPP)
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(lazy="joined")
    domain: Mapped[Domain | None] = relationship(lazy="joined")
    assigned_to: Mapped[User | None] = relationship(lazy="joined")


class KnowledgeGap(UUIDPk, CompanyOwned, Timestamps, Base):
    """A question the assistant could not answer from the knowledge base, on any channel. The list of
    these tells the company which documents to add."""

    __tablename__ = "knowledge_gaps"

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    # no_match = nothing relevant was found; cannot_answer = found text did not answer it (a ticket was raised)
    reason: Mapped[str] = mapped_column(String(32), default="no_match")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | resolved
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    domain: Mapped[Domain | None] = relationship(lazy="joined")


class Callback(UUIDPk, CompanyOwned, Timestamps, Base):
    """A call back the customer asked for on a call ("call me at 5 pm"). The scheduler dials it when
    it is due; the AI agent then talks to them like on an inbound call."""

    __tablename__ = "callbacks"

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), index=True)
    phone: Mapped[str] = mapped_column(String(32))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str | None] = mapped_column(Text)  # what they wanted, in a line, for the agent and the portal
    language: Mapped[str | None] = mapped_column(String(16))  # the language the caller was speaking
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)  # CallbackStatus
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    # The conversation it was asked in, and the call that was placed for it.
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"))
    support_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_calls.id", ondelete="SET NULL")
    )
    provider_call_id: Mapped[str | None] = mapped_column(String(128), unique=True)

    customer: Mapped[Customer] = relationship(lazy="joined")


class TicketEvent(UUIDPk, Base):
    __tablename__ = "ticket_events"

    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(32))  # created | assigned | status_changed | priority_changed | comment
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    actor: Mapped[User | None] = relationship(lazy="joined")


class AgentAssignment(UUIDPk, Base):
    __tablename__ = "agent_assignments"

    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    assigned_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

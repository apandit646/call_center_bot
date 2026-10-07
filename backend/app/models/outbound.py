import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Time, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyOwned, Timestamps, UUIDPk
from app.models.company import Company
from app.models.customers import Customer
from app.models.enums import (
    AnswerStatus,
    AnswerType,
    CallStatus,
    CampaignStatus,
    ContactStatus,
    ImportStatus,
    OfferingType,
    ScheduledCallStatus,
)

# {company} is the registered company name; {name} and any Excel column come from the contact.
DEFAULT_GREETING = (
    "Hello {name}, this is an automated call from {company}. We have a few quick questions for you."
)
DEFAULT_CLOSING = "Thank you for your time. Have a great day!"


class Campaign(UUIDPk, CompanyOwned, Timestamps, Base):
    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    # What the campaign is about, so the AI can answer "which product?" / "what does it do?".
    offering_type: Mapped[str] = mapped_column(String(16), default=OfferingType.GENERAL, server_default="general")
    offering_name: Mapped[str | None] = mapped_column(String(255))
    offering_details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=CampaignStatus.DRAFT, index=True)
    greeting_script: Mapped[str] = mapped_column(Text, default=DEFAULT_GREETING)
    closing_script: Mapped[str] = mapped_column(Text, default=DEFAULT_CLOSING)
    # Primary voice language (Sarvam code): every call starts in it.
    language: Mapped[str] = mapped_column(String(16), default="en-IN")
    # Languages the call switches to when the customer answers in them, e.g. ["hi-IN"].
    alt_languages: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["hi-IN"])
    # Greeting/closing per alternate language: {"hi-IN": {"greeting": "...", "closing": "..."}}
    translations: Mapped[dict[str, dict[str, str]]] = mapped_column(JSON, default=dict)
    # True: the AI holds a natural conversation (voice_agent); False: it reads the script (voice_qa).
    conversational: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Personality & conversation rules for the conversational agent; empty = the default persona.
    agent_prompt: Mapped[str | None] = mapped_column(Text)
    # Scheduling
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    call_window_start: Mapped[time] = mapped_column(Time, default=time(9, 0))
    call_window_end: Mapped[time] = mapped_column(Time, default=time(19, 0))
    # Retry / concurrency rules
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, default=5)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    retry_delay_minutes: Mapped[int] = mapped_column(Integer, default=60)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A campaign is used again and again; each use is a run (services/campaign_runs.py). status,
    # start_at and completed_at above always describe the current run.
    current_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    # Loaded with the campaign: every call needs the company's name, profile, limits and telephony account.
    company: Mapped[Company] = relationship(lazy="joined")
    # The questions asked now. Retired ones (replaced after they collected answers) stay in the table
    # for the old runs' results but are not part of the script any more.
    questions: Mapped[list["CampaignQuestion"]] = relationship(
        back_populates="campaign", order_by="CampaignQuestion.order_index", lazy="selectin", cascade="all, delete-orphan",
        primaryjoin="and_(CampaignQuestion.campaign_id == Campaign.id, CampaignQuestion.is_active)",
    )


class CampaignQuestion(UUIDPk, Timestamps, Base):
    __tablename__ = "campaign_questions"

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(64))  # stable slug for analytics, e.g. "satisfaction"
    text: Mapped[str] = mapped_column(Text)
    answer_type: Mapped[str] = mapped_column(String(16), default=AnswerType.TEXT)
    options: Mapped[list[str]] = mapped_column(JSON, default=list)  # for CHOICE
    # Question text per alternate language: {"hi-IN": "..."}; missing = speak the primary text.
    translations: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    extraction_hint: Mapped[str | None] = mapped_column(Text)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")  # False = retired

    campaign: Mapped[Campaign] = relationship(back_populates="questions")


class CampaignRun(UUIDPk, Timestamps, Base):
    """One use of a campaign: a list of people called with the campaign's script."""

    __tablename__ = "campaign_runs"
    __table_args__ = (UniqueConstraint("campaign_id", "number", name="uq_campaign_runs_number"),)

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="first")  # first | new_list | same_contacts | not_reached
    # Filled in when the run is closed by the next one; the current run's are the campaign's own.
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # the script as it was asked
    # With a new list: leave out people who completed an earlier run.
    skip_completed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    skipped_dnc: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    skipped_completed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ContactImport(UUIDPk, Timestamps, Base):
    """An uploaded Excel/CSV awaiting admin confirmation (the preview step)."""

    __tablename__ = "contact_imports"

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default=ImportStatus.PREVIEW)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_data: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    invalid_data: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))


class CampaignContact(UUIDPk, Timestamps, Base):
    __tablename__ = "campaign_contacts"
    # The same person can be called in several runs of a campaign, but only once per run.
    __table_args__ = (UniqueConstraint("run_id", "customer_id", name="uq_campaign_contacts_run_customer"),)

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign_runs.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=ContactStatus.PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # extra Excel columns
    # "Call me at 5 pm": the time the customer asked to be called back at, until that call is placed.
    callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    callback_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # how often they asked

    customer: Mapped[Customer] = relationship(lazy="joined")


class ScheduledCall(UUIDPk, Timestamps, Base):
    __tablename__ = "scheduled_calls"

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    campaign_contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign_contacts.id", ondelete="CASCADE"), index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=ScheduledCallStatus.PENDING, index=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # attempt = a first call or an ordinary retry; callback_time = the customer said "call me at 5 pm";
    # callback_auto = the customer was busy but gave no time, so they are tried again after the retry delay.
    kind: Mapped[str] = mapped_column(String(16), default="attempt", server_default="attempt", index=True)


class Call(UUIDPk, Timestamps, Base):
    __tablename__ = "calls"

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    campaign_contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign_contacts.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), index=True)
    scheduled_call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scheduled_calls.id"))
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    provider: Mapped[str] = mapped_column(String(32))
    provider_call_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(32), default=CallStatus.INITIATED, index=True)
    current_question_index: Mapped[int] = mapped_column(Integer, default=0)
    # Language the call is currently speaking; follows the customer (see voice_qa.choose_language).
    language: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    recording_url: Mapped[str | None] = mapped_column(String(1024))
    recording_path: Mapped[str | None] = mapped_column(String(512))
    all_questions_answered: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)

    customer: Mapped[Customer] = relationship(lazy="joined")


class CallTranscript(UUIDPk, Base):
    """One row per utterance, in order."""

    __tablename__ = "call_transcripts"

    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    turn_index: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign_questions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallAnswer(UUIDPk, Timestamps, Base):
    __tablename__ = "call_answers"
    __table_args__ = (UniqueConstraint("call_id", "question_id"),)

    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign_questions.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    raw_answer: Mapped[str | None] = mapped_column(Text)
    normalized_answer: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default=AnswerStatus.ANSWERED)

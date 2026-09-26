"""String enums. Stored as plain VARCHAR so adding values never needs a PG enum migration."""
from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    AGENT = "agent"


# ---- WhatsApp support ----
class ConversationStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    CLOSED = "closed"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageRole(StrEnum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    AGENT = "agent"
    SYSTEM = "system"


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PENDING_CUSTOMER = "pending_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


OPEN_TICKET_STATUSES = {TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.PENDING_CUSTOMER}


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketSource(StrEnum):
    WHATSAPP = "whatsapp"
    MANUAL = "manual"


# ---- RAG ----
class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


# ---- Outbound Q&A ----
class CampaignStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AnswerType(StrEnum):
    TEXT = "text"
    YES_NO = "yes_no"
    RATING = "rating"
    NUMBER = "number"
    CHOICE = "choice"


class ImportStatus(StrEnum):
    PREVIEW = "preview"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class ContactStatus(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    DO_NOT_CALL = "do_not_call"


class ScheduledCallStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    CANCELLED = "cancelled"


class CallStatus(StrEnum):
    INITIATED = "initiated"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_CALL_STATUSES = {
    CallStatus.COMPLETED,
    CallStatus.NO_ANSWER,
    CallStatus.BUSY,
    CallStatus.FAILED,
    CallStatus.CANCELED,
}
ACTIVE_CALL_STATUSES = {CallStatus.INITIATED, CallStatus.RINGING, CallStatus.IN_PROGRESS}


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    UNCLEAR = "unclear"
    SKIPPED = "skipped"


class Speaker(StrEnum):
    AI = "ai"
    CUSTOMER = "customer"


# ---- System ----
class WebhookStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"

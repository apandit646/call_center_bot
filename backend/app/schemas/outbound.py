import uuid
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from app.models.enums import AnswerType, CallStatus, CampaignStatus, ContactStatus, ImportStatus, OfferingType
from app.schemas.common import ORMModel
from app.schemas.support import CustomerBrief
from app.services.speech.sarvam import TTS_LANGUAGES


def _check_languages(codes: list[str]) -> list[str]:
    bad = [c for c in codes if c not in TTS_LANGUAGES]
    if bad:
        raise ValueError(f"Unsupported voice language(s) {bad}; use one of {sorted(TTS_LANGUAGES)}")
    return codes


class QuestionIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    text: str = Field(min_length=3)
    answer_type: AnswerType = AnswerType.TEXT
    options: list[str] = []
    extraction_hint: str | None = None
    is_required: bool = True
    translations: dict[str, str] = {}  # {"hi-IN": "..."}; blank ones are auto-translated

    @field_validator("translations")
    @classmethod
    def _valid_translation_langs(cls, v: dict[str, str]) -> dict[str, str]:
        _check_languages(list(v))
        return {k: t.strip() for k, t in v.items() if t and t.strip()}


class QuestionOut(ORMModel):
    id: uuid.UUID
    order_index: int
    key: str
    text: str
    answer_type: AnswerType
    options: list[str]
    extraction_hint: str | None
    is_required: bool
    translations: dict[str, str] = {}


class CampaignSettings(BaseModel):
    description: str | None = None
    # What the campaign is about; the AI answers "which product?" / "what does it do?" from these.
    offering_type: OfferingType | None = None
    offering_name: str | None = Field(default=None, max_length=255)
    offering_details: str | None = Field(default=None, max_length=6000)
    greeting_script: str | None = None
    closing_script: str | None = None
    language: str | None = None
    alt_languages: list[str] | None = None  # switch to these when the customer speaks them
    conversational: bool | None = None  # natural AI conversation instead of a fixed script
    agent_prompt: str | None = Field(default=None, max_length=8000)  # personality & rules; "" = default
    translations: dict[str, dict[str, str]] | None = None  # {"hi-IN": {"greeting": ..., "closing": ...}}
    timezone: str | None = None
    call_window_start: time | None = None
    call_window_end: time | None = None
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=200)
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    retry_delay_minutes: int | None = Field(default=None, ge=1, le=10080)

    @field_validator("language")
    @classmethod
    def _valid_language(cls, v: str | None) -> str | None:
        if v is not None:
            _check_languages([v])
        return v

    @field_validator("alt_languages")
    @classmethod
    def _valid_alt_languages(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _check_languages(list(dict.fromkeys(v)))

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                ZoneInfo(v)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"Unknown timezone '{v}'") from exc
        return v


class CampaignCreate(CampaignSettings):
    name: str = Field(min_length=1, max_length=255)
    questions: list[QuestionIn] = []


class CampaignUpdate(CampaignSettings):
    name: str | None = Field(default=None, min_length=1, max_length=255)


class CampaignOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    offering_type: OfferingType = OfferingType.GENERAL
    offering_name: str | None = None
    offering_details: str | None = None
    status: CampaignStatus
    greeting_script: str
    closing_script: str
    language: str
    alt_languages: list[str]
    translations: dict[str, dict[str, str]]
    conversational: bool = True
    agent_prompt: str | None = None
    start_at: datetime | None
    timezone: str
    call_window_start: time
    call_window_end: time
    max_concurrent_calls: int
    max_attempts: int
    retry_delay_minutes: int
    completed_at: datetime | None
    created_at: datetime
    questions: list[QuestionOut]


class CampaignListItem(CampaignOut):
    contact_count: int = 0
    completed_count: int = 0


class ScheduleRequest(BaseModel):
    start_at: datetime | None = None  # None = start now
    settings: CampaignSettings | None = None


class ImportPreview(ORMModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    filename: str
    status: ImportStatus
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    valid_sample: list[dict[str, Any]] = []
    invalid_data: list[dict[str, Any]] = []
    created_at: datetime


class ContactOut(ORMModel):
    id: uuid.UUID
    customer: CustomerBrief
    status: ContactStatus
    attempts: int
    last_attempt_at: datetime | None
    variables: dict[str, Any]


class CallOut(ORMModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    customer: CustomerBrief
    attempt_number: int
    provider: str
    provider_call_id: str | None
    status: CallStatus
    language: str | None = None
    started_at: datetime | None
    answered_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    all_questions_answered: bool
    has_recording: bool = False
    error: str | None
    created_at: datetime


class TranscriptOut(ORMModel):
    turn_index: int
    speaker: str
    text: str
    question_id: uuid.UUID | None
    created_at: datetime


class AnswerOut(ORMModel):
    question_id: uuid.UUID
    raw_answer: str | None
    normalized_answer: dict[str, Any]
    confidence: float | None
    status: str


class CallDetail(CallOut):
    transcript: list[TranscriptOut]
    answers: list[AnswerOut]


class ResultRow(BaseModel):
    contact_id: uuid.UUID
    customer: CustomerBrief
    status: ContactStatus
    attempts: int
    call_id: uuid.UUID | None
    answers: dict[str, Any]  # question key -> normalized value

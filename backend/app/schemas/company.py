import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel
from app.services.speech.sarvam import TTS_LANGUAGES


def _clean(v: str | None) -> str | None:
    """Blank text means "not set"."""
    return (v.strip() or None) if isinstance(v, str) else v


class _Profile(BaseModel):
    phone: str | None = Field(default=None, max_length=32)
    website: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=2000)
    industry: str | None = Field(default=None, max_length=128)
    # What the AI may tell customers: who the company is and what it sells.
    description: str | None = Field(default=None, max_length=6000)
    products_services: str | None = Field(default=None, max_length=6000)
    timezone: str | None = None

    _strip = field_validator(
        "phone", "website", "address", "industry", "description", "products_services", mode="before"
    )(_clean)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"Unknown timezone '{v}'") from exc
        return v


class _Limits(BaseModel):
    """Set by the platform admin. null = unlimited."""

    max_campaigns: int | None = Field(default=None, ge=0, le=100_000)
    daily_call_limit: int | None = Field(default=None, ge=0, le=10_000_000)
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=200)


class _Integrations(BaseModel):
    """The company's own telephony and WhatsApp accounts.

    Secrets (auth token, access token, app secret) are write-only: omitted = keep the stored value,
    "" = remove it. They are encrypted at rest and never returned by the API.
    """

    telephony_provider: Literal["twilio", "exotel"] | None = None  # one calling provider at a time
    twilio_account_sid: str | None = Field(default=None, max_length=64)
    twilio_auth_token: str | None = Field(default=None, max_length=255)
    twilio_from_number: str | None = Field(default=None, max_length=32)
    exotel_api_key: str | None = Field(default=None, max_length=128)
    exotel_api_token: str | None = Field(default=None, max_length=255)
    exotel_account_sid: str | None = Field(default=None, max_length=64)
    exotel_subdomain: Literal["api.in.exotel.com", "api.exotel.com"] | None = None  # India | Singapore
    exotel_number: str | None = Field(default=None, max_length=32)  # the ExoPhone
    exotel_app_id: str | None = Field(default=None, max_length=32)  # the flow built in Exotel's App Bazaar
    whatsapp_phone_number_id: str | None = Field(default=None, max_length=64)
    whatsapp_access_token: str | None = Field(default=None, max_length=1024)
    whatsapp_app_secret: str | None = Field(default=None, max_length=255)

    _strip_ids = field_validator(
        "twilio_account_sid", "twilio_from_number", "whatsapp_phone_number_id", "exotel_api_key", "exotel_account_sid",
        "exotel_number", "exotel_app_id", mode="before",
    )(_clean)

    @field_validator("exotel_number")
    @classmethod
    def _valid_exophone(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.replace(" ", "").replace("-", "")
            if not (v.lstrip("+").isdigit() and 8 <= len(v.lstrip("+")) <= 15):
                raise ValueError("Enter the ExoPhone number as digits, e.g. 08047112233 or +918047112233")
        return v

    @field_validator("exotel_app_id")
    @classmethod
    def _valid_app_id(cls, v: str | None) -> str | None:
        if v is not None and not v.isdigit():
            raise ValueError("The App ID is the number of your flow in Exotel's App Bazaar")
        return v

    @field_validator("twilio_account_sid")
    @classmethod
    def _valid_sid(cls, v: str | None) -> str | None:
        if v is not None and not (v.startswith("AC") and len(v) == 34):
            raise ValueError("A Twilio Account SID starts with 'AC' and is 34 characters long")
        return v

    @field_validator("twilio_from_number")
    @classmethod
    def _valid_from_number(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.replace(" ", "").replace("-", "")
            if not (v.startswith("+") and v[1:].isdigit() and 8 <= len(v) <= 16):
                raise ValueError("Use the full international format, e.g. +14155550123")
        return v

    @field_validator("whatsapp_phone_number_id")
    @classmethod
    def _valid_phone_number_id(cls, v: str | None) -> str | None:
        if v is not None and not v.isdigit():
            raise ValueError("The WhatsApp Phone Number ID is the numeric id from Meta, not the phone number")
        return v


class _Inbound(BaseModel):
    """Inbound support calls on the company's Twilio number."""

    inbound_enabled: bool | None = None  # switching on points the Twilio number at our inbound webhook
    inbound_language: str | None = None  # the language calls start in; they then follow the caller
    inbound_greeting: str | None = Field(default=None, max_length=600)  # blank = the default greeting

    _strip_greeting = field_validator("inbound_greeting", mode="before")(_clean)

    @field_validator("inbound_language")
    @classmethod
    def _valid_language(cls, v: str | None) -> str | None:
        if v is not None and v not in TTS_LANGUAGES:
            raise ValueError(f"Unsupported voice language '{v}'; use one of {sorted(TTS_LANGUAGES)}")
        return v


class CompanyRegister(_Profile, _Limits):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr  # becomes the company admin's login
    admin_name: str | None = Field(default=None, max_length=255)


class CompanyAdminUpdate(_Profile, _Limits, _Integrations, _Inbound):
    """Platform admin: everything, including limits and suspending the company."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    is_active: bool | None = None
    inbound_allowed: bool | None = None  # whether this company's plan includes inbound calls


class CompanySelfUpdate(_Profile, _Integrations, _Inbound):
    """Company admin: its own profile and channel accounts, never its limits."""

    name: str | None = Field(default=None, min_length=1, max_length=255)


class CompanyBrief(ORMModel):
    id: uuid.UUID
    name: str


class CompanyOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None
    phone: str | None
    website: str | None
    address: str | None
    industry: str | None
    description: str | None
    products_services: str | None
    timezone: str
    is_active: bool
    max_campaigns: int | None
    daily_call_limit: int | None
    max_concurrent_calls: int | None
    # Channel accounts: identifiers are shown, secrets only as "is it set".
    twilio_account_sid: str | None = None
    twilio_from_number: str | None = None
    twilio_auth_token_set: bool = False
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token_set: bool = False
    whatsapp_app_secret_set: bool = False
    telephony_provider: str  # twilio | exotel
    exotel_api_key: str | None = None
    exotel_account_sid: str | None = None
    exotel_subdomain: str | None = None
    exotel_number: str | None = None
    exotel_app_id: str | None = None
    exotel_api_token_set: bool = False
    # What the company pastes into its Exotel flow (admins only, once Exotel is chosen).
    exotel_stream_url: str | None = None  # Voicebot applet; contains the login
    exotel_passthru_url: str | None = None  # Passthru applet after it
    calls_connected: bool  # the chosen provider's account is fully set up
    whatsapp_connected: bool
    inbound_allowed: bool  # set by the platform admin
    inbound_enabled: bool  # set by the company
    inbound_active: bool  # calls to the number are answered right now
    inbound_problem: str | None = None  # why inbound cannot be switched on (None = it can)
    inbound_language: str
    inbound_greeting: str | None = None
    whatsapp_webhook_url: str
    usage: dict[str, Any] | None = None
    created_at: datetime


class Credentials(BaseModel):
    """Shown once, right after they are generated. The password is not stored in clear."""

    email: str
    password: str


class CompanyRegistered(BaseModel):
    company: CompanyOut
    credentials: Credentials


class CredentialsRequest(BaseModel):
    email: EmailStr | None = None  # default: the company email / its first admin


class PlatformSummary(BaseModel):
    companies: int
    active_companies: int
    users: int
    campaigns: int
    running_campaigns: int
    active_calls: int
    calls_last_24h: int

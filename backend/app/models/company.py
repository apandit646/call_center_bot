from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPk


class Company(UUIDPk, Timestamps, Base):
    """A tenant. Everything a company owns (users, customers, campaigns, chats...) carries its id."""

    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    website: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(String(128))
    # What the AI may tell customers about the company (voice agent + WhatsApp bot).
    description: Mapped[str | None] = mapped_column(Text)
    products_services: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")  # "today" for the daily call limit
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # Limits set by the platform admin; None = unlimited.
    max_campaigns: Mapped[int | None] = mapped_column(Integer)
    daily_call_limit: Mapped[int | None] = mapped_column(Integer)
    max_concurrent_calls: Mapped[int | None] = mapped_column(Integer)

    # The company's own Twilio account and caller id. Secrets are encrypted (services/crypto.py).
    twilio_account_sid: Mapped[str | None] = mapped_column(String(64))
    twilio_auth_token_enc: Mapped[str | None] = mapped_column(Text)
    # Unique: an inbound call is routed to its company by the number that was dialled.
    twilio_from_number: Mapped[str | None] = mapped_column(String(32), unique=True)

    # Inbound support calls on that same number. Both switches must be on: the platform admin allows
    # the feature, the company turns it on (which points its Twilio number at our inbound webhook).
    inbound_allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    inbound_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    inbound_language: Mapped[str] = mapped_column(String(16), default="en-IN", server_default="en-IN")
    inbound_greeting: Mapped[str | None] = mapped_column(Text)  # empty = the default greeting

    # The company's own WhatsApp Cloud API number. Inbound webhooks are routed by phone_number_id.
    whatsapp_phone_number_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    whatsapp_access_token_enc: Mapped[str | None] = mapped_column(Text)
    whatsapp_app_secret_enc: Mapped[str | None] = mapped_column(Text)  # of the company's Meta app; required

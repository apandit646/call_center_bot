"""What the platform charges each company, and the bills it sends them.

    BillingPlan   one per company: its prices
    UsageRecord   one per finished call (or charged message), priced at the moment it happened
    Invoice       one per billing month (28th to 27th), with its lines; paid through Stripe
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Identity, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CompanyOwned, Timestamps, UUIDPk

Money = Numeric(12, 2)
Rate = Numeric(10, 4)  # a price per minute or per message can be a fraction of the smallest coin


class BillingPlan(UUIDPk, CompanyOwned, Timestamps, Base):
    """A company's prices, set by the platform admin. No plan (or billing switched off) = not billed."""

    __tablename__ = "billing_plans"
    __table_args__ = (UniqueConstraint("company_id", name="uq_billing_plans_company"),)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR")
    monthly_fee: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    # Calls are charged per started minute.
    outbound_per_minute: Mapped[Decimal] = mapped_column(Rate, default=0, server_default="0")  # campaign calls
    inbound_per_minute: Mapped[Decimal] = mapped_column(Rate, default=0, server_default="0")  # inbound calls and callbacks
    # Replies the assistant sends. 0 = not charged.
    whatsapp_per_message: Mapped[Decimal] = mapped_column(Rate, default=0, server_default="0")
    email_per_message: Mapped[Decimal] = mapped_column(Rate, default=0, server_default="0")
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=18, server_default="18")
    tax_label: Mapped[str] = mapped_column(String(32), default="GST", server_default="GST")
    billing_email: Mapped[str | None] = mapped_column(String(255))  # where invoices are sent; empty = the company's email
    tax_id: Mapped[str | None] = mapped_column(String(64))  # the company's GSTIN, printed on its invoices
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64))


class UsageRecord(UUIDPk, CompanyOwned, Base):
    """One chargeable thing. The price is copied in when it happens, so changing a plan later never
    changes what past usage costs."""

    __tablename__ = "usage_records"
    # One record per call / message: recording the same call again updates it instead of charging twice.
    __table_args__ = (UniqueConstraint("kind", "ref_id", name="uq_usage_records_ref"),)

    kind: Mapped[str] = mapped_column(String(32), index=True)  # UsageKind
    ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))  # the call or message
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    seconds: Mapped[int | None] = mapped_column(Integer)  # the call's real length (calls only)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # minutes charged, or 1 message
    unit_price: Mapped[Decimal] = mapped_column(Rate)
    amount: Mapped[Decimal] = mapped_column(Money)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), index=True)


class Invoice(UUIDPk, CompanyOwned, Timestamps, Base):
    __tablename__ = "invoices"

    number: Mapped[int] = mapped_column(Integer, Identity(start=1001), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="monthly")  # monthly | manual
    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date)  # inclusive
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # InvoiceStatus
    currency: Mapped[str] = mapped_column(String(3))
    subtotal: Mapped[Decimal] = mapped_column(Money)
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    tax_label: Mapped[str] = mapped_column(String(32), default="GST")
    tax_amount: Mapped[Decimal] = mapped_column(Money)
    total: Mapped[Decimal] = mapped_column(Money)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[date] = mapped_column(Date)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_via: Mapped[str | None] = mapped_column(String(16))  # stripe | offline
    note: Mapped[str | None] = mapped_column(Text)
    # Stripe's copy of the invoice: where the company pays, and the PDF.
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    hosted_url: Mapped[str | None] = mapped_column(String(1024))
    pdf_url: Mapped[str | None] = mapped_column(String(1024))
    stripe_error: Mapped[str | None] = mapped_column(Text)  # why it is not on Stripe yet (None = it is, or not needed)

    lines: Mapped[list["InvoiceLine"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="InvoiceLine.position"
    )


class InvoiceLine(UUIDPk, Base):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(32))  # a UsageKind, "monthly_fee" or "adjustment"
    description: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_price: Mapped[Decimal] = mapped_column(Rate)
    amount: Mapped[Decimal] = mapped_column(Money)

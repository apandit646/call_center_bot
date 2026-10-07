"""Billing: price what each company uses, and bill it once a month through Stripe.

    a call ends            -> a UsageRecord, priced with the company's plan at that moment
    the 28th of the month  -> one Invoice per company for the month that just closed (28th .. 27th)
                              = monthly fee + usage + tax, sent through Stripe with a "Pay" link
    Stripe says "paid"     -> the invoice is paid
    still unpaid after the due date and the grace days
                           -> the company's campaigns and callbacks are held until it pays
                              (inbound calls, WhatsApp and email keep working)

A company with no plan, or with billing switched off, is never billed and never held.
BILLING_ENABLED=false in the server settings switches all of it off.
"""
import calendar
import logging
import math
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import BillingPlan, Company, Invoice, InvoiceLine, UsageRecord
from app.services import stripe_client

log = logging.getLogger(__name__)

OUTBOUND_CALL, INBOUND_CALL, CALLBACK_CALL = "outbound_call", "inbound_call", "callback_call"
WHATSAPP_MESSAGE, EMAIL_MESSAGE = "whatsapp_message", "email_message"
CALL_KINDS = (OUTBOUND_CALL, INBOUND_CALL, CALLBACK_CALL)
KINDS = (*CALL_KINDS, WHATSAPP_MESSAGE, EMAIL_MESSAGE)
LABELS = {
    OUTBOUND_CALL: "Campaign calls", INBOUND_CALL: "Inbound calls", CALLBACK_CALL: "Callback calls",
    WHATSAPP_MESSAGE: "WhatsApp replies by the assistant", EMAIL_MESSAGE: "Email replies by the assistant",
}
OPEN, PAID, VOID = "open", "paid", "void"
CENT = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def billed_minutes(seconds: int | None) -> int:
    """Calls are charged per started minute: 1 s -> 1, 60 s -> 1, 61 s -> 2."""
    return math.ceil(seconds / 60) if seconds and seconds > 0 else 0


def _tz(company: Company) -> ZoneInfo:
    try:
        return ZoneInfo(company.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def today_of(company: Company) -> date:
    return datetime.now(_tz(company)).date()


def _billing_day(year: int, month: int) -> date:
    return date(year, month, min(settings.BILLING_DAY, calendar.monthrange(year, month)[1]))


def period_of(day: date) -> tuple[date, date]:
    """The billing month `day` falls in: from the billing day (the 28th) to the day before the next one."""
    start = _billing_day(day.year, day.month)
    if day < start:
        year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
        start = _billing_day(year, month)
    year, month = (start.year, start.month + 1) if start.month < 12 else (start.year + 1, 1)
    return start, _billing_day(year, month) - timedelta(days=1)


def _bounds(company: Company, start: date, end: date) -> tuple[datetime, datetime]:
    """The period's first moment and the first moment after it, in UTC."""
    tz = _tz(company)
    return (
        datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC),
    )


async def plan_of(db: AsyncSession, company_id: uuid.UUID) -> BillingPlan | None:
    return await db.scalar(select(BillingPlan).where(BillingPlan.company_id == company_id))


def billing_on(plan: BillingPlan | None) -> bool:
    return bool(settings.BILLING_ENABLED and plan is not None and plan.enabled)


# --------------------------------------------------------------------------- usage
async def record_call(
    db: AsyncSession, company_id: uuid.UUID, kind: str, ref_id: uuid.UUID, seconds: int | None, occurred_at: datetime | None
) -> None:
    """Price a finished call. Safe to call again for the same call (the provider often reports the
    final length a little later): the record is updated, never doubled. Does not commit."""
    minutes = billed_minutes(seconds)
    if not minutes:
        return
    plan = await plan_of(db, company_id)
    if not billing_on(plan):
        return
    record = await db.scalar(select(UsageRecord).where(UsageRecord.kind == kind, UsageRecord.ref_id == ref_id))
    if record is not None:
        if record.invoice_id is None:  # an invoiced call is final
            record.seconds, record.quantity = seconds, Decimal(minutes)
            record.amount = money(record.unit_price * minutes)
        return
    rate = plan.outbound_per_minute if kind == OUTBOUND_CALL else plan.inbound_per_minute
    db.add(UsageRecord(
        company_id=company_id, kind=kind, ref_id=ref_id, occurred_at=occurred_at or datetime.now(UTC), seconds=seconds,
        quantity=Decimal(minutes), unit_price=rate, amount=money(rate * minutes),
    ))


async def record_message(db: AsyncSession, company_id: uuid.UUID, kind: str, ref_id: uuid.UUID) -> None:
    """Price one reply the assistant sent, if the plan charges for them. Does not commit."""
    plan = await plan_of(db, company_id)
    if not billing_on(plan):
        return
    rate = plan.whatsapp_per_message if kind == WHATSAPP_MESSAGE else plan.email_per_message
    if rate <= 0 or await db.scalar(select(UsageRecord.id).where(UsageRecord.kind == kind, UsageRecord.ref_id == ref_id)):
        return
    db.add(UsageRecord(
        company_id=company_id, kind=kind, ref_id=ref_id, occurred_at=datetime.now(UTC), quantity=Decimal(1),
        unit_price=rate, amount=money(rate),
    ))


async def cost_of(db: AsyncSession, ref_id: uuid.UUID) -> dict[str, Any] | None:
    """What one call cost the company, for its detail page. None if it was not charged."""
    record = await db.scalar(select(UsageRecord).where(UsageRecord.ref_id == ref_id, UsageRecord.kind.in_(CALL_KINDS)))
    if record is None:
        return None
    return {"amount": float(record.amount), "minutes": int(record.quantity), "per_minute": float(record.unit_price)}


def plan_out(plan: BillingPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "enabled": plan.enabled, "currency": plan.currency, "monthly_fee": float(plan.monthly_fee),
        "outbound_per_minute": float(plan.outbound_per_minute), "inbound_per_minute": float(plan.inbound_per_minute),
        "whatsapp_per_message": float(plan.whatsapp_per_message), "email_per_message": float(plan.email_per_message),
        "tax_percent": float(plan.tax_percent), "tax_label": plan.tax_label, "billing_email": plan.billing_email,
        "tax_id": plan.tax_id,
    }


async def usage_summary(db: AsyncSession, company: Company, plan: BillingPlan | None, start: date, end: date) -> dict[str, Any]:
    """What the company used between two dates, and what that comes to: the "this month so far" numbers."""
    since, until = _bounds(company, start, end)
    rows = (
        await db.execute(
            select(UsageRecord.kind, func.count(UsageRecord.id), func.sum(UsageRecord.quantity), func.sum(UsageRecord.amount),
                   func.sum(UsageRecord.seconds))
            .where(UsageRecord.company_id == company.id, UsageRecord.occurred_at >= since, UsageRecord.occurred_at < until)
            .group_by(UsageRecord.kind)
        )
    ).all()
    by_kind = {k: {"kind": k, "label": LABELS[k], "count": 0, "quantity": 0.0, "amount": 0.0, "seconds": 0} for k in KINDS}
    for kind, count, quantity, amount, seconds in rows:
        if kind in by_kind:
            by_kind[kind].update(count=count, quantity=float(quantity or 0), amount=float(amount or 0), seconds=int(seconds or 0))
    calls = sum(by_kind[k]["count"] for k in CALL_KINDS)
    call_minutes = sum(by_kind[k]["quantity"] for k in CALL_KINDS)
    call_amount = money(sum(by_kind[k]["amount"] for k in CALL_KINDS))
    usage = money(sum(r["amount"] for r in by_kind.values()))
    fee = money(plan.monthly_fee) if billing_on(plan) else Decimal(0)
    tax = money((usage + fee) * plan.tax_percent / 100) if billing_on(plan) else Decimal(0)
    return {
        "from": start.isoformat(), "to": end.isoformat(),
        "usage": [r for r in by_kind.values() if r["count"] or r["kind"] in CALL_KINDS],
        "calls": calls, "call_minutes": call_minutes, "call_amount": float(call_amount),
        "average_cost_per_call": float(money(call_amount / calls)) if calls else None,
        "average_minutes_per_call": round(call_minutes / calls, 1) if calls else None,
        "usage_amount": float(usage), "monthly_fee": float(fee), "tax_amount": float(tax),
        "estimated_total": float(usage + fee + tax),
    }


# --------------------------------------------------------------------------- invoices
async def generate_invoice(
    db: AsyncSession, company: Company, plan: BillingPlan, start: date, end: date, kind: str = "monthly",
    include_fee: bool = True, note: str | None = None,
) -> Invoice | None:
    """Bill everything not yet billed up to the end of `end`. None if there is nothing to bill."""
    _, until = _bounds(company, start, end)
    usage = (
        await db.scalars(
            select(UsageRecord)
            .where(UsageRecord.company_id == company.id, UsageRecord.invoice_id.is_(None), UsageRecord.occurred_at < until)
            .with_for_update()
        )
    ).all()
    groups: dict[tuple[str, Decimal], list[UsageRecord]] = defaultdict(list)
    for record in usage:
        groups[(record.kind, record.unit_price)].append(record)

    lines: list[InvoiceLine] = []
    if include_fee and plan.monthly_fee > 0:
        lines.append(InvoiceLine(kind="monthly_fee", description="Monthly platform fee", quantity=Decimal(1),
                                 unit_price=plan.monthly_fee, amount=money(plan.monthly_fee)))
    for (usage_kind, rate), records in sorted(groups.items(), key=lambda g: (KINDS.index(g[0][0]) if g[0][0] in KINDS else 99, g[0][1])):
        quantity = sum((r.quantity for r in records), Decimal(0))
        unit = "min" if usage_kind in CALL_KINDS else "msg"
        count = f"{len(records)} call{'s' if len(records) != 1 else ''}, " if usage_kind in CALL_KINDS else ""
        lines.append(InvoiceLine(
            kind=usage_kind, description=f"{LABELS.get(usage_kind, usage_kind)} ({count}{quantity:f} {unit} at {rate.normalize():f} per {unit})"[:255],
            quantity=quantity, unit_price=rate, amount=money(sum((r.amount for r in records), Decimal(0))),
        ))
    if not lines:
        return None
    for i, line in enumerate(lines):
        line.position = i

    subtotal = money(sum((line.amount for line in lines), Decimal(0)))
    tax = money(subtotal * plan.tax_percent / 100)
    today = today_of(company)
    invoice = Invoice(
        company_id=company.id, kind=kind, period_start=start, period_end=end, status=OPEN, currency=plan.currency,
        subtotal=subtotal, tax_percent=plan.tax_percent, tax_label=plan.tax_label, tax_amount=tax, total=subtotal + tax,
        issued_at=datetime.now(UTC), due_date=today + timedelta(days=settings.BILLING_DUE_DAYS), note=note, lines=lines,
    )
    db.add(invoice)
    await db.flush()
    for record in usage:
        record.invoice_id = invoice.id
    if invoice.total <= 0:  # nothing to pay
        invoice.status, invoice.paid_at, invoice.paid_via = PAID, datetime.now(UTC), "none"
    await db.commit()
    log.info("Invoice #%s for %s: %s %s (%s to %s)", invoice.number, company.name, invoice.total, invoice.currency, start, end)
    if invoice.status == OPEN:
        await send_to_stripe(db, invoice, company, plan)
    return invoice


def invoice_label(invoice: Invoice) -> str:
    return f"INV-{invoice.number}"


async def send_to_stripe(db: AsyncSession, invoice: Invoice, company: Company, plan: BillingPlan) -> None:
    """Put the invoice on Stripe so the company can pay it. A failure is kept on the invoice (it can be
    sent again from the portal); the invoice itself stays valid."""
    if invoice.stripe_invoice_id or invoice.status != OPEN:
        return
    try:
        if not plan.stripe_customer_id:
            plan.stripe_customer_id = await stripe_client.create_customer(
                company.name, plan.billing_email or company.email, str(company.id), plan.tax_id
            )
            await db.commit()
        lines = [(line.description, line.amount) for line in invoice.lines]
        if invoice.tax_amount > 0:
            lines.append((f"{invoice.tax_label} {invoice.tax_percent.normalize():f}%", invoice.tax_amount))
        days = max(1, (invoice.due_date - today_of(company)).days)
        sent = await stripe_client.send_invoice(
            plan.stripe_customer_id, str(invoice.id), invoice.currency, lines, days,
            f"{invoice_label(invoice)}: {invoice.period_start:%d %b %Y} to {invoice.period_end:%d %b %Y}",
            {"invoice_id": str(invoice.id), "company_id": str(company.id), "number": invoice_label(invoice)},
        )
        invoice.stripe_invoice_id = sent["id"]
        invoice.hosted_url, invoice.pdf_url = sent.get("hosted_invoice_url"), sent.get("invoice_pdf")
        invoice.stripe_error = None
    except Exception as exc:
        log.error("Invoice #%s could not be sent through Stripe: %s", invoice.number, exc)
        invoice.stripe_error = str(exc)[:1000]
    await db.commit()


async def mark_paid(db: AsyncSession, invoice: Invoice, via: str) -> None:
    if invoice.status != OPEN:
        return
    invoice.status, invoice.paid_at, invoice.paid_via = PAID, datetime.now(UTC), via
    await db.flush()
    await refresh_hold(db, await db.get(Company, invoice.company_id))
    await db.commit()
    log.info("Invoice #%s paid (%s)", invoice.number, via)


async def void(db: AsyncSession, invoice: Invoice) -> None:
    """Cancel an unpaid invoice. What it billed becomes unbilled again, so it goes onto the next invoice."""
    if invoice.status != OPEN:
        return
    records = await db.scalars(select(UsageRecord).where(UsageRecord.invoice_id == invoice.id))
    for record in records.all():
        record.invoice_id = None
    invoice.status = VOID
    await db.flush()
    await refresh_hold(db, await db.get(Company, invoice.company_id))
    await db.commit()


def overdue_since(invoice: Invoice, today: date) -> int:
    """Days past the due date (0 = not overdue)."""
    return max(0, (today - invoice.due_date).days) if invoice.status == OPEN else 0


async def refresh_hold(db: AsyncSession, company: Company) -> bool:
    """Hold the company's outgoing calls while an invoice is unpaid past the due date and the grace days."""
    today = today_of(company)
    plan = await plan_of(db, company.id)
    held = False
    if billing_on(plan):
        open_invoices = await db.scalars(select(Invoice).where(Invoice.company_id == company.id, Invoice.status == OPEN))
        held = any(overdue_since(i, today) > settings.BILLING_GRACE_DAYS for i in open_invoices.all())
    if company.billing_hold != held:
        company.billing_hold = held
        log.warning("%s: outgoing calls %s", company.name, "HELD for an overdue invoice" if held else "released (invoice paid)")
    return held


# --------------------------------------------------------------------------- the monthly run
async def tick(db: AsyncSession) -> int:
    """Create the invoice of every company whose billing month has closed, and update who is held.
    Safe to run as often as you like: a period is only ever billed once. Returns the invoices created."""
    if not settings.BILLING_ENABLED:
        return 0
    created = 0
    plans = (await db.scalars(select(BillingPlan).where(BillingPlan.enabled.is_(True)))).all()
    for plan in plans:
        company = await db.get(Company, plan.company_id)
        if company is None or not company.is_active:
            continue
        today = today_of(company)
        current_start, _ = period_of(today)
        start, end = period_of(current_start - timedelta(days=1))  # the month that just closed
        if plan.created_at.astimezone(_tz(company)).date() <= end:  # the plan existed during that month
            already = await db.scalar(
                select(Invoice.id).where(
                    Invoice.company_id == company.id, Invoice.kind == "monthly", Invoice.period_start == start, Invoice.status != VOID
                )
            )
            if already is None:
                try:
                    if await generate_invoice(db, company, plan, start, end) is not None:
                        created += 1
                except Exception:
                    await db.rollback()
                    log.exception("Could not create the invoice of %s for %s", company.name, start)
                    continue
        await refresh_hold(db, company)
        await db.commit()
    return created


async def handle_stripe_event(db: AsyncSession, event: dict[str, Any]) -> str:
    """A verified Stripe webhook event. Returns what was done, for the log."""
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if not kind.startswith("invoice."):
        return "ignored"
    invoice = await db.scalar(select(Invoice).where(Invoice.stripe_invoice_id == obj.get("id")))
    if invoice is None:
        return "unknown invoice"
    if kind in ("invoice.paid", "invoice.payment_succeeded"):
        await mark_paid(db, invoice, "offline" if obj.get("paid_out_of_band") else "stripe")
        return "paid"
    if kind == "invoice.voided":
        await void(db, invoice)
        return "voided"
    if kind == "invoice.payment_failed":
        invoice.note = ((invoice.note or "") + f"\nA payment attempt failed on {datetime.now(UTC):%d %b %Y}.").strip()
        await db.commit()
        return "payment failed"
    return "ignored"

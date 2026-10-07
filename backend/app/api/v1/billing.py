"""Billing: the platform admin sets each company's prices and manages its invoices; a company's own
admin sees its prices, what it has used this month, and its invoices (with the link to pay)."""
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser, PageParams, SuperAdmin, not_found
from app.core.config import settings
from app.models import BillingPlan, Company, Invoice
from app.schemas.common import ORMModel, Page
from app.services import billing_service, stripe_client
from app.services.audit import audit

router = APIRouter(tags=["billing"])


class PlanIn(BaseModel):
    enabled: bool = True
    currency: str = Field(default="INR", pattern="^[A-Za-z]{3}$")
    monthly_fee: Decimal = Field(default=Decimal(0), ge=0, le=10_000_000)
    outbound_per_minute: Decimal = Field(default=Decimal(0), ge=0, le=10_000)
    inbound_per_minute: Decimal = Field(default=Decimal(0), ge=0, le=10_000)
    whatsapp_per_message: Decimal = Field(default=Decimal(0), ge=0, le=10_000)
    email_per_message: Decimal = Field(default=Decimal(0), ge=0, le=10_000)
    tax_percent: Decimal = Field(default=Decimal(18), ge=0, le=100)
    tax_label: str = Field(default="GST", min_length=1, max_length=32)
    billing_email: EmailStr | None = None
    tax_id: str | None = Field(default=None, max_length=64)


class LineOut(ORMModel):
    kind: str
    description: str
    quantity: float
    unit_price: float
    amount: float


class InvoiceOut(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str | None = None
    number: int
    label: str = ""
    kind: str
    period_start: date
    period_end: date
    status: str  # open | paid | void
    overdue_days: int = 0
    currency: str
    subtotal: float
    tax_percent: float
    tax_label: str
    tax_amount: float
    total: float
    issued_at: datetime
    due_date: date
    paid_at: datetime | None
    paid_via: str | None
    note: str | None
    hosted_url: str | None  # where the company pays (Stripe)
    pdf_url: str | None
    stripe_error: str | None = None
    lines: list[LineOut] = []


class ManualInvoice(BaseModel):
    include_fee: bool = False
    note: str | None = Field(default=None, max_length=500)


def _invoice_out(invoice: Invoice, company: Company, for_platform: bool = False) -> InvoiceOut:
    out = InvoiceOut.model_validate(invoice)
    out.label = billing_service.invoice_label(invoice)
    out.company_name = company.name
    out.overdue_days = billing_service.overdue_since(invoice, billing_service.today_of(company))
    if not for_platform:
        out.stripe_error = None  # the company only needs to know whether it can pay online
    return out


async def _summary(db: DB, company: Company, for_platform: bool) -> dict[str, Any]:
    plan = await billing_service.plan_of(db, company.id)
    today = billing_service.today_of(company)
    start, end = billing_service.period_of(today)
    invoices = (
        await db.scalars(select(Invoice).where(Invoice.company_id == company.id).order_by(Invoice.issued_at.desc()).limit(12))
    ).all()
    due = [i for i in invoices if i.status == billing_service.OPEN]
    return {
        "company": {"id": str(company.id), "name": company.name},
        "billing_on": billing_service.billing_on(plan),
        "plan": billing_service.plan_out(plan),
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "next_invoice_date": (end + timedelta(days=1)).isoformat(),  # the billing day that closes this period
        "this_period": await billing_service.usage_summary(db, company, plan, start, min(end, today)),
        "amount_due": float(sum((i.total for i in due), Decimal(0))),
        "on_hold": company.billing_hold,
        "stripe_connected": stripe_client.configured(),
        "due_days": settings.BILLING_DUE_DAYS, "grace_days": settings.BILLING_GRACE_DAYS,
        "invoices": [_invoice_out(i, company, for_platform) for i in invoices],
    }


# --------------------------------------------------------------------------- the company's own view
@router.get("/billing")
async def my_billing(db: DB, user: AdminUser) -> dict[str, Any]:
    """The company admin's Billing page: prices, this month so far (with the average cost per call), invoices."""
    return await _summary(db, await db.get(Company, user.company_id), for_platform=False)


@router.get("/billing/invoices", response_model=Page[InvoiceOut])
async def my_invoices(db: DB, user: AdminUser, pg: PageParams):
    company = await db.get(Company, user.company_id)
    stmt = select(Invoice).where(Invoice.company_id == company.id, Invoice.status != billing_service.VOID)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await db.scalars(stmt.order_by(Invoice.issued_at.desc()).offset(pg.offset).limit(pg.size))).all()
    return Page(items=[_invoice_out(i, company) for i in rows], total=total, page=pg.page, size=pg.size)


# --------------------------------------------------------------------------- the platform admin
async def _company(db: DB, company_id: uuid.UUID) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise not_found("Company")
    return company


async def _invoice(db: DB, invoice_id: uuid.UUID) -> tuple[Invoice, Company]:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise not_found("Invoice")
    return invoice, await db.get(Company, invoice.company_id)


@router.get("/platform/billing")
async def platform_billing(db: DB, _: SuperAdmin) -> dict[str, Any]:
    """Every company: is it billed, what it has used this month, and what it owes."""
    companies = (await db.scalars(select(Company).order_by(Company.name))).all()
    plans = {p.company_id: p for p in (await db.scalars(select(BillingPlan))).all()}
    owed = dict((await db.execute(
        select(Invoice.company_id, func.sum(Invoice.total)).where(Invoice.status == billing_service.OPEN).group_by(Invoice.company_id)
    )).all())
    paid = dict((await db.execute(
        select(Invoice.company_id, func.sum(Invoice.total)).where(Invoice.status == billing_service.PAID).group_by(Invoice.company_id)
    )).all())
    rows = []
    for company in companies:
        plan = plans.get(company.id)
        today = billing_service.today_of(company)
        start, end = billing_service.period_of(today)
        usage = await billing_service.usage_summary(db, company, plan, start, min(end, today))
        rows.append({
            "company_id": str(company.id), "name": company.name, "is_active": company.is_active,
            "billing_on": billing_service.billing_on(plan), "currency": plan.currency if plan else None,
            "outbound_per_minute": float(plan.outbound_per_minute) if plan else None,
            "inbound_per_minute": float(plan.inbound_per_minute) if plan else None,
            "calls": usage["calls"], "call_minutes": usage["call_minutes"], "average_cost_per_call": usage["average_cost_per_call"],
            "estimated_total": usage["estimated_total"], "amount_due": float(owed.get(company.id) or 0),
            "amount_paid": float(paid.get(company.id) or 0), "on_hold": company.billing_hold,
        })
    return {
        "billing_enabled": settings.BILLING_ENABLED, "stripe_connected": stripe_client.configured(),
        "stripe_webhook_url": f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/webhooks/stripe",
        "stripe_webhook_ready": bool(settings.STRIPE_WEBHOOK_SECRET), "billing_day": settings.BILLING_DAY,
        "companies": rows,
        "totals": {"due": sum(r["amount_due"] for r in rows), "paid": sum(r["amount_paid"] for r in rows),
                   "this_period": sum(r["estimated_total"] for r in rows)},
    }


@router.get("/companies/{company_id}/billing")
async def company_billing(company_id: uuid.UUID, db: DB, _: SuperAdmin) -> dict[str, Any]:
    return await _summary(db, await _company(db, company_id), for_platform=True)


@router.put("/companies/{company_id}/billing-plan")
async def set_plan(company_id: uuid.UUID, body: PlanIn, db: DB, user: SuperAdmin) -> dict[str, Any]:
    """Set (or change) a company's prices. New prices apply to calls from now on; past usage keeps the
    price it was recorded at."""
    company = await _company(db, company_id)
    plan = await billing_service.plan_of(db, company.id)
    if plan is None:
        plan = BillingPlan(company_id=company.id)
        db.add(plan)
    changes = body.model_dump()
    changes["currency"] = changes["currency"].upper()
    for field, value in changes.items():
        setattr(plan, field, value)
    await db.flush()
    await billing_service.refresh_hold(db, company)
    audit(db, user.id, "set_billing_plan", "company", company.id, {k: str(v) for k, v in changes.items()})
    await db.commit()
    return await _summary(db, company, for_platform=True)


@router.post("/companies/{company_id}/invoices", response_model=InvoiceOut, status_code=201)
async def bill_now(company_id: uuid.UUID, body: ManualInvoice, db: DB, user: SuperAdmin):
    """Bill what the company has used so far, without waiting for the billing day."""
    company = await _company(db, company_id)
    plan = await billing_service.plan_of(db, company.id)
    if not billing_service.billing_on(plan):
        raise HTTPException(status.HTTP_409_CONFLICT, "Billing is off for this company. Save its price plan (with billing on) first.")
    today = billing_service.today_of(company)
    start, _ = billing_service.period_of(today)
    invoice = await billing_service.generate_invoice(db, company, plan, start, today, kind="manual", include_fee=body.include_fee, note=body.note)
    if invoice is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is nothing to bill: no unbilled usage" + ("" if body.include_fee else " (and the monthly fee was not included)") + ".")
    audit(db, user.id, "create_invoice", "invoice", invoice.id, {"total": str(invoice.total)})
    await db.commit()
    return _invoice_out(await db.get(Invoice, invoice.id, populate_existing=True), company, for_platform=True)


@router.post("/invoices/{invoice_id}/{action}", response_model=InvoiceOut)
async def invoice_action(invoice_id: uuid.UUID, action: str, db: DB, user: SuperAdmin):
    """mark-paid = paid outside Stripe (bank transfer); void = cancel it; send = try Stripe again."""
    invoice, company = await _invoice(db, invoice_id)
    if action not in ("mark-paid", "void", "send"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown action")
    if invoice.status != billing_service.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This invoice is already {invoice.status}")
    if action == "send":
        if invoice.stripe_invoice_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This invoice is already on Stripe")
        await billing_service.send_to_stripe(db, invoice, company, await billing_service.plan_of(db, company.id))
    else:
        if invoice.stripe_invoice_id:  # keep Stripe's copy in step, so its "Pay" link stops asking for money
            try:
                if action == "void":
                    await stripe_client.void_invoice(invoice.stripe_invoice_id)
                else:
                    await stripe_client.mark_paid_outside_stripe(invoice.stripe_invoice_id)
            except Exception as exc:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Stripe could not be updated, so nothing was changed: {exc}") from exc
        await (billing_service.void(db, invoice) if action == "void" else billing_service.mark_paid(db, invoice, "offline"))
    audit(db, user.id, action, "invoice", invoice.id)
    await db.commit()
    return _invoice_out(await db.get(Invoice, invoice.id, populate_existing=True), company, for_platform=True)


# --------------------------------------------------------------------------- Stripe -> us
@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request, db: DB) -> dict[str, str]:
    """Stripe tells us when an invoice is paid (or voided). Only requests signed with our webhook secret count."""
    payload = await request.body()
    if not stripe_client.verify_webhook(payload, request.headers.get("Stripe-Signature")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature")
    try:
        event = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from None
    return {"status": await billing_service.handle_stripe_event(db, event)}

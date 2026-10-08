"""The platform admin's own dashboard and reports: numbers across every company.

    /platform/dashboard   what needs the platform admin right now, and today's activity
    /platform/reports     the last 7 / 30 / 90 days, per day and per company

Only counts and money are returned: never a company's tickets, customers, messages or recordings.
Days are counted in one timezone for every company (PLATFORM_TIMEZONE), so the charts line up.
Money is added up in one currency (the one most price plans use); a company billed in another
currency keeps its own figures in its own row and is named in "other_currencies".
"""
import uuid
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query
from sqlalchemy import exists, extract, func, literal_column, select

from app.api.deps import DB, SuperAdmin
from app.core.config import settings
from app.models import (
    BillingPlan, Call, Campaign, Company, Conversation, Invoice, IssueReport, SupportCall, Ticket, UsageRecord, User,
)
from app.models.enums import ACTIVE_CALL_STATUSES, CallStatus, CampaignStatus, Channel, ConversationStatus
from app.services import billing_service, company_service, stripe_client

router = APIRouter(prefix="/platform", tags=["platform"])
CHANNELS = [Channel.WHATSAPP, Channel.PHONE, Channel.EMAIL]
OPEN_ISSUES = ("open", "in_progress")
NEAR_LIMIT = 0.9  # a limit is "almost reached" from 90%
LEVELS = {"high": 0, "medium": 1, "low": 2}


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.PLATFORM_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


async def _per_day(db: DB, column, counted, since: datetime, tz: ZoneInfo) -> Counter[str]:
    """How many rows per calendar day of the platform's timezone. The database counts per quarter of an
    hour (every timezone is a whole number of those from UTC) and the days are put together here, so
    nothing depends on the database knowing the timezone."""
    slot = func.floor(extract("epoch", column) / literal_column("900"))
    days: Counter[str] = Counter()
    for number, count in (await db.execute(select(slot, func.count(counted)).where(column >= since).group_by(slot))).all():
        days[datetime.fromtimestamp(int(number) * 900, tz).date().isoformat()] += count
    return days


async def _by_company(db: DB, stmt) -> dict[uuid.UUID, Any]:
    """A `SELECT company_id, value ... GROUP BY company_id` as a dictionary."""
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


def _currencies(plans: dict[uuid.UUID, BillingPlan], invoice_currencies: list[str]) -> tuple[str, list[str]]:
    """The currency money is added up in, and the others in use."""
    used = Counter(p.currency for p in plans.values())
    main = used.most_common(1)[0][0] if used else (invoice_currencies[0] if invoice_currencies else "INR")
    return main, sorted((set(used) | set(invoice_currencies)) - {main})


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# --------------------------------------------------------------------------- dashboard
@router.get("/dashboard")
async def dashboard(db: DB, _: SuperAdmin) -> dict[str, Any]:
    tz = _tz()
    today = datetime.now(tz).date()
    since = _midnight(today, tz)
    companies = (await db.scalars(select(Company).order_by(Company.name))).all()
    plans = {p.company_id: p for p in (await db.scalars(select(BillingPlan))).all()}
    open_invoices = (await db.scalars(select(Invoice).where(Invoice.status == billing_service.OPEN).order_by(Invoice.due_date))).all()
    currency, others = _currencies(plans, sorted({i.currency for i in open_invoices}))

    campaign_calls = await _by_company(db, select(Campaign.company_id, func.count(Call.id)).join(Call, Call.campaign_id == Campaign.id)
                                       .where(Call.created_at >= since).group_by(Campaign.company_id))
    inbound_calls = await _by_company(db, select(SupportCall.company_id, func.count(SupportCall.id))
                                      .where(SupportCall.started_at >= since).group_by(SupportCall.company_id))
    tickets = await _by_company(db, select(Ticket.company_id, func.count(Ticket.id)).where(Ticket.created_at >= since).group_by(Ticket.company_id))
    conversations = await _by_company(db, select(Conversation.company_id, func.count(Conversation.id))
                                      .where(Conversation.created_at >= since).group_by(Conversation.company_id))
    live = Counter(await _by_company(db, select(Campaign.company_id, func.count(Call.id)).join(Call, Call.campaign_id == Campaign.id)
                                     .where(Call.status.in_(list(ACTIVE_CALL_STATUSES))).group_by(Campaign.company_id)))
    live.update(await _by_company(db, select(SupportCall.company_id, func.count(SupportCall.id))
                                  .where(SupportCall.status == CallStatus.IN_PROGRESS).group_by(SupportCall.company_id)))
    campaigns = await _by_company(db, select(Campaign.company_id, func.count(Campaign.id)).group_by(Campaign.company_id))
    running = await db.scalar(select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.RUNNING)) or 0
    issues = await _by_company(db, select(IssueReport.company_id, func.count(IssueReport.id))
                               .where(IssueReport.level == "platform", IssueReport.status.in_(OPEN_ISSUES)).group_by(IssueReport.company_id))
    period_start, _period_end = billing_service.period_of(today)
    usage = await _by_company(db, select(UsageRecord.company_id, func.sum(UsageRecord.amount))
                              .where(UsageRecord.occurred_at >= _midnight(period_start, tz)).group_by(UsageRecord.company_id))

    owed: dict[uuid.UUID, list[Invoice]] = defaultdict(list)
    for invoice in open_invoices:
        owed[invoice.company_id].append(invoice)

    attention: list[dict[str, Any]] = []

    def flag(company: Company, level: str, kind: str, text: str, target: str = "company") -> None:
        attention.append({"level": level, "kind": kind, "company_id": str(company.id), "company": company.name, "text": text, "target": target})

    rows = []
    for company in companies:
        cid = company.id
        rows.append({
            "company_id": str(cid), "name": company.name, "is_active": company.is_active,
            "inbound_calls": inbound_calls.get(cid, 0), "campaign_calls": campaign_calls.get(cid, 0), "live_calls": live.get(cid, 0),
            "tickets": tickets.get(cid, 0), "conversations": conversations.get(cid, 0),
        })
        if issues.get(cid):
            flag(company, "medium", "issues", f"{_plural(issues[cid], 'issue report')} waiting for you", "issues")
        if not company.is_active:
            flag(company, "low", "suspended", "Suspended: nobody in this company can sign in, and nothing is answered")
            continue
        late = [i for i in owed[cid] if i.due_date < today]
        if company.billing_hold:
            flag(company, "high", "on_hold", "Campaigns and call-backs are on hold until an overdue invoice is paid", "billing")
        elif late:
            days = (today - late[0].due_date).days
            flag(company, "medium", "overdue", f"{billing_service.invoice_label(late[0])} is {_plural(days, 'day')} overdue "
                 f"({late[0].currency} {late[0].total:,.2f})", "billing")
        failed = [i for i in owed[cid] if i.stripe_error]
        if failed and stripe_client.configured():
            flag(company, "medium", "stripe_error", f"{billing_service.invoice_label(failed[0])} could not be sent through Stripe", "billing")
        # Counted from midnight in the platform's timezone; the company's own limit resets at its own midnight.
        if company.daily_call_limit and campaign_calls.get(cid, 0) >= NEAR_LIMIT * company.daily_call_limit:
            used, limit = campaign_calls[cid], company.daily_call_limit
            flag(company, "medium" if used >= limit else "low", "call_limit",
                 f"Daily call limit {'reached' if used >= limit else 'almost reached'}: {used:,} of {limit:,} calls today")
        if company.max_campaigns and campaigns.get(cid, 0) >= NEAR_LIMIT * company.max_campaigns:
            used, limit = campaigns[cid], company.max_campaigns
            flag(company, "low", "campaign_limit", f"Campaign limit {'reached' if used >= limit else 'almost reached'}: {used} of {limit}")
        missing = [name for name, ok in (("calling", company_service.calling_credentials(company)), ("WhatsApp", company_service.whatsapp_credentials(company))) if not ok]
        if missing:
            flag(company, "low", "not_connected", f"Not connected yet: {' and '.join(missing)}")
    attention.sort(key=lambda a: (LEVELS[a["level"]], a["company"].lower()))
    rows.sort(key=lambda r: (-(r["inbound_calls"] + r["campaign_calls"] + r["tickets"] + r["conversations"] + r["live_calls"]), r["name"].lower()))

    in_currency = [c.id for c in companies if c.id in plans and plans[c.id].currency == currency]
    due = [i for i in open_invoices if i.currency == currency]
    return {
        "date": today.isoformat(), "timezone": str(tz), "currency": currency, "other_currencies": others,
        "totals": {
            "companies": len(companies), "active_companies": sum(1 for c in companies if c.is_active),
            "users": await db.scalar(select(func.count(User.id)).where(User.company_id.isnot(None))) or 0,
            "inbound_calls_today": sum(inbound_calls.values()), "campaign_calls_today": sum(campaign_calls.values()),
            "calls_today": sum(inbound_calls.values()) + sum(campaign_calls.values()), "live_calls": sum(live.values()),
            "tickets_today": sum(tickets.values()), "conversations_today": sum(conversations.values()), "running_campaigns": running,
            "period_from": period_start.isoformat(), "period_usage": float(sum((usage.get(cid) or 0) for cid in in_currency)),
            "amount_due": float(sum(i.total for i in due)), "amount_overdue": float(sum(i.total for i in due if i.due_date < today)),
            "open_invoices": len(open_invoices), "companies_on_hold": sum(1 for c in companies if c.billing_hold and c.is_active),
            "open_issues": sum(issues.values()),
        },
        "attention": attention,
        "companies": rows,
        "setup": {
            "billing_enabled": settings.BILLING_ENABLED, "stripe_connected": stripe_client.configured(),
            "stripe_webhook_ready": bool(settings.STRIPE_WEBHOOK_SECRET), "email_enabled": settings.EMAIL_ENABLED,
            "billing_day": settings.BILLING_DAY,
        },
    }


# --------------------------------------------------------------------------- reports
def _months_back(today: date, count: int) -> list[date]:
    """The first day of the last `count` calendar months, oldest first."""
    months, year, month = [], today.year, today.month
    for _ in range(count):
        months.append(date(year, month, 1))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return months[::-1]


@router.get("/reports")
async def reports(db: DB, _: SuperAdmin, days: int = Query(30, ge=1, le=90)) -> dict[str, Any]:
    tz = _tz()
    today = datetime.now(tz).date()
    first = today - timedelta(days=days - 1)
    since = _midnight(first, tz)
    companies = (await db.scalars(select(Company).order_by(Company.name))).all()
    plans = {p.company_id: p for p in (await db.scalars(select(BillingPlan))).all()}
    months = _months_back(today, 6)
    # Invoices are few (one per company per month), so they are added up here rather than in SQL.
    invoices = (await db.scalars(select(Invoice).where(
        Invoice.status != billing_service.VOID,
        (Invoice.status == billing_service.OPEN) | (Invoice.issued_at >= _midnight(months[0], tz)) | (Invoice.paid_at >= _midnight(months[0], tz)),
    ))).all()
    currency, others = _currencies(plans, sorted({i.currency for i in invoices}))

    # --- per day
    daily = {(first + timedelta(days=i)).isoformat(): {"date": (first + timedelta(days=i)).isoformat(), "inbound_calls": 0,
                                                     "campaign_calls": 0, "tickets": 0, "conversations": 0} for i in range(days)}
    for key, column, model_id in (("campaign_calls", Call.created_at, Call.id), ("inbound_calls", SupportCall.started_at, SupportCall.id),
                                  ("tickets", Ticket.created_at, Ticket.id), ("conversations", Conversation.created_at, Conversation.id)):
        for when, count in (await _per_day(db, column, model_id, since, tz)).items():
            if when in daily:
                daily[when][key] = count

    # --- per company
    out_calls = {r[0]: r for r in (await db.execute(
        select(Campaign.company_id, func.count(Call.id), func.sum(Call.duration_seconds)).join(Call, Call.campaign_id == Campaign.id)
        .where(Call.created_at >= since).group_by(Campaign.company_id))).all()}
    in_calls = {r[0]: r for r in (await db.execute(
        select(SupportCall.company_id, func.count(SupportCall.id), func.sum(SupportCall.duration_seconds))
        .where(SupportCall.started_at >= since).group_by(SupportCall.company_id))).all()}
    tickets = await _by_company(db, select(Ticket.company_id, func.count(Ticket.id)).where(Ticket.created_at >= since).group_by(Ticket.company_id))
    # A conversation the assistant handled alone: it is finished and no ticket was needed.
    has_ticket = exists().where(Ticket.conversation_id == Conversation.id).label("has_ticket")
    talk: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: {"conversations": 0, "finished": 0, "resolved_by_ai": 0})
    by_channel = {ch.value: {"channel": ch.value, "conversations": 0, "finished": 0, "resolved_by_ai": 0} for ch in CHANNELS}
    for cid, channel, state, ticketed, count in (await db.execute(
        select(Conversation.company_id, Conversation.channel, Conversation.status, has_ticket, func.count(Conversation.id))
        .where(Conversation.created_at >= since).group_by(Conversation.company_id, Conversation.channel, Conversation.status, has_ticket)
    )).all():
        for row in (talk[cid], by_channel.get(channel)):
            if row is None:
                continue
            row["conversations"] += count
            if state != ConversationStatus.OPEN:
                row["finished"] += count
                if state == ConversationStatus.RESOLVED and not ticketed:
                    row["resolved_by_ai"] += count
    usage: dict[uuid.UUID, dict[str, float]] = defaultdict(lambda: {"amount": 0.0, "call_amount": 0.0, "charged_calls": 0})
    for cid, kind, count, amount in (await db.execute(
        select(UsageRecord.company_id, UsageRecord.kind, func.count(UsageRecord.id), func.sum(UsageRecord.amount))
        .where(UsageRecord.occurred_at >= since).group_by(UsageRecord.company_id, UsageRecord.kind))).all():
        usage[cid]["amount"] += float(amount or 0)
        if kind in billing_service.CALL_KINDS:
            usage[cid]["call_amount"] += float(amount or 0)
            usage[cid]["charged_calls"] += count
    invoiced: dict[uuid.UUID, float] = defaultdict(float)
    unpaid: dict[uuid.UUID, float] = defaultdict(float)
    for invoice in invoices:
        if invoice.issued_at >= since:
            invoiced[invoice.company_id] += float(invoice.total)
        if invoice.status == billing_service.OPEN:
            unpaid[invoice.company_id] += float(invoice.total)

    rows = []
    for company in companies:
        cid = company.id
        outbound, inbound = out_calls.get(cid), in_calls.get(cid)
        seconds = int((outbound[2] if outbound else 0) or 0) + int((inbound[2] if inbound else 0) or 0)
        spent, chat = usage[cid], talk[cid]
        rows.append({
            "company_id": str(cid), "name": company.name, "is_active": company.is_active,
            "currency": plans[cid].currency if cid in plans else None,
            "inbound_calls": inbound[1] if inbound else 0, "campaign_calls": outbound[1] if outbound else 0,
            "calls": (inbound[1] if inbound else 0) + (outbound[1] if outbound else 0), "call_minutes": round(seconds / 60),
            "usage_amount": round(spent["amount"], 2),
            "average_cost_per_call": round(spent["call_amount"] / spent["charged_calls"], 2) if spent["charged_calls"] else None,
            "tickets": tickets.get(cid, 0), "conversations": chat["conversations"],
            "ai_resolved_rate": round(chat["resolved_by_ai"] / chat["finished"], 3) if chat["finished"] else None,
            "invoiced": round(invoiced[cid], 2), "amount_due": round(unpaid[cid], 2),
        })
    rows.sort(key=lambda r: (-r["calls"], -r["conversations"], r["name"].lower()))

    # --- money per month, in the main currency
    by_month = {m.strftime("%Y-%m"): {"month": m.strftime("%Y-%m"), "invoiced": 0.0, "paid": 0.0} for m in months}
    for invoice in invoices:
        if invoice.currency != currency:
            continue
        issued = invoice.issued_at.astimezone(tz).strftime("%Y-%m")
        if issued in by_month:
            by_month[issued]["invoiced"] += float(invoice.total)
        if invoice.status == billing_service.PAID and invoice.paid_at is not None:
            paid = invoice.paid_at.astimezone(tz).strftime("%Y-%m")
            if paid in by_month:
                by_month[paid]["paid"] += float(invoice.total)
    for month in by_month.values():
        month["invoiced"], month["paid"] = round(month["invoiced"], 2), round(month["paid"], 2)

    main = [r for r in rows if r["currency"] == currency]
    finished = sum(r["finished"] for r in by_channel.values())
    ai = sum(r["resolved_by_ai"] for r in by_channel.values())
    return {
        "days": days, "from": first.isoformat(), "to": today.isoformat(), "timezone": str(tz),
        "currency": currency, "other_currencies": others,
        "totals": {
            "calls": sum(r["calls"] for r in rows), "inbound_calls": sum(r["inbound_calls"] for r in rows),
            "campaign_calls": sum(r["campaign_calls"] for r in rows), "call_minutes": sum(r["call_minutes"] for r in rows),
            "usage_amount": round(sum(r["usage_amount"] for r in main), 2), "invoiced": round(sum(r["invoiced"] for r in main), 2),
            "amount_due": round(sum(r["amount_due"] for r in main), 2),
            "tickets_created": sum(r["tickets"] for r in rows), "conversations": sum(r["conversations"] for r in by_channel.values()),
            "conversations_finished": finished, "resolved_by_ai": ai, "ai_resolved_rate": round(ai / finished, 3) if finished else None,
            "new_companies": sum(1 for c in companies if c.created_at >= since),
            "active_companies": sum(1 for r in rows if r["calls"] or r["conversations"] or r["tickets"]),
        },
        "daily": list(daily.values()),
        "months": list(by_month.values()),
        "channels": list(by_channel.values()),
        "companies": rows,
    }

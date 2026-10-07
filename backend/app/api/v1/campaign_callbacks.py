"""Call-backs promised on campaign (outbound) calls, in one list for the whole company.

A campaign call-back is the campaign's own next call to a contact who said "I can't talk right now":
at the time they chose, or automatically after the retry delay when they gave none. The campaign
places the call as always; this is the view of that schedule, with the two things a supervisor
needs to do to it - move it, or cancel it - and the reason whenever a call-back is late.
"""
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DB, ManagerUser, PageParams, TenantUser, not_found
from app.models import Call, Campaign, CampaignContact, CampaignRun, Company, ScheduledCall, User
from app.models.enums import ACTIVE_CALL_STATUSES, CallStatus, CampaignStatus, ContactStatus, ScheduledCallStatus
from app.schemas.common import Page
from app.schemas.support import CustomerBrief
from app.services import call_service, company_service, outbound_callback
from app.services.audit import audit

router = APIRouter(prefix="/callbacks/campaign", tags=["insights"])

KINDS = ("callback_time", "callback_auto")
WAITING = (ScheduledCallStatus.PENDING, ScheduledCallStatus.QUEUED)
LATE_AFTER = timedelta(minutes=5)  # a call-back still not placed this long after its time is "overdue"
MAX_DAYS_AHEAD = 14


class CampaignCallbackOut(BaseModel):
    id: uuid.UUID  # the scheduled call
    campaign_id: uuid.UUID
    campaign_name: str
    run: int | None
    customer: CustomerBrief
    scheduled_at: datetime
    chosen_by_customer: bool  # False = no time was agreed; called again automatically after the retry delay
    # waiting | on_hold | overdue | calling | done | not_reached | cancelled
    status: str
    reason: str | None = None  # why it is on hold or overdue
    times_asked: int
    call_id: uuid.UUID | None = None  # the call that was placed for it
    can_edit: bool = False


class Reschedule(BaseModel):
    scheduled_at: datetime


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _clock(t: time) -> str:
    return f"{t.hour % 12 or 12}{f':{t.minute:02d}' if t.minute else ''} {'AM' if t.hour < 12 else 'PM'}"


def _base(company_id: uuid.UUID):
    return (
        select(ScheduledCall, Campaign, CampaignContact)
        .join(Campaign, Campaign.id == ScheduledCall.campaign_id)
        .join(CampaignContact, CampaignContact.id == ScheduledCall.campaign_contact_id)
        .where(Campaign.company_id == company_id, ScheduledCall.kind.in_(KINDS))
    )


async def _late_reason(db: DB, campaign: Campaign, company: Company, now: datetime, capacity: dict[str, Any]) -> str:
    """Why a call-back whose time has passed has not been placed yet."""
    if not company.is_active:
        return "The company is suspended."
    if company.billing_hold:
        return "Outgoing calls are on hold for an overdue invoice."
    if not outbound_callback.in_window(campaign, now.astimezone(_tz(campaign.timezone))):
        return (
            f"Outside the campaign's calling hours ({_clock(campaign.call_window_start)} to {_clock(campaign.call_window_end)}); "
            "it is called when they open."
        )
    if "left" not in capacity:
        capacity["left"] = await company_service.call_capacity(db, company, now)
    if capacity["left"] is not None and capacity["left"] <= 0:
        return "Today's call limit is reached; it is called tomorrow."
    return "Waiting for a free line."


async def _rows(db: DB, rows: list[tuple[ScheduledCall, Campaign, CampaignContact]], user: User) -> list[CampaignCallbackOut]:
    now = datetime.now(UTC)
    company = await db.get(Company, user.company_id)
    ids = [sc.id for sc, _, _ in rows]
    placed = {
        c.scheduled_call_id: c
        for c in (await db.scalars(select(Call).where(Call.scheduled_call_id.in_(ids)))).unique().all()
    } if ids else {}
    run_ids = {contact.run_id for _, _, contact in rows if contact.run_id}
    numbers = dict((await db.execute(select(CampaignRun.id, CampaignRun.number).where(CampaignRun.id.in_(run_ids)))).all()) if run_ids else {}
    capacity: dict[str, Any] = {}
    manager = user.role in ("admin", "supervisor")
    out = []
    for sc, campaign, contact in rows:
        call = placed.get(sc.id)
        reason = None
        if sc.status == ScheduledCallStatus.CANCELLED:
            state = "cancelled"
        elif sc.status in WAITING:
            if campaign.status == CampaignStatus.PAUSED:
                state, reason = "on_hold", "The campaign is paused. Resume it and this call-back is made."
            elif campaign.status in (CampaignStatus.CANCELLED, CampaignStatus.COMPLETED, CampaignStatus.DRAFT):
                state, reason = "on_hold", f"The campaign is {campaign.status}."
            elif sc.scheduled_at < now - LATE_AFTER:
                state, reason = "overdue", await _late_reason(db, campaign, company, now, capacity)
            else:
                state = "waiting"
        elif call is None or call.status in ACTIVE_CALL_STATUSES:
            state = "calling"
        else:
            state = "done" if call.status == CallStatus.COMPLETED else "not_reached"
        out.append(CampaignCallbackOut(
            id=sc.id, campaign_id=campaign.id, campaign_name=campaign.name, run=numbers.get(contact.run_id, campaign.run_number),
            customer=CustomerBrief.model_validate(contact.customer), scheduled_at=sc.scheduled_at,
            chosen_by_customer=sc.kind == "callback_time", status=state, reason=reason, times_asked=contact.callback_count,
            call_id=call.id if call else None, can_edit=manager and sc.status == ScheduledCallStatus.PENDING,
        ))
    return out


@router.get("", response_model=Page[CampaignCallbackOut])
async def list_campaign_callbacks(
    db: DB, user: TenantUser, pg: PageParams,
    show: Literal["upcoming", "finished", "all"] = "upcoming",
    when: Literal["today", "tomorrow", "week"] | None = None,
    campaign_id: uuid.UUID | None = None,
):
    """Call-backs promised on campaign calls. Upcoming ones soonest first; finished ones newest first.
    when: only those scheduled today / tomorrow / in the next 7 days (the company's own days)."""
    stmt = _base(user.company_id)
    if show == "upcoming":
        stmt = stmt.where(ScheduledCall.status.in_(WAITING))
    elif show == "finished":
        stmt = stmt.where(ScheduledCall.status.notin_(WAITING))
    if campaign_id:
        stmt = stmt.where(ScheduledCall.campaign_id == campaign_id)
    if when:
        company = await db.get(Company, user.company_id)
        tz = _tz(company.timezone)
        today = datetime.now(tz).date()
        first = today + timedelta(days=1 if when == "tomorrow" else 0)
        last = first + timedelta(days=7 if when == "week" else 1)
        stmt = stmt.where(
            ScheduledCall.scheduled_at >= datetime.combine(first, time.min, tzinfo=tz),
            ScheduledCall.scheduled_at < datetime.combine(last, time.min, tzinfo=tz),
        )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    order = ScheduledCall.scheduled_at.asc() if show == "upcoming" else ScheduledCall.scheduled_at.desc()
    rows = (await db.execute(stmt.order_by(order).offset(pg.offset).limit(pg.size))).unique().all()
    return Page(items=await _rows(db, [tuple(r) for r in rows], user), total=total, page=pg.page, size=pg.size)


async def _one(db: DB, scheduled_call_id: uuid.UUID, user: User) -> tuple[ScheduledCall, Campaign, CampaignContact]:
    row = (await db.execute(_base(user.company_id).where(ScheduledCall.id == scheduled_call_id))).unique().first()
    if row is None:
        raise not_found("Callback")
    sc, campaign, contact = row
    if sc.status != ScheduledCallStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "This call-back is already being made or is finished, so it cannot be changed")
    return sc, campaign, contact


@router.patch("/{scheduled_call_id}", response_model=CampaignCallbackOut)
async def reschedule_campaign_callback(scheduled_call_id: uuid.UUID, body: Reschedule, db: DB, user: ManagerUser):
    """Move a waiting call-back. The new time must be inside the campaign's calling hours."""
    sc, campaign, contact = await _one(db, scheduled_call_id, user)
    when = body.scheduled_at if body.scheduled_at.tzinfo else body.scheduled_at.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    if when < now - timedelta(minutes=1):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose a time in the future")
    if when > now + timedelta(days=MAX_DAYS_AHEAD):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Choose a time within the next {MAX_DAYS_AHEAD} days")
    local = when.astimezone(_tz(campaign.timezone))
    if not outbound_callback.in_window(campaign, local):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{_clock(local.time())} is outside this campaign's calling hours ({_clock(campaign.call_window_start)} to "
            f"{_clock(campaign.call_window_end)}, {campaign.timezone}). Choose a time inside them.",
        )
    old = sc.scheduled_at
    sc.scheduled_at, sc.kind = when, "callback_time"  # a person chose this time now
    contact.callback_at = when
    audit(db, user.id, "reschedule", "campaign_callback", sc.id, {"from": old.isoformat(), "to": when.isoformat()})
    await db.commit()
    return (await _rows(db, [(sc, campaign, contact)], user))[0]


@router.post("/{scheduled_call_id}/cancel", response_model=CampaignCallbackOut)
async def cancel_campaign_callback(scheduled_call_id: uuid.UUID, db: DB, user: ManagerUser):
    """Cancel one call-back. The contact is marked not reached for this run; nothing else in the campaign changes."""
    sc, campaign, contact = await _one(db, scheduled_call_id, user)
    sc.status = ScheduledCallStatus.CANCELLED
    contact.status, contact.callback_at = ContactStatus.FAILED, None
    audit(db, user.id, "cancel", "campaign_callback", sc.id, {"customer": str(contact.customer_id)})
    await db.flush()
    await call_service.check_campaign_completion(db, campaign)  # it may have been the campaign's last pending contact
    await db.commit()
    return (await _rows(db, [(sc, campaign, contact)], user))[0]


async def due_counts(db: DB, company: Company) -> dict[str, int]:
    """For the dashboard: campaign call-backs still to make today, and how many are late."""
    now = datetime.now(UTC)
    tz = _tz(company.timezone)
    end_of_day = datetime.combine(datetime.now(tz).date() + timedelta(days=1), time.min, tzinfo=tz)
    waiting = (
        select(func.count(ScheduledCall.id))
        .join(Campaign, Campaign.id == ScheduledCall.campaign_id)
        .where(Campaign.company_id == company.id, ScheduledCall.kind.in_(KINDS), ScheduledCall.status.in_(WAITING))
    )
    return {
        "due_today": await db.scalar(waiting.where(ScheduledCall.scheduled_at < end_of_day)) or 0,
        "overdue": await db.scalar(waiting.where(ScheduledCall.scheduled_at < now - LATE_AFTER)) or 0,
    }

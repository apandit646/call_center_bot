"""Reports, the unanswered-questions list and callbacks: what happened, what the assistant could not
answer, and who asked to be called back. Everything is for the caller's own company only."""
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DB, ManagerUser, PageParams, TenantUser, owned
from app.models import Call, Callback, Campaign, Company, Conversation, KnowledgeGap, SupportCall, Ticket, User
from app.models.enums import OPEN_TICKET_STATUSES, CallbackStatus, CallOutcome, CallStatus, Channel, ConversationStatus
from app.schemas.common import ORMModel, Page
from app.schemas.support import CustomerBrief, DomainBrief
from app.services.audit import audit

router = APIRouter(tags=["insights"])
CHANNELS = [Channel.WHATSAPP, Channel.PHONE, Channel.EMAIL]


# --------------------------------------------------------------------------- reports
@router.get("/reports/overview")
async def overview(db: DB, user: TenantUser, days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """Day-by-day numbers for the last `days` days (in the company's own timezone), for the Reports page."""
    cid = user.company_id
    company = await db.get(Company, cid)
    try:
        tz = ZoneInfo(company.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    today = datetime.now(tz).date()
    first = today - timedelta(days=days - 1)
    since = datetime.combine(first, time.min, tzinfo=tz).astimezone(UTC)
    dates = [first + timedelta(days=i) for i in range(days)]
    day = lambda moment: moment.astimezone(tz).date().isoformat()  # noqa: E731

    tickets = (
        await db.execute(
            select(Ticket.created_at, Ticket.resolved_at, Ticket.source, Ticket.status, Ticket.assigned_to_id, Ticket.conversation_id)
            .where(Ticket.company_id == cid, (Ticket.created_at >= since) | (Ticket.resolved_at >= since))
        )
    ).all()
    conversations = (
        await db.execute(
            select(Conversation.id, Conversation.created_at, Conversation.channel, Conversation.status)
            .where(Conversation.company_id == cid, Conversation.created_at >= since)
        )
    ).all()
    calls = (
        await db.execute(
            select(SupportCall.started_at, SupportCall.outcome, SupportCall.status)
            .where(SupportCall.company_id == cid, SupportCall.started_at >= since)
        )
    ).all()

    daily = {d.isoformat(): {"date": d.isoformat(), "tickets_created": 0, "tickets_resolved": 0, "conversations": 0, "inbound_calls": 0} for d in dates}
    created = [t for t in tickets if t.created_at >= since]
    resolved = [t for t in tickets if t.resolved_at is not None and t.resolved_at >= since]
    for t in created:
        daily[day(t.created_at)]["tickets_created"] += 1
    for t in resolved:
        daily[day(t.resolved_at)]["tickets_resolved"] += 1
    for c in conversations:
        daily[day(c.created_at)]["conversations"] += 1
    for call in calls:
        daily[day(call.started_at)]["inbound_calls"] += 1

    # A conversation the assistant handled alone: it is finished and no ticket was needed.
    with_ticket = {t.conversation_id for t in tickets if t.conversation_id}
    with_ticket |= set(
        (await db.scalars(select(Ticket.conversation_id).where(Ticket.conversation_id.in_([c.id for c in conversations])))).all()
    ) if conversations else set()
    by_channel = {ch.value: {"channel": ch.value, "conversations": 0, "finished": 0, "resolved_by_ai": 0, "tickets": 0} for ch in CHANNELS}
    for c in conversations:
        row = by_channel.get(c.channel)
        if row is None:
            continue
        row["conversations"] += 1
        if c.status != ConversationStatus.OPEN:
            row["finished"] += 1
            if c.status == ConversationStatus.RESOLVED and c.id not in with_ticket:
                row["resolved_by_ai"] += 1
    for t in created:
        if t.source in by_channel:
            by_channel[t.source]["tickets"] += 1
    finished = sum(r["finished"] for r in by_channel.values())
    ai_resolved = sum(r["resolved_by_ai"] for r in by_channel.values())

    outcomes = Counter(call.outcome or ("in_progress" if call.status == CallStatus.IN_PROGRESS else CallOutcome.ABANDONED.value) for call in calls)
    hours = [(t.resolved_at - t.created_at).total_seconds() / 3600 for t in resolved]
    names = dict(
        (await db.execute(select(User.id, User.full_name).where(User.company_id == cid))).all()
    )
    per_agent = Counter(t.assigned_to_id for t in resolved if t.assigned_to_id)

    async def count(stmt) -> int:
        return await db.scalar(stmt) or 0

    campaign_calls = (
        await db.execute(
            select(Call.status, func.count(Call.id)).join(Campaign, Campaign.id == Call.campaign_id)
            .where(Campaign.company_id == cid, Call.created_at >= since).group_by(Call.status)
        )
    ).all()
    campaign_by_status = defaultdict(int, {s: n for s, n in campaign_calls})
    callbacks = dict(
        (await db.execute(
            select(Callback.status, func.count(Callback.id)).where(Callback.company_id == cid, Callback.created_at >= since)
            .group_by(Callback.status)
        )).all()
    )
    return {
        "days": days, "from": first.isoformat(), "to": today.isoformat(), "timezone": str(tz),
        "totals": {
            "tickets_created": len(created),
            "tickets_resolved": len(resolved),
            "tickets_open": await count(select(func.count(Ticket.id)).where(Ticket.company_id == cid, Ticket.status.in_(OPEN_TICKET_STATUSES))),
            "avg_resolution_hours": round(sum(hours) / len(hours), 1) if hours else None,
            "conversations": len(conversations),
            "conversations_finished": finished,
            "resolved_by_ai": ai_resolved,
            "ai_resolved_rate": round(ai_resolved / finished, 3) if finished else None,
            "inbound_calls": len(calls),
            "campaign_calls": sum(campaign_by_status.values()),
            "campaign_calls_completed": campaign_by_status[CallStatus.COMPLETED],
            "callbacks_booked": sum(callbacks.values()),
            "callbacks_completed": callbacks.get(CallbackStatus.COMPLETED, 0),
            "open_gaps": await count(select(func.count(KnowledgeGap.id)).where(KnowledgeGap.company_id == cid, KnowledgeGap.status == "open")),
        },
        "daily": list(daily.values()),
        "channels": list(by_channel.values()),
        "call_outcomes": [{"outcome": k, "count": v} for k, v in outcomes.most_common()],
        "agents": [{"name": names.get(agent_id, "Former user"), "resolved": n} for agent_id, n in per_agent.most_common(5)],
    }


# --------------------------------------------------------------------------- unanswered questions
class GapOut(ORMModel):
    id: uuid.UUID
    question: str
    channel: str
    language: str | None
    reason: str
    status: str
    domain: DomainBrief | None
    conversation_id: uuid.UUID | None
    created_at: datetime
    resolved_at: datetime | None


@router.get("/knowledge/gaps", response_model=Page[GapOut])
async def list_gaps(db: DB, user: TenantUser, pg: PageParams, status_: str = Query("open", alias="status", pattern="^(open|resolved|all)$")):
    """Questions the assistant could not answer from the knowledge base, newest first."""
    stmt = select(KnowledgeGap).where(KnowledgeGap.company_id == user.company_id)
    if status_ != "all":
        stmt = stmt.where(KnowledgeGap.status == status_)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = (await db.scalars(stmt.order_by(KnowledgeGap.created_at.desc()).offset(pg.offset).limit(pg.size))).all()
    return Page(items=items, total=total, page=pg.page, size=pg.size)


@router.post("/knowledge/gaps/{gap_id}/{action}", response_model=GapOut)
async def set_gap(gap_id: uuid.UUID, action: str, db: DB, user: ManagerUser):
    """resolve = a document now covers it (or it needs none); reopen = it does not after all."""
    if action not in ("resolve", "reopen"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown action")
    gap = await owned(db, KnowledgeGap, gap_id, user, "Question")
    gap.status = "resolved" if action == "resolve" else "open"
    gap.resolved_at = datetime.now(UTC) if action == "resolve" else None
    await db.commit()
    return await db.get(KnowledgeGap, gap.id, populate_existing=True)


# --------------------------------------------------------------------------- callbacks
class CallbackOut(ORMModel):
    id: uuid.UUID
    customer: CustomerBrief
    phone: str
    due_at: datetime
    reason: str | None
    language: str | None
    status: str
    attempts: int
    error: str | None
    source_conversation_id: uuid.UUID | None
    conversation_id: uuid.UUID | None = None  # the conversation of the call that was placed
    created_at: datetime


class CallbackEdit(BaseModel):
    due_at: datetime


async def _callback_out(db: DB, callbacks: list[Callback]) -> list[CallbackOut]:
    call_ids = [c.support_call_id for c in callbacks if c.support_call_id]
    conversations = dict(
        (await db.execute(select(SupportCall.id, SupportCall.conversation_id).where(SupportCall.id.in_(call_ids)))).all()
    ) if call_ids else {}
    out = []
    for c in callbacks:
        item = CallbackOut.model_validate(c)
        item.conversation_id = conversations.get(c.support_call_id)
        out.append(item)
    return out


@router.get("/callbacks", response_model=Page[CallbackOut])
async def list_callbacks(db: DB, user: TenantUser, pg: PageParams, status_: CallbackStatus | None = Query(None, alias="status")):
    """Calls customers asked for. Upcoming ones first, then the most recent."""
    stmt = select(Callback).where(Callback.company_id == user.company_id)
    if status_:
        stmt = stmt.where(Callback.status == status_)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    pending = Callback.status.in_([CallbackStatus.SCHEDULED, CallbackStatus.CALLING])
    rows = (
        await db.scalars(stmt.order_by(pending.desc(), Callback.due_at.desc()).offset(pg.offset).limit(pg.size))
    ).unique().all()
    return Page(items=await _callback_out(db, list(rows)), total=total, page=pg.page, size=pg.size)


@router.post("/callbacks/{callback_id}/cancel", response_model=CallbackOut)
async def cancel_callback(callback_id: uuid.UUID, db: DB, user: TenantUser):
    callback = await owned(db, Callback, callback_id, user, "Callback")
    if callback.status != CallbackStatus.SCHEDULED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a callback that is still waiting can be cancelled")
    callback.status = CallbackStatus.CANCELLED
    audit(db, user.id, "cancel", "callback", callback.id)
    await db.commit()
    return (await _callback_out(db, [await db.get(Callback, callback.id, populate_existing=True)]))[0]


@router.patch("/callbacks/{callback_id}", response_model=CallbackOut)
async def reschedule_callback(callback_id: uuid.UUID, body: CallbackEdit, db: DB, user: TenantUser):
    """Move a waiting callback, or try a failed / cancelled one again at a new time."""
    callback = await owned(db, Callback, callback_id, user, "Callback")
    if callback.status in (CallbackStatus.CALLING, CallbackStatus.COMPLETED):
        raise HTTPException(status.HTTP_409_CONFLICT, "This callback is already being made or is done")
    due = body.due_at if body.due_at.tzinfo else body.due_at.replace(tzinfo=UTC)
    if due < datetime.now(UTC) - timedelta(minutes=1):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose a time in the future")
    callback.due_at, callback.status, callback.attempts, callback.error = due, CallbackStatus.SCHEDULED, 0, None
    audit(db, user.id, "reschedule", "callback", callback.id, {"due_at": due.isoformat()})
    await db.commit()
    return (await _callback_out(db, [await db.get(Callback, callback.id, populate_existing=True)]))[0]

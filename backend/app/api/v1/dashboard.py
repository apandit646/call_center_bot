from datetime import UTC, datetime, time

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser, PageParams, TenantUser
from app.models import AuditLog, Call, Campaign, Company, Conversation, SupportCall, Ticket, User
from app.models.enums import (
    OPEN_TICKET_STATUSES,
    CallOutcome,
    CallStatus,
    CampaignStatus,
    Channel,
    ConversationStatus,
    TicketSource,
)
from app.services import company_service, voice_support

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary")
async def summary(db: DB, user: TenantUser):
    """Today's numbers for the caller's company only."""
    cid = user.company_id
    today = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    company = await db.get(Company, cid)

    async def count(stmt) -> int:
        return await db.scalar(stmt) or 0

    def calls(*where):
        return (
            select(func.count(Call.id))
            .join(Campaign, Campaign.id == Call.campaign_id)
            .where(Campaign.company_id == cid, *where)
        )

    tickets_by_priority = dict(
        (
            await db.execute(
                select(Ticket.priority, func.count())
                .where(Ticket.company_id == cid, Ticket.status.in_(list(OPEN_TICKET_STATUSES)))
                .group_by(Ticket.priority)
            )
        ).all()
    )
    # The WhatsApp block counts chats only; phone conversations are reported under "inbound".
    conversations = select(func.count(Conversation.id)).where(
        Conversation.company_id == cid, Conversation.channel == Channel.WHATSAPP
    )
    tickets = select(func.count(Ticket.id)).where(Ticket.company_id == cid)
    inbound = select(func.count(SupportCall.id)).where(SupportCall.company_id == cid)
    return {
        "whatsapp": {
            "conversations_today": await count(conversations.where(Conversation.created_at >= today)),
            "open_conversations": await count(conversations.where(Conversation.status == ConversationStatus.OPEN)),
            "resolved_by_ai_today": await count(
                conversations.where(Conversation.status == ConversationStatus.RESOLVED, Conversation.updated_at >= today)
            ),
            "escalated_today": await count(
                conversations.where(Conversation.status == ConversationStatus.ESCALATED, Conversation.updated_at >= today)
            ),
        },
        "tickets": {
            "open": sum(tickets_by_priority.values()),
            "unassigned": await count(
                tickets.where(Ticket.status.in_(list(OPEN_TICKET_STATUSES)), Ticket.assigned_to_id.is_(None))
            ),
            "created_today": await count(tickets.where(Ticket.created_at >= today)),
            "open_by_priority": tickets_by_priority,
        },
        "outbound": {
            "running_campaigns": await count(
                select(func.count(Campaign.id)).where(Campaign.company_id == cid, Campaign.status == CampaignStatus.RUNNING)
            ),
            "calls_today": await count(calls(Call.created_at >= today)),
            "completed_calls_today": await count(calls(Call.created_at >= today, Call.status == CallStatus.COMPLETED)),
            "active_calls": await count(
                calls(Call.status.in_([CallStatus.INITIATED, CallStatus.RINGING, CallStatus.IN_PROGRESS]))
            ),
        },
        "inbound": {
            "active": voice_support.inbound_active(company),
            "calls_today": await count(inbound.where(SupportCall.started_at >= today)),
            "answered_by_ai_today": await count(
                inbound.where(SupportCall.started_at >= today, SupportCall.outcome == CallOutcome.ANSWERED)
            ),
            "tickets_today": await count(
                tickets.where(Ticket.created_at >= today, Ticket.source == TicketSource.PHONE)
            ),
            "active_calls": await count(inbound.where(SupportCall.status == CallStatus.IN_PROGRESS)),
        },
        # Usage against the limits the platform admin set (null limit = unlimited).
        "usage": await company_service.usage(db, company),
    }


@router.get("/audit-logs")
async def audit_logs(db: DB, user: AdminUser, pg: PageParams, entity_type: str | None = None):
    # A company sees the actions of its own users.
    stmt = select(AuditLog).join(User, User.id == AuditLog.actor_id).where(User.company_id == user.company_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await db.scalars(stmt.order_by(AuditLog.created_at.desc()).offset(pg.offset).limit(pg.size))).all()
    return {
        "items": [
            {
                "id": str(r.id), "actor_id": str(r.actor_id) if r.actor_id else None, "action": r.action,
                "entity_type": r.entity_type, "entity_id": r.entity_id, "data": r.data,
                "ip_address": r.ip_address, "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total, "page": pg.page, "size": pg.size,
    }

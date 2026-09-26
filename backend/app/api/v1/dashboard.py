from datetime import UTC, datetime, time

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser, CurrentUser, PageParams
from app.models import AuditLog, Call, Campaign, Conversation, Ticket
from app.models.enums import OPEN_TICKET_STATUSES, CallStatus, CampaignStatus, ConversationStatus

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary")
async def summary(db: DB, _: CurrentUser):
    today = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)

    async def count(stmt) -> int:
        return await db.scalar(stmt) or 0

    tickets_by_priority = dict(
        (
            await db.execute(
                select(Ticket.priority, func.count())
                .where(Ticket.status.in_(list(OPEN_TICKET_STATUSES)))
                .group_by(Ticket.priority)
            )
        ).all()
    )
    return {
        "whatsapp": {
            "conversations_today": await count(select(func.count(Conversation.id)).where(Conversation.created_at >= today)),
            "open_conversations": await count(
                select(func.count(Conversation.id)).where(Conversation.status == ConversationStatus.OPEN)
            ),
            "resolved_by_ai_today": await count(
                select(func.count(Conversation.id)).where(
                    Conversation.status == ConversationStatus.RESOLVED, Conversation.updated_at >= today
                )
            ),
            "escalated_today": await count(
                select(func.count(Conversation.id)).where(
                    Conversation.status == ConversationStatus.ESCALATED, Conversation.updated_at >= today
                )
            ),
        },
        "tickets": {
            "open": sum(tickets_by_priority.values()),
            "unassigned": await count(
                select(func.count(Ticket.id)).where(
                    Ticket.status.in_(list(OPEN_TICKET_STATUSES)), Ticket.assigned_to_id.is_(None)
                )
            ),
            "created_today": await count(select(func.count(Ticket.id)).where(Ticket.created_at >= today)),
            "open_by_priority": tickets_by_priority,
        },
        "outbound": {
            "running_campaigns": await count(
                select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.RUNNING)
            ),
            "calls_today": await count(select(func.count(Call.id)).where(Call.created_at >= today)),
            "completed_calls_today": await count(
                select(func.count(Call.id)).where(Call.created_at >= today, Call.status == CallStatus.COMPLETED)
            ),
            "active_calls": await count(
                select(func.count(Call.id)).where(
                    Call.status.in_([CallStatus.INITIATED, CallStatus.RINGING, CallStatus.IN_PROGRESS])
                )
            ),
        },
    }


@router.get("/audit-logs")
async def audit_logs(db: DB, _: AdminUser, pg: PageParams, entity_type: str | None = None):
    stmt = select(AuditLog)
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

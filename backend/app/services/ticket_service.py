import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentAssignment, AgentProfile, Domain, Ticket, TicketEvent, User
from app.models.enums import OPEN_TICKET_STATUSES, TicketStatus, UserRole
from app.services.queue import Queues, enqueue

log = logging.getLogger(__name__)


def add_event(
    db: AsyncSession, ticket: Ticket, event_type: str, actor_id: uuid.UUID | None = None, **data: Any
) -> None:
    db.add(TicketEvent(ticket_id=ticket.id, actor_id=actor_id, event_type=event_type, data=data))


async def create_ticket(db: AsyncSession, *, actor_id: uuid.UUID | None = None, **fields: Any) -> Ticket:
    """Create a ticket, record the event and queue it for auto-assignment. Commits."""
    ticket = Ticket(**fields)
    db.add(ticket)
    await db.flush()
    add_event(db, ticket, "created", actor_id, source=ticket.source, priority=ticket.priority)
    await db.commit()
    await db.refresh(ticket)
    if ticket.assigned_to_id is None:
        await enqueue(Queues.TICKET_ASSIGNMENT, {"ticket_id": str(ticket.id)})
    return ticket


async def assign_ticket(
    db: AsyncSession, ticket: Ticket, agent_id: uuid.UUID | None, actor_id: uuid.UUID | None = None
) -> None:
    """Assign (or unassign with agent_id=None). Caller commits."""
    if ticket.assigned_to_id == agent_id:
        return
    now = datetime.now(UTC)
    await db.execute(
        update(AgentAssignment)
        .where(AgentAssignment.ticket_id == ticket.id, AgentAssignment.is_active.is_(True))
        .values(is_active=False, unassigned_at=now)
    )
    previous = ticket.assigned_to_id
    ticket.assigned_to_id = agent_id
    if agent_id:
        db.add(AgentAssignment(ticket_id=ticket.id, agent_id=agent_id, assigned_by_id=actor_id))
        if ticket.status == TicketStatus.OPEN:
            ticket.status = TicketStatus.IN_PROGRESS
    add_event(
        db, ticket, "assigned", actor_id,
        from_agent_id=str(previous) if previous else None,
        to_agent_id=str(agent_id) if agent_id else None,
        auto=actor_id is None,
    )


def change_status(ticket: Ticket, db: AsyncSession, status: str, actor_id: uuid.UUID | None) -> None:
    if ticket.status == status:
        return
    add_event(db, ticket, "status_changed", actor_id, **{"from": ticket.status, "to": status})
    ticket.status = status
    if status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        ticket.resolved_at = datetime.now(UTC)


async def auto_assign(db: AsyncSession, ticket_id: uuid.UUID) -> uuid.UUID | None:
    """Assign to the least-loaded available agent who handles the ticket's domain."""
    ticket = await db.get(Ticket, ticket_id, with_for_update={"of": Ticket})
    if ticket is None or ticket.assigned_to_id or ticket.status not in OPEN_TICKET_STATUSES:
        return None

    domain_code = None
    if ticket.domain_id:
        domain = await db.get(Domain, ticket.domain_id)
        domain_code = domain.code if domain else None

    open_count = (
        select(func.count(Ticket.id))
        .where(Ticket.assigned_to_id == User.id, Ticket.status.in_(list(OPEN_TICKET_STATUSES)))
        .correlate(User)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(User, AgentProfile, open_count.label("load"))
            .join(AgentProfile, AgentProfile.user_id == User.id)
            .where(User.is_active.is_(True), User.role == UserRole.AGENT, AgentProfile.is_available.is_(True))
            .order_by(open_count)
        )
    ).all()

    for user, profile, load in rows:
        handles_domain = not profile.domain_codes or (domain_code in profile.domain_codes)
        if handles_domain and load < profile.max_open_tickets:
            await assign_ticket(db, ticket, user.id)
            await db.commit()
            log.info("Auto-assigned ticket #%s to %s", ticket.number, user.email)
            return user.id

    await db.commit()
    log.info("No available agent for ticket #%s; left unassigned", ticket.number)
    return None

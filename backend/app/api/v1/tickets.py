import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, PageParams, TenantUser, owned
from app.models import Customer, Domain, Ticket, TicketEvent, User
from app.models.enums import TicketPriority, TicketSource, TicketStatus, UserRole
from app.schemas.common import Page
from app.schemas.support import (
    TicketAssign,
    TicketComment,
    TicketCreate,
    TicketDetail,
    TicketEventOut,
    TicketOut,
    TicketUpdate,
)
from app.services import ticket_service

router = APIRouter(prefix="/tickets", tags=["tickets"])


async def _load(db: DB, ticket_id: uuid.UUID, user: User) -> Ticket:
    ticket = await owned(db, Ticket, ticket_id, user, "Ticket")
    if user.role == UserRole.AGENT and ticket.assigned_to_id not in (None, user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This ticket is assigned to another agent")
    return ticket


async def _detail(db: DB, ticket: Ticket) -> TicketDetail:
    events = (
        await db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id).order_by(TicketEvent.created_at))
    ).all()
    return TicketDetail(
        **TicketOut.model_validate(ticket).model_dump(), events=[TicketEventOut.model_validate(e) for e in events]
    )


@router.get("", response_model=Page[TicketOut])
async def list_tickets(
    db: DB,
    user: TenantUser,
    pg: PageParams,
    status_: TicketStatus | None = Query(None, alias="status"),
    priority: TicketPriority | None = None,
    domain_id: uuid.UUID | None = None,
    assigned_to_id: uuid.UUID | None = None,
    mine: bool = False,
    unassigned: bool = False,
    q: str | None = None,
):
    stmt = select(Ticket).join(Customer, Customer.id == Ticket.customer_id).where(Ticket.company_id == user.company_id)
    if status_:
        stmt = stmt.where(Ticket.status == status_)
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    if domain_id:
        stmt = stmt.where(Ticket.domain_id == domain_id)
    if assigned_to_id:
        stmt = stmt.where(Ticket.assigned_to_id == assigned_to_id)
    if mine or user.role == UserRole.AGENT and not unassigned:
        stmt = stmt.where(Ticket.assigned_to_id == user.id)
    if unassigned:
        stmt = stmt.where(Ticket.assigned_to_id.is_(None))
    if q:
        like = f"%{q}%"
        conds = [Ticket.issue.ilike(like), Customer.name.ilike(like), Customer.phone.ilike(like)]
        if q.lstrip("#").isdigit():
            conds.append(Ticket.number == int(q.lstrip("#")))
        stmt = stmt.where(or_(*conds))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = (await db.scalars(stmt.order_by(Ticket.created_at.desc()).offset(pg.offset).limit(pg.size))).unique().all()
    return Page(items=items, total=total, page=pg.page, size=pg.size)


@router.get("/{ticket_id}", response_model=TicketDetail)
async def get_ticket(ticket_id: uuid.UUID, db: DB, user: TenantUser):
    return await _detail(db, await _load(db, ticket_id, user))


@router.post("", response_model=TicketDetail, status_code=201)
async def create_ticket(body: TicketCreate, db: DB, user: TenantUser):
    await owned(db, Customer, body.customer_id, user, "Customer")
    if body.domain_id:
        await owned(db, Domain, body.domain_id, user, "Domain")
    ticket = await ticket_service.create_ticket(
        db, actor_id=user.id, source=TicketSource.MANUAL, company_id=user.company_id, **body.model_dump()
    )
    return await _detail(db, await db.get(Ticket, ticket.id, populate_existing=True))


@router.patch("/{ticket_id}", response_model=TicketDetail)
async def update_ticket(ticket_id: uuid.UUID, body: TicketUpdate, db: DB, user: TenantUser):
    ticket = await _load(db, ticket_id, user)
    if body.status:
        ticket_service.change_status(ticket, db, body.status, user.id)
    if body.priority and body.priority != ticket.priority:
        ticket_service.add_event(db, ticket, "priority_changed", user.id, **{"from": ticket.priority, "to": body.priority})
        ticket.priority = body.priority
    if "domain_id" in body.model_fields_set and body.domain_id != ticket.domain_id:
        if body.domain_id:
            await owned(db, Domain, body.domain_id, user, "Domain")
        ticket_service.add_event(db, ticket, "domain_changed", user.id, to=str(body.domain_id))
        ticket.domain_id = body.domain_id
    await db.commit()
    return await _detail(db, await db.get(Ticket, ticket.id, populate_existing=True))


@router.post("/{ticket_id}/assign", response_model=TicketDetail)
async def assign(ticket_id: uuid.UUID, body: TicketAssign, db: DB, user: TenantUser):
    ticket = await _load(db, ticket_id, user)
    if user.role == UserRole.AGENT and body.agent_id not in (user.id, None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Agents can only assign tickets to themselves")
    if body.agent_id:
        agent = await owned(db, User, body.agent_id, user, "Agent")
        if not agent.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    await ticket_service.assign_ticket(db, ticket, body.agent_id, user.id)
    await db.commit()
    return await _detail(db, await db.get(Ticket, ticket.id, populate_existing=True))


@router.post("/{ticket_id}/comments", response_model=TicketDetail)
async def comment(ticket_id: uuid.UUID, body: TicketComment, db: DB, user: TenantUser):
    ticket = await _load(db, ticket_id, user)
    ticket_service.add_event(db, ticket, "comment", user.id, text=body.text)
    await db.commit()
    return await _detail(db, ticket)

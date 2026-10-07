"""Inbound support calls of the caller's company: who called, when, and how each call ended."""
from fastapi import APIRouter
from sqlalchemy import func, or_, select

from app.api.deps import DB, PageParams, TenantUser
from app.models import Customer, SupportCall, Ticket
from app.models.enums import CallOutcome
from app.schemas.common import Page
from app.schemas.support import CustomerBrief, InboundCallOut

router = APIRouter(prefix="/inbound-calls", tags=["inbound calls"])


@router.get("", response_model=Page[InboundCallOut])
async def list_inbound_calls(
    db: DB, user: TenantUser, pg: PageParams, outcome: CallOutcome | None = None, q: str | None = None
):
    stmt = (
        select(SupportCall, Customer)
        .join(Customer, Customer.id == SupportCall.customer_id)
        .where(SupportCall.company_id == user.company_id)
    )
    if outcome:
        stmt = stmt.where(SupportCall.outcome == outcome)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.phone.ilike(like), SupportCall.from_number.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await db.execute(stmt.order_by(SupportCall.started_at.desc()).offset(pg.offset).limit(pg.size))).all()

    ticket_ids = [call.ticket_id for call, _ in rows if call.ticket_id]
    numbers = dict((await db.execute(select(Ticket.id, Ticket.number).where(Ticket.id.in_(ticket_ids)))).all()) if ticket_ids else {}
    items = []
    for call, customer in rows:
        item = InboundCallOut.model_validate(call)
        item.customer = CustomerBrief.model_validate(customer)
        item.has_recording = bool(call.recording_path)
        item.ticket_number = numbers.get(call.ticket_id)
        item.is_callback = bool((call.state or {}).get("callback_id"))
        items.append(item)
    return Page(items=items, total=total, page=pg.page, size=pg.size)

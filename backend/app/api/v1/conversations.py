import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, PageParams, TenantUser, owned
from app.models import Conversation, Customer, Message, Ticket
from app.models.enums import ConversationStatus
from app.schemas.common import Page
from app.schemas.support import AgentMessageCreate, ConversationDetail, ConversationOut, MessageOut
from app.services.conversation_manager import send_agent_message

router = APIRouter(prefix="/conversations", tags=["whatsapp conversations"])


@router.get("", response_model=Page[ConversationOut])
async def list_conversations(
    db: DB,
    user: TenantUser,
    pg: PageParams,
    status_: ConversationStatus | None = Query(None, alias="status"),
    domain_id: uuid.UUID | None = None,
    q: str | None = None,
):
    stmt = (
        select(Conversation)
        .join(Customer, Customer.id == Conversation.customer_id)
        .where(Conversation.company_id == user.company_id)
    )
    if status_:
        stmt = stmt.where(Conversation.status == status_)
    if domain_id:
        stmt = stmt.where(Conversation.domain_id == domain_id)
    if q:
        stmt = stmt.where(or_(Customer.name.ilike(f"%{q}%"), Customer.phone.ilike(f"%{q}%")))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = (
        await db.scalars(stmt.order_by(Conversation.last_message_at.desc()).offset(pg.offset).limit(pg.size))
    ).unique().all()
    return Page(items=items, total=total, page=pg.page, size=pg.size)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, db: DB, user: TenantUser):
    conversation = await owned(db, Conversation, conversation_id, user, "Conversation")
    messages = (
        await db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at))
    ).all()
    ticket_ids = (await db.scalars(select(Ticket.id).where(Ticket.conversation_id == conversation.id))).all()
    return ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in messages],
        ticket_ids=list(ticket_ids),
    )


@router.post("/{conversation_id}/messages", response_model=MessageOut, status_code=201)
async def reply_as_agent(conversation_id: uuid.UUID, body: AgentMessageCreate, db: DB, user: TenantUser):
    """Send a WhatsApp message to the customer as a human agent, from the company's own number."""
    conversation = await owned(db, Conversation, conversation_id, user, "Conversation")
    if conversation.status == ConversationStatus.CLOSED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Conversation is closed")
    try:
        return await send_agent_message(db, conversation, body.text, user.id)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"WhatsApp send failed: {exc}") from exc


@router.post("/{conversation_id}/close", response_model=ConversationOut)
async def close_conversation(conversation_id: uuid.UUID, db: DB, user: TenantUser):
    conversation = await owned(db, Conversation, conversation_id, user, "Conversation")
    conversation.status = ConversationStatus.CLOSED
    await db.commit()
    return conversation

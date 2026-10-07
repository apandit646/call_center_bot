"""WhatsApp and email webhooks: verify, persist, enqueue, return 200 fast. All work happens in the worker.

One URL serves every company. Each event names the WhatsApp number that received it
(metadata.phone_number_id); that number tells us which company the message belongs to.
"""
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import DB
from app.core.config import settings
from app.models import Message, WebhookEvent
from app.models.enums import WebhookStatus
from app.services import company_service, email_manager, whatsapp_client
from app.services.queue import Queues, enqueue

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/whatsapp")
async def verify_whatsapp(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
):
    if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed")


@router.post("/whatsapp")
async def receive_whatsapp(request: Request, db: DB):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from None

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            company = await company_service.company_for_whatsapp(db, phone_number_id)
            if company is None:
                log.warning("WhatsApp event for number %s: no active company has it connected; ignored", phone_number_id)
                continue
            # Nothing is stored or queued until the signature checks out against this company's own app secret.
            if not whatsapp_client.verify_signature(body, signature, company):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
            contacts = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}

            for msg in value.get("messages", []):
                event = {
                    "message": msg, "profile_name": contacts.get(msg.get("from")), "company_id": str(company.id),
                }
                stmt = (
                    insert(WebhookEvent)
                    .values(source="whatsapp", external_id=msg.get("id"), event_type=f"message.{msg.get('type')}",
                            payload=event)
                    .on_conflict_do_nothing(index_elements=["source", "external_id"])
                    .returning(WebhookEvent.id)
                )
                event_id = await db.scalar(stmt)
                await db.commit()
                if event_id:  # None = duplicate delivery from Meta
                    await enqueue(Queues.WHATSAPP_INCOMING, {"webhook_event_id": str(event_id)})

            for st in value.get("statuses", []):  # delivery receipts for our outbound messages
                msg = await db.scalar(
                    insert(WebhookEvent)
                    .values(source="whatsapp", external_id=f"{st.get('id')}:{st.get('status')}",
                            event_type="status", payload=st, status=WebhookStatus.PROCESSED)
                    .on_conflict_do_nothing(index_elements=["source", "external_id"])
                    .returning(WebhookEvent.id)
                )
                if msg:
                    await db.execute(
                        update(Message).where(Message.wa_message_id == st.get("id")).values(delivery_status=st.get("status"))
                    )
                await db.commit()
    return {"status": "ok"}


@router.post("/email/{token}")
async def receive_email(token: str, request: Request, db: DB):
    """SendGrid Inbound Parse posts each email sent to a company's support address here. The token in
    the address is that company's own (shown in its Company Settings), which is what tells us whose
    email this is and that it came from its SendGrid set-up."""
    company = await company_service.company_for_email_token(db, token)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown address")
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    email = email_manager.parse_inbound(form)
    if email is None:
        log.warning("Email for %s without a usable sender; ignored", company.name)
        return {"status": "ignored"}
    event = {
        "company_id": str(company.id), "message_id": email.message_id, "from_address": email.from_address,
        "from_name": email.from_name, "subject": email.subject, "text": email.text, "references": email.references,
        "automatic": email.automatic,
    }
    event_id = await db.scalar(
        insert(WebhookEvent)
        .values(source="email", external_id=f"{company.id}:{email.message_id}"[:255], event_type="message", payload=event)
        .on_conflict_do_nothing(index_elements=["source", "external_id"])
        .returning(WebhookEvent.id)
    )
    await db.commit()
    if event_id:  # None = SendGrid delivered the same email twice
        await enqueue(Queues.EMAIL_INCOMING, {"webhook_event_id": str(event_id)})
    return {"status": "ok"}

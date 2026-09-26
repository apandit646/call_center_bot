"""WhatsApp support workers: incoming messages (RAG chatbot), knowledge ingestion, ticket assignment.

Run: python -m app.workers.support_worker
"""
import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.database import SessionLocal
from app.core.redis import get_redis
from app.models import WebhookEvent
from app.models.enums import WebhookStatus
from app.services import conversation_manager, document_processing, ticket_service, whatsapp_client
from app.services.queue import Queues
from app.workers.base import run

log = logging.getLogger(__name__)
UNSUPPORTED_REPLY = (
    "Sorry, I can only read text messages right now. Please type your question.\n"
    "माफ़ कीजिए, अभी मैं सिर्फ़ text messages पढ़ सकता हूँ। कृपया अपना सवाल type करें।"
)


async def handle_whatsapp(payload: dict[str, Any]) -> None:
    event_id = uuid.UUID(payload["webhook_event_id"])
    async with SessionLocal() as db:
        event = await db.get(WebhookEvent, event_id)
        if event is None or event.status == WebhookStatus.PROCESSED:
            return
        message = event.payload["message"]
        wa_id = message["from"]
        text = (message.get("text") or {}).get("body") or (message.get("button") or {}).get("text")
        interactive = message.get("interactive") or {}
        text = text or (interactive.get("button_reply") or interactive.get("list_reply") or {}).get("title")
        inbound = conversation_manager.InboundMessage(
            wa_id=wa_id,
            wa_message_id=message["id"],
            text=text or f"[{message.get('type', 'unknown')} message]",
            profile_name=event.payload.get("profile_name"),
        )

        # One customer's messages are processed strictly in order, even across worker replicas.
        async with get_redis().lock(f"lock:whatsapp:{wa_id}", timeout=120, blocking_timeout=90):
            try:
                if text:
                    await conversation_manager.handle_inbound(db, inbound)
                else:
                    await whatsapp_client.send_text(wa_id, UNSUPPORTED_REPLY)
                event.status = WebhookStatus.PROCESSED if text else WebhookStatus.IGNORED
            except Exception as exc:
                await db.rollback()
                event = await db.get(WebhookEvent, event_id)
                event.status = WebhookStatus.FAILED
                event.error = str(exc)[:2000]
                log.exception("Failed processing WhatsApp message %s", inbound.wa_message_id)
            event.processed_at = datetime.now(UTC)
            await db.commit()


async def handle_ingest(payload: dict[str, Any]) -> None:
    async with SessionLocal() as db:
        await document_processing.process_document(db, uuid.UUID(payload["document_id"]))


async def handle_assignment(payload: dict[str, Any]) -> None:
    async with SessionLocal() as db:
        await ticket_service.auto_assign(db, uuid.UUID(payload["ticket_id"]))


if __name__ == "__main__":
    asyncio.run(
        run([
            (Queues.WHATSAPP_INCOMING, handle_whatsapp, 8),
            (Queues.KNOWLEDGE_INGEST, handle_ingest, 2),
            (Queues.TICKET_ASSIGNMENT, handle_assignment, 1),
        ])
    )

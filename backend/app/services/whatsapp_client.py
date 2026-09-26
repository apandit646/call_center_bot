"""WhatsApp Business Cloud API client."""
import hashlib
import hmac
import logging
import uuid

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)


def verify_signature(body: bytes, signature_header: str | None) -> bool:
    """Validate Meta's X-Hub-Signature-256 header. Skipped when no app secret is configured."""
    if not settings.WHATSAPP_APP_SECRET:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


async def send_text(to_wa_id: str, body: str) -> str:
    """Send a text message and return the WhatsApp message id."""
    if not (settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID):
        fake_id = f"local-{uuid.uuid4().hex}"
        log.info("[whatsapp:dry-run] to=%s id=%s body=%r", to_wa_id, fake_id, body)
        return fake_id

    url = f"{settings.WHATSAPP_API_BASE}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_wa_id,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"})
        resp.raise_for_status()
        return resp.json()["messages"][0]["id"]

"""WhatsApp Business Cloud API client. Each company uses only its own number, access token and
Meta app secret (see company_service); until it has saved all three, WhatsApp is off for it."""
import hashlib
import hmac
import logging

import httpx

from app.core.config import settings
from app.models import Company
from app.services.company_service import whatsapp_credentials

log = logging.getLogger(__name__)

NOT_CONNECTED = (
    "WhatsApp is not connected for this company. Add the Phone number ID, access token and app secret "
    "in Company Settings."
)


class WhatsAppNotConnected(RuntimeError):
    pass


def verify_signature(body: bytes, signature_header: str | None, company: Company | None) -> bool:
    """Validate Meta's X-Hub-Signature-256 header with the company's own Meta app secret.

    Always enforced: a company without WhatsApp connected has no secret, so nothing verifies for it."""
    creds = whatsapp_credentials(company)
    if creds is None or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(creds.app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


async def send_text(to_wa_id: str, body: str, company: Company | None) -> str:
    """Send a text message from the company's number and return the WhatsApp message id."""
    creds = whatsapp_credentials(company)
    if creds is None:
        raise WhatsAppNotConnected(NOT_CONNECTED)

    url = f"{settings.WHATSAPP_API_BASE}/{creds.phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_wa_id,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {creds.access_token}"})
        resp.raise_for_status()
        return resp.json()["messages"][0]["id"]

import ipaddress
import logging
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.redis import get_redis
from app.models import Company
from app.services.company_service import (
    ExotelCredentials,
    calling_credentials,
    exotel_credentials,
    twilio_credentials,
)
from app.services.telephony.base import Heard, StatusUpdate, TelephonyProvider
from app.services.telephony.mock import NOT_CONFIGURED

log = logging.getLogger(__name__)
_REACHABLE_KEY = "public_url:reachable"


def get_provider(company: Company | None = None, name: str | None = None) -> TelephonyProvider:
    """The telephony provider for one company's calls: the account of the provider it chose
    (Twilio or Exotel), or the mock provider (which refuses to dial) until it connects one.

    name: the provider a call was actually placed on. A call in progress finishes on that
    provider even if the company switches meanwhile."""
    if name == "twilio":
        creds = twilio_credentials(company)
    elif name == "exotel":
        creds = exotel_credentials(company)
    else:
        creds = calling_credentials(company)
    if isinstance(creds, ExotelCredentials):
        from app.services.telephony.exotel import ExotelProvider

        return ExotelProvider(creds)
    if creds is not None:
        from app.services.telephony.twilio import TwilioProvider

        return TwilioProvider(creds.account_sid, creds.auth_token, creds.from_number)
    from app.services.telephony.mock import MockProvider

    return MockProvider()


BILLING_HOLD = (
    "Outgoing calls are on hold because an invoice is overdue. Pay it from the Billing page and calls start again "
    "automatically. Inbound calls, WhatsApp and email are not affected."
)


def calling_problem(company: Company | None) -> str | None:
    """Why this company cannot place calls right now, or None if it can.

    Calling is off until the company connects its own account with the provider it chose (there is
    no shared one), and the provider must reach our callbacks, audio and audio stream over the
    internet, so localhost/LAN addresses fail.
    """
    if calling_credentials(company) is None:
        return NOT_CONFIGURED
    url = settings.PUBLIC_BASE_URL
    host = (urlparse(url).hostname or "").lower()
    private = host in {"localhost", ""} or "." not in host
    try:
        ip = ipaddress.ip_address(host)
        private = private or ip.is_private or ip.is_loopback or ip.is_unspecified
    except ValueError:
        pass
    if private:
        return (
            f"PUBLIC_BASE_URL is '{url}', which the telephony provider cannot reach. Expose the API with "
            "'ngrok http 8000' (or cloudflared) and set PUBLIC_BASE_URL to the public https URL, then restart."
        )
    if company is not None and company.telephony_provider == "exotel" and not url.lower().startswith("https://"):
        return f"PUBLIC_BASE_URL is '{url}'. Exotel needs a secure address (https), because its audio stream uses wss."
    return None


async def public_url_down() -> str | None:
    """Why the telephony provider would fail to reach us right now, or None if our public address answers.

    calling_problem only looks at how the address is written. A tunnel (ngrok / cloudflared) can die
    while the address stays in .env; the provider would then still dial the customer and, getting no
    instructions from us, play them its "application error" message and hang up. So we ask our own
    public address for /health before dialling. A good answer is remembered for a minute; a bad one
    is never cached, so calls resume as soon as the address works again.
    """
    if not settings.PUBLIC_URL_CHECK:
        return None
    redis = get_redis()
    try:
        if await redis.get(_REACHABLE_KEY):
            return None
    except Exception:  # Redis hiccup: fall through and check for real
        pass
    url = settings.PUBLIC_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
            resp = await client.get(f"{url}/health")
        ok = resp.status_code < 500 and "status" in resp.text  # our own /health, not a tunnel error page
        detail = f"it answered HTTP {resp.status_code}"
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"[:160] or "no answer"
    if ok:
        try:
            await redis.set(_REACHABLE_KEY, "1", ex=60)
        except Exception:
            pass
        return None
    log.error("PUBLIC_BASE_URL %s is not reachable (%s)", url, detail)
    return (
        f"The public address '{url}' is not answering from the internet ({detail}), so the telephony provider "
        "cannot reach this system and callers would only hear an error message. Start the tunnel again "
        "(ngrok / cloudflared), put its https address in PUBLIC_BASE_URL, and restart."
    )


async def calling_blocker(company: Company | None) -> str | None:
    """Everything that stops this company's outgoing calls right now: an overdue invoice, its setup,
    then whether we are reachable."""
    if company is not None and company.billing_hold:
        return BILLING_HOLD
    return calling_problem(company) or await public_url_down()


__all__ = [
    "Heard", "StatusUpdate", "TelephonyProvider", "calling_blocker", "calling_problem", "get_provider", "public_url_down",
]

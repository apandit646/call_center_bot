import ipaddress
from functools import lru_cache
from urllib.parse import urlparse

from app.core.config import settings
from app.services.telephony.base import Heard, StatusUpdate, TelephonyProvider


@lru_cache
def get_provider() -> TelephonyProvider:
    if settings.TELEPHONY_PROVIDER == "twilio":
        from app.services.telephony.twilio import TwilioProvider

        return TwilioProvider()
    from app.services.telephony.mock import MockProvider

    return MockProvider()


def public_url_problem() -> str | None:
    """Why real calls can't work with the current PUBLIC_BASE_URL, or None if it looks usable.

    Twilio must reach our callbacks and audio over the internet, so localhost/LAN addresses fail.
    """
    if settings.TELEPHONY_PROVIDER == "mock":
        return None
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
    return None


__all__ = ["Heard", "StatusUpdate", "TelephonyProvider", "get_provider", "public_url_problem"]

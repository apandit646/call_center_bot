import ipaddress
from urllib.parse import urlparse

from app.core.config import settings
from app.models import Company
from app.services.company_service import twilio_credentials
from app.services.telephony.base import Heard, StatusUpdate, TelephonyProvider
from app.services.telephony.mock import NOT_CONFIGURED


def get_provider(company: Company | None = None) -> TelephonyProvider:
    """The telephony provider for one company's calls: its own Twilio account and number, or the
    mock provider (which refuses to dial) until the company connects one."""
    creds = twilio_credentials(company)
    if creds is not None:
        from app.services.telephony.twilio import TwilioProvider

        return TwilioProvider(creds.account_sid, creds.auth_token, creds.from_number)
    from app.services.telephony.mock import MockProvider

    return MockProvider()


def calling_problem(company: Company | None) -> str | None:
    """Why this company cannot place calls right now, or None if it can.

    Calling is off until the company connects its own Twilio account (there is no shared one), and
    Twilio must reach our callbacks and audio over the internet, so localhost/LAN addresses fail.
    """
    if twilio_credentials(company) is None:
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
    return None


__all__ = ["Heard", "StatusUpdate", "TelephonyProvider", "calling_problem", "get_provider"]

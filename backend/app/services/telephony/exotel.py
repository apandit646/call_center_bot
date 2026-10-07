"""Exotel voice.

Exotel does not let our server steer a call step by step the way Twilio does (no TwiML). The call
runs a flow the company built in its Exotel dashboard, and that flow's **Voicebot applet** opens a
two-way audio WebSocket to us for the conversation (services/voice_stream.py). So this provider
only does the REST side: start an outbound call into the flow, read status callbacks, fetch
recordings. The flow is:  Voicebot applet -> Passthru applet -> Hangup.

Exotel does not sign its callbacks, so each one carries a key derived from a per-company secret.
"""
import hashlib
import hmac
import logging
import uuid

import httpx
from fastapi import Request, Response

from app.core.config import settings
from app.models.enums import CallStatus
from app.services import speech
from app.services.company_service import ExotelCredentials
from app.services.telephony.base import Heard, StatusUpdate, TelephonyProvider
from app.services.voice_qa import VoiceAction

log = logging.getLogger(__name__)

_STATUS_MAP = {
    "queued": CallStatus.INITIATED,
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.IN_PROGRESS,
    "completed": CallStatus.COMPLETED,
    "busy": CallStatus.BUSY,
    "no-answer": CallStatus.NO_ANSWER,
    "failed": CallStatus.FAILED,
    "canceled": CallStatus.CANCELED,
}


def _base() -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/telephony/exotel"


def callback_key(secret: str, purpose: str) -> str:
    """The key a callback URL must carry. Derived, so the stream password itself is never put in a URL query."""
    return hmac.new(secret.encode(), purpose.encode(), hashlib.sha256).hexdigest()[:40]


def valid_key(secret: str, purpose: str, key: str | None) -> bool:
    return bool(key) and hmac.compare_digest(callback_key(secret, purpose), key)


def stream_url(company_id: uuid.UUID, secret: str | None = None) -> str:
    """The WebSocket address for the Voicebot applet. With `secret`, it ends in the key that lets
    Exotel in. The key is part of the path, so the address is a plain wss:// URL that any dashboard
    field accepts (a "user:password@host" address also works, see the stream endpoint)."""
    host = _base().replace("https://", "", 1).replace("http://", "", 1)
    address = f"wss://{host}/stream/{company_id}"
    return f"{address}/{callback_key(secret, 'stream')}" if secret else address


def passthru_url(company_id: uuid.UUID, secret: str) -> str:
    """The address for the Passthru applet that follows the Voicebot applet (call ended, recording ready)."""
    return f"{_base()}/{company_id}/passthru?key={callback_key(secret, 'passthru')}"


def status_url(call_id: uuid.UUID, secret: str) -> str:
    return f"{_base()}/calls/{call_id}/status?key={callback_key(secret, str(call_id))}"


class ExotelProvider(TelephonyProvider):
    name = "exotel"

    def __init__(self, creds: ExotelCredentials) -> None:
        """One instance per company account (see telephony.get_provider)."""
        self._creds = creds
        self._auth = (creds.api_key, creds.api_token)
        self._api = f"https://{creds.subdomain}/v1/Accounts/{creds.account_sid}"

    # ------------------------------------------------------------------ calls
    async def prepare_call(self, utterances: dict[str, list[str]]) -> None:
        for language, texts in utterances.items():
            await speech.prewarm(texts, language)

    async def place_call(self, call_id: uuid.UUID, to_phone: str) -> str:
        """Call the customer and, when they answer, connect them to the company's flow."""
        creds = self._creds
        data = {
            "From": to_phone,  # Exotel's name for the number it dials first
            "CallerId": creds.number,
            "Url": f"http://my.exotel.com/{creds.account_sid}/exoml/start_voice/{creds.app_id}",
            "CallType": "trans",
            "TimeOut": "30",
            "CustomField": str(call_id),
            # Only the callback address. Exotel rejects StatusCallbackEvents on a call into a flow
            # ("Invalid 'StatusCallbackEvents' specified"); the default is one callback when the call
            # ends, sent as a form, which is all we need.
            "StatusCallback": status_url(call_id, creds.secret),
        }
        async with httpx.AsyncClient(timeout=20, auth=self._auth) as client:
            resp = await client.post(f"{self._api}/Calls/connect.json", data=data)
        if resp.is_error:
            try:
                detail = resp.json().get("RestException", {}).get("Message") or resp.text
            except ValueError:
                detail = resp.text
            raise RuntimeError(f"Exotel rejected the call ({resp.status_code}): {detail}")
        return resp.json()["Call"]["Sid"]

    async def place_callback(self, callback_id: uuid.UUID, to_phone: str) -> str:
        """Same as a campaign call: the flow's Voicebot applet opens the audio stream, and the stream
        recognises this call by its id (services/voice_stream.py)."""
        creds = self._creds
        data = {
            "From": to_phone,
            "CallerId": creds.number,
            "Url": f"http://my.exotel.com/{creds.account_sid}/exoml/start_voice/{creds.app_id}",
            "CallType": "trans",
            "TimeOut": "30",
            "CustomField": f"callback:{callback_id}",
            "StatusCallback": f"{_base()}/callbacks/{callback_id}/status?key={callback_key(creds.secret, str(callback_id))}",
        }
        async with httpx.AsyncClient(timeout=20, auth=self._auth) as client:
            resp = await client.post(f"{self._api}/Calls/connect.json", data=data)
        if resp.is_error:
            try:
                detail = resp.json().get("RestException", {}).get("Message") or resp.text
            except ValueError:
                detail = resp.text
            raise RuntimeError(f"Exotel rejected the call ({resp.status_code}): {detail}")
        return resp.json()["Call"]["Sid"]

    async def call_details(self, provider_call_id: str) -> dict:
        """What Exotel recorded for a call: its duration and how each leg ended. {} if it can't be read."""
        try:
            async with httpx.AsyncClient(timeout=10, auth=self._auth) as client:
                resp = await client.get(f"{self._api}/Calls/{provider_call_id}.json", params={"details": "true"})
            call = resp.json().get("Call") if resp.status_code == 200 else None
            return call if isinstance(call, dict) else {}
        except Exception as exc:
            log.warning("Could not read Exotel call %s: %r", provider_call_id, exc)
            return {}

    # ------------------------------------------------------------------ no HTTP call control on Exotel
    async def render(self, call_id: uuid.UUID, action: VoiceAction, listen_url: str | None = None) -> Response:
        raise RuntimeError("Exotel calls are driven over the Voicebot audio stream, not by call-control responses")

    async def verify_request(self, request: Request) -> bool:
        return False  # the Twilio-style callbacks are never Exotel's; its own callbacks check a key instead

    async def transcribe(self, form: dict[str, str], language: str) -> Heard:
        raise RuntimeError("Exotel audio is transcribed from the Voicebot audio stream")

    async def configure_inbound(self, voice_url: str | None, status_url: str | None) -> None:
        """Nothing to do: Exotel has no API for this. The company assigns its flow to the ExoPhone
        in the Exotel dashboard (the portal shows the steps)."""

    # ------------------------------------------------------------------ callbacks
    def parse_status(self, form: dict[str, str]) -> StatusUpdate:
        duration = str(form.get("ConversationDuration") or form.get("DialCallDuration") or "")
        return StatusUpdate(
            status=_STATUS_MAP.get(str(form.get("Status") or form.get("CallStatus") or "").lower()),
            duration_seconds=int(duration) if duration.isdigit() else None,
            provider_call_id=form.get("CallSid") or None,
            recording_url=form.get("RecordingUrl") or None,
        )

    async def download_recording(self, url: str) -> bytes | None:
        # Recordings are usually plain storage links; only add the API login if the link asks for it.
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code in (401, 403):
                resp = await client.get(url, auth=self._auth)
            resp.raise_for_status()
            return resp.content

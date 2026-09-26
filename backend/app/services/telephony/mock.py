"""Placeholder provider used when no real telephony is configured. It never fabricates calls or
answers: placing a call fails immediately with an explanation, so no fake data reaches the database."""
import uuid

from fastapi import Request, Response

from app.models.enums import CallStatus
from app.services import voice_qa
from app.services.telephony.base import StatusUpdate, TelephonyProvider

NOT_CONFIGURED = (
    "No real telephony provider configured (TELEPHONY_PROVIDER=mock). "
    "Set TELEPHONY_PROVIDER=twilio with Twilio credentials and a public PUBLIC_BASE_URL to place real calls."
)


class MockProvider(TelephonyProvider):
    name = "mock"

    async def place_call(self, call_id: uuid.UUID, to_phone: str) -> str:
        raise RuntimeError(NOT_CONFIGURED)

    async def render(self, call_id: uuid.UUID, action: voice_qa.VoiceAction) -> Response:
        return Response(content=" | ".join(action.say), media_type="text/plain")

    async def verify_request(self, request: Request) -> bool:
        return True

    async def transcribe(self, form: dict[str, str], language: str) -> str:
        return form.get("speech", "")

    def parse_status(self, form: dict[str, str]) -> StatusUpdate:
        status = form.get("status")
        return StatusUpdate(status=CallStatus(status) if status in set(CallStatus) else None)

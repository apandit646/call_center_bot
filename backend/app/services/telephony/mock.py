"""Placeholder provider for a company that has not connected its Twilio account. It never fabricates
calls or answers: placing a call fails immediately with an explanation, so no fake data reaches the
database. (Campaigns of such a company can't be scheduled at all; see telephony.calling_problem.)"""
import uuid

from fastapi import Request, Response

from app.models.enums import CallStatus
from app.services import voice_qa
from app.services.telephony.base import Heard, StatusUpdate, TelephonyProvider

NOT_CONFIGURED = (
    "No telephony account connected for this company. Add the company's Twilio Account SID, Auth Token "
    "and calling number in Company Settings to place real calls."
)


class MockProvider(TelephonyProvider):
    name = "mock"

    async def place_call(self, call_id: uuid.UUID, to_phone: str) -> str:
        raise RuntimeError(NOT_CONFIGURED)

    async def render(self, call_id: uuid.UUID, action: voice_qa.VoiceAction) -> Response:
        return Response(content=" | ".join(action.say), media_type="text/plain")

    async def verify_request(self, request: Request) -> bool:
        return True

    async def transcribe(self, form: dict[str, str], language: str) -> Heard:
        return Heard(form.get("speech", ""), form.get("language"))

    def parse_status(self, form: dict[str, str]) -> StatusUpdate:
        status = form.get("status")
        return StatusUpdate(status=CallStatus(status) if status in set(CallStatus) else None)

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from fastapi import Request, Response

from app.core.config import settings
from app.models.enums import CallStatus
from app.services.voice_qa import VoiceAction


@dataclass
class Heard:
    """What the customer said in one answer, and the language the recogniser detected (if any)."""
    text: str
    language: str | None = None


@dataclass
class StatusUpdate:
    status: CallStatus | None
    duration_seconds: int | None = None
    provider_call_id: str | None = None
    recording_url: str | None = None


def callback_url(call_id: uuid.UUID, path: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/telephony/calls/{call_id}/{path}"


class TelephonyProvider(ABC):
    name: str

    async def prepare_call(self, utterances: dict[str, list[str]]) -> None:
        """Called before dialling with everything the call may say, per language (e.g. to pre-generate TTS)."""

    @abstractmethod
    async def place_call(self, call_id: uuid.UUID, to_phone: str) -> str:
        """Start an outbound call and return the provider's call id."""

    @abstractmethod
    async def render(self, call_id: uuid.UUID, action: VoiceAction, listen_url: str | None = None) -> Response:
        """Turn a VoiceAction into the provider's call-control response.

        listen_url: where the customer's next utterance is posted. Default: the outbound campaign
        answer callback for call_id; inbound support calls pass their own turn URL."""

    def reject(self) -> Response:
        """Refuse an incoming call without answering it (nobody is charged)."""
        return Response(content="rejected", media_type="text/plain")

    async def configure_inbound(self, voice_url: str | None, status_url: str | None) -> None:
        """Point the company's phone number at our inbound webhook (None = stop taking calls).
        Raises with the provider's reason if the number cannot be configured."""
        raise RuntimeError("This telephony provider cannot receive calls")

    async def start_recording(self, provider_call_id: str, callback: str) -> None:
        """Start recording a live call; the provider posts the recording to `callback` when it ends."""

    @abstractmethod
    async def verify_request(self, request: Request) -> bool:
        """Check that a webhook really came from the provider."""

    @abstractmethod
    async def transcribe(self, form: dict[str, str], language: str) -> Heard:
        """What the customer said in an answer callback (text '' for silence)."""

    @abstractmethod
    def parse_status(self, form: dict[str, str]) -> StatusUpdate:
        ...

    async def download_recording(self, url: str) -> bytes | None:
        return None

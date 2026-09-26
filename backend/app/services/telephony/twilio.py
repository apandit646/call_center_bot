"""Twilio Programmable Voice.

Two speech modes (settings.SPEECH_PROVIDER):
- sarvam: questions are Sarvam Bulbul audio played with <Play>; each answer is captured with
          <Record>, downloaded and transcribed by Sarvam Saaras, then deleted from Twilio.
- twilio: <Say> (Amazon Polly voice) + <Gather input="speech"> using Twilio's own recognition.
"""
import asyncio
import base64
import hashlib
import hmac
import logging
import uuid
from xml.sax.saxutils import escape, quoteattr

import httpx
from fastapi import Request, Response

from app.core.config import settings
from app.models.enums import CallStatus
from app.services import speech
from app.services.speech.sarvam import get_sarvam
from app.services.telephony.base import StatusUpdate, TelephonyProvider, callback_url
from app.services.voice_qa import VoiceAction

log = logging.getLogger(__name__)
_background: set[asyncio.Task] = set()

_STATUS_MAP = {
    "queued": CallStatus.INITIATED,
    "initiated": CallStatus.INITIATED,
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.IN_PROGRESS,
    "answered": CallStatus.IN_PROGRESS,
    "completed": CallStatus.COMPLETED,
    "busy": CallStatus.BUSY,
    "no-answer": CallStatus.NO_ANSWER,
    "failed": CallStatus.FAILED,
    "canceled": CallStatus.CANCELED,
}


class TwilioProvider(TelephonyProvider):
    name = "twilio"

    def __init__(self) -> None:
        if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER):
            raise RuntimeError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER must be set")
        self._auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        self._base = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}"
        self.use_sarvam = settings.SPEECH_PROVIDER == "sarvam"

    # ------------------------------------------------------------------ calls
    async def prepare_call(self, utterances: list[str], language: str) -> None:
        if self.use_sarvam:
            await speech.prewarm(utterances, language)

    async def place_call(self, call_id: uuid.UUID, to_phone: str) -> str:
        # httpx form data must be a dict; a list value is sent as a repeated field.
        data = {
            "To": to_phone,
            "From": settings.TWILIO_FROM_NUMBER,
            "Url": callback_url(call_id, "start"),
            "Method": "POST",
            "StatusCallback": callback_url(call_id, "status"),
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            "Record": "true",
            "RecordingStatusCallback": callback_url(call_id, "recording"),
            "RecordingStatusCallbackMethod": "POST",
            "Timeout": "30",
        }
        async with httpx.AsyncClient(timeout=20, auth=self._auth) as client:
            resp = await client.post(f"{self._base}/Calls.json", data=data)
        if resp.is_error:
            # Surface Twilio's reason (e.g. unverified number on a trial account) in the call's error field.
            try:
                detail = resp.json().get("message", resp.text)
            except ValueError:
                detail = resp.text
            raise RuntimeError(f"Twilio rejected the call ({resp.status_code}): {detail}")
        return resp.json()["sid"]

    # ------------------------------------------------------------------ TwiML
    async def render(self, call_id: uuid.UUID, action: VoiceAction) -> Response:
        parts = ['<?xml version="1.0" encoding="UTF-8"?><Response>']
        listening = action.gather_question_index is not None
        answer_url = f"{callback_url(call_id, 'answer')}?q={action.gather_question_index}"

        if self.use_sarvam:
            for text in action.say:
                parts += await self._play(text, action.language)
            if listening:
                parts.append(
                    f"<Record action={quoteattr(answer_url)} method=\"POST\" playBeep=\"false\" trim=\"trim-silence\" "
                    f"timeout=\"{settings.ANSWER_SILENCE_SECONDS}\" maxLength=\"{settings.ANSWER_MAX_SECONDS}\"/>"
                )
                # Twilio skips the action URL when nothing was recorded; treat that as silence.
                parts.append(f"<Redirect method=\"POST\">{escape(answer_url)}</Redirect>")
        else:
            say = [self._say(t, action.language) for t in action.say]
            if listening:
                # The question plays inside <Gather> so the customer can answer over it.
                parts += say[:-1]
                parts.append(
                    f"<Gather input=\"speech\" action={quoteattr(answer_url)} method=\"POST\" speechTimeout=\"auto\" "
                    f"language={quoteattr(action.language)} actionOnEmptyResult=\"true\">{say[-1] if say else ''}</Gather>"
                )
            else:
                parts += say
        if action.hangup:
            parts.append("<Hangup/>")
        parts.append("</Response>")
        return Response(content="".join(parts), media_type="application/xml")

    async def _play(self, text: str, language: str) -> list[str]:
        try:
            names = await speech.audio_files(text, language)
            return [f"<Play>{escape(speech.audio_url(n))}</Play>" for n in names]
        except Exception:
            # Never drop a live call because TTS failed: fall back to Twilio's own voice.
            log.exception("Sarvam TTS failed; falling back to <Say>")
            return [self._say(text, language)]

    @staticmethod
    def _say(text: str, language: str) -> str:
        return f"<Say voice={quoteattr(settings.VOICE_NAME)} language={quoteattr(language)}>{escape(text)}</Say>"

    # ------------------------------------------------------------------ callbacks
    async def verify_request(self, request: Request) -> bool:
        if not settings.TWILIO_VALIDATE_SIGNATURE:
            return True
        signature = request.headers.get("X-Twilio-Signature", "")
        # Twilio signs the public URL it called, not whatever host a reverse proxy forwards to us.
        url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        form = await request.form()
        payload = url + "".join(f"{k}{v}" for k, v in sorted(form.multi_items()))
        digest = hmac.new(settings.TWILIO_AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode(), signature)

    async def transcribe(self, form: dict[str, str], language: str) -> str:
        if not self.use_sarvam:
            return form.get("SpeechResult", "")
        recording_url = form.get("RecordingUrl")
        if not recording_url or form.get("RecordingDuration") == "0":
            return ""
        audio = await self._fetch_answer_recording(recording_url)
        if audio is None:
            return ""
        try:
            result = await get_sarvam().speech_to_text(audio, "answer.wav", language)
            log.info("Sarvam STT (%s): %r", result.language_code, result.text)
            return result.text
        except Exception:
            log.exception("Sarvam STT failed; treating answer as not heard")
            return ""
        finally:
            if sid := form.get("RecordingSid"):
                self._delete_recording_later(sid)

    async def _fetch_answer_recording(self, url: str) -> bytes | None:
        # The recording can take a moment to become downloadable after <Record> finishes.
        async with httpx.AsyncClient(timeout=10, auth=self._auth, follow_redirects=True) as client:
            for attempt in range(6):
                resp = await client.get(f"{url}.wav")
                if resp.status_code == 200:
                    return resp.content
                if resp.status_code != 404:
                    break
                await asyncio.sleep(0.3 * (attempt + 1))
        log.error("Could not download answer recording %s (HTTP %s)", url, resp.status_code)
        return None

    def _delete_recording_later(self, recording_sid: str) -> None:
        """Answer clips are only needed for STT; the full-call recording is kept separately."""

        async def delete() -> None:
            try:
                async with httpx.AsyncClient(timeout=10, auth=self._auth) as client:
                    await client.delete(f"{self._base}/Recordings/{recording_sid}.json")
            except Exception:
                log.warning("Could not delete Twilio recording %s", recording_sid)

        task = asyncio.create_task(delete())
        _background.add(task)
        task.add_done_callback(_background.discard)

    def parse_status(self, form: dict[str, str]) -> StatusUpdate:
        duration = form.get("CallDuration")
        return StatusUpdate(
            status=_STATUS_MAP.get(form.get("CallStatus", "")),
            duration_seconds=int(duration) if duration and duration.isdigit() else None,
            provider_call_id=form.get("CallSid"),
            recording_url=f"{form['RecordingUrl']}.mp3" if form.get("RecordingUrl") else None,
        )

    async def download_recording(self, url: str) -> bytes | None:
        async with httpx.AsyncClient(timeout=60, auth=self._auth, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

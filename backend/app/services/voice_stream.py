"""A call carried over a two-way audio WebSocket (Exotel's Voicebot applet).

Twilio asks us what to do at every step; a streaming provider instead hands us the caller's live
audio and plays whatever audio we send back. This module turns that stream into the same
turn-taking the rest of the system already speaks:

    speak the lines -> wait until they finished playing -> listen until the caller stops talking
        -> speech-to-text (Sarvam) -> the usual engine decides the reply -> speak ...

The engines are untouched: outbound campaign calls use voice_qa / voice_agent, inbound calls use
voice_support. Only the plumbing differs.

Audio in both directions is raw 16-bit, 8 kHz, mono PCM (little-endian), base64 inside JSON events:
    from the provider:  connected, start, media, dtmf, mark, stop
    to the provider:    media, mark, clear
"""
import array
import asyncio
import base64
import io
import json
import logging
import uuid
import wave
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Call, Callback, Campaign, Company, SupportCall
from app.models.enums import CallStatus
from app.services import call_languages, speech, storage, voice_agent, voice_qa, voice_support
from app.services.queue import Queues, enqueue
from app.services.speech.sarvam import get_sarvam
from app.services.telephony.base import Heard
from app.services.voice_qa import VoiceAction

log = logging.getLogger(__name__)

SAMPLE_RATE = 8000
FRAME_BYTES = 320  # 20 ms of 16-bit 8 kHz mono; the provider wants chunks in multiples of this
FRAME_MS = 20
SEND_CHUNK_BYTES = 3200  # 100 ms, the smallest chunk the provider accepts
_TIMEOUT: dict[str, Any] = {"event": "_timeout"}  # nothing arrived in time (the call may still be alive)


# --------------------------------------------------------------------------- audio helpers
def wav_to_pcm(data: bytes) -> bytes:
    """A WAV file -> 16-bit 8 kHz mono PCM, which is what the phone line carries."""
    with wave.open(io.BytesIO(data), "rb") as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"unsupported WAV sample width: {width * 8} bit")
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if channels > 1:  # keep the first channel
        samples = samples[::channels]
    if rate != SAMPLE_RATE and len(samples):  # nearest-sample resampling is fine for telephone speech
        step = rate / SAMPLE_RATE
        samples = array.array("h", (samples[int(i * step)] for i in range(int(len(samples) / step))))
    return samples.tobytes()


def pcm_to_wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return out.getvalue()


def _rms(frame: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(frame)
    return (sum(s * s for s in samples) / len(samples)) ** 0.5 if samples else 0.0


class Listener:
    """Decides when the caller has finished speaking, from the audio alone.

    Works on audio time (frames received), not wall-clock time, so it behaves the same under load.
    `feed` returns "speech" when an utterance is complete, "silence" if the caller said nothing
    for too long, otherwise None (keep feeding).
    """

    START_FRAMES = 3  # 60 ms of sound in a row = speech has started (ignores clicks)
    PRE_ROLL_FRAMES = 10  # keep 200 ms before the start so the first syllable is not clipped

    def __init__(self, start_ms: int | None = None) -> None:
        """start_ms: how much continuous sound counts as the start of speech (longer while the agent
        itself is talking, so only a real interruption stops it)."""
        self._start_frames = max(1, start_ms // FRAME_MS) if start_ms else self.START_FRAMES
        self.result: str | None = None  # "speech" / "silence" once the utterance is decided
        self._threshold = float(settings.STREAM_SPEECH_RMS)
        self._noise = 0.0
        self._partial = bytearray()
        self._pre_roll: list[bytes] = []
        self._speech = bytearray()
        self._started = False
        self._loud_run = 0
        self._quiet_ms = 0
        self._elapsed_ms = 0

    def audio(self) -> bytes:
        return bytes(self._speech)

    @property
    def started(self) -> bool:
        """The caller has begun to speak."""
        return self._started

    def feed(self, pcm: bytes) -> str | None:
        if self.result is not None:
            return self.result
        self._partial += pcm
        while len(self._partial) >= FRAME_BYTES:
            frame = bytes(self._partial[:FRAME_BYTES])
            del self._partial[:FRAME_BYTES]
            if (result := self._frame(frame)) is not None:
                self.result = result
                return result
        return None

    def _frame(self, frame: bytes) -> str | None:
        level = _rms(frame)
        # A noisy line raises the bar, so steady background noise is not taken for speech.
        loud = level > max(self._threshold, self._noise * 2.5)
        self._elapsed_ms += FRAME_MS
        if not self._started:
            self._pre_roll = [*self._pre_roll[-(self.PRE_ROLL_FRAMES - 1):], frame]
            self._loud_run = self._loud_run + 1 if loud else 0
            if not loud:
                self._noise = level if not self._noise else 0.9 * self._noise + 0.1 * level
            if self._loud_run >= self._start_frames:
                self._started = True
                self._speech += b"".join(self._pre_roll)
                self._elapsed_ms = 0
            elif self._elapsed_ms >= settings.STREAM_NO_SPEECH_SECONDS * 1000:
                return "silence"
            return None
        self._speech += frame
        self._quiet_ms = 0 if loud else self._quiet_ms + FRAME_MS
        if self._quiet_ms >= settings.STREAM_END_SILENCE_MS or self._elapsed_ms >= settings.ANSWER_MAX_SECONDS * 1000:
            return "speech"
        return None


# --------------------------------------------------------------------------- speech services
# Module-level so a different speech backend (or a test) can replace them.
async def synthesize(text: str, language: str) -> bytes:
    """Text -> PCM for the line. Uses the same cached audio as Twilio calls, so lines generated
    before dialling play instantly."""
    names = await speech.audio_files(text, language)
    return b"".join(wav_to_pcm(storage.read_bytes(f"tts/{name}")) for name in names)


async def transcribe(pcm: bytes, language: str) -> Heard:
    result = await get_sarvam().speech_to_text(pcm_to_wav(pcm), "answer.wav", language)
    log.info("Sarvam STT (%s): %r", result.language_code, result.text)
    return Heard(result.text, result.language_code)


# --------------------------------------------------------------------------- one call
class StreamSession:
    """One phone call on one WebSocket, for one company."""

    def __init__(self, ws: WebSocket, company_id: uuid.UUID) -> None:
        self.ws = ws
        self.company_id = company_id
        self.stream_sid = ""
        self.kind = ""  # "outbound" (campaign call) | "inbound" (support call)
        self.call_id: uuid.UUID | None = None
        self.started_at = datetime.now(UTC)
        self._marks = 0
        self._over = False  # the caller hung up or the socket closed

    async def run(self) -> None:
        try:
            start = await self._wait_for_start()
            if start is None:
                return
            action = await self._begin(start)
            if action is None:
                return
            while True:
                interruption = await self._speak(action)
                if self._over or action.hangup or action.gather_question_index is None:
                    break
                heard = await self._listen(await self._recognition_language(), interruption)
                if heard is None:
                    break
                action = await self._turn(action.gather_question_index, heard)
        except Exception:
            log.exception("Stream call %s failed", self.call_id)
        finally:
            await self._finish()

    # ------------------------------------------------------------------ events
    async def _receive(self, timeout: float = 20.0) -> dict[str, Any] | None:
        """The next event from the provider; _TIMEOUT if none came in time; None when the call is over."""
        if self._over:
            return None
        try:
            raw = await asyncio.wait_for(self.ws.receive_text(), timeout)
        except TimeoutError:
            return _TIMEOUT
        except (WebSocketDisconnect, RuntimeError):
            self._over = True
            return None
        try:
            event = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(event, dict):
            return {}
        if event.get("event") == "stop":
            self._over = True
            return None
        return event

    async def _send(self, event: dict[str, Any]) -> None:
        if self._over:
            return
        try:
            await self.ws.send_text(json.dumps({**event, "stream_sid": self.stream_sid}))
        except (WebSocketDisconnect, RuntimeError):
            self._over = True

    async def _wait_for_start(self) -> dict[str, Any] | None:
        while (event := await self._receive(timeout=15.0)) is not None and event is not _TIMEOUT:
            if event.get("event") == "start":
                self.stream_sid = event.get("stream_sid") or event.get("start", {}).get("stream_sid") or ""
                return event.get("start") or {}
        return None

    # ------------------------------------------------------------------ the conversation
    async def _begin(self, start: dict[str, Any]) -> VoiceAction | None:
        """Work out which call this stream belongs to and say the opening lines."""
        call_sid = str(start.get("call_sid") or "")
        async with SessionLocal() as db:
            company = await db.get(Company, self.company_id)
            outbound_id = await db.scalar(
                select(Call.id).join(Campaign, Campaign.id == Call.campaign_id)
                .where(Call.provider_call_id == call_sid, Campaign.company_id == company.id)
            ) if call_sid else None
            if outbound_id is not None:  # a campaign call we placed: the customer just picked up
                call = await db.get(Call, outbound_id, with_for_update={"of": Call})
                campaign = await db.get(Campaign, call.campaign_id)
                self.kind, self.call_id = "outbound", call.id
                return await (voice_agent if campaign.conversational else voice_qa).start(db, call)
            is_callback = bool(call_sid) and await db.scalar(
                select(Callback.id).where(Callback.provider_call_id == call_sid, Callback.company_id == company.id)
            )
            if not is_callback and not voice_support.inbound_active(company):
                log.info("Stream for %s refused: not one of its campaign calls and inbound calls are off", company.name)
                return None
            form = {"CallSid": call_sid, "From": str(start.get("from") or ""), "To": str(start.get("to") or "")}
            call, action = await voice_support.start(db, company, form, "exotel")
            self.kind, self.call_id = "inbound", call.id
            return action

    async def _recognition_language(self) -> str:
        if self.kind == "inbound":
            async with SessionLocal() as db:
                return call_languages.recognition_language(await db.get(Company, self.company_id))
        async with SessionLocal() as db:
            call = await db.get(Call, self.call_id)
            campaign = await db.get(Campaign, call.campaign_id)
            return voice_qa.recognition_language(campaign, call.language or campaign.language)

    async def _turn(self, turn: int, heard: Heard) -> VoiceAction:
        async with SessionLocal() as db:
            if self.kind == "inbound":
                call = await db.get(SupportCall, self.call_id, with_for_update=True)
                return await voice_support.handle_turn(db, call, turn, heard.text, heard.language)
            call = await db.get(Call, self.call_id, with_for_update={"of": Call})
            campaign = await db.get(Campaign, call.campaign_id)
            if campaign.conversational:
                return await voice_agent.handle_turn(db, call, turn, heard.text, heard.language)
            return await voice_qa.handle_answer(db, call, turn, heard.text, heard.language)

    # ------------------------------------------------------------------ speaking and listening
    async def _speak(self, action: VoiceAction) -> "Listener | None":
        """Send the lines as audio, then wait until the provider says they finished playing.

        Barge-in: the caller's audio keeps arriving while the agent talks. If they start speaking, the
        rest of the agent's audio is dropped at once and what they are saying is returned (as the
        listener already holding the start of it), so the next reply answers that, not the old line.
        """
        sent_ms = 0
        for text in action.say:
            try:
                pcm = await synthesize(text, action.language)
            except Exception:
                log.exception("Call %s: could not synthesise %r; skipping the line", self.call_id, text[:60])
                continue
            pcm += b"\x00" * (-len(pcm) % FRAME_BYTES)
            pcm += b"\x00" * max(0, SEND_CHUNK_BYTES - len(pcm))  # nothing shorter than the minimum chunk
            for i in range(0, len(pcm), SEND_CHUNK_BYTES):
                chunk = pcm[i : i + SEND_CHUNK_BYTES]
                await self._send({"event": "media", "media": {"payload": base64.b64encode(chunk).decode()}})
            sent_ms += len(pcm) // FRAME_BYTES * FRAME_MS
        if not sent_ms or self._over:
            return None
        self._marks += 1
        name = f"spoken-{self._marks}"
        await self._send({"event": "mark", "mark": {"name": name}})
        # A goodbye is never cut short: the call is ending anyway.
        ear = Listener(settings.STREAM_BARGE_IN_MS) if settings.STREAM_BARGE_IN and not action.hangup else None
        # The mark comes back when playback ends. If it never does, the audio length is the fallback.
        deadline = asyncio.get_running_loop().time() + sent_ms / 1000 + 1.5
        while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
            event = await self._receive(timeout=remaining)
            if event is None or event is _TIMEOUT:
                break
            if event.get("event") == "mark" and event.get("mark", {}).get("name") == name:
                break
            if ear is not None and event.get("event") == "media":
                try:
                    ear.feed(base64.b64decode(event.get("media", {}).get("payload") or ""))
                except ValueError:
                    continue
                if ear.started:
                    await self._send({"event": "clear"})  # stop talking: drop everything not yet played
                    log.info("Call %s: the caller interrupted; the agent stopped speaking", self.call_id)
                    return ear
        return None

    async def _listen(self, language: str, listener: "Listener | None" = None) -> Heard | None:
        """Collect the caller's next utterance and transcribe it. None = the caller hung up.

        listener: when the caller interrupted the agent, the listener that already holds the start
        of what they are saying."""
        listener = listener or Listener()
        while True:
            if listener.result == "speech":  # the interruption was already a complete sentence
                try:
                    return await transcribe(listener.audio(), language)
                except Exception:
                    log.exception("Call %s: speech-to-text failed; treating the answer as not heard", self.call_id)
                    return Heard("")
            event = await self._receive()
            if event is None or event is _TIMEOUT:  # hung up, or the audio stopped arriving
                self._over = True
                return None
            if event.get("event") != "media":
                continue
            try:
                pcm = base64.b64decode(event.get("media", {}).get("payload") or "")
            except ValueError:
                continue
            result = listener.feed(pcm)
            if result == "silence":
                return Heard("")
            if result == "speech":
                try:
                    return await transcribe(listener.audio(), language)
                except Exception:
                    log.exception("Call %s: speech-to-text failed; treating the answer as not heard", self.call_id)
                    return Heard("")

    # ------------------------------------------------------------------ the end
    async def _finish(self) -> None:
        """Close the stream (the flow then moves on to its Passthru and Hangup applets) and record that the call is over."""
        try:
            await self.ws.close()
        except Exception:  # already closed by the provider
            pass
        if self.call_id is None:
            return
        duration = int((datetime.now(UTC) - self.started_at).total_seconds())
        if self.kind == "inbound":
            async with SessionLocal() as db:
                await voice_support.finish(db, self.call_id, CallStatus.COMPLETED, duration)
        else:
            # The provider's own status callback follows with the exact duration and the recording.
            await enqueue(Queues.CALL_EVENTS, {"call_id": str(self.call_id), "status": CallStatus.COMPLETED.value})
        log.info("Stream call %s (%s) ended after %ds", self.call_id, self.kind, duration)

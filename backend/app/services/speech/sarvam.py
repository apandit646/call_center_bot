"""Sarvam AI speech APIs: Saaras (speech-to-text) and Bulbul (text-to-speech).

Docs: https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe
      https://docs.sarvam.ai/api-reference-docs/text-to-speech/convert
"""
import asyncio
import base64
import logging
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

# Languages Bulbul can speak. Saaras can transcribe many more.
TTS_LANGUAGES = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"}
_TTS_MAX_CHARS = {"bulbul:v3": 2500, "bulbul:v2": 1500}


class SarvamError(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    language_code: str | None


class SarvamClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not settings.SARVAM_API_KEY:
            raise SarvamError("SARVAM_API_KEY is not set")
        self._client = httpx.AsyncClient(
            base_url=settings.SARVAM_API_BASE,
            headers={"api-subscription-key": settings.SARVAM_API_KEY},
            timeout=httpx.Timeout(20, connect=5),
            transport=transport,
        )

    async def _post(self, path: str, **kwargs) -> dict:
        # One retry on rate limiting / transient server errors; calls are live, so keep it short.
        for attempt in range(2):
            resp = await self._client.post(path, **kwargs)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                await asyncio.sleep(0.5)
                continue
            if resp.is_error:
                raise SarvamError(f"Sarvam {path} failed: {resp.status_code} {resp.text[:300]}")
            return resp.json()
        raise AssertionError("unreachable")

    async def text_to_speech(self, text: str, language: str) -> list[bytes]:
        """Synthesise text; returns one WAV per chunk (long text is split on sentence boundaries)."""
        language = tts_language(language)
        audios: list[bytes] = []
        for chunk in _split(text, _TTS_MAX_CHARS.get(settings.SARVAM_TTS_MODEL, 1500)):
            body = {
                "text": chunk,
                "language_code": language,
                "model": settings.SARVAM_TTS_MODEL,
                "speaker": settings.SARVAM_TTS_SPEAKER,
                "pace": settings.SARVAM_TTS_PACE,
                # Phone audio is 8 kHz anyway; smaller files start playing sooner.
                "speech_sample_rate": 8000,
                "output_audio_codec": "wav",
            }
            data = await self._post("/text-to-speech", json=body)
            audios += [base64.b64decode(a) for a in data.get("audios", [])]
        if not audios:
            raise SarvamError("Sarvam returned no audio")
        return audios

    async def translate(self, text: str, source: str, target: str) -> str:
        body = {
            "input": text[:1000],  # mayura:v1 limit
            "source_language_code": source,
            "target_language_code": target,
            "model": "mayura:v1",
            # code-mixed = how people actually talk on calls: Hindi, keeping common English words.
            "mode": settings.SARVAM_TRANSLATE_MODE,
            "speaker_gender": settings.SARVAM_TTS_SPEAKER_GENDER,
        }
        data = await self._post("/translate", json=body)
        translated = (data.get("translated_text") or "").strip()
        if not translated:
            raise SarvamError("Sarvam returned an empty translation")
        return translated

    async def speech_to_text(self, audio: bytes, filename: str, language: str | None) -> Transcript:
        form = {"model": settings.SARVAM_STT_MODEL, "language_code": stt_language(language)}
        if settings.SARVAM_STT_MODE and settings.SARVAM_STT_MODEL.startswith("saaras:v3"):
            form["mode"] = settings.SARVAM_STT_MODE
        data = await self._post("/speech-to-text", data=form, files={"file": (filename, audio, "audio/wav")})
        return Transcript(text=(data.get("transcript") or "").strip(), language_code=data.get("language_code"))


def tts_language(language: str | None) -> str:
    """Bulbul needs one of its supported codes; campaign languages like en-US fall back to en-IN."""
    if language in TTS_LANGUAGES:
        return language
    prefix = (language or "en").split("-")[0]
    return next((code for code in TTS_LANGUAGES if code.startswith(prefix + "-")), "en-IN")


def stt_language(language: str | None) -> str:
    if settings.SARVAM_STT_LANGUAGE:  # e.g. "unknown" = auto-detect
        return settings.SARVAM_STT_LANGUAGE
    return tts_language(language) if language else "unknown"


def _split(text: str, limit: int) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?।])\s+", text):
        while len(sentence) > limit:  # a single enormous sentence
            chunks.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


_client: SarvamClient | None = None


def get_sarvam() -> SarvamClient:
    global _client
    if _client is None:
        _client = SarvamClient()
    return _client

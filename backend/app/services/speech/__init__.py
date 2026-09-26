"""Voice for outbound calls: cached text-to-speech audio served to the telephony provider.

Audio is content-addressed: the same text + language + voice settings always maps to the same
file, so each question is synthesised once per campaign rather than once per call. File names are
an HMAC of the content (keyed with the JWT secret), so the public audio URLs cannot be guessed
from a known sentence - the greeting contains the customer's name.
"""
import asyncio
import hashlib
import hmac
import logging
import re

from app.core.config import settings
from app.services import storage
from app.services.speech.sarvam import get_sarvam, tts_language

log = logging.getLogger(__name__)
AUDIO_NAME_RE = re.compile(r"^[a-f0-9]{64}(-\d+)?\.wav$")


def _key(text: str, language: str) -> str:
    material = "|".join([
        settings.SARVAM_TTS_MODEL, settings.SARVAM_TTS_SPEAKER, str(settings.SARVAM_TTS_PACE),
        tts_language(language), text.strip(),
    ])
    return hmac.new(settings.JWT_SECRET_KEY.encode(), material.encode(), hashlib.sha256).hexdigest()


async def audio_files(text: str, language: str) -> list[str]:
    """Return file names (see AUDIO_NAME_RE) for the spoken text, synthesising on a cache miss."""
    key = _key(text, language)
    first = f"{key}.wav"
    if storage.exists(f"tts/{first}"):
        names, i = [first], 1
        while storage.exists(f"tts/{key}-{i}.wav"):
            names.append(f"{key}-{i}.wav")
            i += 1
        return names

    audios = await get_sarvam().text_to_speech(text, language)
    names = [first] + [f"{key}-{i}.wav" for i in range(1, len(audios))]
    # Write continuation chunks first so a concurrent reader that sees the first file sees them all.
    for name, audio in reversed(list(zip(names, audios, strict=True))):
        storage.save_at(f"tts/{name}", audio)
    return names


def audio_url(name: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/telephony/audio/{name}"


async def prewarm(texts: list[str], language: str, timeout: float = 30) -> None:
    """Synthesise everything a call will say before dialling, so the live call never waits on TTS."""
    unique = list(dict.fromkeys(t for t in texts if t and t.strip()))
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(audio_files(t, language) for t in unique), return_exceptions=True), timeout
        )
        failed = [r for r in results if isinstance(r, Exception)]
        if failed:
            log.warning("TTS prewarm: %d/%d utterances failed (%r)", len(failed), len(unique), failed[0])
    except TimeoutError:
        log.warning("TTS prewarm timed out; remaining audio will be generated during the call")

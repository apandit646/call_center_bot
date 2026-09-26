"""Fill in the alternate-language versions of a campaign's script (questions, greeting, closing).

Admins write the script once in the primary language; missing translations are generated with
Sarvam (code-mixed, i.e. natural spoken Hindi) or Gemini as a fallback, and stored so the admin
can review and edit them. Anything that can't be translated simply falls back to the primary text.
"""
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.models import Campaign
from app.services.gemini import get_gemini
from app.services.speech.sarvam import get_sarvam, tts_language

log = logging.getLogger(__name__)
_PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
LANGUAGE_NAMES = {
    "hi-IN": "Hindi", "en-IN": "English", "bn-IN": "Bengali", "gu-IN": "Gujarati", "kn-IN": "Kannada",
    "ml-IN": "Malayalam", "mr-IN": "Marathi", "od-IN": "Odia", "pa-IN": "Punjabi", "ta-IN": "Tamil", "te-IN": "Telugu",
}


async def translate_text(text: str, source: str, target: str) -> str | None:
    """Translate keeping {placeholders} intact; None if no translator is available or it failed."""
    placeholders = _PLACEHOLDER.findall(text)
    # Proper-noun-like tokens survive machine translation far better than {braces}.
    masked = text
    for i, ph in enumerate(placeholders):
        masked = masked.replace(ph, f"Zqx{i}", 1)

    translated = None
    if settings.SARVAM_API_KEY:
        try:
            translated = await get_sarvam().translate(masked, tts_language(source), tts_language(target))
        except Exception:
            log.exception("Sarvam translation failed; trying Gemini")
    if translated is None and get_gemini().enabled:
        system = (
            f"Translate the user's text from {LANGUAGE_NAMES.get(source, source)} to "
            f"{LANGUAGE_NAMES.get(target, target)} as it would be spoken naturally on a customer phone call. "
            "For Hindi, write in Devanagari but keep everyday English words (product, service, app, payment) in "
            "English. Keep tokens like Zqx0 exactly as they are. Reply with the translation only."
        )
        translated = await get_gemini().generate_text(system, masked, fallback="") or None
    if not translated:
        return None

    for i, ph in enumerate(placeholders):
        if f"Zqx{i}" not in translated:
            log.warning("Translation dropped placeholder %s; keeping the primary text for %r", ph, text[:60])
            return None
        translated = translated.replace(f"Zqx{i}", ph)
    return translated


async def ensure_translations(db: AsyncSession, campaign: Campaign, force: bool = False) -> int:
    """Translate missing (or, with force, all) script lines. Commits. Returns how many were written."""
    written = 0
    for lang in campaign.alt_languages or []:
        if lang == campaign.language:
            continue
        for q in campaign.questions:
            if force or not (q.translations or {}).get(lang):
                if text := await translate_text(q.text, campaign.language, lang):
                    q.translations = {**(q.translations or {}), lang: text}
                    flag_modified(q, "translations")
                    written += 1
        script = dict((campaign.translations or {}).get(lang) or {})
        for field, source_text in (("greeting", campaign.greeting_script), ("closing", campaign.closing_script)):
            if force or not script.get(field):
                if text := await translate_text(source_text, campaign.language, lang):
                    script[field] = text
                    written += 1
        campaign.translations = {**(campaign.translations or {}), lang: script}
        flag_modified(campaign, "translations")
    await db.commit()
    return written


def missing_translations(campaign: Campaign) -> bool:
    for lang in campaign.alt_languages or []:
        if lang == campaign.language:
            continue
        script = (campaign.translations or {}).get(lang) or {}
        if not script.get("greeting") or not script.get("closing"):
            return True
        if any(not (q.translations or {}).get(lang) for q in campaign.questions):
            return True
    return False

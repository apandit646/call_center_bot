import phonenumbers

from app.core.config import settings


def normalize_phone(raw: str | int | float | None, region: str | None = None) -> str | None:
    """Return the E.164 form of a phone number, or None if it is not a valid number."""
    if raw is None:
        return None
    if isinstance(raw, float):  # Excel often stores phone numbers as floats
        raw = str(int(raw))
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit() and len(text) > 10 and not text.startswith("0"):
        text = "+" + text  # "919876543210" -> "+919876543210"
    try:
        parsed = phonenumbers.parse(text, region or settings.DEFAULT_PHONE_REGION)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

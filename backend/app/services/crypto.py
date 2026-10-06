"""Encryption for secrets stored in the database (each company's Twilio / WhatsApp credentials).

A database dump alone must not hand out every tenant's telephony account, so the tokens are stored
as Fernet ciphertext and only decrypted in memory when a call or message is sent.
"""
import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


@lru_cache
def _fernet() -> Fernet:
    material = settings.SECRETS_ENCRYPTION_KEY or settings.JWT_SECRET_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str | None:
    """None when there is nothing stored or it can't be read (the encryption key was changed)."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None

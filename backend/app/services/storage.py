"""Object storage. Local-disk implementation; swap for S3/GCS by keeping the same interface."""
import re
import uuid
from pathlib import Path

from app.core.config import settings

_ROOT = Path(settings.STORAGE_DIR).resolve()


def _safe_name(filename: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)
    return name[-120:] or "file"


def save_bytes(folder: str, filename: str, data: bytes) -> str:
    """Store bytes and return the storage key (relative path)."""
    key = f"{folder}/{uuid.uuid4().hex}_{_safe_name(filename)}"
    path = _ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def save_at(key: str, data: bytes) -> str:
    """Store bytes under an exact, caller-chosen key (used for content-addressed caches)."""
    path = resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)  # atomic, so a concurrent reader never sees a half-written file
    return key


def exists(key: str) -> bool:
    return resolve(key).is_file()


def resolve(key: str) -> Path:
    path = (_ROOT / key).resolve()
    if not path.is_relative_to(_ROOT):
        raise ValueError("invalid storage key")
    return path


def read_bytes(key: str) -> bytes:
    return resolve(key).read_bytes()


def delete(key: str | None) -> None:
    if key:
        resolve(key).unlink(missing_ok=True)

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def audit(
    db: AsyncSession,
    actor_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    data: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """Add an audit row to the session; it is committed with the caller's transaction."""
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            data=data or {},
            ip_address=ip_address,
        )
    )

"""Idempotent first-run data: the platform super admin.

Companies (and their default support domains and admin logins) are created by the super admin in
the portal - see services/company_service.register.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models import User
from app.models.enums import UserRole

log = logging.getLogger(__name__)


async def seed_initial_data(db: AsyncSession) -> None:
    if await db.scalar(select(User.id).where(User.role == UserRole.SUPER_ADMIN).limit(1)):
        return
    email = settings.FIRST_ADMIN_EMAIL.lower()
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        db.add(
            User(
                email=email,
                full_name="Platform Administrator",
                hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
                role=UserRole.SUPER_ADMIN,
            )
        )
        log.warning("Created platform super admin %s - change the password after first login.", email)
    else:
        # Upgrade from the single-company version: the first admin becomes the platform owner. Their
        # old data now lives in "Default Company"; issue a company login for it from the Companies page.
        user.role = UserRole.SUPER_ADMIN
        user.company_id = None
        log.warning("Promoted %s to platform super admin (multi-company upgrade).", email)
    await db.commit()

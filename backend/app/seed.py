"""Idempotent first-run data: an admin user and the default support domains."""
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models import Domain, User
from app.models.enums import UserRole

log = logging.getLogger(__name__)

DEFAULT_DOMAINS = [
    ("billing", "Billing", "Payments, invoices, charges, subscriptions and failed or deducted payments.",
     ["payment", "invoice", "charged", "deducted", "subscription", "bill", "upi", "card"]),
    ("refund", "Refund", "Refund requests, refund status and refund timelines.",
     ["refund", "money back", "return", "cancel order"]),
    ("technical", "Technical", "App or website errors, bugs, login problems, crashes and performance.",
     ["error", "bug", "crash", "not working", "slow", "otp", "app"]),
    ("account", "Account", "Profile, password, email/phone change, KYC and account closure.",
     ["account", "password", "profile", "kyc", "email", "delete account"]),
]


async def seed_initial_data(db: AsyncSession) -> None:
    if not await db.scalar(select(func.count(User.id))):
        db.add(
            User(
                email=settings.FIRST_ADMIN_EMAIL.lower(),
                full_name="Administrator",
                hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
                role=UserRole.ADMIN,
            )
        )
        log.warning("Created initial admin %s - change the password after first login.", settings.FIRST_ADMIN_EMAIL)
    if not await db.scalar(select(func.count(Domain.id))):
        db.add_all(Domain(code=c, name=n, description=d, keywords=k) for c, n, d, k in DEFAULT_DOMAINS)
    await db.commit()

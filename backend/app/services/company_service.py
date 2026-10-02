"""Companies (tenants): registration, generated logins, limits, usage, and each company's own
Twilio / WhatsApp credentials.

There is no shared account and no fallback: a company's calls and messages only ever use the
Twilio and WhatsApp accounts that company connected. Until it connects one, that channel is off
for it: campaigns cannot be scheduled, and WhatsApp messages are neither received nor sent.
"""
import logging
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import Call, Campaign, Company, Domain, ScheduledCall, SupportCall, User
from app.models.enums import ACTIVE_CALL_STATUSES, ScheduledCallStatus, UserRole
from app.services.crypto import decrypt

log = logging.getLogger(__name__)

# Every new company starts with these support domains and can edit them.
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


class CompanyError(ValueError):
    pass


# --------------------------------------------------------------------------- registration & logins
def generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in password) and any(c.isupper() for c in password) and any(c.isdigit() for c in password):
            return password


async def register(
    db: AsyncSession, *, name: str, email: str, admin_name: str | None = None, **fields: Any
) -> tuple[Company, str]:
    """Create a company, its default support domains and its admin login. Returns (company, password).

    The password is generated here and never stored in clear: the caller shows it once. Caller commits.
    """
    email = email.lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise CompanyError("A user with this email already exists")
    company = Company(name=name, email=email, **fields)
    db.add(company)
    await db.flush()
    db.add_all(
        Domain(company_id=company.id, code=c, name=n, description=d, keywords=k) for c, n, d, k in DEFAULT_DOMAINS
    )
    password = generate_password()
    db.add(
        User(
            email=email,
            full_name=admin_name or f"{name} Admin",
            hashed_password=hash_password(password),
            role=UserRole.ADMIN,
            company_id=company.id,
        )
    )
    return company, password


async def issue_credentials(db: AsyncSession, company: Company, email: str | None = None) -> tuple[str, str]:
    """Give the company admin a new generated password (creating the admin if there is none).

    Returns (login email, password). Caller commits.
    """
    target = (email or company.email or "").lower() or None
    user = await db.scalar(select(User).where(User.email == target)) if target else None
    if user is not None and user.company_id != company.id:
        raise CompanyError("This email belongs to a user outside this company")
    if user is None and email is None:
        user = await db.scalar(
            select(User)
            .where(User.company_id == company.id, User.role == UserRole.ADMIN)
            .order_by(User.created_at)
            .limit(1)
        )
    password = generate_password()
    if user is None:
        if not target:
            raise CompanyError("Set the company email first: it becomes the admin login")
        user = User(email=target, full_name=f"{company.name} Admin", role=UserRole.ADMIN, company_id=company.id)
        db.add(user)
    user.hashed_password = hash_password(password)
    user.is_active = True
    return user.email, password


# --------------------------------------------------------------------------- what the AI knows
def profile_text(company: Company | None) -> str:
    """The company facts the AI may state on a call or in a chat. Empty lines are left out."""
    if company is None:
        return ""
    rows = [
        ("Company name", company.name),
        ("Industry", company.industry),
        ("About the company", company.description),
        ("Products and services", company.products_services),
        ("Address", company.address),
        ("Phone", company.phone),
        ("Email", company.email),
        ("Website", company.website),
    ]
    return "\n".join(f"{label}: {value.strip()}" for label, value in rows if value and value.strip())


# --------------------------------------------------------------------------- limits & usage
def day_start(company: Company, now: datetime | None = None) -> datetime:
    """Midnight today in the company's timezone: the daily call limit resets here."""
    try:
        tz = ZoneInfo(company.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = UTC
    local = (now or datetime.now(UTC)).astimezone(tz)
    return datetime.combine(local.date(), time.min, tzinfo=tz).astimezone(UTC)


async def campaign_count(db: AsyncSession, company_id: uuid.UUID) -> int:
    return await db.scalar(select(func.count(Campaign.id)).where(Campaign.company_id == company_id)) or 0


async def calls_today(db: AsyncSession, company: Company, now: datetime | None = None) -> int:
    return await db.scalar(
        select(func.count(Call.id))
        .join(Campaign, Campaign.id == Call.campaign_id)
        .where(Campaign.company_id == company.id, Call.created_at >= day_start(company, now))
    ) or 0


async def active_calls(db: AsyncSession, company_id: uuid.UUID) -> int:
    return await db.scalar(
        select(func.count(Call.id))
        .join(Campaign, Campaign.id == Call.campaign_id)
        .where(Campaign.company_id == company_id, Call.status.in_(list(ACTIVE_CALL_STATUSES)))
    ) or 0


async def queued_calls(db: AsyncSession, company_id: uuid.UUID) -> int:
    """Calls already handed to the call worker but not dialled yet; they count against the limits."""
    return await db.scalar(
        select(func.count(ScheduledCall.id))
        .join(Campaign, Campaign.id == ScheduledCall.campaign_id)
        .where(Campaign.company_id == company_id, ScheduledCall.status == ScheduledCallStatus.QUEUED)
    ) or 0


async def campaign_limit_problem(db: AsyncSession, company: Company) -> str | None:
    if company.max_campaigns is None:
        return None
    used = await campaign_count(db, company.id)
    if used >= company.max_campaigns:
        return (
            f"Your plan allows {company.max_campaigns} campaign(s) and {used} already exist. "
            "Delete a draft campaign or ask the platform admin to raise the limit."
        )
    return None


async def call_capacity(db: AsyncSession, company: Company, now: datetime | None = None) -> int | None:
    """How many more calls this company may start right now; None = no company limit applies."""
    limits = []
    if company.daily_call_limit is not None:
        limits.append(company.daily_call_limit - await calls_today(db, company, now))
    if company.max_concurrent_calls is not None:
        limits.append(company.max_concurrent_calls - await active_calls(db, company.id))
    if not limits:
        return None
    return max(0, min(limits) - await queued_calls(db, company.id))


async def usage(db: AsyncSession, company: Company) -> dict[str, Any]:
    return {
        "campaigns": await campaign_count(db, company.id),
        "max_campaigns": company.max_campaigns,
        "calls_today": await calls_today(db, company),
        "daily_call_limit": company.daily_call_limit,
        "active_calls": await active_calls(db, company.id),
        "max_concurrent_calls": company.max_concurrent_calls,
        "users": await db.scalar(select(func.count(User.id)).where(User.company_id == company.id)) or 0,
        "inbound_calls_today": await db.scalar(
            select(func.count(SupportCall.id)).where(
                SupportCall.company_id == company.id, SupportCall.started_at >= day_start(company)
            )
        ) or 0,
    }


# --------------------------------------------------------------------------- channel credentials
@dataclass(frozen=True)
class TwilioCredentials:
    account_sid: str
    auth_token: str
    from_number: str


@dataclass(frozen=True)
class WhatsAppCredentials:
    phone_number_id: str
    access_token: str
    app_secret: str  # verifies that inbound webhooks really come from the company's Meta app


def twilio_credentials(company: Company | None) -> TwilioCredentials | None:
    """The company's own Twilio account, or None if it has not connected one."""
    if company is None or not (company.twilio_account_sid and company.twilio_from_number):
        return None
    token = decrypt(company.twilio_auth_token_enc)
    if not token:
        if company.twilio_auth_token_enc:
            log.error("Company %s: stored Twilio auth token is unreadable (encryption key changed?)", company.id)
        return None
    return TwilioCredentials(company.twilio_account_sid, token, company.twilio_from_number)


def whatsapp_credentials(company: Company | None) -> WhatsAppCredentials | None:
    """The company's own WhatsApp Cloud API number, or None until it has saved all three of the
    Phone number ID, access token and app secret."""
    if company is None or not company.whatsapp_phone_number_id:
        return None
    token, secret = decrypt(company.whatsapp_access_token_enc), decrypt(company.whatsapp_app_secret_enc)
    if not (token and secret):
        if (company.whatsapp_access_token_enc and not token) or (company.whatsapp_app_secret_enc and not secret):
            log.error("Company %s: stored WhatsApp secrets are unreadable (encryption key changed?)", company.id)
        return None
    return WhatsAppCredentials(company.whatsapp_phone_number_id, token, secret)


async def company_for_whatsapp(db: AsyncSession, phone_number_id: str | None) -> Company | None:
    """Which company an inbound WhatsApp event belongs to, from the number that received it.

    None for a number that no active company has fully connected: the event is dropped rather
    than risk showing one company's customer message to another.
    """
    if not phone_number_id:
        return None
    company = await db.scalar(select(Company).where(Company.whatsapp_phone_number_id == phone_number_id))
    if company is None or not company.is_active or whatsapp_credentials(company) is None:
        return None
    return company

"""Companies (tenants).

/companies ...  platform super admin: register a company, set its limits, suspend it, issue logins.
/company        a company's own view: profile the AI uses, usage vs limits, Twilio / WhatsApp accounts.
"""
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, AdminUser, PageParams, SuperAdmin, TenantUser, not_found
from app.core.config import settings
from app.models import Call, Campaign, Company, User
from app.models.enums import ACTIVE_CALL_STATUSES, CampaignStatus, UserRole
from app.schemas.common import Page
from app.schemas.company import (
    CompanyAdminUpdate,
    CompanyOut,
    CompanyRegister,
    CompanyRegistered,
    CompanySelfUpdate,
    Credentials,
    CredentialsRequest,
    PlatformSummary,
)
from app.services import call_languages, company_service, voice_support
from app.services.audit import audit
from app.services.crypto import decrypt, encrypt
from app.services.telephony import get_provider, public_url_down
from app.services.telephony.exotel import passthru_url, stream_url

router = APIRouter(tags=["companies"])

# API field -> encrypted column
_SECRETS = {
    "twilio_auth_token": "twilio_auth_token_enc",
    "exotel_api_token": "exotel_api_token_enc",
    "whatsapp_access_token": "whatsapp_access_token_enc",
    "whatsapp_app_secret": "whatsapp_app_secret_enc",
    "sendgrid_api_key": "sendgrid_api_key_enc",
}


def _out(company: Company, usage: dict[str, Any] | None = None, integrations: bool = True) -> CompanyOut:
    out = CompanyOut(
        id=company.id, name=company.name, email=company.email, phone=company.phone, website=company.website,
        address=company.address, industry=company.industry, description=company.description,
        products_services=company.products_services, timezone=company.timezone, is_active=company.is_active,
        max_campaigns=company.max_campaigns, daily_call_limit=company.daily_call_limit,
        max_concurrent_calls=company.max_concurrent_calls,
        telephony_provider=company.telephony_provider,
        calls_connected=company_service.calling_credentials(company) is not None,
        whatsapp_connected=company_service.whatsapp_credentials(company) is not None,
        whatsapp_webhook_url=f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/webhooks/whatsapp",
        inbound_allowed=company.inbound_allowed, inbound_enabled=company.inbound_enabled,
        inbound_active=voice_support.inbound_active(company), inbound_problem=voice_support.inbound_problem(company),
        inbound_language=company.inbound_language,
        inbound_languages=call_languages.allowed_codes(company),
        inbound_greeting=company.inbound_greeting,
        email_enabled=company.email_enabled, email_address=company.email_address, email_from_name=company.email_from_name,
        email_connected=company_service.email_credentials(company) is not None,
        email_active=company_service.email_problem(company) is None,
        email_problem=company_service.email_problem(company),
        billing_hold=company.billing_hold,
        usage=usage, created_at=company.created_at,
    )
    if integrations:  # account identifiers are for admins only; secrets are never returned
        out.twilio_account_sid = company.twilio_account_sid
        out.twilio_from_number = company.twilio_from_number
        out.twilio_auth_token_set = bool(company.twilio_auth_token_enc)
        out.exotel_api_key = company.exotel_api_key
        out.exotel_account_sid = company.exotel_account_sid
        out.exotel_subdomain = company.exotel_subdomain
        out.exotel_number = company.exotel_number
        out.exotel_app_id = company.exotel_app_id
        out.exotel_api_token_set = bool(company.exotel_api_token_enc)
        if company.telephony_provider == "exotel" and (secret := decrypt(company.exotel_secret_enc)):
            out.exotel_stream_url = stream_url(company.id, secret)
            out.exotel_passthru_url = passthru_url(company.id, secret)
        out.whatsapp_phone_number_id = company.whatsapp_phone_number_id
        out.whatsapp_access_token_set = bool(company.whatsapp_access_token_enc)
        out.whatsapp_app_secret_set = bool(company.whatsapp_app_secret_enc)
        out.sendgrid_api_key_set = bool(company.sendgrid_api_key_enc)
        if company.email_webhook_token:
            out.email_webhook_url = (
                f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/webhooks/email/{company.email_webhook_token}"
            )
    return out


async def _apply(db: DB, company: Company, changes: dict[str, Any]) -> list[str]:
    """Apply a PATCH body (already exclude_unset). Returns the changed field names for the audit log."""
    number_id = changes.get("whatsapp_phone_number_id")
    if number_id and number_id != company.whatsapp_phone_number_id:
        if await db.scalar(select(Company.id).where(Company.whatsapp_phone_number_id == number_id)):
            raise HTTPException(status.HTTP_409_CONFLICT, "This WhatsApp number is already linked to another company")
    calling_number = changes.get("twilio_from_number")
    if calling_number and calling_number != company.twilio_from_number:
        # Inbound calls are routed to a company by the number dialled, so a number has one owner.
        if await db.scalar(select(Company.id).where(Company.twilio_from_number == calling_number)):
            raise HTTPException(status.HTTP_409_CONFLICT, "This calling number is already linked to another company")
    address = changes.get("email_address")
    if address:
        changes["email_address"] = address = address.lower()
        if address != company.email_address and await db.scalar(select(Company.id).where(Company.email_address == address)):
            raise HTTPException(status.HTTP_409_CONFLICT, "This support email address is already linked to another company")
        if not company.email_webhook_token:
            # Our side of the SendGrid set-up: the private address its Inbound Parse posts to. Generated once.
            company.email_webhook_token = secrets.token_urlsafe(24)
    for field in ("name", "timezone", "inbound_enabled", "inbound_allowed", "inbound_language", "telephony_provider",
                  "exotel_subdomain", "email_enabled", "inbound_languages"):
        if field in changes and changes[field] is None:  # required columns: null means "leave as is"
            del changes[field]
    provider = changes.get("telephony_provider")
    if provider and provider != company.telephony_provider:
        # Calls already in progress finish on the provider that placed them, but a running campaign
        # would start dialling on the new one mid-way: pause it first.
        running = await db.scalar(
            select(func.count(Campaign.id)).where(
                Campaign.company_id == company.id, Campaign.status.in_([CampaignStatus.RUNNING, CampaignStatus.SCHEDULED])
            )
        )
        if running:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Pause your {running} running or scheduled campaign(s) before switching the calling provider.",
            )
    if (provider or company.telephony_provider) == "exotel" and not company.exotel_secret_enc:
        # Our side of the Exotel setup: the login its audio stream must use. Generated once.
        company.exotel_secret_enc = encrypt(secrets.token_urlsafe(24))

    # The number inbound calls arrive on now, before anything changes.
    inbound_before = (
        (company_service.calling_number(company), get_provider(company)) if voice_support.inbound_active(company) else None
    )
    for field, value in changes.items():
        if field in _SECRETS:
            if value is not None:  # None = keep; "" = remove
                setattr(company, _SECRETS[field], encrypt(value.strip()) if value.strip() else None)
        elif field == "email":
            company.email = value.lower() if value else None
        else:
            setattr(company, field, value)
    if changes.get("email_enabled") and company_service.email_credentials(company) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Email can't be switched on yet: add your support email address and SendGrid API key first.",
        )
    if company.email_enabled and company_service.email_credentials(company) is None:
        company.email_enabled = False  # the account was disconnected: email goes off with it
    await _sync_inbound(company, changes, inbound_before)
    return sorted(changes)


async def _sync_inbound(company: Company, changes: dict[str, Any], before: tuple[str, Any] | None) -> None:
    """Keep the company's number in step with its inbound switch. On Twilio, switching on (or
    changing the account while on) points the number at our inbound webhook and switching off clears
    it. On Exotel there is nothing to call: the company assigns its flow to the ExoPhone itself.

    Nothing is saved if the provider refuses, so the portal never shows inbound as on when it is not."""
    touched = {
        "inbound_enabled", "inbound_allowed", "telephony_provider", "twilio_account_sid", "twilio_auth_token",
        "twilio_from_number", "exotel_api_key", "exotel_api_token", "exotel_account_sid", "exotel_number",
        "exotel_app_id",
    }
    if not touched & changes.keys():
        return
    if company.inbound_enabled and (problem := voice_support.inbound_problem(company)):
        if changes.get("inbound_enabled"):  # they asked to switch it on: say why it can't be
            raise HTTPException(status.HTTP_409_CONFLICT, problem)
        company.inbound_enabled = False  # e.g. the account was disconnected: inbound goes off with it
    if changes.get("inbound_enabled") and (problem := await public_url_down()):
        # Pointing the number at an address that is down would make every caller hear an error.
        raise HTTPException(status.HTTP_409_CONFLICT, problem)
    number = company_service.calling_number(company)
    old_provider = before[1] if before and (not company.inbound_enabled or before[0] != number) else None
    try:
        await voice_support.sync_number(company, old_provider)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Could not set up inbound calls on {number}: {exc}"
        ) from exc


async def _company(db: DB, company_id: uuid.UUID) -> Company:
    company = await db.get(Company, company_id)
    if company is None:
        raise not_found("Company")
    return company


# ------------------------------------------------------------------ platform super admin
@router.get("/platform/summary", response_model=PlatformSummary)
async def platform_summary(db: DB, _: SuperAdmin):
    async def count(stmt) -> int:
        return await db.scalar(stmt) or 0

    return PlatformSummary(
        companies=await count(select(func.count(Company.id))),
        active_companies=await count(select(func.count(Company.id)).where(Company.is_active.is_(True))),
        users=await count(select(func.count(User.id)).where(User.company_id.isnot(None))),
        campaigns=await count(select(func.count(Campaign.id))),
        running_campaigns=await count(select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.RUNNING)),
        active_calls=await count(select(func.count(Call.id)).where(Call.status.in_(list(ACTIVE_CALL_STATUSES)))),
        calls_last_24h=await count(
            select(func.count(Call.id)).where(Call.created_at >= datetime.now(UTC) - timedelta(hours=24))
        ),
    )


@router.get("/companies", response_model=Page[CompanyOut])
async def list_companies(db: DB, _: SuperAdmin, pg: PageParams, q: str | None = None, active: bool | None = None):
    stmt = select(Company)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Company.name.ilike(like), Company.email.ilike(like)))
    if active is not None:
        stmt = stmt.where(Company.is_active.is_(active))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    companies = (await db.scalars(stmt.order_by(Company.created_at.desc()).offset(pg.offset).limit(pg.size))).all()
    items = [_out(c, await company_service.usage(db, c)) for c in companies]
    return Page(items=items, total=total, page=pg.page, size=pg.size)


@router.post("/companies", response_model=CompanyRegistered, status_code=201)
async def register_company(body: CompanyRegister, db: DB, admin: SuperAdmin):
    """Register a company and generate its admin login. The password is returned once, here."""
    fields = body.model_dump(exclude={"name", "email", "admin_name"})
    if fields.get("timezone") is None:
        fields.pop("timezone")
    try:
        company, password = await company_service.register(
            db, name=body.name, email=body.email, admin_name=body.admin_name, **fields
        )
    except company_service.CompanyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit(db, admin.id, "register", "company", company.id, {"name": company.name, "email": company.email})
    await db.commit()
    await db.refresh(company)
    return CompanyRegistered(
        company=_out(company, await company_service.usage(db, company)),
        credentials=Credentials(email=company.email, password=password),
    )


@router.get("/companies/{company_id}", response_model=CompanyOut)
async def get_company(company_id: uuid.UUID, db: DB, _: SuperAdmin):
    company = await _company(db, company_id)
    return _out(company, await company_service.usage(db, company))


@router.patch("/companies/{company_id}", response_model=CompanyOut)
async def update_company(company_id: uuid.UUID, body: CompanyAdminUpdate, db: DB, admin: SuperAdmin):
    company = await _company(db, company_id)
    changed = await _apply(db, company, body.model_dump(exclude_unset=True))
    audit(db, admin.id, "update", "company", company.id, {"fields": changed})
    await db.commit()
    await db.refresh(company)
    return _out(company, await company_service.usage(db, company))


@router.post("/companies/{company_id}/credentials", response_model=Credentials)
async def issue_credentials(company_id: uuid.UUID, body: CredentialsRequest, db: DB, admin: SuperAdmin):
    """Generate a new password for the company's admin (creating the admin if there is none)."""
    company = await _company(db, company_id)
    try:
        email, password = await company_service.issue_credentials(db, company, body.email)
    except company_service.CompanyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit(db, admin.id, "issue_credentials", "company", company.id, {"email": email})
    await db.commit()
    return Credentials(email=email, password=password)


# ------------------------------------------------------------------ the company's own view
@router.get("/company", response_model=CompanyOut)
async def my_company(db: DB, user: TenantUser):
    company = await _company(db, user.company_id)
    return _out(company, await company_service.usage(db, company), integrations=user.role == UserRole.ADMIN)


@router.patch("/company", response_model=CompanyOut)
async def update_my_company(body: CompanySelfUpdate, db: DB, user: AdminUser):
    company = await _company(db, user.company_id)
    changed = await _apply(db, company, body.model_dump(exclude_unset=True))
    audit(db, user.id, "update", "company", company.id, {"fields": changed})
    await db.commit()
    await db.refresh(company)
    return _out(company, await company_service.usage(db, company))

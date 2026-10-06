import io
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import delete, func, select

from app.api.deps import DB, ManagerUser, PageParams, TenantUser, not_found, owned
from app.models import Call, CallAnswer, Campaign, CampaignContact, CampaignQuestion, Company, ContactImport, User
from app.models.enums import CampaignStatus, ContactStatus, ImportStatus
from app.schemas.common import Page
from app.schemas.outbound import (
    CallOut,
    CampaignCreate,
    CampaignListItem,
    CampaignOut,
    CampaignUpdate,
    ContactOut,
    ImportPreview,
    QuestionIn,
    ResultRow,
    ScheduleRequest,
)
from app.schemas.support import CustomerBrief
from app.services import campaign_service, campaign_translation, company_service, excel_import, storage
from app.services.audit import audit
from app.services.telephony import calling_blocker
from app.services.voice_agent import DEFAULT_AGENT_PROMPT

router = APIRouter(prefix="/campaigns", tags=["outbound campaigns"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
EDITABLE = (CampaignStatus.DRAFT, CampaignStatus.SCHEDULED, CampaignStatus.PAUSED)


async def _campaign(db: DB, campaign_id: uuid.UUID, user: User) -> Campaign:
    """The caller's own campaign; every campaign sub-resource (contacts, imports, calls, results) goes through this."""
    return await owned(db, Campaign, campaign_id, user, "Campaign")


def _set_questions(campaign: Campaign, questions: list[QuestionIn]) -> None:
    keys = [q.key for q in questions]
    if len(keys) != len(set(keys)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Question keys must be unique")
    campaign.questions = [CampaignQuestion(order_index=i, **q.model_dump()) for i, q in enumerate(questions)]


async def _require_calling(company: Company) -> None:
    """Calls go out only on the company's own Twilio account, and only while the provider can reach us."""
    if problem := await calling_blocker(company):
        raise HTTPException(status.HTTP_409_CONFLICT, problem)


# ------------------------------------------------------------------ CRUD
@router.get("", response_model=Page[CampaignListItem])
async def list_campaigns(db: DB, user: TenantUser, pg: PageParams, status_: CampaignStatus | None = Query(None, alias="status")):
    stmt = select(Campaign).where(Campaign.company_id == user.company_id)
    if status_:
        stmt = stmt.where(Campaign.status == status_)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    campaigns = (await db.scalars(stmt.order_by(Campaign.created_at.desc()).offset(pg.offset).limit(pg.size))).all()
    counts = {
        (cid, st): n
        for cid, st, n in (
            await db.execute(
                select(CampaignContact.campaign_id, CampaignContact.status, func.count())
                .where(CampaignContact.campaign_id.in_([c.id for c in campaigns]))
                .group_by(CampaignContact.campaign_id, CampaignContact.status)
            )
        ).all()
    }
    items = []
    for c in campaigns:
        item = CampaignListItem.model_validate(c)
        item.contact_count = sum(n for (cid, _), n in counts.items() if cid == c.id)
        item.completed_count = counts.get((c.id, ContactStatus.COMPLETED), 0)
        items.append(item)
    return Page(items=items, total=total, page=pg.page, size=pg.size)


@router.post("", response_model=CampaignOut, status_code=201)
async def create_campaign(body: CampaignCreate, db: DB, user: ManagerUser):
    # The platform admin can cap how many campaigns a company may create.
    if problem := await company_service.campaign_limit_problem(db, await db.get(Company, user.company_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, problem)
    fields = body.model_dump(exclude={"questions"}, exclude_none=True)
    campaign = Campaign(**fields, created_by_id=user.id, company_id=user.company_id)
    _set_questions(campaign, body.questions)
    db.add(campaign)
    await db.flush()
    audit(db, user.id, "create", "campaign", campaign.id, {"name": body.name})
    await db.commit()
    return await db.get(Campaign, campaign.id, populate_existing=True)


@router.get("/agent-prompt/default")
async def default_agent_prompt(user: TenantUser) -> dict[str, str]:
    """The default personality & conversation rules, used when a campaign doesn't set its own."""
    return {"agent_prompt": DEFAULT_AGENT_PROMPT}


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: uuid.UUID, db: DB, user: TenantUser):
    return await _campaign(db, campaign_id, user)


@router.patch("/{campaign_id}", response_model=CampaignOut)
async def update_campaign(campaign_id: uuid.UUID, body: CampaignUpdate, db: DB, user: ManagerUser):
    campaign = await _campaign(db, campaign_id, user)
    if campaign.status not in EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot edit a {campaign.status} campaign; pause it first")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    audit(db, user.id, "update", "campaign", campaign.id)
    await db.commit()
    return campaign


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: uuid.UUID, db: DB, user: ManagerUser):
    campaign = await _campaign(db, campaign_id, user)
    if campaign.status != CampaignStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft campaigns can be deleted; cancel it instead")
    await db.delete(campaign)
    audit(db, user.id, "delete", "campaign", campaign_id, {"name": campaign.name})
    await db.commit()


@router.put("/{campaign_id}/questions", response_model=CampaignOut)
async def replace_questions(campaign_id: uuid.UUID, body: list[QuestionIn], db: DB, user: ManagerUser):
    campaign = await _campaign(db, campaign_id, user)
    if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.PAUSED):
        raise HTTPException(status.HTTP_409_CONFLICT, "Questions can only be changed while draft or paused")
    if await db.scalar(select(func.count(CallAnswer.id)).where(CallAnswer.campaign_id == campaign.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Calls already recorded answers; create a new campaign instead")
    await db.execute(delete(CampaignQuestion).where(CampaignQuestion.campaign_id == campaign.id))
    await db.flush()
    _set_questions(campaign, body)
    await db.commit()
    return await db.get(Campaign, campaign.id, populate_existing=True)


@router.post("/{campaign_id}/translate", response_model=CampaignOut)
async def translate_script(campaign_id: uuid.UUID, db: DB, user: ManagerUser, overwrite: bool = False):
    """Generate the alternate-language script (e.g. Hindi) from the primary one for review.

    Blank translations are filled; with overwrite=true existing ones are regenerated too."""
    campaign = await _campaign(db, campaign_id, user)
    if campaign.status not in EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot edit a {campaign.status} campaign; pause it first")
    written = await campaign_translation.ensure_translations(db, campaign, force=overwrite)
    if written == 0 and campaign_translation.missing_translations(campaign):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Could not translate: set SARVAM_API_KEY (or GEMINI_API_KEY), or type the translations manually.",
        )
    audit(db, user.id, "translate", "campaign", campaign.id, {"written": written, "overwrite": overwrite})
    await db.commit()
    return await db.get(Campaign, campaign.id, populate_existing=True)


# ------------------------------------------------------------------ Excel import
def _preview(imp: ContactImport) -> ImportPreview:
    out = ImportPreview.model_validate(imp)
    out.valid_sample = imp.valid_data[:50]
    return out


@router.post("/{campaign_id}/imports", response_model=ImportPreview, status_code=201)
async def upload_contacts(campaign_id: uuid.UUID, db: DB, user: ManagerUser, file: UploadFile = File(...)):
    """Step 1: upload + validate. Nothing is added to the campaign until the import is confirmed."""
    campaign = await _campaign(db, campaign_id, user)
    if campaign.status in (CampaignStatus.COMPLETED, CampaignStatus.CANCELLED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Campaign is {campaign.status}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 10 MB)")
    filename = file.filename or "contacts.xlsx"
    try:
        valid, invalid, duplicates = await excel_import.build_preview(db, campaign.id, filename, data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Could not read file: {exc}") from exc

    imp = ContactImport(
        campaign_id=campaign.id,
        filename=filename,
        storage_path=storage.save_bytes(f"imports/{campaign.id}", filename, data),
        total_rows=len(valid) + len(invalid) + duplicates,
        valid_rows=len(valid),
        invalid_rows=len(invalid),
        duplicate_rows=duplicates,
        valid_data=valid,
        invalid_data=invalid,
        created_by_id=user.id,
    )
    db.add(imp)
    await db.commit()
    return _preview(imp)


@router.get("/{campaign_id}/imports/{import_id}", response_model=ImportPreview)
async def get_import(campaign_id: uuid.UUID, import_id: uuid.UUID, db: DB, user: TenantUser):
    await _campaign(db, campaign_id, user)
    imp = await db.get(ContactImport, import_id)
    if imp is None or imp.campaign_id != campaign_id:
        raise not_found("Import")
    return _preview(imp)


@router.post("/{campaign_id}/imports/{import_id}/confirm", response_model=ImportPreview)
async def confirm_import(campaign_id: uuid.UUID, import_id: uuid.UUID, db: DB, user: ManagerUser):
    """Step 2: admin confirmed the preview -> customers + campaign contacts are created."""
    campaign = await _campaign(db, campaign_id, user)
    imp = await db.get(ContactImport, import_id)
    if imp is None or imp.campaign_id != campaign_id:
        raise not_found("Import")
    try:
        added = await excel_import.confirm_import(db, imp, campaign.company_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit(db, user.id, "confirm_import", "campaign", campaign_id, {"import_id": str(import_id), "added": added})
    await db.commit()
    return _preview(imp)


@router.post("/{campaign_id}/imports/{import_id}/discard", response_model=ImportPreview)
async def discard_import(campaign_id: uuid.UUID, import_id: uuid.UUID, db: DB, user: ManagerUser):
    await _campaign(db, campaign_id, user)
    imp = await db.get(ContactImport, import_id)
    if imp is None or imp.campaign_id != campaign_id:
        raise not_found("Import")
    if imp.status == ImportStatus.PREVIEW:
        imp.status = ImportStatus.DISCARDED
        await db.commit()
    return _preview(imp)


@router.get("/{campaign_id}/contacts", response_model=Page[ContactOut])
async def list_contacts(
    campaign_id: uuid.UUID, db: DB, user: TenantUser, pg: PageParams, status_: ContactStatus | None = Query(None, alias="status")
):
    await _campaign(db, campaign_id, user)
    stmt = select(CampaignContact).where(CampaignContact.campaign_id == campaign_id)
    if status_:
        stmt = stmt.where(CampaignContact.status == status_)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = (await db.scalars(stmt.order_by(CampaignContact.created_at).offset(pg.offset).limit(pg.size))).all()
    return Page(items=items, total=total, page=pg.page, size=pg.size)


# ------------------------------------------------------------------ lifecycle
@router.post("/{campaign_id}/schedule", response_model=CampaignOut)
async def schedule_campaign(campaign_id: uuid.UUID, body: ScheduleRequest, db: DB, user: ManagerUser):
    campaign = await _campaign(db, campaign_id, user)
    await _require_calling(campaign.company)
    if body.settings:
        for field, value in body.settings.model_dump(exclude_none=True).items():
            setattr(campaign, field, value)
    start_at = body.start_at
    if start_at and start_at.tzinfo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start_at must include a timezone offset")
    try:
        count = await campaign_service.schedule(db, campaign, start_at)
    except campaign_service.CampaignError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit(db, user.id, "schedule", "campaign", campaign.id, {"calls": count, "start_at": str(campaign.start_at)})
    await db.commit()
    return campaign


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(campaign_id: uuid.UUID, db: DB, user: ManagerUser):
    campaign = await _campaign(db, campaign_id, user)
    if campaign.status not in (CampaignStatus.RUNNING, CampaignStatus.SCHEDULED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot pause a {campaign.status} campaign")
    campaign.status = CampaignStatus.PAUSED
    audit(db, user.id, "pause", "campaign", campaign.id)
    await db.commit()
    return campaign


@router.post("/{campaign_id}/resume", response_model=CampaignOut)
async def resume_campaign(campaign_id: uuid.UUID, db: DB, user: ManagerUser):
    campaign = await _campaign(db, campaign_id, user)
    await _require_calling(campaign.company)
    if campaign.status != CampaignStatus.PAUSED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only paused campaigns can be resumed")
    # Contacts imported while paused still need scheduled calls.
    await campaign_service.schedule(db, campaign, datetime.now(UTC))
    campaign.status = CampaignStatus.RUNNING
    audit(db, user.id, "resume", "campaign", campaign.id)
    await db.commit()
    return campaign


@router.post("/{campaign_id}/cancel", response_model=CampaignOut)
async def cancel_campaign(campaign_id: uuid.UUID, db: DB, user: ManagerUser):
    campaign = await _campaign(db, campaign_id, user)
    if campaign.status in (CampaignStatus.COMPLETED, CampaignStatus.CANCELLED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Campaign is already {campaign.status}")
    await campaign_service.cancel(db, campaign)
    audit(db, user.id, "cancel", "campaign", campaign.id)
    await db.commit()
    return campaign


# ------------------------------------------------------------------ results
@router.get("/{campaign_id}/analytics")
async def campaign_analytics(campaign_id: uuid.UUID, db: DB, user: TenantUser):
    return await campaign_service.analytics(db, await _campaign(db, campaign_id, user))


@router.get("/{campaign_id}/calls", response_model=Page[CallOut])
async def list_calls(campaign_id: uuid.UUID, db: DB, user: TenantUser, pg: PageParams):
    await _campaign(db, campaign_id, user)
    stmt = select(Call).where(Call.campaign_id == campaign_id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    calls = (await db.scalars(stmt.order_by(Call.created_at.desc()).offset(pg.offset).limit(pg.size))).all()
    items = []
    for c in calls:
        item = CallOut.model_validate(c)
        item.has_recording = bool(c.recording_path)
        items.append(item)
    return Page(items=items, total=total, page=pg.page, size=pg.size)


async def _result_rows(db: DB, campaign: Campaign, offset: int | None, limit: int | None) -> tuple[int, list[ResultRow]]:
    stmt = select(CampaignContact).where(CampaignContact.campaign_id == campaign.id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(CampaignContact.created_at)
    if offset is not None:
        stmt = stmt.offset(offset).limit(limit)
    contacts = (await db.scalars(stmt)).all()

    keys = {q.id: q.key for q in campaign.questions}
    answers = (
        await db.execute(
            select(CallAnswer, Call.campaign_contact_id)
            .join(Call, Call.id == CallAnswer.call_id)
            .where(Call.campaign_contact_id.in_([c.id for c in contacts]))
            .order_by(CallAnswer.created_at)
        )
    ).all()
    by_contact: dict[uuid.UUID, tuple[uuid.UUID, dict]] = {}
    for ans, contact_id in answers:  # later attempts overwrite earlier ones
        call_id, values = by_contact.get(contact_id, (ans.call_id, {}))
        values[keys.get(ans.question_id, str(ans.question_id))] = ans.normalized_answer.get("value")
        by_contact[contact_id] = (ans.call_id, values)

    rows = [
        ResultRow(
            contact_id=c.id,
            customer=CustomerBrief.model_validate(c.customer),
            status=c.status,
            attempts=c.attempts,
            call_id=by_contact.get(c.id, (None, {}))[0],
            answers=by_contact.get(c.id, (None, {}))[1],
        )
        for c in contacts
    ]
    return total, rows


@router.get("/{campaign_id}/results", response_model=Page[ResultRow])
async def campaign_results(campaign_id: uuid.UUID, db: DB, user: TenantUser, pg: PageParams):
    total, rows = await _result_rows(db, await _campaign(db, campaign_id, user), pg.offset, pg.size)
    return Page(items=rows, total=total, page=pg.page, size=pg.size)


@router.get("/{campaign_id}/results/export")
async def export_results(campaign_id: uuid.UUID, db: DB, user: TenantUser):
    campaign = await _campaign(db, campaign_id, user)
    _, rows = await _result_rows(db, campaign, None, None)
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    keys = [q.key for q in campaign.questions]
    ws.append(["name", "phone", "status", "attempts", *keys])
    for r in rows:
        ws.append([r.customer.name, r.customer.phone, r.status, r.attempts,
                   *[_cell(r.answers.get(k)) for k in keys]])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"campaign-{campaign.id}-results.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _cell(value):
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)

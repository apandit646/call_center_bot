import io
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import delete, func, or_, select, update

from app.api.deps import DB, ManagerUser, PageParams, TenantUser, not_found, owned
from app.models import (
    Call,
    CallAnswer,
    Campaign,
    CampaignContact,
    CampaignQuestion,
    CampaignRun,
    Company,
    ContactImport,
    User,
)
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
    RunOut,
    RunStart,
    ScheduleRequest,
)
from app.schemas.support import CustomerBrief
from app.services import campaign_runs, campaign_service, campaign_translation, company_service, excel_import, storage
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


async def _run(db: DB, campaign: Campaign, which: str | None) -> CampaignRun | None:
    """The run a request is about: nothing = the current one, "all" = every run (None), or a run's id."""
    try:
        return await campaign_runs.resolve(db, campaign, which)
    except campaign_runs.RunError as exc:
        raise not_found("Run") from exc


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
                .join(Campaign, Campaign.id == CampaignContact.campaign_id)
                .where(
                    CampaignContact.campaign_id.in_([c.id for c in campaigns]),
                    # the current run's contacts (contacts with no run recorded belong to it)
                    or_(CampaignContact.run_id == Campaign.current_run_id, CampaignContact.run_id.is_(None)),
                )
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
    await campaign_runs.ensure(db, campaign)  # Run 1
    audit(db, user.id, "create", "campaign", campaign.id, {"name": body.name})
    await db.commit()
    return await db.get(Campaign, campaign.id, populate_existing=True)


# What a copy takes from the original: the script and the settings, never its contacts, schedule or results.
_COPIED = (
    "description", "offering_type", "offering_name", "offering_details", "greeting_script", "closing_script", "language",
    "alt_languages", "translations", "conversational", "agent_prompt", "timezone", "call_window_start", "call_window_end",
    "max_concurrent_calls", "max_attempts", "retry_delay_minutes",
)
_COPIED_QUESTION = ("order_index", "key", "text", "answer_type", "options", "translations", "extraction_hint", "is_required")


@router.post("/{campaign_id}/duplicate", response_model=CampaignOut, status_code=201)
async def duplicate_campaign(campaign_id: uuid.UUID, db: DB, user: ManagerUser):
    """A new draft with the same script, questions, languages and settings - and no contacts. For a
    similar campaign with a few changes; to call a new list with the SAME script, start a new run instead."""
    source = await _campaign(db, campaign_id, user)
    if problem := await company_service.campaign_limit_problem(db, await db.get(Company, user.company_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, problem)
    copy = Campaign(
        name=f"{source.name} (copy)"[:255], company_id=user.company_id, created_by_id=user.id,
        **{field: getattr(source, field) for field in _COPIED},
    )
    copy.questions = [CampaignQuestion(**{f: getattr(q, f) for f in _COPIED_QUESTION}) for q in source.questions]
    db.add(copy)
    await db.flush()
    await campaign_runs.ensure(db, copy)  # Run 1
    audit(db, user.id, "duplicate", "campaign", copy.id, {"name": copy.name, "from": str(source.id)})
    await db.commit()
    return await db.get(Campaign, copy.id, populate_existing=True)


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
    if campaign.run_number > 1:  # a draft that is really "run again": its earlier runs hold results
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This campaign has earlier runs with results, so it cannot be deleted. Cancel this run instead.")
    await db.delete(campaign)
    audit(db, user.id, "delete", "campaign", campaign_id, {"name": campaign.name})
    await db.commit()


@router.put("/{campaign_id}/questions", response_model=CampaignOut)
async def replace_questions(campaign_id: uuid.UUID, body: list[QuestionIn], db: DB, user: ManagerUser):
    campaign = await _campaign(db, campaign_id, user)
    if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.PAUSED):
        raise HTTPException(status.HTTP_409_CONFLICT, "Questions can only be changed while draft or paused")
    run = await campaign_runs.ensure(db, campaign)
    answered_now = await db.scalar(
        select(func.count(CallAnswer.id))
        .join(Call, Call.id == CallAnswer.call_id)
        .join(CampaignContact, CampaignContact.id == Call.campaign_contact_id)
        .where(campaign_runs.contacts_of(campaign, run))
    )
    if answered_now:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This run's calls already recorded answers. Finish or cancel the run; the questions can be changed before the next one.",
        )
    # Questions that collected answers in earlier runs are retired, not deleted: those runs keep
    # showing exactly what was asked. Questions nobody answered are simply replaced.
    used = select(CallAnswer.question_id).where(CallAnswer.campaign_id == campaign.id)
    await db.execute(
        update(CampaignQuestion)
        .where(CampaignQuestion.campaign_id == campaign.id, CampaignQuestion.is_active, CampaignQuestion.id.in_(used))
        .values(is_active=False)
    )
    await db.execute(delete(CampaignQuestion).where(CampaignQuestion.campaign_id == campaign.id, CampaignQuestion.is_active))
    await db.flush()
    await db.refresh(campaign, ["questions"])
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
        run = await campaign_runs.ensure(db, campaign)
        valid, invalid, duplicates = await excel_import.build_preview(db, campaign.id, filename, data, run.id)
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
    run = await campaign_runs.ensure(db, campaign)
    try:
        added = await excel_import.confirm_import(
            db, imp, campaign.company_id, run,
            never_call=await campaign_runs.never_call(db, campaign),
            completed=await campaign_runs.already_completed(db, campaign, run) if run.skip_completed else None,
        )
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
    campaign_id: uuid.UUID, db: DB, user: TenantUser, pg: PageParams, status_: ContactStatus | None = Query(None, alias="status"),
    run: str | None = None,
):
    """The contacts of one run: the current run by default, ?run=<id> for another, ?run=all for every run."""
    campaign = await _campaign(db, campaign_id, user)
    stmt = select(CampaignContact).where(campaign_runs.contacts_of(campaign, await _run(db, campaign, run)))
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


# ------------------------------------------------------------------ runs: using the campaign again
@router.get("/{campaign_id}/runs", response_model=list[RunOut])
async def list_runs(campaign_id: uuid.UUID, db: DB, user: TenantUser):
    """Every time this campaign was used, newest first, with how each run went."""
    runs = await campaign_runs.listing(db, await _campaign(db, campaign_id, user))
    await db.commit()  # a campaign from before runs existed gets its Run 1 here
    return runs


@router.post("/{campaign_id}/runs", response_model=RunOut, status_code=201)
async def start_run(campaign_id: uuid.UUID, body: RunStart, db: DB, user: ManagerUser):
    """Run the campaign again with the same script. Allowed once the current run has completed or
    been cancelled. The campaign becomes a draft again: upload contacts (new_list) and/or schedule it."""
    campaign = await _campaign(db, campaign_id, user)
    try:
        run = await campaign_runs.start(db, campaign, body.source, body.skip_completed)
    except campaign_runs.RunError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit(db, user.id, "start_run", "campaign", campaign.id, {"run": run.number, "source": body.source})
    await db.commit()
    campaign = await db.get(Campaign, campaign.id, populate_existing=True)
    return next(r for r in await campaign_runs.listing(db, campaign) if r["id"] == str(run.id))


# ------------------------------------------------------------------ results
@router.get("/{campaign_id}/analytics")
async def campaign_analytics(campaign_id: uuid.UUID, db: DB, user: TenantUser, run: str | None = None):
    """The current run by default; ?run=<id> for another; ?run=all for every run together."""
    campaign = await _campaign(db, campaign_id, user)
    return await campaign_service.analytics(db, campaign, await _run(db, campaign, run))


@router.get("/{campaign_id}/calls", response_model=Page[CallOut])
async def list_calls(campaign_id: uuid.UUID, db: DB, user: TenantUser, pg: PageParams, run: str | None = None):
    campaign = await _campaign(db, campaign_id, user)
    stmt = (
        select(Call).join(CampaignContact, CampaignContact.id == Call.campaign_contact_id)
        .where(campaign_runs.contacts_of(campaign, await _run(db, campaign, run)))
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    calls = (await db.scalars(stmt.order_by(Call.created_at.desc()).offset(pg.offset).limit(pg.size))).all()
    items = []
    for c in calls:
        item = CallOut.model_validate(c)
        item.has_recording = bool(c.recording_path)
        items.append(item)
    return Page(items=items, total=total, page=pg.page, size=pg.size)


async def _result_rows(
    db: DB, campaign: Campaign, offset: int | None, limit: int | None, run: CampaignRun | None
) -> tuple[int, list[ResultRow]]:
    stmt = select(CampaignContact).where(campaign_runs.contacts_of(campaign, run))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(CampaignContact.created_at)
    if offset is not None:
        stmt = stmt.offset(offset).limit(limit)
    contacts = (await db.scalars(stmt)).all()
    # Every question the campaign ever asked (retired ones too), so an old run's answers keep their names.
    keys = dict(
        (await db.execute(select(CampaignQuestion.id, CampaignQuestion.key).where(CampaignQuestion.campaign_id == campaign.id))).all()
    )
    numbers = dict((await db.execute(select(CampaignRun.id, CampaignRun.number).where(CampaignRun.campaign_id == campaign.id))).all())
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
            run=numbers.get(c.run_id, campaign.run_number),
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
async def campaign_results(campaign_id: uuid.UUID, db: DB, user: TenantUser, pg: PageParams, run: str | None = None):
    campaign = await _campaign(db, campaign_id, user)
    total, rows = await _result_rows(db, campaign, pg.offset, pg.size, await _run(db, campaign, run))
    return Page(items=rows, total=total, page=pg.page, size=pg.size)


@router.get("/{campaign_id}/results/export")
async def export_results(campaign_id: uuid.UUID, db: DB, user: TenantUser, run: str | None = None):
    campaign = await _campaign(db, campaign_id, user)
    chosen = await _run(db, campaign, run)
    _, rows = await _result_rows(db, campaign, None, None, chosen)
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    # One column per question that has an answer in these rows, the current script's questions first.
    keys = list(dict.fromkeys([*[q.key for q in campaign.questions], *[k for r in rows for k in r.answers]]))
    if chosen is not None and chosen.id != campaign.current_run_id and chosen.questions:
        keys = list(dict.fromkeys([*[q["key"] for q in chosen.questions], *[k for r in rows for k in r.answers]]))
    ws.append(["run", "name", "phone", "status", "attempts", *keys])
    for r in rows:
        ws.append([r.run, r.customer.name, r.customer.phone, r.status, r.attempts,
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

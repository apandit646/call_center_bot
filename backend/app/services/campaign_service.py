import logging
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Call, CallAnswer, Campaign, CampaignContact, CampaignQuestion, CampaignRun, ScheduledCall
from app.models.enums import (
    ACTIVE_CALL_STATUSES,
    AnswerStatus,
    AnswerType,
    CallStatus,
    CampaignStatus,
    ContactStatus,
    ScheduledCallStatus,
)
from app.services import call_service, campaign_runs, campaign_translation, company_service, voice_support
from app.services.queue import Queues, enqueue

log = logging.getLogger(__name__)


class CampaignError(ValueError):
    pass


async def schedule(db: AsyncSession, campaign: Campaign, start_at: datetime | None) -> int:
    """Create one ScheduledCall per pending contact. Returns how many calls were scheduled."""
    if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.SCHEDULED, CampaignStatus.PAUSED):
        raise CampaignError(f"Cannot schedule a campaign that is {campaign.status}")
    if not campaign.questions:
        raise CampaignError("Add at least one question before scheduling")

    start_at = start_at or datetime.now(UTC)
    run = await campaign_runs.ensure(db, campaign)
    contact_ids = (
        await db.scalars(
            # Only the current run: an earlier, cancelled run may still hold people who were never called.
            select(CampaignContact.id).where(campaign_runs.contacts_of(campaign, run), CampaignContact.status == ContactStatus.PENDING)
        )
    ).all()
    if not contact_ids and campaign.status == CampaignStatus.DRAFT:
        raise CampaignError("Import contacts before scheduling")

    db.add_all(
        ScheduledCall(campaign_id=campaign.id, campaign_contact_id=cid, scheduled_at=start_at) for cid in contact_ids
    )
    if contact_ids:
        await db.execute(
            update(CampaignContact).where(CampaignContact.id.in_(contact_ids)).values(status=ContactStatus.SCHEDULED)
        )
    # Re-scheduling moves first-attempt calls that are still waiting to the new start time
    # (otherwise a changed start time silently keeps the old one). Retries keep their own delay.
    moved = (
        await db.execute(
            update(ScheduledCall)
            .where(
                ScheduledCall.campaign_id == campaign.id,
                ScheduledCall.status == ScheduledCallStatus.PENDING,
                ScheduledCall.attempt_number == 1,
                ScheduledCall.scheduled_at != start_at,
            )
            .values(scheduled_at=start_at)
            .returning(ScheduledCall.id)
        )
    ).all()
    if moved:
        log.info("Campaign %s: moved %d waiting call(s) to %s", campaign.name, len(moved), start_at.isoformat())
    campaign.start_at = start_at
    run.started_at = run.started_at or start_at
    campaign.status = CampaignStatus.SCHEDULED
    await db.commit()
    # Fill any missing Hindi (etc.) lines now so calls never wait on translation. Failures are not
    # fatal: untranslated lines are spoken in the primary language.
    try:
        await campaign_translation.ensure_translations(db, campaign)
    except Exception:
        log.exception("Translating campaign %s failed; untranslated lines will use the primary language", campaign.id)
        await db.rollback()
    return len(contact_ids) + len(moved)


async def cancel(db: AsyncSession, campaign: Campaign) -> None:
    campaign.status = CampaignStatus.CANCELLED
    await db.execute(
        update(ScheduledCall)
        .where(
            ScheduledCall.campaign_id == campaign.id,
            ScheduledCall.status.in_([ScheduledCallStatus.PENDING, ScheduledCallStatus.QUEUED]),
        )
        .values(status=ScheduledCallStatus.CANCELLED)
    )
    await db.execute(
        update(CampaignContact)
        .where(CampaignContact.campaign_id == campaign.id, CampaignContact.status == ContactStatus.SCHEDULED)
        .values(status=ContactStatus.PENDING, callback_at=None)
    )
    await db.commit()


def in_call_window(campaign: Campaign, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(campaign.timezone)).time()
    start, end = campaign.call_window_start, campaign.call_window_end
    return start <= local <= end if start <= end else (local >= start or local <= end)


async def scheduler_tick(db: AsyncSession) -> int:
    """Move due scheduled calls onto the Redis call queue, honouring call windows, concurrency and
    each company's limits (daily calls, concurrent calls) set by the platform admin."""
    now = datetime.now(UTC)
    queued_total = 0

    # Requeue jobs stuck in QUEUED (e.g. the call worker died before dispatching).
    await db.execute(
        update(ScheduledCall)
        .where(ScheduledCall.status == ScheduledCallStatus.QUEUED, ScheduledCall.queued_at < now - timedelta(minutes=10))
        .values(status=ScheduledCallStatus.PENDING)
    )

    campaigns = (
        await db.scalars(
            select(Campaign).where(
                Campaign.status.in_([CampaignStatus.SCHEDULED, CampaignStatus.RUNNING]),
                Campaign.start_at <= now,
            )
        )
    ).all()
    global_free = settings.GLOBAL_MAX_CONCURRENT_CALLS - await call_service.active_call_count(db) - await _queued(db)
    # Calls each company may still start in this tick; None = that company has no limit.
    company_free: dict[uuid.UUID, int | None] = {}

    for campaign in campaigns:
        company = campaign.company
        if not company.is_active:
            continue  # suspended company: nothing dials until it is re-activated
        if campaign.status == CampaignStatus.SCHEDULED:
            campaign.status = CampaignStatus.RUNNING
            log.info("Campaign %s is now running", campaign.name)
        await call_service.check_campaign_completion(db, campaign)
        if campaign.status != CampaignStatus.RUNNING or not in_call_window(campaign, now):
            continue

        free = campaign.max_concurrent_calls - await call_service.active_call_count(db, campaign.id)
        free -= await _queued(db, campaign)
        free = min(free, global_free)
        if company.id not in company_free:
            company_free[company.id] = await company_service.call_capacity(db, company, now)
        capacity = company_free[company.id]
        if capacity is not None:
            # Daily limit reached: the calls stay pending and go out after midnight (company timezone).
            free = min(free, capacity)
        if free <= 0:
            continue

        due = (
            await db.scalars(
                select(ScheduledCall)
                .where(
                    ScheduledCall.campaign_id == campaign.id,
                    ScheduledCall.status == ScheduledCallStatus.PENDING,
                    ScheduledCall.scheduled_at <= now,
                )
                .order_by(ScheduledCall.scheduled_at)
                .limit(free)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for sc in due:
            sc.status = ScheduledCallStatus.QUEUED
            sc.queued_at = now
        await db.commit()
        for sc in due:
            await enqueue(Queues.OUTBOUND_CALLS, {"scheduled_call_id": str(sc.id)})
        queued_total += len(due)
        global_free -= len(due)
        if capacity is not None:
            company_free[company.id] = capacity - len(due)

    await _fail_stale_calls(db, now)
    await voice_support.close_stale(db, now)
    await db.commit()
    return queued_total


async def _queued(db: AsyncSession, campaign: Campaign | None = None) -> int:
    stmt = select(func.count(ScheduledCall.id)).where(ScheduledCall.status == ScheduledCallStatus.QUEUED)
    if campaign:
        stmt = stmt.where(ScheduledCall.campaign_id == campaign.id)
    return await db.scalar(stmt) or 0


async def _fail_stale_calls(db: AsyncSession, now: datetime) -> None:
    """Calls whose final status callback never arrived would otherwise block concurrency forever."""
    stale = (
        await db.scalars(
            select(Call.id).where(
                Call.status.in_(list(ACTIVE_CALL_STATUSES)),
                Call.created_at < now - timedelta(minutes=settings.CALL_STALE_MINUTES),
            )
        )
    ).all()
    for call_id in stale:
        await call_service.apply_status(db, call_id, CallStatus.FAILED, error="No final status received (stale)")


# --------------------------------------------------------------------------- analytics
async def analytics(db: AsyncSession, campaign: Campaign, run: CampaignRun | None) -> dict[str, Any]:
    """How a run went, question by question. run None = every run of the campaign together."""
    mine = campaign_runs.contacts_of(campaign, run)
    of_run = lambda stmt: stmt.join(CampaignContact, CampaignContact.id == Call.campaign_contact_id).where(mine)  # noqa: E731
    contact_rows = (
        await db.execute(select(CampaignContact.status, func.count()).where(mine).group_by(CampaignContact.status))
    ).all()
    call_rows = (await db.execute(of_run(select(Call.status, func.count())).group_by(Call.status))).all()
    avg_duration = await db.scalar(of_run(select(func.avg(Call.duration_seconds))).where(Call.duration_seconds.isnot(None)))

    answers = (
        await db.execute(
            of_run(
                select(CallAnswer.question_id, CallAnswer.status, CallAnswer.normalized_answer, CallAnswer.raw_answer)
                .join(Call, Call.id == CallAnswer.call_id)
            )
        )
    ).all()
    by_question: dict[Any, list] = {}
    for qid, status, normalized, raw in answers:
        by_question.setdefault(str(qid), []).append((status, normalized or {}, raw))

    # Which questions to report on, as (ids, key, text, answer_type). A closed run reports the script
    # it was asked with; "all runs" joins a question's earlier and later versions by their key.
    if run is None:
        every = (
            await db.scalars(
                select(CampaignQuestion).where(CampaignQuestion.campaign_id == campaign.id)
                .order_by(CampaignQuestion.is_active.desc(), CampaignQuestion.order_index)
            )
        ).all()
        merged: dict[str, tuple[set[str], str, str, str]] = {}
        for q in every:  # active ones first, so their wording names the group
            ids, key, text, answer_type = merged.setdefault(q.key, (set(), q.key, q.text, q.answer_type))
            ids.add(str(q.id))
        groups = list(merged.values())
    elif run.id != campaign.current_run_id and run.questions:
        groups = [({q["id"]}, q["key"], q["text"], q["answer_type"]) for q in run.questions]
    else:
        groups = [({str(q.id)}, q.key, q.text, q.answer_type) for q in campaign.questions]

    questions = []
    for ids, key, text, answer_type in groups:
        rows = [row for qid in ids for row in by_question.get(qid, [])]
        answered = [(n, raw) for s, n, raw in rows if s == AnswerStatus.ANSWERED]
        item: dict[str, Any] = {
            "question_id": sorted(ids)[0],
            "key": key,
            "text": text,
            "answer_type": answer_type,
            "responses": len(rows),
            "answered": len(answered),
        }
        values = [n.get("value") for n, _ in answered if n.get("value") is not None]
        if answer_type in (AnswerType.YES_NO, AnswerType.CHOICE):
            item["distribution"] = dict(Counter(str(v) for v in values))
        elif answer_type in (AnswerType.RATING, AnswerType.NUMBER):
            nums = [float(v) for v in values if isinstance(v, (int, float))]
            item["average"] = round(sum(nums) / len(nums), 2) if nums else None
            item["distribution"] = dict(Counter(str(int(v)) if v.is_integer() else str(v) for v in nums))
        else:
            item["samples"] = [raw for _, raw in answered[-10:] if raw]
        questions.append(item)

    contacts = {status: count for status, count in contact_rows}
    total = sum(contacts.values())
    return {
        "run": run.number if run else None,
        "contacts": {"total": total, **contacts},
        "calls": {"total": sum(c for _, c in call_rows), **{s: c for s, c in call_rows}},
        "completion_rate": round(contacts.get(ContactStatus.COMPLETED, 0) / total, 3) if total else 0,
        "avg_call_duration_seconds": round(float(avg_duration), 1) if avg_duration else None,
        "questions": questions,
    }

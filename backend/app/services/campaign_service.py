import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Call, CallAnswer, Campaign, CampaignContact, ScheduledCall
from app.models.enums import (
    ACTIVE_CALL_STATUSES,
    AnswerStatus,
    AnswerType,
    CallStatus,
    CampaignStatus,
    ContactStatus,
    ScheduledCallStatus,
)
from app.services import call_service, campaign_translation
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
    contact_ids = (
        await db.scalars(
            select(CampaignContact.id).where(
                CampaignContact.campaign_id == campaign.id, CampaignContact.status == ContactStatus.PENDING
            )
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
        .values(status=ContactStatus.PENDING)
    )
    await db.commit()


def in_call_window(campaign: Campaign, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(campaign.timezone)).time()
    start, end = campaign.call_window_start, campaign.call_window_end
    return start <= local <= end if start <= end else (local >= start or local <= end)


async def scheduler_tick(db: AsyncSession) -> int:
    """Move due scheduled calls onto the Redis call queue, honouring windows and concurrency."""
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

    for campaign in campaigns:
        if campaign.status == CampaignStatus.SCHEDULED:
            campaign.status = CampaignStatus.RUNNING
            log.info("Campaign %s is now running", campaign.name)
        await call_service.check_campaign_completion(db, campaign)
        if campaign.status != CampaignStatus.RUNNING or not in_call_window(campaign, now):
            continue

        free = campaign.max_concurrent_calls - await call_service.active_call_count(db, campaign.id)
        free -= await _queued(db, campaign)
        free = min(free, global_free)
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

    await _fail_stale_calls(db, now)
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
async def analytics(db: AsyncSession, campaign: Campaign) -> dict[str, Any]:
    contact_rows = (
        await db.execute(
            select(CampaignContact.status, func.count())
            .where(CampaignContact.campaign_id == campaign.id)
            .group_by(CampaignContact.status)
        )
    ).all()
    call_rows = (
        await db.execute(
            select(Call.status, func.count()).where(Call.campaign_id == campaign.id).group_by(Call.status)
        )
    ).all()
    avg_duration = await db.scalar(
        select(func.avg(Call.duration_seconds)).where(Call.campaign_id == campaign.id, Call.duration_seconds.isnot(None))
    )

    answers = (
        await db.execute(
            select(CallAnswer.question_id, CallAnswer.status, CallAnswer.normalized_answer, CallAnswer.raw_answer)
            .where(CallAnswer.campaign_id == campaign.id)
        )
    ).all()
    by_question: dict[Any, list] = {}
    for qid, status, normalized, raw in answers:
        by_question.setdefault(qid, []).append((status, normalized or {}, raw))

    questions = []
    for q in campaign.questions:
        rows = by_question.get(q.id, [])
        answered = [(n, raw) for s, n, raw in rows if s == AnswerStatus.ANSWERED]
        item: dict[str, Any] = {
            "question_id": str(q.id),
            "key": q.key,
            "text": q.text,
            "answer_type": q.answer_type,
            "responses": len(rows),
            "answered": len(answered),
        }
        values = [n.get("value") for n, _ in answered if n.get("value") is not None]
        if q.answer_type in (AnswerType.YES_NO, AnswerType.CHOICE):
            item["distribution"] = dict(Counter(str(v) for v in values))
        elif q.answer_type in (AnswerType.RATING, AnswerType.NUMBER):
            nums = [float(v) for v in values if isinstance(v, (int, float))]
            item["average"] = round(sum(nums) / len(nums), 2) if nums else None
            item["distribution"] = dict(Counter(str(int(v)) if v.is_integer() else str(v) for v in nums))
        else:
            item["samples"] = [raw for _, raw in answered[-10:] if raw]
        questions.append(item)

    contacts = {status: count for status, count in contact_rows}
    total = sum(contacts.values())
    return {
        "contacts": {"total": total, **contacts},
        "calls": {"total": sum(c for _, c in call_rows), **{s: c for s, c in call_rows}},
        "completion_rate": round(contacts.get(ContactStatus.COMPLETED, 0) / total, 3) if total else 0,
        "avg_call_duration_seconds": round(float(avg_duration), 1) if avg_duration else None,
        "questions": questions,
    }

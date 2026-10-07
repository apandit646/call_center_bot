"""Outbound call lifecycle: dispatch a scheduled call, apply provider status events, retry or finish."""
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, CallAnswer, Campaign, CampaignContact, CampaignQuestion, ScheduledCall
from app.models.enums import (
    ACTIVE_CALL_STATUSES,
    TERMINAL_CALL_STATUSES,
    AnswerStatus,
    CallStatus,
    CampaignStatus,
    ContactStatus,
    ScheduledCallStatus,
)
from app.services import campaign_translation, storage, voice_qa
from app.services.answer_extraction import PENDING, extract_answer
from app.services import billing_service, campaign_runs, outbound_callback
from app.services.telephony import calling_blocker, get_provider

log = logging.getLogger(__name__)


async def dispatch(db: AsyncSession, scheduled_call_id: uuid.UUID) -> None:
    sc = await db.get(ScheduledCall, scheduled_call_id, with_for_update=True)
    if sc is None or sc.status != ScheduledCallStatus.QUEUED:
        return
    campaign = await db.get(Campaign, sc.campaign_id)
    company = campaign.company
    if campaign.status != CampaignStatus.RUNNING or not company.is_active:
        # Paused/cancelled (or the company was suspended) while queued: hand it back to the scheduler,
        # or drop it if cancelled.
        sc.status = ScheduledCallStatus.CANCELLED if campaign.status == CampaignStatus.CANCELLED else ScheduledCallStatus.PENDING
        await db.commit()
        return
    if problem := await calling_blocker(company):
        # No Twilio account connected, or our public address is down: the customer would be rung and
        # hear the provider's error message, and the attempt would be burnt. Pause the campaign and
        # hand the call back instead; resuming the campaign shows this same reason until it is fixed.
        sc.status = ScheduledCallStatus.PENDING
        campaign.status = CampaignStatus.PAUSED
        await db.commit()
        log.error("Paused campaign %s without dialling: %s", campaign.name, problem)
        return

    contact = await db.get(CampaignContact, sc.campaign_contact_id)
    provider = get_provider(company)  # the company's own Twilio account and number
    if campaign_translation.missing_translations(campaign):
        # Normally filled when the campaign is scheduled; covers edits made afterwards.
        await campaign_translation.ensure_translations(db, campaign)
    # Generate the call's speech in every language it may switch to, so the customer never waits.
    await provider.prepare_call(voice_qa.all_utterances(campaign, contact, contact.customer.name))
    call = Call(
        campaign_id=campaign.id,
        campaign_contact_id=contact.id,
        customer_id=contact.customer_id,
        scheduled_call_id=sc.id,
        attempt_number=sc.attempt_number,
        provider=provider.name,
        status=CallStatus.INITIATED,
        started_at=datetime.now(UTC),
    )
    db.add(call)
    sc.status = ScheduledCallStatus.DISPATCHED
    contact.status = ContactStatus.IN_PROGRESS
    contact.attempts += 1
    contact.last_attempt_at = datetime.now(UTC)
    contact.callback_at = None  # the call they asked for is being made now
    await db.commit()

    try:
        call.provider_call_id = await provider.place_call(call.id, contact.customer.phone)
        await db.commit()
        log.info("Placed call %s to %s (attempt %d)", call.id, contact.customer.phone, sc.attempt_number)
    except Exception as exc:
        log.exception("Failed to place call %s", call.id)
        await apply_status(db, call.id, CallStatus.FAILED, error=str(exc))


async def apply_status(
    db: AsyncSession,
    call_id: uuid.UUID,
    status: CallStatus | str | None,
    duration_seconds: int | None = None,
    provider_call_id: str | None = None,
    error: str | None = None,
) -> None:
    call = await db.get(Call, call_id, with_for_update={"of": Call})
    if call is None:
        log.warning("Status event for unknown call %s", call_id)
        return
    if provider_call_id and not call.provider_call_id:
        call.provider_call_id = provider_call_id
    if duration_seconds is not None:
        call.duration_seconds = duration_seconds
    if error:
        call.error = error[:2000]
    if status is None or call.status in TERMINAL_CALL_STATUSES:
        if duration_seconds is not None and call.status in TERMINAL_CALL_STATUSES:
            await _bill(db, call)  # the provider reported the final length after the call had ended
        await db.commit()
        return

    call.status = CallStatus(status)
    if call.status == CallStatus.IN_PROGRESS and not call.answered_at:
        call.answered_at = datetime.now(UTC)
    if call.status in TERMINAL_CALL_STATUSES:
        call.ended_at = datetime.now(UTC)
        await _finalize(db, call)
        await _bill(db, call)
    await db.commit()
    if call.status in TERMINAL_CALL_STATUSES:
        await enrich_answers(db, call.id)


async def _bill(db: AsyncSession, call: Call) -> None:
    """Price the call for the company's invoice (only calls that were answered have a length)."""
    campaign = await db.get(Campaign, call.campaign_id)
    await billing_service.record_call(
        db, campaign.company_id, billing_service.OUTBOUND_CALL, call.id, call.duration_seconds, call.answered_at or call.created_at
    )


async def _finalize(db: AsyncSession, call: Call) -> None:
    """Decide the contact's outcome: completed, retry later, or failed."""
    contact = await db.get(CampaignContact, call.campaign_contact_id)
    campaign = await db.get(Campaign, call.campaign_id)
    answered = await db.scalar(
        select(func.count(CallAnswer.id)).where(CallAnswer.call_id == call.id, CallAnswer.status == AnswerStatus.ANSWERED)
    )
    active_campaign = campaign.status not in (CampaignStatus.CANCELLED, CampaignStatus.COMPLETED)
    if contact.status == ContactStatus.DO_NOT_CALL:
        log.info("Call %s: customer asked not to be called again; no retries", call.id)
    elif await voice_qa.callback_requested(call.id) and active_campaign and not call.all_questions_answered:
        # "I'm busy, call me later" - honour it even if some answers were given. At the time they
        # asked for, if they gave one (the agent only agrees to times inside the calling hours);
        # otherwise after the retry delay.
        asked_for = await voice_qa.callback_time(call.id)
        if asked_for is not None and asked_for < datetime.now(UTC):
            asked_for = None
        when = asked_for or datetime.now(UTC) + timedelta(minutes=max(campaign.retry_delay_minutes, 60))
        if contact.callback_count < outbound_callback.MAX_PER_CONTACT and contact.attempts > 0:
            contact.attempts -= 1  # a call-back the customer asked for is not one of the campaign's retry attempts
        contact.callback_count += 1
        contact.callback_at = asked_for
        db.add(
            ScheduledCall(
                campaign_id=campaign.id, campaign_contact_id=contact.id, scheduled_at=when, attempt_number=contact.attempts + 1,
                kind="callback_time" if asked_for else "callback_auto",
            )
        )
        contact.status = ContactStatus.SCHEDULED
        log.info("Call %s: customer asked for a callback; scheduled for %s", call.id, when.isoformat())
    elif call.status == CallStatus.COMPLETED and answered:
        contact.status = ContactStatus.COMPLETED
    elif contact.attempts < campaign.max_attempts and campaign.status not in (
        CampaignStatus.CANCELLED, CampaignStatus.COMPLETED
    ):
        db.add(
            ScheduledCall(
                campaign_id=campaign.id,
                campaign_contact_id=contact.id,
                scheduled_at=datetime.now(UTC) + timedelta(minutes=campaign.retry_delay_minutes),
                attempt_number=contact.attempts + 1,
            )
        )
        contact.status = ContactStatus.SCHEDULED
        log.info("Call %s ended %s; retry %d scheduled", call.id, call.status, contact.attempts + 1)
    else:
        contact.status = ContactStatus.FAILED
    await db.flush()
    await check_campaign_completion(db, campaign)


async def enrich_answers(db: AsyncSession, call_id: uuid.UUID) -> None:
    """After the call: let Gemini clean up answers that were saved raw to keep the live call fast
    (free-text answers get an English summary and any extra facts)."""
    rows = (
        await db.execute(
            select(CallAnswer, CampaignQuestion)
            .join(CampaignQuestion, CampaignQuestion.id == CallAnswer.question_id)
            .where(CallAnswer.call_id == call_id)
        )
    ).all()
    pending = [(a, q) for a, q in rows if (a.normalized_answer or {}).get(PENDING)]
    for answer, question in pending:
        try:
            result = await extract_answer(question, answer.raw_answer or "")
            normalized = {k: v for k, v in result["normalized_answer"].items() if k != PENDING}
            if normalized.get("value") in (None, ""):
                normalized["value"] = answer.raw_answer
            answer.normalized_answer = normalized
            answer.confidence = result.get("confidence")
        except Exception:
            log.exception("Could not enrich answer %s", answer.id)
            answer.normalized_answer = {k: v for k, v in answer.normalized_answer.items() if k != PENDING}
    if pending:
        await db.commit()
        log.info("Call %s: enriched %d free-text answer(s) after the call", call_id, len(pending))


async def check_campaign_completion(db: AsyncSession, campaign: Campaign) -> None:
    if campaign.status not in (CampaignStatus.RUNNING, CampaignStatus.SCHEDULED):
        return
    remaining = await db.scalar(
        select(func.count(CampaignContact.id)).where(
            campaign_runs.contacts_of(campaign, await campaign_runs.ensure(db, campaign)),  # the current run only
            CampaignContact.status.in_([ContactStatus.PENDING, ContactStatus.SCHEDULED, ContactStatus.IN_PROGRESS]),
        )
    )
    if remaining == 0:
        campaign.status = CampaignStatus.COMPLETED
        campaign.completed_at = datetime.now(UTC)
        log.info("Campaign %s completed", campaign.id)


async def store_recording(db: AsyncSession, call_id: uuid.UUID, recording_url: str) -> None:
    call = await db.get(Call, call_id)
    if call is None:
        return
    call.recording_url = recording_url
    try:
        campaign = await db.get(Campaign, call.campaign_id)
        data = await get_provider(campaign.company, call.provider).download_recording(recording_url)
        if data:
            call.recording_path = storage.save_bytes(f"recordings/{call.campaign_id}", f"{call.id}.mp3", data)
    except Exception:
        log.exception("Could not download recording for call %s", call_id)
    await db.commit()


async def handle_event(db: AsyncSession, event: dict[str, Any]) -> None:
    call_id = uuid.UUID(event["call_id"])
    if event.get("recording_url"):
        await store_recording(db, call_id, event["recording_url"])
    if event.get("status") or event.get("duration_seconds") is not None:
        await apply_status(
            db, call_id, event.get("status"), event.get("duration_seconds"), event.get("provider_call_id"),
            event.get("error"),
        )


async def active_call_count(db: AsyncSession, campaign_id: uuid.UUID | None = None) -> int:
    stmt = select(func.count(Call.id)).where(Call.status.in_(list(ACTIVE_CALL_STATUSES)))
    if campaign_id:
        stmt = stmt.where(Call.campaign_id == campaign_id)
    return await db.scalar(stmt) or 0

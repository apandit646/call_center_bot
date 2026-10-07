"""Calls the customer asked for: "call me at 5 pm".

    a call (or the portal) books a Callback -> the scheduler dials it when it is due
        -> the customer picks up -> the same AI agent as on an inbound call talks to them

A callback that is not answered is tried again a few times before it is marked failed, so the
team can see who could not be reached.
"""
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Callback, Company
from app.models.enums import CallbackStatus, CallStatus
from app.services.telephony import calling_blocker, get_provider

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_MINUTES = 10
STUCK_MINUTES = 20  # a dialled callback with no news for this long is treated as not answered
_NOT_REACHED = {CallStatus.NO_ANSWER, CallStatus.BUSY, CallStatus.FAILED, CallStatus.CANCELED}
_REASONS = {
    CallStatus.NO_ANSWER: "The customer did not pick up.", CallStatus.BUSY: "The customer's line was busy.",
    CallStatus.FAILED: "The call could not be connected.", CallStatus.CANCELED: "The call was cancelled before it connected.",
}


async def tick(db: AsyncSession, now: datetime | None = None) -> int:
    """Dial every callback that is due. Returns how many were dialled. Called by the scheduler."""
    now = now or datetime.now(UTC)
    stuck = await db.scalars(
        select(Callback).where(
            Callback.status == CallbackStatus.CALLING, Callback.support_call_id.is_(None),
            Callback.updated_at < now - timedelta(minutes=STUCK_MINUTES),
        ).with_for_update(skip_locked=True, of=Callback)
    )
    for callback in stuck.all():
        _missed(callback, "No answer was reported for the call.", now)
    await db.commit()

    due = (
        await db.scalars(
            select(Callback)
            .where(Callback.status == CallbackStatus.SCHEDULED, Callback.due_at <= now)
            .order_by(Callback.due_at).limit(20).with_for_update(skip_locked=True, of=Callback)
        )
    ).all()
    dialled = 0
    for callback in due:
        dialled += await dial(db, callback, now)
    return dialled


async def dial(db: AsyncSession, callback: Callback, now: datetime | None = None) -> int:
    """Place one callback. 1 if the call was placed, 0 if it could not be (the reason is saved on it)."""
    now = now or datetime.now(UTC)
    company = await db.get(Company, callback.company_id)
    problem = None if company and company.is_active else "The company is suspended."
    problem = problem or await calling_blocker(company)
    if problem:
        # Nothing to retry until someone fixes the set-up: say why, visibly.
        callback.status, callback.error = CallbackStatus.FAILED, problem[:1000]
        await db.commit()
        log.error("Callback %s not dialled: %s", callback.id, problem)
        return 0

    callback.status, callback.error = CallbackStatus.CALLING, None
    callback.attempts += 1
    callback.support_call_id = None
    await db.commit()  # visible as "calling" before the phone rings, and never dialled twice
    try:
        callback.provider_call_id = await get_provider(company).place_callback(callback.id, callback.phone)
    except Exception as exc:
        log.exception("Callback %s could not be dialled", callback.id)
        _missed(callback, str(exc)[:500], now)
        await db.commit()
        return 0
    await db.commit()
    log.info("Callback %s: dialling %s (attempt %d)", callback.id, callback.phone, callback.attempts)
    return 1


async def apply_status(db: AsyncSession, callback_id: uuid.UUID, status: CallStatus | None) -> None:
    """The provider's final word on a callback's call."""
    callback = await db.get(Callback, callback_id, with_for_update={"of": Callback})
    if callback is None or callback.status != CallbackStatus.CALLING:
        return
    if status == CallStatus.COMPLETED and callback.support_call_id is not None:
        callback.status = CallbackStatus.COMPLETED
    elif status == CallStatus.COMPLETED:
        _missed(callback, "The call connected, but the agent never got to speak (the call's audio did not start).", datetime.now(UTC))
    elif status in _NOT_REACHED:
        _missed(callback, _REASONS[status], datetime.now(UTC))
    await db.commit()


async def completed(db: AsyncSession, callback_id: str | uuid.UUID) -> None:
    """The conversation of a callback's call is over (the customer was reached)."""
    callback = await db.get(Callback, uuid.UUID(str(callback_id)))
    if callback is not None and callback.status == CallbackStatus.CALLING:
        callback.status = CallbackStatus.COMPLETED


def _missed(callback: Callback, reason: str, now: datetime) -> None:
    callback.error = reason
    if callback.attempts < MAX_ATTEMPTS:
        callback.status, callback.due_at = CallbackStatus.SCHEDULED, now + timedelta(minutes=RETRY_MINUTES)
    else:
        callback.status = CallbackStatus.FAILED

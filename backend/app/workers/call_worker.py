"""Outbound call workers: place queued calls and apply provider call events.

Run: python -m app.workers.call_worker
"""
import asyncio
import uuid
from typing import Any

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import call_service
from app.services.queue import Queues
from app.services.telephony import public_url_problem
from app.workers.base import run


async def handle_outbound(payload: dict[str, Any]) -> None:
    async with SessionLocal() as db:
        await call_service.dispatch(db, uuid.UUID(payload["scheduled_call_id"]))


async def handle_call_event(payload: dict[str, Any]) -> None:
    async with SessionLocal() as db:
        await call_service.handle_event(db, payload)


if __name__ == "__main__":
    if problem := public_url_problem():
        print(f"WARNING: real calls will not be placed - {problem}", flush=True)
    # Placing a call is a short REST request, so a handful of consumers keeps up with
    # GLOBAL_MAX_CONCURRENT_CALLS; the scheduler is what enforces concurrency limits.
    asyncio.run(
        run([
            (Queues.OUTBOUND_CALLS, handle_outbound, min(10, settings.GLOBAL_MAX_CONCURRENT_CALLS)),
            (Queues.CALL_EVENTS, handle_call_event, 4),
        ])
    )

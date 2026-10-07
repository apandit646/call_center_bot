"""Scheduler: every few seconds, move due calls from PostgreSQL onto the Redis call queue.

Run exactly one instance: python -m app.workers.scheduler
(A Redis lock makes accidental extra replicas harmless.)
"""
import asyncio
import logging

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.redis import get_redis
from app.services import callback_service, campaign_service
from app.workers.base import run

log = logging.getLogger(__name__)


async def scheduler_loop(stop: asyncio.Event) -> None:
    log.info("Scheduler started (every %ss)", settings.SCHEDULER_INTERVAL_SECONDS)
    while not stop.is_set():
        try:
            lock = get_redis().lock("lock:scheduler", timeout=settings.SCHEDULER_INTERVAL_SECONDS * 6)
            if await lock.acquire(blocking=False):
                try:
                    async with SessionLocal() as db:
                        queued = await campaign_service.scheduler_tick(db)
                    if queued:
                        log.info("Queued %d call(s)", queued)
                    async with SessionLocal() as db:  # calls customers asked for ("call me at 5 pm")
                        dialled = await callback_service.tick(db)
                    if dialled:
                        log.info("Dialled %d callback(s)", dialled)
                finally:
                    await lock.release()
        except Exception:
            log.exception("Scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.SCHEDULER_INTERVAL_SECONDS)
        except TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(run([], extra=[scheduler_loop]))

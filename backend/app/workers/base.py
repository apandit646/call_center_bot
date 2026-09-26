"""Tiny worker runtime: N concurrent consumers per queue, graceful shutdown, crash recovery."""
import asyncio
import logging
import os
import signal
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.database import engine
from app.core.logging import setup_logging
from app.core.redis import close_redis
from app.services import queue

log = logging.getLogger(__name__)
Handler = Callable[[dict[str, Any]], Awaitable[None]]


def worker_id(name: str, index: int) -> str:
    # Stable across restarts of the same container/host so in-flight jobs get recovered.
    base = os.environ.get("WORKER_ID") or socket.gethostname()
    return f"{base}:{name}:{index}"


async def consume(queue_name: str, handler: Handler, wid: str, stop: asyncio.Event) -> None:
    await queue.recover(queue_name, wid)
    log.info("Consumer %s listening on %s", wid, queue_name)
    while not stop.is_set():
        try:
            job = await queue.dequeue(queue_name, wid, timeout=2)
        except Exception:
            log.exception("Redis error on %s; retrying", queue_name)
            await asyncio.sleep(2)
            continue
        if job is None:
            continue
        raw, payload = job
        try:
            await handler(payload)
        except Exception:
            # PostgreSQL state (webhook_events / scheduled_calls) records the failure; don't poison-loop.
            log.exception("Job failed on %s: %s", queue_name, payload)
        finally:
            await queue.ack(queue_name, wid, raw)


async def run(consumers: list[tuple[str, Handler, int]], extra: list[Callable[[asyncio.Event], Awaitable[None]]] = ()) -> None:
    """consumers: (queue_name, handler, concurrency)."""
    setup_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    tasks = [
        asyncio.create_task(consume(q, h, worker_id(q, i), stop))
        for q, h, n in consumers
        for i in range(n)
    ]
    tasks += [asyncio.create_task(fn(stop)) for fn in extra]
    try:
        await asyncio.gather(*tasks)
    finally:
        await close_redis()
        await engine.dispose()

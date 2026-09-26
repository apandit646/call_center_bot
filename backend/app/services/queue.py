"""Reliable Redis list queues.

Redis carries jobs and temporary state only - PostgreSQL stays the source of truth, so every job
payload is just an id that the worker re-loads from the database.

Each job is atomically moved to a per-worker processing list (BLMOVE) and removed on ack, so a
crashed worker's in-flight jobs are recovered when a worker with the same id restarts.
"""
import json
import logging
from typing import Any

from app.core.redis import get_redis

log = logging.getLogger(__name__)


class Queues:
    WHATSAPP_INCOMING = "whatsapp.incoming"
    KNOWLEDGE_INGEST = "knowledge.ingest"
    TICKET_ASSIGNMENT = "ticket.assignment"
    OUTBOUND_CALLS = "outbound.calls"
    CALL_EVENTS = "call.events"


def _processing(queue: str, worker_id: str) -> str:
    return f"{queue}:processing:{worker_id}"


async def enqueue(queue: str, payload: dict[str, Any]) -> None:
    await get_redis().lpush(queue, json.dumps(payload, default=str))


async def dequeue(queue: str, worker_id: str, timeout: int = 5) -> tuple[str, dict[str, Any]] | None:
    raw = await get_redis().blmove(queue, _processing(queue, worker_id), timeout, "RIGHT", "LEFT")
    if raw is None:
        return None
    try:
        return raw, json.loads(raw)
    except json.JSONDecodeError:
        log.error("Dropping malformed job on %s: %r", queue, raw)
        await ack(queue, worker_id, raw)
        return None


async def ack(queue: str, worker_id: str, raw: str) -> None:
    await get_redis().lrem(_processing(queue, worker_id), 1, raw)


async def recover(queue: str, worker_id: str) -> int:
    """Push jobs left in this worker's processing list (from a crash) back onto the queue."""
    redis = get_redis()
    moved = 0
    while await redis.lmove(_processing(queue, worker_id), queue, "RIGHT", "LEFT"):
        moved += 1
    if moved:
        log.warning("Recovered %d in-flight job(s) on %s", moved, queue)
    return moved

"""Measured Redis backlogs and durable assistant work; failures are not zero."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, Depends
from app.api.v1.dependencies import CurrentUser, get_db_client, require_platform_admin
from app.core.postgres_adapter import Client
from app.core.db_utils import acquire_with_tenant
from app.domain.services.queue_service import DialerQueueService
from app.domain.services.queue_factory import resolve_queue_backend
from app.domain.services.streams_queue_service import STREAM_PRIORITY, STREAM_NORMAL, GROUP_NAME
from ._shared import _utc_now
from .schemas import QueueStatus, QueuesResponse

router = APIRouter()

def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Invalid counter observation")
    return value

async def _dialer_backlog(redis) -> QueueStatus:
    scope = "Redis list backlog: priority + tenant queues + scheduled retries; processing is the dialer processing ZSET."
    try:
        if redis is None:
            raise RuntimeError("Redis unavailable")
        async def measure():
            cursor, seen = 0, set()
            for _ in range(25):
                cursor, keys = await redis.scan(cursor, match="dialer:tenant:*:queue", count=200)
                seen.update(keys)
                if len(seen) > 5000:
                    raise RuntimeError("Queue inventory exceeds bounded observation")
                if int(cursor) == 0:
                    break
            else:
                raise RuntimeError("Queue scan did not finish within observation bound")
            keys = [DialerQueueService.PRIORITY_QUEUE, *seen]
            lengths = await asyncio.gather(*(redis.llen(key) for key in keys))
            scheduled, processing = await asyncio.gather(
                redis.zcard(DialerQueueService.SCHEDULED_ZSET), redis.zcard(DialerQueueService.PROCESSING_ZSET))
            return sum(_count(value) for value in lengths) + _count(scheduled), _count(processing)
        pending, processing = await asyncio.wait_for(measure(), timeout=3.0)
        return QueueStatus(name="Dialer lists", status="measured", pending=pending, processing=processing,
            scope=scope, detail="Current Redis counters; not a cross-command atomic snapshot. Historical success and latency are not measured.")
    except Exception:
        return QueueStatus(name="Dialer lists", scope=scope, detail="Redis backlog observation failed, timed out, or exceeded its scan bound.")

async def _streams_backlog(redis) -> QueueStatus:
    scope = "Redis Streams dialers consumer group: undelivered lag and unacknowledged entries; excludes shared scheduled retries."
    try:
        if redis is None:
            raise RuntimeError("Redis unavailable")
        async def measure():
            pending, processing = 0, 0
            for stream in (STREAM_PRIORITY, STREAM_NORMAL):
                key_type = await redis.type(stream)
                if key_type in ("none", b"none"):
                    continue
                groups = await redis.xinfo_groups(stream)
                selected = next((group for group in groups if group.get("name", group.get(b"name")) in (GROUP_NAME, GROUP_NAME.encode())), None)
                if selected is None:
                    raise RuntimeError("Consumer group observation unavailable")
                pending += _count(selected.get("lag", selected.get(b"lag")))
                processing += _count(selected.get("pending", selected.get(b"pending")))
            return pending, processing
        pending, processing = await asyncio.wait_for(measure(), timeout=3.0)
        return QueueStatus(name="Dialer streams", status="measured", pending=pending, processing=processing,
            scope=scope, detail="Consumer-group counters, not stream length; acknowledged history is excluded.")
    except Exception:
        return QueueStatus(name="Dialer streams", scope=scope, detail="Stream consumer-group observation unavailable; no zero fallback.")

async def _action_backlog(pool) -> QueueStatus:
    scope = "PostgreSQL assistant_actions: pending/scheduled and running/processing; completed/failed outcomes finished within 24 hours."
    try:
        async with acquire_with_tenant(pool, None, timeout=3.0) as conn:
            row = await asyncio.wait_for(conn.fetchrow("""
                SELECT COUNT(*) FILTER (WHERE status IN ('pending','scheduled')) AS pending,
                       COUNT(*) FILTER (WHERE status IN ('running','processing')) AS processing,
                       COUNT(*) FILTER (WHERE status='failed' AND completed_at >= NOW()-INTERVAL '24 hours') AS failed,
                       COUNT(*) FILTER (WHERE status='completed' AND completed_at >= NOW()-INTERVAL '24 hours') AS completed,
                       AVG(duration_ms) FILTER (WHERE status='completed' AND completed_at >= NOW()-INTERVAL '24 hours'
                                                AND duration_ms >= 0) AS avg_ms
                FROM assistant_actions
            """), timeout=3.0)
        if row is None:
            raise RuntimeError("Missing action aggregate")
        completed, failed = _count(row["completed"]), _count(row["failed"])
        total = completed + failed
        return QueueStatus(name="Assistant actions", status="measured", pending=_count(row["pending"]),
            processing=_count(row["processing"]), failed=failed, completed_24h=completed,
            success_rate_24h=round(completed / total * 100, 1) if total else None,
            avg_processing_time_ms=round(float(row["avg_ms"])) if row["avg_ms"] is not None else None,
            scope=scope, detail="Recorded action states and durations; does not prove downstream delivery.")
    except Exception:
        return QueueStatus(name="Assistant actions", scope=scope, detail="Durable action workload could not be read.")

@router.get("/health/queues", response_model=QueuesResponse)
async def get_queues_status(admin_user: CurrentUser = Depends(require_platform_admin), db_client: Client = Depends(get_db_client)):
    from app.core.container import get_container
    try:
        container = get_container()
        redis = getattr(container, "redis", None) if container.is_initialized else None
    except Exception:
        redis = None
    tasks = [_dialer_backlog(redis), _action_backlog(db_client.pool)]
    if resolve_queue_backend() == "streams":
        tasks.append(_streams_backlog(redis))
    queues = list(await asyncio.gather(*tasks))
    return QueuesResponse(queues=queues,
        total_pending=sum(q.pending for q in queues) if all(q.pending is not None for q in queues) else None,
        total_processing=sum(q.processing for q in queues) if all(q.processing is not None for q in queues) else None,
        checked_at=_utc_now())

"""Horizontal-scaling dialer queue backed by Redis Streams (T2.2).

Alternative to the list-based `DialerQueueService`. Ships as an
opt-in so existing single-worker deploys keep running on the
list-based queue until they're ready to migrate.

Why Streams
-----------
Redis list queues (`LPUSH` + `BRPOP`) work fine with exactly one
worker. With N workers they still work, but any scaling story is
bespoke: sharding by tenant, splitting the key space, manual
orphan sweeps. Streams give us all three for free:

- **Consumer groups.** `XREADGROUP` hands each message to exactly
  one consumer in the group. Add a worker process → it joins the
  group and starts pulling from wherever the cursor is.
- **Pending-entries list.** Every delivered-but-not-XACKed message
  shows up in `XPENDING`. A watchdog `XCLAIM`s entries idle for too
  long — a dead worker's jobs are re-dispatched automatically.
- **Cursor per consumer group.** Multiple groups on the same stream
  give us primary + audit / shadow consumers without extra plumbing.

Design
------
- **Two streams for priority.** `dialer:stream:priority` for jobs
  priority >= 8; `dialer:stream:normal` for the rest. Workers read
  priority first, then normal.
- **Consumer group name: `"dialers"`.** One group, many consumers.
  Pods auto-name themselves with `POD_ID` or hostname.
- **Retry schedule stays in a ZSET** (`dialer:scheduled`). Streams
  don't have a native delayed-delivery primitive; the existing ZSET
  pattern is fine and keeps diffs small. A supervisor promotes due
  entries from the ZSET into the stream.
- **ACK or re-claim.** Successful dequeue → XACK. Timeout-based
  XCLAIM sweeps stuck entries (default: idle > 5 minutes).
- **Not wired into `dialer_worker.py` yet.** The integration is
  mechanical but needs a staging dry-run. See the "migration"
  section below.
"""
from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.models.dialer_job import DialerJob

logger = logging.getLogger(__name__)


# Stream / group names — kept as module constants so operators can
# `redis-cli XLEN dialer:stream:priority` to inspect depth.
STREAM_PRIORITY = "dialer:stream:priority"
STREAM_NORMAL = "dialer:stream:normal"
GROUP_NAME = "dialers"
SCHEDULED_ZSET = "dialer:scheduled"

HIGH_PRIORITY_THRESHOLD = 8

# XCLAIM anything idle longer than this — a dead worker's jobs get
# redispatched. Conservative; bump lower when you trust your workers.
DEFAULT_CLAIM_IDLE_MS = 5 * 60 * 1000  # 5 minutes


@dataclass
class StreamDequeueResult:
    """Carries the job plus the stream/entry IDs needed to XACK or
    XCLAIM later. Callers MUST ack on success."""
    stream: str
    entry_id: str
    job: DialerJob


def resolve_consumer_name() -> str:
    """Stable identifier for this pod. Prefer `POD_ID`, fall back to
    the hostname. Consumer names are free-text in the Streams
    protocol — uniqueness per group is the only constraint."""
    pod = (os.getenv("POD_ID") or "").strip()
    if pod:
        return pod
    try:
        return socket.gethostname()
    except Exception:
        return "unknown-consumer"


class DialerStreamsQueueService:
    """Consumer-group queue built on Redis Streams."""

    def __init__(self, redis_client: Any):
        self._redis = redis_client
        self._consumer = resolve_consumer_name()
        self._groups_ensured = False

    # ──────────────────────────────────────────────────────────────────
    # Group bootstrap
    # ──────────────────────────────────────────────────────────────────

    async def ensure_groups(self) -> None:
        """Create the consumer group on both streams if missing.
        Idempotent — BUSYGROUP is treated as success. Call once per
        worker before the first dequeue."""
        if self._groups_ensured:
            return
        for stream in (STREAM_PRIORITY, STREAM_NORMAL):
            try:
                await self._redis.xgroup_create(
                    name=stream,
                    groupname=GROUP_NAME,
                    id="$",       # start from tail; historical msgs
                                  # were processed by the list-queue era.
                    mkstream=True,
                )
            except Exception as exc:
                # BUSYGROUP when the group already exists — fine.
                if "BUSYGROUP" in str(exc):
                    continue
                logger.warning(
                    "streams_group_create_failed stream=%s err=%s",
                    stream, exc,
                )
        self._groups_ensured = True

    # ──────────────────────────────────────────────────────────────────
    # Enqueue
    # ──────────────────────────────────────────────────────────────────

    async def enqueue_job(self, job: DialerJob) -> bool:
        """XADD the job to the priority or normal stream."""
        stream = (
            STREAM_PRIORITY if job.priority >= HIGH_PRIORITY_THRESHOLD
            else STREAM_NORMAL
        )
        payload = json.dumps(job.to_redis_dict())
        try:
            await self._redis.xadd(stream, {"job": payload})
        except Exception as exc:
            logger.error("streams_enqueue_failed job=%s err=%s", job.job_id, exc)
            return False
        return True

    # ──────────────────────────────────────────────────────────────────
    # Dequeue
    # ──────────────────────────────────────────────────────────────────

    async def dequeue_job(self, *, block_ms: int = 2000) -> Optional[StreamDequeueResult]:
        """Read one job for this consumer. Priority stream is tried
        first; if empty, falls through to the normal stream with the
        same consumer. Returns None on timeout / no messages.

        Callers must `await ack(...)` on success so XPENDING doesn't
        grow unbounded.
        """
        await self.ensure_groups()
        # XREADGROUP with ">" means "only messages this consumer
        # hasn't been delivered yet". That's the right semantic for
        # a worker loop.
        for stream in (STREAM_PRIORITY, STREAM_NORMAL):
            try:
                result = await self._redis.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=self._consumer,
                    streams={stream: ">"},
                    count=1,
                    block=block_ms if stream == STREAM_NORMAL else 0,
                )
            except Exception as exc:
                logger.warning("streams_dequeue_failed stream=%s err=%s", stream, exc)
                continue
            parsed = _extract_first_job(result)
            if parsed is not None:
                entry_id, payload = parsed
                try:
                    job = DialerJob.from_redis_dict(json.loads(payload))
                except Exception as exc:
                    logger.error(
                        "streams_dequeue_decode_failed stream=%s entry=%s err=%s",
                        stream, entry_id, exc,
                    )
                    # Ack the bad message so it doesn't poison the
                    # pending list — we're unable to process it.
                    try:
                        await self._redis.xack(stream, GROUP_NAME, entry_id)
                    except Exception:
                        pass
                    continue
                return StreamDequeueResult(stream=stream, entry_id=entry_id, job=job)
        return None

    async def ack(self, stream: str, entry_id: str) -> None:
        """Acknowledge successful processing. Removes the entry from
        XPENDING so the watchdog won't re-claim it."""
        try:
            await self._redis.xack(stream, GROUP_NAME, entry_id)
        except Exception as exc:
            logger.warning(
                "streams_ack_failed stream=%s entry=%s err=%s",
                stream, entry_id, exc,
            )

    # ──────────────────────────────────────────────────────────────────
    # Watchdog: reclaim dead-worker jobs
    # ──────────────────────────────────────────────────────────────────

    async def reclaim_stale(
        self,
        *,
        idle_ms: int = DEFAULT_CLAIM_IDLE_MS,
    ) -> int:
        """Scan XPENDING and XCLAIM entries idle longer than
        `idle_ms`. Called periodically from a supervisor / watchdog.
        Returns the count of reclaimed entries."""
        reclaimed = 0
        for stream in (STREAM_PRIORITY, STREAM_NORMAL):
            try:
                pending = await self._redis.xpending_range(
                    name=stream,
                    groupname=GROUP_NAME,
                    min="-", max="+", count=100,
                )
            except Exception as exc:
                logger.debug(
                    "streams_xpending_failed stream=%s err=%s",
                    stream, exc,
                )
                continue
            stale_ids: list[str] = []
            for entry in pending or []:
                entry_id = _pending_id(entry)
                idle = _pending_idle(entry)
                if entry_id and idle >= idle_ms:
                    stale_ids.append(entry_id)
            if not stale_ids:
                continue
            try:
                await self._redis.xclaim(
                    name=stream,
                    groupname=GROUP_NAME,
                    consumername=self._consumer,
                    min_idle_time=idle_ms,
                    message_ids=stale_ids,
                )
                reclaimed += len(stale_ids)
                logger.info(
                    "streams_reclaimed stream=%s count=%d",
                    stream, len(stale_ids),
                )
            except Exception as exc:
                logger.warning("streams_xclaim_failed stream=%s err=%s", stream, exc)
        return reclaimed

    # ──────────────────────────────────────────────────────────────────
    # Observability
    # ──────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────
    # Compatibility shims for the list-queue interface
    # ──────────────────────────────────────────────────────────────────
    #
    # These exist so call sites that already expect the list-queue
    # surface (campaign_service in particular) can swap the backend
    # without a parallel rewrite. The streams equivalents are:
    #
    #   close()                          — no-op; the Redis pool is
    #                                       owned by the container.
    #   clear_campaign_jobs(campaign_id) — log-only; bulk-deleting
    #                                       campaign-tagged entries from
    #                                       a stream requires either
    #                                       worker cooperation (XACK with
    #                                       a 'skipped' outcome) or a
    #                                       full re-stream. Operators
    #                                       who set DIALER_QUEUE_BACKEND
    #                                       =streams must rely on
    #                                       campaign-status checks at
    #                                       dequeue time instead. The
    #                                       worker already guards against
    #                                       processing jobs whose campaign
    #                                       was stopped.

    async def close(self) -> None:  # pragma: no cover — trivial
        return None

    async def clear_campaign_jobs(self, campaign_id: str) -> int:
        logger.warning(
            "streams_clear_campaign_jobs_noop campaign=%s — streams backend "
            "doesn't support bulk removal; rely on stop-campaign DB flag and "
            "the worker's per-job campaign status check.",
            campaign_id,
        )
        return 0

    # ──────────────────────────────────────────────────────────────────

    async def get_queue_stats(self) -> dict:
        """Shape-compatible with the list-based queue's stats so
        dashboards don't need a rewrite during migration."""
        stats: dict = {}
        for label, stream in (("priority", STREAM_PRIORITY), ("normal", STREAM_NORMAL)):
            try:
                length = int(await self._redis.xlen(stream))
            except Exception:
                length = 0
            stats[f"{label}_stream_length"] = length
        try:
            scheduled = int(await self._redis.zcard(SCHEDULED_ZSET))
        except Exception:
            scheduled = 0
        stats["scheduled_count"] = scheduled
        return stats


# ──────────────────────────────────────────────────────────────────
# Redis response parsing helpers.  Kept pure so they're easy to
# unit-test against synthetic payloads.
# ──────────────────────────────────────────────────────────────────

def _extract_first_job(result: Any) -> Optional[tuple[str, str]]:
    """XREADGROUP returns a nested structure:
        [[stream_name, [[entry_id, {field: value, ...}], ...]]]
    Pull the first entry's id + 'job' field, decoding bytes.
    """
    if not result:
        return None
    for _stream, entries in result:
        for entry in entries or []:
            entry_id, fields = entry[0], entry[1]
            entry_id = _as_str(entry_id)
            # `fields` is a dict; our payload lives at key 'job'.
            raw = None
            if isinstance(fields, dict):
                raw = fields.get("job") or fields.get(b"job")
            if raw is None:
                continue
            payload = _as_str(raw)
            return entry_id, payload
    return None


def _as_str(v: Any) -> str:
    if isinstance(v, (bytes, bytearray)):
        return v.decode()
    return str(v)


def _pending_id(entry: Any) -> Optional[str]:
    """XPENDING entry shape varies by client version; support both
    list-of-tuples and dict-of-fields."""
    if isinstance(entry, dict):
        eid = entry.get("message_id") or entry.get(b"message_id")
        return _as_str(eid) if eid else None
    if isinstance(entry, (list, tuple)) and entry:
        return _as_str(entry[0])
    return None


def _pending_idle(entry: Any) -> int:
    if isinstance(entry, dict):
        idle = entry.get("time_since_delivered") or entry.get(b"time_since_delivered") or 0
    elif isinstance(entry, (list, tuple)) and len(entry) >= 3:
        idle = entry[2]
    else:
        idle = 0
    try:
        return int(idle)
    except (TypeError, ValueError):
        return 0

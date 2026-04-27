"""Dialer-queue backend factory (T2.2 integration).

Decides at runtime whether the dialer pipeline gets the legacy
list-based `DialerQueueService` or the new Streams-based
`DialerStreamsQueueService`.

Why a factory instead of a direct swap
--------------------------------------
The two services have OVERLAPPING but not IDENTICAL interfaces:

  Common (safe to swap):
    enqueue_job(job)
    get_queue_stats()

  List-only (worker depends on these):
    dequeue_job(...) -> DialerJob   (auto-removed from queue on read)
    process_scheduled_jobs() -> int
    schedule_retry(job, delay_seconds)
    mark_completed / mark_failed / mark_skipped
    clear_queue / clear_campaign_jobs

  Streams-only:
    dequeue_job(block_ms) -> StreamDequeueResult  (must be ACK'd)
    ack(stream, entry_id)
    reclaim_stale(idle_ms)

A complete migration to streams therefore requires:
  1. The worker's dequeue loop to handle StreamDequeueResult and call
     ack on success. (Right now it expects DialerJob and assumes the
     read removed the entry.)
  2. The retry / completion paths to keep using the list service for
     auxiliary state (scheduled ZSET, processing set, mark_*) OR a
     new Streams-aware implementation of those.

Until the worker is rewired, the factory only delivers the streams
service to call sites that exclusively use the OVERLAP — currently
that's `campaign_service.start_campaign` (enqueue + stats only) and
read-only health/observability hooks.

Opt-in policy
-------------
- `DIALER_QUEUE_BACKEND=list` (default) — current behaviour.
- `DIALER_QUEUE_BACKEND=streams` — call sites in the OVERLAP get
  the streams service. The worker keeps using the list service
  via the explicit `legacy_list_service` parameter; this lets us
  send NEW campaigns to the streams backend while LEGACY
  in-flight retries continue draining from the list queue.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from app.domain.services.queue_service import DialerQueueService
from app.domain.services.streams_queue_service import (
    DialerStreamsQueueService,
)

logger = logging.getLogger(__name__)

BACKEND_LIST = "list"
BACKEND_STREAMS = "streams"


def resolve_queue_backend() -> str:
    """Return either `"list"` (default) or `"streams"`."""
    raw = (os.getenv("DIALER_QUEUE_BACKEND") or BACKEND_LIST).strip().lower()
    if raw not in (BACKEND_LIST, BACKEND_STREAMS):
        logger.warning(
            "dialer_queue_backend_invalid value=%r — falling back to %s",
            raw, BACKEND_LIST,
        )
        return BACKEND_LIST
    return raw


async def get_enqueue_service(
    *,
    redis_client: Any = None,
    legacy_list_service: Optional[DialerQueueService] = None,
) -> Any:
    """Return the right queue service for enqueue + stats.

    Caller passes `legacy_list_service` if it already has one
    initialised — preserves the existing pool. When backend=streams,
    we ignore the list service and instantiate the streams variant
    against the supplied Redis client.
    """
    backend = resolve_queue_backend()
    if backend == BACKEND_STREAMS:
        if redis_client is None:
            logger.warning(
                "dialer_queue_streams_requires_redis — falling back to list backend",
            )
            backend = BACKEND_LIST
        else:
            svc = DialerStreamsQueueService(redis_client)
            await svc.ensure_groups()
            logger.info("dialer_queue_backend=streams")
            return svc

    # List backend — preserve existing init semantics.
    if legacy_list_service is None:
        legacy_list_service = DialerQueueService()
        await legacy_list_service.initialize()
    logger.info("dialer_queue_backend=list")
    return legacy_list_service

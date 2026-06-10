"""Fire-and-forget emitter for the user-facing Event Stream.

Writes rows to `stream_events`, which backs the Event Stream panel on
the campaigns page. Used by the campaigns endpoints, dialer worker, and
telephony rate limiter to surface real activity instead of the previous
client-side mock generator.

The contract is intentionally narrow:

  - One function, `emit_event`. No batching, no async queue. Each call
    is a single INSERT on the caller's connection.
  - Errors are logged at WARNING and swallowed. Event emission must
    never fail a business operation (a campaign start succeeded; the
    user not seeing it in the stream is a UX problem, not a data
    problem).
  - Tenant isolation is enforced by RLS on the table; callers must
    supply the right tenant_id.

For higher-throughput emission points (e.g. dialer worker batch
progress), wrap calls in a Redis SETNX/EXPIRE throttle so the table
isn't hammered. See `dialer_worker.py` for the canonical example.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import asyncpg

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"campaign", "system", "alert", "user_action", "milestone", "call"}
VALID_SEVERITIES = {None, "info", "warning", "critical"}


async def emit_event(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    category: str,
    title: str,
    description: Optional[str] = None,
    severity: Optional[str] = None,
    related_campaign_id: Optional[str] = None,
    related_call_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Insert one row into `stream_events`. Never raises."""
    if category not in VALID_CATEGORIES:
        logger.warning("emit_event.invalid_category category=%s title=%r", category, title)
        return
    if severity not in VALID_SEVERITIES:
        logger.warning("emit_event.invalid_severity severity=%s", severity)
        severity = None

    metadata_json = json.dumps(metadata) if metadata is not None else None

    try:
        await conn.execute(
            """
            INSERT INTO stream_events
                (tenant_id, category, title, description, severity,
                 related_campaign_id, related_call_id, actor_user_id, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            """,
            tenant_id,
            category,
            title,
            description,
            severity,
            related_campaign_id,
            related_call_id,
            actor_user_id,
            metadata_json,
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget by design
        logger.warning(
            "emit_event.failed tenant=%s category=%s title=%r error=%s",
            tenant_id, category, title, exc,
        )


async def cleanup_expired_events_loop(
    pool: asyncpg.Pool,
    stop_event: asyncio.Event,
    interval_seconds: int = 6 * 3600,
) -> None:
    """Periodically delete stream_events past their expires_at.

    The table's expires_at defaults to now()+90d, but nothing ever DELETEd the
    expired rows, so the table grew forever and the /events polling query slowed
    over time. Runs an indexed delete (idx_se_expires) every ~6h, in bounded
    batches so a large backlog can't lock the table. Best-effort — never raises.
    """
    while not stop_event.is_set():
        try:
            total = 0
            async with pool.acquire() as conn:
                await conn.execute("SET app.bypass_rls = 'on'")
                await conn.execute(
                    "SET app.current_tenant_id = '00000000-0000-0000-0000-000000000000'"
                )
                # Delete in bounded batches so a large backlog can't long-lock.
                for _ in range(200):  # hard cap = 1M rows/run, plenty
                    status = await conn.execute(
                        """
                        DELETE FROM stream_events
                         WHERE id IN (
                            SELECT id FROM stream_events
                             WHERE expires_at < now()
                             LIMIT 5000
                         )
                        """
                    )
                    # asyncpg returns a tag like "DELETE 5000"
                    n = int(status.split()[-1]) if status and status.startswith("DELETE") else 0
                    total += n
                    if n < 5000:
                        break
            if total:
                logger.info("stream_events cleanup deleted=%d", total)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stream_events cleanup failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass


async def emit_event_via_pool(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    category: str,
    title: str,
    description: Optional[str] = None,
    severity: Optional[str] = None,
    related_campaign_id: Optional[str] = None,
    related_call_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Convenience wrapper for callers that don't already hold a conn.

    Used by background workers (dialer_worker) that emit events outside
    of a request lifecycle. Acquires one connection from the pool per
    call — fine at low rates, throttled higher up at the worker level.
    """
    try:
        async with pool.acquire() as conn:
            await emit_event(
                conn,
                tenant_id=tenant_id,
                category=category,
                title=title,
                description=description,
                severity=severity,
                related_campaign_id=related_campaign_id,
                related_call_id=related_call_id,
                actor_user_id=actor_user_id,
                metadata=metadata,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_event_via_pool.failed error=%s", exc)

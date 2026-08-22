"""Persistent platform-wide operational controls.

These controls must be shared by every API process and dialer worker. Keeping
them in PostgreSQL avoids the false-control failure mode of a module-level
boolean that resets on restart and differs between workers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundCallPause:
    paused: bool
    paused_at: datetime | None = None
    paused_by: str | None = None
    reason: str | None = None


async def get_outbound_call_pause(db_pool: Any) -> OutboundCallPause:
    """Return the shared outbound-call pause state.

    An installation running code briefly before migration 0014 is applied
    remains unpaused for deployment compatibility. Other database failures are
    not swallowed: CallGuard treats them as fail-closed because an operator's
    emergency pause must not silently stop working during a database fault.
    """
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT outbound_calls_paused, paused_at, paused_by, pause_reason
                FROM platform_runtime_controls
                WHERE id = 1
                """
            )
    except asyncpg.UndefinedTableError:
        logger.warning(
            "platform_runtime_controls is not migrated; outbound calls remain enabled"
        )
        return OutboundCallPause(paused=False)

    if not row:
        return OutboundCallPause(paused=False)
    return OutboundCallPause(
        paused=bool(row["outbound_calls_paused"]),
        paused_at=row["paused_at"],
        paused_by=str(row["paused_by"]) if row["paused_by"] else None,
        reason=row["pause_reason"],
    )


async def set_outbound_call_pause(
    db_pool: Any,
    *,
    paused: bool,
    actor_id: str,
    reason: str | None = None,
) -> OutboundCallPause:
    """Idempotently set the platform-wide outbound-call pause state."""
    normalized_reason = reason.strip()[:500] if reason and reason.strip() else None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO platform_runtime_controls (
                id, outbound_calls_paused, paused_at, paused_by,
                pause_reason, updated_at
            ) VALUES (
                1, $1, CASE WHEN $1 THEN NOW() ELSE NULL END,
                CASE WHEN $1 THEN $2::uuid ELSE NULL END,
                CASE WHEN $1 THEN $3 ELSE NULL END,
                NOW()
            )
            ON CONFLICT (id) DO UPDATE
               SET outbound_calls_paused = EXCLUDED.outbound_calls_paused,
                   paused_at = EXCLUDED.paused_at,
                   paused_by = EXCLUDED.paused_by,
                   pause_reason = EXCLUDED.pause_reason,
                   updated_at = NOW()
            RETURNING outbound_calls_paused, paused_at, paused_by, pause_reason
            """,
            paused,
            actor_id,
            normalized_reason,
        )
    return OutboundCallPause(
        paused=bool(row["outbound_calls_paused"]),
        paused_at=row["paused_at"],
        paused_by=str(row["paused_by"]) if row["paused_by"] else None,
        reason=row["pause_reason"],
    )

"""Support background passes. Each is idempotent and safe to run concurrently.

Every 15 minutes: mark missed deadlines, once per ticket per deadline, and
alert staff. Hourly: close tickets resolved more than 7 days ago, delete
uploads never attached within 24 hours, mark conversations idle for 24 hours
as abandoned, and delete bot transcripts past the retention window (only for
conversations that never became a ticket; a ticket keeps its conversation).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.core.db_utils import acquire_with_tenant
from app.domain.services.support import attachments, notifications
from app.domain.services.support.config import SupportSettings
from app.domain.services.support.lifecycle import REOPEN_WINDOW
from app.infrastructure.metrics import support_metrics as metrics

logger = logging.getLogger(__name__)

IDLE_CONVERSATION = timedelta(hours=24)
BATCH = 500

_MARK_SQL = {
    "first_response": """UPDATE support_tickets SET first_response_breached_at = $1
        WHERE id IN (SELECT id FROM support_tickets
                     WHERE status IN ('open','in_progress','waiting_on_customer')
                       AND first_responded_at IS NULL AND first_response_breached_at IS NULL
                       AND first_response_due_at <= $1
                     ORDER BY first_response_due_at LIMIT $2 FOR UPDATE SKIP LOCKED)
        RETURNING id, tenant_id, priority""",
    "resolution": """UPDATE support_tickets SET resolution_breached_at = $1
        WHERE id IN (SELECT id FROM support_tickets
                     WHERE status IN ('open','in_progress','waiting_on_customer')
                       AND resolution_breached_at IS NULL AND resolution_due_at <= $1
                     ORDER BY resolution_due_at LIMIT $2 FOR UPDATE SKIP LOCKED)
        RETURNING id, tenant_id, priority""",
}


async def mark_missed_deadlines(pool, settings: SupportSettings, *, now: datetime | None = None) -> dict:
    """Stamp each newly missed deadline in the same statement that finds it.

    The breach marker is the "once" guarantee: a ticket whose marker is set is
    never selected again for that deadline, even by a concurrent run.
    """
    now = now or datetime.now(timezone.utc)
    marked: dict[str, list] = {}
    for target, sql in _MARK_SQL.items():
        async with acquire_with_tenant(pool, None) as conn:
            rows = await conn.fetch(sql, now, BATCH)
            for row in rows:
                await conn.execute(
                    """INSERT INTO support_ticket_events (ticket_id, tenant_id, type, to_value)
                       VALUES ($1, $2, 'sla_breached', $3)""", row["id"], row["tenant_id"], target)
        marked[target] = rows
    for target, rows in marked.items():
        for row in rows:
            metrics.sla_breach(target)
            label = "First response deadline missed" if target == "first_response" else "Resolution deadline missed"
            try:
                await notifications.alert_staff(pool, row["id"], str(row["tenant_id"]), label, settings,
                                                kind=f"sla_{target}")
            except Exception as exc:
                logger.warning("support_sla_alert_failed error_type=%s", type(exc).__name__)
    return {target: len(rows) for target, rows in marked.items()}


async def close_resolved(pool, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    async with acquire_with_tenant(pool, None) as conn:
        rows = await conn.fetch(
            """UPDATE support_tickets SET status = 'closed', closed_at = $1, updated_at = $1, version = version + 1
               WHERE id IN (SELECT id FROM support_tickets WHERE status = 'resolved' AND resolved_at < $2
                            ORDER BY resolved_at LIMIT $3 FOR UPDATE SKIP LOCKED)
               RETURNING id, tenant_id""", now, now - REOPEN_WINDOW, BATCH)
        for row in rows:
            await conn.execute(
                """INSERT INTO support_ticket_events (ticket_id, tenant_id, type, from_value, to_value)
                   VALUES ($1, $2, 'status_changed', 'resolved', 'closed')""", row["id"], row["tenant_id"])
    return len(rows)


async def abandon_idle_conversations(pool, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    async with acquire_with_tenant(pool, None) as conn:
        result = await conn.execute(
            """UPDATE support_conversations SET status = 'abandoned', ended_at = $1, updated_at = $1
               WHERE status = 'active' AND ticket_id IS NULL AND updated_at < $2""", now, now - IDLE_CONVERSATION)
    return int(result.split()[-1])


async def purge_old_transcripts(pool, settings: SupportSettings, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.transcript_retention_days)
    async with acquire_with_tenant(pool, None) as conn:
        result = await conn.execute(
            """DELETE FROM support_conversations
               WHERE id IN (SELECT id FROM support_conversations
                            WHERE status <> 'active' AND ticket_id IS NULL AND ended_at < $1
                            ORDER BY ended_at LIMIT $2)""", cutoff, BATCH)
    return int(result.split()[-1])


async def run_sla_pass(pool, settings: SupportSettings) -> dict:
    return await mark_missed_deadlines(pool, settings)


async def run_hourly_pass(pool, settings: SupportSettings) -> dict:
    return {
        "closed": await close_resolved(pool),
        "uploads_deleted": await attachments.delete_unattached(pool),
        "conversations_abandoned": await abandon_idle_conversations(pool),
        "transcripts_deleted": await purge_old_transcripts(pool, settings),
    }

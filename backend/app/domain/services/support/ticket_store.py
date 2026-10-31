"""Ticket SQL. Every statement names ``tenant_id`` explicitly; never rely on RLS."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence
from uuid import UUID

from app.domain.services.support.errors import SupportConflict, SupportForbidden
from app.domain.services.support.receipts import to_json

TICKET_COLUMNS = """t.id, t.tenant_id, t.number, t.requester_user_id, t.conversation_id, t.subject,
    t.category, t.priority, t.status, t.assignee_user_id, t.related_campaign_id, t.related_call_id,
    t.diagnostics, t.first_response_due_at, t.resolution_due_at, t.first_responded_at,
    t.first_response_breached_at, t.resolution_breached_at, t.resolved_at, t.closed_at,
    t.csat_score, t.csat_comment, t.version, t.created_at, t.updated_at,
    rq.name AS requester_name, rq.email AS requester_email"""

_TICKET_FROM = "support_tickets t LEFT JOIN user_profiles rq ON rq.id = t.requester_user_id"

# Columns a status/priority/assignment change may set. Never from client keys.
_UPDATABLE = frozenset({
    "status", "priority", "category", "assignee_user_id", "first_response_due_at", "resolution_due_at",
    "first_responded_at", "resolved_at", "closed_at", "csat_score", "csat_comment",
})


def _ticket(row) -> dict | None:
    if row is None:
        return None
    ticket = dict(row)
    if isinstance(ticket.get("diagnostics"), str):
        ticket["diagnostics"] = json.loads(ticket["diagnostics"])
    return ticket


async def fetch_ticket(conn, ticket_id: UUID, tenant_id: str, *, for_update: bool = False) -> dict | None:
    lock = " FOR UPDATE OF t" if for_update else ""
    row = await conn.fetchrow(
        f"SELECT {TICKET_COLUMNS} FROM {_TICKET_FROM} WHERE t.id = $1 AND t.tenant_id = $2{lock}",
        ticket_id, tenant_id,
    )
    return _ticket(row)


async def fetch_ticket_tenant(conn, ticket_id: UUID) -> str | None:
    """Platform staff only (bypass context): which tenant owns this ticket."""
    value = await conn.fetchval("SELECT tenant_id FROM support_tickets WHERE id = $1", ticket_id)
    return str(value) if value else None


async def insert_ticket(conn, *, tenant_id: str, requester_id: str, conversation_id: UUID | None,
                        subject: str, category: str, priority: str, first_due: datetime,
                        resolution_due: datetime, related_campaign_id: UUID | None,
                        related_call_id: UUID | None, diagnostics: dict, request_id: UUID,
                        now: datetime) -> UUID:
    return await conn.fetchval(
        """INSERT INTO support_tickets
               (tenant_id, requester_user_id, conversation_id, subject, category, priority,
                first_response_due_at, resolution_due_at, related_campaign_id, related_call_id,
                diagnostics, request_id, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13, $13)
           RETURNING id""",
        tenant_id, requester_id, conversation_id, subject, category, priority, first_due, resolution_due,
        related_campaign_id, related_call_id, json.dumps(to_json(diagnostics)), request_id, now,
    )


async def update_ticket(conn, ticket_id: UUID, tenant_id: str, *, now: datetime, **fields: Any) -> int:
    """Apply fields, bump version and updated_at. Returns the new version."""
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    assignments = [f"{name} = ${index + 3}" for index, name in enumerate(fields)]
    assignments += ["version = version + 1", f"updated_at = ${len(fields) + 3}"]
    version = await conn.fetchval(
        f"UPDATE support_tickets SET {', '.join(assignments)} WHERE id = $1 AND tenant_id = $2 RETURNING version",
        ticket_id, tenant_id, *fields.values(), now,
    )
    if version is None:
        raise SupportConflict("This ticket changed while saving. Reload it and retry.")
    return version


async def insert_message(conn, *, ticket_id: UUID, tenant_id: str, author_id: str | None, author_type: str,
                         body: str, is_internal: bool, request_id: UUID | None, now: datetime) -> dict:
    row = await conn.fetchrow(
        """INSERT INTO support_ticket_messages
               (ticket_id, tenant_id, author_user_id, author_type, body, is_internal, request_id, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           RETURNING id, ticket_id, author_user_id, author_type, body, is_internal, created_at""",
        ticket_id, tenant_id, author_id, author_type, body, is_internal, request_id, now,
    )
    return dict(row)


async def insert_event(conn, *, ticket_id: UUID, tenant_id: str, actor_id: str | None, type: str,
                       from_value: Any = None, to_value: Any = None) -> None:
    await conn.execute(
        """INSERT INTO support_ticket_events (ticket_id, tenant_id, actor_user_id, type, from_value, to_value)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        ticket_id, tenant_id, actor_id, type,
        None if from_value is None else str(from_value)[:200],
        None if to_value is None else str(to_value)[:200],
    )


async def attach_files(conn, *, tenant_id: str, ticket_id: UUID, message_id: UUID,
                       attachment_ids: Sequence[UUID], uploader_id: str, staff: bool, now: datetime) -> None:
    """Link the uploader's own unattached files to this message, or refuse all of them."""
    if not attachment_ids:
        return
    ids = list(dict.fromkeys(attachment_ids))
    linked = await conn.fetch(
        """UPDATE support_ticket_attachments
           SET tenant_id = $1, ticket_id = $2, message_id = $3, attached_at = $6
           WHERE id = ANY($4::uuid[]) AND message_id IS NULL AND uploaded_by = $5
             AND (tenant_id = $1 OR ($7 AND tenant_id IS NULL))
           RETURNING id""",
        tenant_id, ticket_id, message_id, ids, uploader_id, now, staff,
    )
    if len(linked) != len(ids):
        raise SupportForbidden("One or more attachments are not available to attach. Upload them again.",
                               code="attachment_not_available")


async def fetch_messages(conn, ticket_id: UUID, tenant_id: str, *, include_internal: bool) -> list[dict]:
    rows = await conn.fetch(
        """SELECT m.id, m.author_user_id, m.author_type, m.body, m.is_internal, m.created_at,
                  up.name AS author_name
           FROM support_ticket_messages m LEFT JOIN user_profiles up ON up.id = m.author_user_id
           WHERE m.ticket_id = $1 AND m.tenant_id = $2 AND ($3 OR NOT m.is_internal)
           ORDER BY m.created_at, m.id""",
        ticket_id, tenant_id, include_internal,
    )
    return [dict(row) for row in rows]


async def fetch_attachments(conn, ticket_id: UUID, tenant_id: str, message_ids: Sequence[UUID]) -> list[dict]:
    if not message_ids:
        return []
    rows = await conn.fetch(
        """SELECT id, message_id, filename, size_bytes, content_type
           FROM support_ticket_attachments
           WHERE ticket_id = $1 AND tenant_id = $2 AND message_id = ANY($3::uuid[])
           ORDER BY created_at, id""",
        ticket_id, tenant_id, list(message_ids),
    )
    return [dict(row) for row in rows]


async def fetch_events(conn, ticket_id: UUID, tenant_id: str) -> list[dict]:
    rows = await conn.fetch(
        """SELECT e.id, e.type, e.from_value, e.to_value, e.created_at, e.actor_user_id, up.name AS actor_name
           FROM support_ticket_events e LEFT JOIN user_profiles up ON up.id = e.actor_user_id
           WHERE e.ticket_id = $1 AND e.tenant_id = $2 ORDER BY e.created_at, e.id""",
        ticket_id, tenant_id,
    )
    return [dict(row) for row in rows]


async def mark_read(conn, *, user_id: str, ticket_id: UUID, tenant_id: str, now: datetime) -> None:
    await conn.execute(
        """INSERT INTO support_ticket_reads (user_id, ticket_id, tenant_id, last_seen_at)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (user_id, ticket_id) DO UPDATE
               SET last_seen_at = GREATEST(support_ticket_reads.last_seen_at, EXCLUDED.last_seen_at)
           WHERE support_ticket_reads.tenant_id = EXCLUDED.tenant_id""",
        user_id, ticket_id, tenant_id, now,
    )


# A ticket is unread for a user when a public staff message is newer than the
# user's last view (or the user has never opened it).
_UNREAD_SQL = """EXISTS (
    SELECT 1 FROM support_ticket_messages sm
    WHERE sm.ticket_id = t.id AND sm.tenant_id = t.tenant_id
      AND sm.author_type = 'staff' AND NOT sm.is_internal
      AND sm.created_at > COALESCE((SELECT r.last_seen_at FROM support_ticket_reads r
                                    WHERE r.user_id = $2 AND r.ticket_id = t.id AND r.tenant_id = t.tenant_id),
                                   '-infinity'::timestamptz))"""


async def list_customer_tickets(conn, *, tenant_id: str, user_id: str, team: bool, status: str | None,
                                after: tuple[datetime, UUID] | None, limit: int) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT t.id, t.number, t.subject, t.status, t.priority, t.category, t.updated_at,
                   {_UNREAD_SQL} AS unread
            FROM support_tickets t
            WHERE t.tenant_id = $1
              AND ($3 OR t.requester_user_id = $2)
              AND ($4::text IS NULL OR t.status = $4)
              AND ($5::timestamptz IS NULL OR (t.updated_at, t.id) < ($5, $6::uuid))
            ORDER BY t.updated_at DESC, t.id DESC
            LIMIT $7""",
        tenant_id, user_id, team, status, after[0] if after else None, after[1] if after else None, limit,
    )
    return [dict(row) for row in rows]


async def unread_ticket_ids(conn, *, tenant_id: str, user_id: str) -> list[UUID]:
    rows = await conn.fetch(
        f"""SELECT t.id FROM support_tickets t
            WHERE t.tenant_id = $1 AND t.requester_user_id = $2 AND {_UNREAD_SQL}
            ORDER BY t.updated_at DESC, t.id DESC LIMIT 200""",
        tenant_id, user_id,
    )
    return [row["id"] for row in rows]


async def find_recent_open_ticket(conn, *, tenant_id: str, user_id: str, category: str,
                                  since: datetime) -> dict | None:
    row = await conn.fetchrow(
        """SELECT id, number, subject, status FROM support_tickets
           WHERE tenant_id = $1 AND requester_user_id = $2 AND category = $3
             AND status IN ('open','in_progress','waiting_on_customer') AND created_at >= $4
           ORDER BY created_at DESC LIMIT 1""",
        tenant_id, user_id, category, since,
    )
    return dict(row) if row else None


async def tenant_owns(conn, table: str, record_id: UUID, tenant_id: str) -> bool:
    if table not in {"campaigns", "calls"}:
        raise ValueError(f"no ownership check for {table}")
    return bool(await conn.fetchval(
        f"SELECT EXISTS (SELECT 1 FROM {table} WHERE id = $1 AND tenant_id = $2)", record_id, tenant_id))

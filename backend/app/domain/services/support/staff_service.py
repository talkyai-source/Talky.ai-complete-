"""Platform staff console: queue, ticket detail, changes, replies and metrics.

Staff work across tenants (platform context), so each child write names the
ticket's own tenant explicitly. Writes follow the existing lock order: audit
chain lock, current platform-admin authority, request receipt, ticket row.
A stale ``version`` is a 409, so of two concurrent edits exactly one wins.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.core.security.platform_admin_guard import (
    require_current_platform_admin,
    require_platform_admin_mutation,
)
from app.domain.services.audit_logger import AuditEvent
from app.domain.services.support import conversation_store, lifecycle, projection, receipts
from app.domain.services.support import ticket_store as store
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.config import SupportSettings
from app.domain.services.support.errors import (
    InvalidTransition,
    SupportNotFound,
    SupportValidationError,
    VersionConflict,
)
from app.domain.services.support.pagination import decode_cursor, next_cursor
from app.domain.services.support.priority import deadlines
from app.domain.services.support.ticket_service import WriteResult

PAGE_SIZE = 50
_NUMBER = re.compile(r"^\s*(?:sup-?)?0*(\d{1,12})\s*$", re.IGNORECASE)
_BREACHED_SQL = """(t.first_response_breached_at IS NOT NULL OR t.resolution_breached_at IS NOT NULL OR
    (t.status NOT IN ('resolved','closed') AND ((t.first_responded_at IS NULL AND t.first_response_due_at <= $1)
     OR t.resolution_due_at <= $1)))"""
_AT_RISK_SQL = f"""(NOT {_BREACHED_SQL} AND t.status NOT IN ('resolved','closed') AND
    (t.resolution_due_at <= $1 + interval '1 hour' OR
     (t.first_responded_at IS NULL AND t.first_response_due_at <= $1 + interval '1 hour')))"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def list_tickets(pool, actor: SupportActor, *, status: str | None, priority: str | None,
                       category: str | None, assignee: str | None, tenant_id: UUID | None,
                       sla: str | None, q: str | None, cursor: str | None) -> dict:
    now = _now()
    params: list[Any] = [now]
    where: list[str] = []

    def bind(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if status:
        where.append(f"t.status = {bind(status)}")
    if priority:
        where.append(f"t.priority = {bind(priority)}")
    if category:
        where.append(f"t.category = {bind(category)}")
    if tenant_id:
        where.append(f"t.tenant_id = {bind(tenant_id)}")
    if assignee == "me":
        where.append(f"t.assignee_user_id = {bind(actor.user_id)}::uuid")
    elif assignee == "unassigned":
        where.append("t.assignee_user_id IS NULL")
    elif assignee:
        try:
            where.append(f"t.assignee_user_id = {bind(UUID(assignee))}")
        except ValueError as exc:
            raise SupportValidationError("assignee must be me, unassigned or a user id.") from exc
    if sla == "breached":
        where.append(_BREACHED_SQL)
    elif sla == "at_risk":
        where.append(_AT_RISK_SQL)
    if q and q.strip():
        number = _NUMBER.match(q)
        where.append(f"t.number = {bind(int(number.group(1)))}" if number
                     else f"t.subject ILIKE {bind(_like(q.strip()[:100]))}")
    after = decode_cursor(cursor)
    if after:
        where.append(f"(t.updated_at, t.id) < ({bind(after[0])}, {bind(after[1])})")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    async with acquire_with_tenant(pool, None, user_id=actor.user_id) as conn:
        await require_current_platform_admin(conn, actor.user_id)
        rows = await conn.fetch(
            f"""SELECT t.id, t.number, t.subject, t.status, t.priority, t.category, t.updated_at, t.created_at,
                       t.tenant_id, tn.business_name AS tenant_name, t.assignee_user_id, asg.name AS assignee_name,
                       t.first_response_due_at, t.resolution_due_at, {_BREACHED_SQL} AS breached
                FROM support_tickets t
                JOIN tenants tn ON tn.id = t.tenant_id
                LEFT JOIN user_profiles asg ON asg.id = t.assignee_user_id
                {clause}
                ORDER BY t.updated_at DESC, t.id DESC LIMIT {PAGE_SIZE + 1}""",
            *params,
        )
    items = []
    for row in rows[:PAGE_SIZE]:
        item = projection.list_item(row)
        item.update({"tenant": {"id": row["tenant_id"], "name": row["tenant_name"]},
                     "assignee": {"id": row["assignee_user_id"], "name": row["assignee_name"]}
                     if row["assignee_user_id"] else None,
                     "sla": {"first_response_due_at": row["first_response_due_at"],
                             "resolution_due_at": row["resolution_due_at"], "breached": row["breached"]},
                     "created_at": row["created_at"]})
        items.append(item)
    return {"items": items, "next_cursor": next_cursor([dict(r) for r in rows], PAGE_SIZE)}


async def _detail(conn, ticket: dict, now: datetime) -> dict:
    tenant_id = ticket["tenant_id"]
    messages = await store.fetch_messages(conn, ticket["id"], tenant_id, include_internal=True)
    attachments = await store.fetch_attachments(conn, ticket["id"], tenant_id, [m["id"] for m in messages])
    tenant = await conn.fetchrow(
        """SELECT tn.id, tn.business_name AS name, COALESCE(p.name, tn.plan_id) AS plan
           FROM tenants tn LEFT JOIN plans p ON p.id = tn.plan_id WHERE tn.id = $1""", tenant_id)
    assignee = None
    if ticket.get("assignee_user_id"):
        assignee = await conn.fetchrow("SELECT id, name, email FROM user_profiles WHERE id = $1",
                                       ticket["assignee_user_id"])
    conversation: list[dict] = []
    if ticket.get("conversation_id"):
        conversation = [projection.bot_message(m) for m in
                        await conversation_store.fetch_messages(conn, ticket["conversation_id"], tenant_id)]
    events = await store.fetch_events(conn, ticket["id"], tenant_id)
    return projection.staff_ticket(ticket, messages, attachments, now=now, tenant=dict(tenant) if tenant else None,
                                   assignee=dict(assignee) if assignee else None, conversation=conversation,
                                   events=events)


async def _ticket_anywhere(conn, ticket_id: UUID, *, for_update: bool) -> dict:
    tenant_id = await store.fetch_ticket_tenant(conn, ticket_id)
    if tenant_id is None:
        raise SupportNotFound("Ticket not found.")
    ticket = await store.fetch_ticket(conn, ticket_id, tenant_id, for_update=for_update)
    if ticket is None:
        raise SupportNotFound("Ticket not found.")
    return ticket


async def get_ticket(pool, actor: SupportActor, ticket_id: UUID) -> dict:
    async with acquire_with_tenant(pool, None, user_id=actor.user_id) as conn:
        await require_current_platform_admin(conn, actor.user_id)
        ticket = await _ticket_anywhere(conn, ticket_id, for_update=False)
        return await _detail(conn, ticket, _now())


async def _begin_staff_write(conn, audit, actor: SupportActor, ticket_id: UUID, request_id: UUID,
                             operation: str) -> tuple[str, dict | None]:
    await audit.lock_transaction(conn)
    await require_platform_admin_mutation(conn, actor.user_id)
    tenant_id = await store.fetch_ticket_tenant(conn, ticket_id)
    if tenant_id is None:
        raise SupportNotFound("Ticket not found.")
    replay = await receipts.claim(conn, tenant_id=tenant_id, request_id=request_id,
                                  actor_id=actor.user_id, operation=operation)
    return tenant_id, replay


async def _valid_assignee(conn, user_id: UUID) -> bool:
    return bool(await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM user_profiles WHERE id = $1 AND role = 'platform_admin'
                          AND is_active AND is_verified)""", user_id))


async def patch_ticket(pool, audit, actor: SupportActor, ticket_id: UUID, *, request_id: UUID, version: int,
                       changes: dict[str, Any], settings: SupportSettings) -> WriteResult:
    """``changes`` holds only the fields the client sent (status, priority, category, assignee_user_id)."""
    now = _now()
    async with acquire_with_tenant(pool, None, user_id=actor.user_id) as conn:
        tenant_id, replay = await _begin_staff_write(conn, audit, actor, ticket_id, request_id, "staff.ticket.patch")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        ticket = await store.fetch_ticket(conn, ticket_id, tenant_id, for_update=True)
        if ticket["version"] != version:
            raise VersionConflict()
        update: dict[str, Any] = {}
        events: list[tuple[str, Any, Any]] = []
        target = changes.get("status")
        if target is not None and lifecycle.staff_transition(ticket["status"], target):
            update["status"] = target
            if target == "resolved":
                update["resolved_at"] = now
            elif target == "closed":
                update["closed_at"] = now
            elif ticket["status"] == "resolved":
                update["resolved_at"] = None
            events.append(("status_changed", ticket["status"], target))
        level = changes.get("priority")
        if level is not None and level != ticket["priority"]:
            if ticket["status"] == "closed":
                raise InvalidTransition("A closed ticket cannot be changed.")
            update["priority"] = level
            update["first_response_due_at"], update["resolution_due_at"] = deadlines(
                level, ticket["created_at"], settings)
            events.append(("priority_changed", ticket["priority"], level))
        category = changes.get("category")
        if category is not None and category != ticket["category"]:
            update["category"] = category
            events.append(("category_changed", ticket["category"], category))
        if "assignee_user_id" in changes:
            assignee = changes["assignee_user_id"]
            current = ticket.get("assignee_user_id")
            if str(assignee) != str(current) and not (assignee is None and current is None):
                if assignee is not None and not await _valid_assignee(conn, assignee):
                    raise SupportValidationError("The assignee must be an active platform admin.",
                                                 code="invalid_assignee")
                update["assignee_user_id"] = assignee
                events.append(("assigned", current, assignee))
        if update:
            await store.update_ticket(conn, ticket_id, tenant_id, now=now, **update)
            for kind, before, after in events:
                await store.insert_event(conn, ticket_id=ticket_id, tenant_id=tenant_id, actor_id=actor.user_id,
                                         type=kind, from_value=before, to_value=after)
            await audit.log_in_transaction(
                conn, event_type=AuditEvent.SUPPORT_TICKET_CHANGED, action="support.ticket.update",
                actor_id=actor.user_id, actor_role=actor.role, tenant_id=tenant_id,
                resource_type="support_ticket", resource_id=ticket_id,
                before_state={k: ticket.get(k) for k in update}, after_state=update)
        ticket = await store.fetch_ticket(conn, ticket_id, tenant_id)
        body = await receipts.save(conn, tenant_id=tenant_id, request_id=request_id, actor_id=actor.user_id,
                                   operation="staff.ticket.patch", resource_id=ticket_id, status_code=200,
                                   response=await _detail(conn, ticket, now))
    return WriteResult(body, 200, effects={"ticket_id": ticket_id, "tenant_id": tenant_id,
                                            "resolved": update.get("status") == "resolved"})


async def add_staff_message(pool, audit, actor: SupportActor, ticket_id: UUID, *, request_id: UUID, body: str,
                            internal: bool, attachment_ids: Sequence[UUID]) -> WriteResult:
    now = _now()
    async with acquire_with_tenant(pool, None, user_id=actor.user_id) as conn:
        tenant_id, replay = await _begin_staff_write(conn, audit, actor, ticket_id, request_id, "staff.ticket.message")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        ticket = await store.fetch_ticket(conn, ticket_id, tenant_id, for_update=True)
        if ticket["status"] == "closed":
            raise InvalidTransition("This ticket is closed.")
        message = await store.insert_message(conn, ticket_id=ticket_id, tenant_id=tenant_id, author_id=actor.user_id,
                                             author_type="staff", body=body, is_internal=internal,
                                             request_id=request_id, now=now)
        await store.attach_files(conn, tenant_id=tenant_id, ticket_id=ticket_id, message_id=message["id"],
                                 attachment_ids=attachment_ids, uploader_id=actor.user_id, staff=True, now=now)
        update = {}
        if not internal and ticket.get("first_responded_at") is None:
            update["first_responded_at"] = now
        await store.update_ticket(conn, ticket_id, tenant_id, now=now, **update)
        await store.insert_event(conn, ticket_id=ticket_id, tenant_id=tenant_id, actor_id=actor.user_id,
                                 type="internal_note" if internal else "staff_replied")
        await audit.log_in_transaction(
            conn, event_type=AuditEvent.SUPPORT_TICKET_CHANGED,
            action="support.ticket.internal_note" if internal else "support.ticket.reply",
            actor_id=actor.user_id, actor_role=actor.role, tenant_id=tenant_id, resource_type="support_ticket",
            resource_id=ticket_id, metadata={"message_id": str(message["id"]), "attachments": len(attachment_ids)})
        attachments = await store.fetch_attachments(conn, ticket_id, tenant_id, [message["id"]])
        message["author_name"] = await conn.fetchval("SELECT name FROM user_profiles WHERE id = $1", actor.user_id)
        response = projection.staff_message(
            message, projection.attachments_by_message(attachments).get(message["id"], []))
        saved = await receipts.save(conn, tenant_id=tenant_id, request_id=request_id, actor_id=actor.user_id,
                                    operation="staff.ticket.message", resource_id=message["id"], status_code=201,
                                    response=response)
    return WriteResult(saved, 201, effects={"ticket_id": ticket_id, "tenant_id": tenant_id,
                                             "message_id": message["id"], "public": not internal})


async def metrics_summary(pool, actor: SupportActor, *, days: int) -> dict:
    now = _now()
    since = now - timedelta(days=days)
    async with acquire_with_tenant(pool, None, user_id=actor.user_id) as conn:
        await require_current_platform_admin(conn, actor.user_id)
        conversations = await conn.fetchrow(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE status = 'resolved_by_bot') AS solved,
                      count(*) FILTER (WHERE status <> 'active') AS ended
               FROM support_conversations WHERE created_at >= $1""", since)
        tickets = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE created_at >= $1) AS created,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM first_responded_at - created_at))
                          FILTER (WHERE created_at >= $1 AND first_responded_at IS NOT NULL) AS median_first_seconds,
                      avg(csat_score) FILTER (WHERE resolved_at >= $1) AS avg_csat
               FROM support_tickets""", since)
        open_rows = await conn.fetch(
            """SELECT priority, count(*) AS n FROM support_tickets
               WHERE status IN ('open','in_progress','waiting_on_customer') GROUP BY priority""")
        breaches = await conn.fetchval(
            "SELECT count(*) FROM support_ticket_events WHERE type = 'sla_breached' AND created_at >= $1", since)
    ended = conversations["ended"] or 0
    median = tickets["median_first_seconds"]
    return {
        "days": days,
        "conversations": conversations["total"],
        "bot_solved_pct": round(100.0 * conversations["solved"] / ended, 1) if ended else None,
        "tickets_created": tickets["created"],
        "open_by_priority": {level: 0 for level in ("urgent", "high", "normal", "low")}
        | {row["priority"]: row["n"] for row in open_rows},
        "sla_breaches": breaches,
        "median_first_response_minutes": round(float(median) / 60.0, 1) if median is not None else None,
        "avg_csat": round(float(tickets["avg_csat"]), 2) if tickets["avg_csat"] is not None else None,
    }


__all__ = ["list_tickets", "get_ticket", "patch_ticket", "add_staff_message", "metrics_summary"]

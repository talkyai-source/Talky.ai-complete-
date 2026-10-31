"""Customer ticket flows: create, read, list, reply, resolve, reopen, rate, unread.

The tenant always comes from the actor. Another tenant's ticket is 403; a
colleague's ticket is readable only with support:read_team and never writable.
Every write is idempotent through ``receipts``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.support import (
    conversation_store as conversations,
)
from app.domain.services.support import (
    lifecycle,
    projection,
    receipts,
)
from app.domain.services.support import (
    ticket_store as store,
)
from app.domain.services.support.access import classify_missing
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.config import SupportSettings
from app.domain.services.support.errors import InvalidTransition, SupportConflict, SupportForbidden
from app.domain.services.support.pagination import decode_cursor, next_cursor
from app.domain.services.support.priority import compute_priority, deadlines
from app.domain.services.support.vocabulary import ticket_display_number

logger = logging.getLogger(__name__)

DUPLICATE_WINDOW = timedelta(days=7)
PAGE_SIZE = 25


@dataclass(frozen=True)
class WriteResult:
    body: dict
    status_code: int = 200
    replayed: bool = False
    # Facts for after-commit side effects (notifications, audit, metrics).
    effects: dict[str, Any] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _locked_ticket(conn, ticket_id: UUID, tenant_id: str) -> dict:
    ticket = await store.fetch_ticket(conn, ticket_id, tenant_id, for_update=True)
    if ticket is None:
        await classify_missing(conn, "support_tickets", ticket_id)
    return ticket


def _require_requester(ticket: dict, actor: SupportActor) -> None:
    if str(ticket.get("requester_user_id")) != str(actor.user_id):
        raise SupportForbidden("Only the person who opened this ticket can change it.", code="not_requester")


async def _ticket_view(conn, ticket: dict, actor: SupportActor, now: datetime) -> dict:
    messages = await store.fetch_messages(conn, ticket["id"], ticket["tenant_id"], include_internal=False)
    attachments = await store.fetch_attachments(conn, ticket["id"], ticket["tenant_id"], [m["id"] for m in messages])
    return projection.customer_ticket(ticket, messages, attachments, viewer_id=actor.user_id, now=now)


async def _require_owned(conn, table: str, record_id: UUID | None, tenant_id: str) -> None:
    if record_id is not None and not await store.tenant_owns(conn, table, record_id, tenant_id):
        await classify_missing(conn, table, record_id)


async def create_ticket(pool, actor: SupportActor, *, request_id: UUID, subject: str, category: str,
                        description: str, conversation_id: UUID | None, related_campaign_id: UUID | None,
                        related_call_id: UUID | None, attachment_ids: Sequence[UUID],
                        settings: SupportSettings) -> WriteResult:
    tenant_id = actor.require_tenant()
    now = _now()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        replay = await receipts.claim(conn, tenant_id=tenant_id, request_id=request_id,
                                      actor_id=actor.user_id, operation="ticket.create")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        await _require_owned(conn, "campaigns", related_campaign_id, tenant_id)
        await _require_owned(conn, "calls", related_call_id, tenant_id)

        diagnostics: dict = {"source": "ticket_form"}
        findings, account = (), None
        if conversation_id is not None:
            conversation = await conversations.fetch_conversation(conn, conversation_id, tenant_id, for_update=True)
            if conversation is None:
                await classify_missing(conn, "support_conversations", conversation_id)
            if str(conversation["user_id"]) != str(actor.user_id):
                raise SupportForbidden("This conversation belongs to someone else.", code="not_conversation_owner")
            if conversation["ticket_id"] is not None:
                raise SupportConflict("A ticket was already opened from this conversation.",
                                      code="conversation_already_escalated")
            diagnostics = conversations.diagnostics_snapshot(
                conversation, await conversations.fetch_messages(conn, conversation_id, tenant_id))
            findings = tuple(conversations.finding_from_json(f) for f in diagnostics["findings"])
            account = diagnostics.get("account")

        priority, priority_reason = compute_priority(
            category=category, text=f"{subject}\n{description}", findings=findings, account=account)
        diagnostics["priority_reason"] = priority_reason
        first_due, resolution_due = deadlines(priority, now, settings)
        ticket_id = await store.insert_ticket(
            conn, tenant_id=tenant_id, requester_id=actor.user_id, conversation_id=conversation_id,
            subject=subject, category=category, priority=priority, first_due=first_due,
            resolution_due=resolution_due, related_campaign_id=related_campaign_id,
            related_call_id=related_call_id, diagnostics=diagnostics, request_id=request_id, now=now)
        message = await store.insert_message(
            conn, ticket_id=ticket_id, tenant_id=tenant_id, author_id=actor.user_id, author_type="customer",
            body=description, is_internal=False, request_id=request_id, now=now)
        await store.attach_files(conn, tenant_id=tenant_id, ticket_id=ticket_id, message_id=message["id"],
                                 attachment_ids=attachment_ids, uploader_id=actor.user_id, staff=False, now=now)
        await store.insert_event(conn, ticket_id=ticket_id, tenant_id=tenant_id, actor_id=actor.user_id,
                                 type="created", to_value=priority)
        await store.mark_read(conn, user_id=actor.user_id, ticket_id=ticket_id, tenant_id=tenant_id, now=now)
        if conversation_id is not None:
            await conversations.update_conversation(conn, conversation_id, tenant_id, now=now,
                                                    status="escalated", ticket_id=ticket_id, ended_at=now)
        ticket = await store.fetch_ticket(conn, ticket_id, tenant_id)
        body = await receipts.save(conn, tenant_id=tenant_id, request_id=request_id, actor_id=actor.user_id,
                                   operation="ticket.create", resource_id=ticket_id, status_code=201,
                                   response=await _ticket_view(conn, ticket, actor, now))
    return WriteResult(body, 201, effects={"ticket_id": ticket_id, "tenant_id": tenant_id, "priority": priority,
                                            "category": category, "from_conversation": conversation_id is not None})


async def get_ticket(pool, actor: SupportActor, ticket_id: UUID) -> dict:
    """Read a ticket (requester, or a team reader) and mark it read for the viewer."""
    tenant_id = actor.require_tenant()
    now = _now()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        ticket = await store.fetch_ticket(conn, ticket_id, tenant_id)
        if ticket is None:
            await classify_missing(conn, "support_tickets", ticket_id)
        if str(ticket.get("requester_user_id")) != str(actor.user_id) and not actor.can_read_team:
            raise SupportForbidden("This ticket belongs to a colleague.", code="not_requester")
        view = await _ticket_view(conn, ticket, actor, now)
        await store.mark_read(conn, user_id=actor.user_id, ticket_id=ticket_id, tenant_id=tenant_id, now=now)
    return view


async def list_tickets(pool, actor: SupportActor, *, scope: str, status: str | None, cursor: str | None) -> dict:
    tenant_id = actor.require_tenant()
    team = scope == "team"
    if team and not actor.can_read_team:
        raise SupportForbidden("Viewing the team's tickets needs the support:read_team permission.",
                               code="missing_permission")
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        rows = await store.list_customer_tickets(
            conn, tenant_id=tenant_id, user_id=actor.user_id, team=team, status=status,
            after=decode_cursor(cursor), limit=PAGE_SIZE + 1)
    return {"items": [projection.list_item(row) for row in rows[:PAGE_SIZE]],
            "next_cursor": next_cursor(rows, PAGE_SIZE)}


async def add_customer_message(pool, actor: SupportActor, ticket_id: UUID, *, request_id: UUID, body: str,
                               attachment_ids: Sequence[UUID]) -> WriteResult:
    tenant_id = actor.require_tenant()
    now = _now()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        replay = await receipts.claim(conn, tenant_id=tenant_id, request_id=request_id,
                                      actor_id=actor.user_id, operation="ticket.message")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        ticket = await _locked_ticket(conn, ticket_id, tenant_id)
        _require_requester(ticket, actor)
        new_status = lifecycle.after_customer_reply(ticket, now)
        message = await store.insert_message(
            conn, ticket_id=ticket_id, tenant_id=tenant_id, author_id=actor.user_id, author_type="customer",
            body=body, is_internal=False, request_id=request_id, now=now)
        await store.attach_files(conn, tenant_id=tenant_id, ticket_id=ticket_id, message_id=message["id"],
                                 attachment_ids=attachment_ids, uploader_id=actor.user_id, staff=False, now=now)
        changes: dict[str, Any] = {}
        if new_status != ticket["status"]:
            changes["status"] = new_status
            if ticket["status"] == "resolved":
                changes["resolved_at"] = None
            await store.insert_event(conn, ticket_id=ticket_id, tenant_id=tenant_id, actor_id=actor.user_id,
                                     type="status_changed", from_value=ticket["status"], to_value=new_status)
        await store.update_ticket(conn, ticket_id, tenant_id, now=now, **changes)
        await store.insert_event(conn, ticket_id=ticket_id, tenant_id=tenant_id, actor_id=actor.user_id,
                                 type="customer_replied")
        await store.mark_read(conn, user_id=actor.user_id, ticket_id=ticket_id, tenant_id=tenant_id, now=now)
        attachments = await store.fetch_attachments(conn, ticket_id, tenant_id, [message["id"]])
        message["author_name"] = ticket.get("requester_name")
        response = projection.customer_message(message, projection.attachments_by_message(attachments).get(message["id"], []))
        saved = await receipts.save(conn, tenant_id=tenant_id, request_id=request_id, actor_id=actor.user_id,
                                    operation="ticket.message", resource_id=message["id"], status_code=201,
                                    response=response)
    return WriteResult(saved, 201)


async def _customer_transition(pool, actor: SupportActor, ticket_id: UUID, *, request_id: UUID,
                               operation: str, decide) -> WriteResult:
    tenant_id = actor.require_tenant()
    now = _now()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        replay = await receipts.claim(conn, tenant_id=tenant_id, request_id=request_id,
                                      actor_id=actor.user_id, operation=operation)
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        ticket = await _locked_ticket(conn, ticket_id, tenant_id)
        _require_requester(ticket, actor)
        changes, event = decide(ticket, now)
        await store.update_ticket(conn, ticket_id, tenant_id, now=now, **changes)
        await store.insert_event(conn, ticket_id=ticket_id, tenant_id=tenant_id, actor_id=actor.user_id, **event)
        ticket = await store.fetch_ticket(conn, ticket_id, tenant_id)
        body = await receipts.save(conn, tenant_id=tenant_id, request_id=request_id, actor_id=actor.user_id,
                                   operation=operation, resource_id=ticket_id, status_code=200,
                                   response=await _ticket_view(conn, ticket, actor, now))
    return WriteResult(body, 200)


async def resolve_ticket(pool, actor: SupportActor, ticket_id: UUID, *, request_id: UUID) -> WriteResult:
    def decide(ticket, now):
        target = lifecycle.customer_resolve(ticket)
        return ({"status": target, "resolved_at": now},
                {"type": "status_changed", "from_value": ticket["status"], "to_value": target})
    return await _customer_transition(pool, actor, ticket_id, request_id=request_id,
                                      operation="ticket.resolve", decide=decide)


async def reopen_ticket(pool, actor: SupportActor, ticket_id: UUID, *, request_id: UUID) -> WriteResult:
    def decide(ticket, now):
        target = lifecycle.customer_reopen(ticket, now)
        return ({"status": target, "resolved_at": None, "closed_at": None},
                {"type": "status_changed", "from_value": ticket["status"], "to_value": target})
    return await _customer_transition(pool, actor, ticket_id, request_id=request_id,
                                      operation="ticket.reopen", decide=decide)


async def rate_ticket(pool, actor: SupportActor, ticket_id: UUID, *, request_id: UUID, score: int,
                      comment: str | None) -> WriteResult:
    def decide(ticket, now):
        if not lifecycle.can_rate(ticket):
            raise InvalidTransition("A ticket can be rated once, after it is resolved.")
        return ({"csat_score": score, "csat_comment": comment or None},
                {"type": "csat_submitted", "to_value": score})
    return await _customer_transition(pool, actor, ticket_id, request_id=request_id,
                                      operation="ticket.csat", decide=decide)


async def unread(pool, actor: SupportActor) -> dict:
    tenant_id = actor.require_tenant()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        ids = await store.unread_ticket_ids(conn, tenant_id=tenant_id, user_id=actor.user_id)
    return {"count": len(ids), "ticket_ids": ids}


async def existing_open_ticket(conn, *, tenant_id: str, user_id: str, category: str, now: datetime) -> dict | None:
    """The same person's open ticket in this category from the last 7 days."""
    row = await store.find_recent_open_ticket(conn, tenant_id=tenant_id, user_id=user_id, category=category,
                                              since=now - DUPLICATE_WINDOW)
    if row is None:
        return None
    return {"id": row["id"], "number": ticket_display_number(row["number"]), "subject": row["subject"],
            "status": row["status"]}


__all__ = ["WriteResult", "create_ticket", "get_ticket", "list_tickets", "add_customer_message",
           "resolve_ticket", "reopen_ticket", "rate_ticket", "unread", "existing_open_ticket"]

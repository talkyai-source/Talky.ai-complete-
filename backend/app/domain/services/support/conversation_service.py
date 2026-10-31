"""Support bot conversations: start, read, send a message, feedback, escalate.

A message is handled in three steps so no database transaction stays open
during the model call: (1) store the customer message and check the limits,
(2) run the bot, (3) store the reply, the hand-over decision and the receipt.
A retry after a crash between (1) and (3) resumes the stored message; a
duplicate that arrives while the first attempt is still running gets 409.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.support import conversation_store as store
from app.domain.services.support import escalation, projection, receipts
from app.domain.services.support.access import classify_missing
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.bot.diagnostics import ToolExecutor
from app.domain.services.support.bot.runner import run_turn
from app.domain.services.support.bot.types import BotRequest, BotTurn, Source, ToolTrace
from app.domain.services.support.config import SupportSettings
from app.domain.services.support.errors import (
    RequestIdConflict,
    RequestInProgress,
    SupportConflict,
    SupportForbidden,
)
from app.domain.services.support.ticket_service import WriteResult, existing_open_ticket
from app.domain.services.support.ticket_store import tenant_owns
from app.infrastructure.metrics import support_metrics as metrics

logger = logging.getLogger(__name__)

GREETING = ("Hi, I'm the Talk-Lee help assistant. Tell me what's going wrong and I'll check your account "
            "and the help articles. You can ask for a person at any time.")
SUGGESTIONS = ("My campaign isn't making calls", "I have a billing question",
               "My caller ID isn't verified", "A connector stopped working")
HANDOVER_TEXT = {
    "bot_unavailable": "I can't check this right now. A support person can help: review the ticket details "
                       "below and send it.",
    "limit_turns": "This conversation has reached its message limit. A support person can take it from here: "
                   "send the ticket below.",
    "limit_daily": "The help assistant has reached today's limit for your account. A support person can help "
                   "instead: send the ticket below.",
    "not_solved": "Sorry that didn't fix it. A support person can take over: review the ticket details below "
                  "and send it.",
}
DEFAULT_HANDOVER = ("This needs a support person. I've prepared a ticket with the details: review it and send it, "
                    "and the team will reply here and by email.")
ASK_MORE = ("Sorry that didn't help. Can you tell me more, such as the exact error message or which campaign "
            "or call is affected?")
RESUME_AFTER = timedelta(seconds=60)
HISTORY_LIMIT = 12

_ROUTE = re.compile(r"^/[A-Za-z0-9/_\-\[\].]{0,200}$")
_VERSION = re.compile(r"^[A-Za-z0-9._+\-]{1,40}$")
_VIEWPORT = re.compile(r"^\d{2,5}x\d{2,5}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean_page_context(raw: dict | None) -> dict:
    """Keep only well-formed fields. Ids are checked against the tenant later."""
    raw = raw or {}
    context: dict[str, Any] = {}
    if isinstance(raw.get("route"), str) and _ROUTE.match(raw["route"]):
        context["route"] = raw["route"]
    for key in ("campaign_id", "call_id"):
        try:
            if raw.get(key):
                context[key] = str(UUID(str(raw[key])))
        except ValueError:
            pass
    if isinstance(raw.get("app_version"), str) and _VERSION.match(raw["app_version"]):
        context["app_version"] = raw["app_version"]
    if isinstance(raw.get("viewport"), str) and _VIEWPORT.match(raw["viewport"]):
        context["viewport"] = raw["viewport"]
    return context


async def _drop_foreign_ids(conn, context: dict, tenant_id: str) -> dict:
    """Page context is a hint, not an access request: ids the tenant doesn't own are dropped."""
    for key, table in (("campaign_id", "campaigns"), ("call_id", "calls")):
        if key in context and not await tenant_owns(conn, table, UUID(context[key]), tenant_id):
            context.pop(key)
    return context


async def _owned_conversation(conn, conversation_id: UUID, actor: SupportActor, *, for_update: bool) -> dict:
    conversation = await store.fetch_conversation(conn, conversation_id, actor.tenant_id, for_update=for_update)
    if conversation is None:
        await classify_missing(conn, "support_conversations", conversation_id)
    if str(conversation["user_id"]) != str(actor.user_id):
        raise SupportForbidden("This conversation belongs to someone else.", code="not_conversation_owner")
    return conversation


def _require_active(conversation: dict) -> None:
    if conversation["ticket_id"] is not None or conversation["status"] != "active":
        raise SupportConflict("This conversation has ended. Start a new one.", code="conversation_closed")


def turn_from_message(message: dict) -> BotTurn:
    """Rebuild what a stored assistant reply checked and found, for the ticket draft."""
    diagnostics = message.get("diagnostics") or {}
    return BotTurn(
        answer=message["content"],
        sources=tuple(Source(s["slug"], s["title"], s["url"]) for s in message.get("sources") or []),
        tool_calls=tuple(ToolTrace(t.get("name", ""), t.get("summary", "")) for t in message.get("tool_calls") or []),
        findings=tuple(store.finding_from_json(f) for f in diagnostics.get("findings") or []),
        category=diagnostics.get("category") or "other",
        account=diagnostics.get("account"),
        error=diagnostics.get("error"),
    )


async def _build_escalation(conn, conversation: dict, actor: SupportActor, reason: str, now: datetime) -> dict:
    messages = await store.fetch_messages(conn, conversation["id"], actor.tenant_id)
    turns = [turn_from_message(m) for m in messages if m["role"] == "assistant" and m.get("in_reply_to")]
    account = next((t.account for t in reversed(turns) if t.account), None)
    result = escalation.build_escalation(
        reason=reason, user_messages=[m["content"] for m in messages if m["role"] == "user"], turns=turns,
        page_context=conversation.get("page_context") or {}, account=account, category_hint=None)
    result["existing_ticket"] = await existing_open_ticket(
        conn, tenant_id=actor.tenant_id, user_id=actor.user_id, category=result["draft"]["category"], now=now)
    return result


async def _offer_escalation(conn, conversation: dict, actor: SupportActor, reason: str, now: datetime) -> dict:
    offer = await _build_escalation(conn, conversation, actor, reason, now)
    await store.update_conversation(conn, conversation["id"], actor.tenant_id, now=now, escalation=offer)
    metrics.escalation(reason)
    return offer


async def start_conversation(pool, actor: SupportActor, *, request_id: UUID, page_context: dict | None) -> WriteResult:
    tenant_id = actor.require_tenant()
    now = _now()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        replay = await receipts.claim(conn, tenant_id=tenant_id, request_id=request_id,
                                      actor_id=actor.user_id, operation="conversation.start")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        context = await _drop_foreign_ids(conn, clean_page_context(page_context), tenant_id)
        conversation = await store.insert_conversation(conn, tenant_id=tenant_id, user_id=actor.user_id,
                                                       page_context=context, request_id=request_id, now=now)
        await store.insert_message(conn, conversation_id=conversation["id"], tenant_id=tenant_id,
                                   role="assistant", content=GREETING, now=now)
        body = await receipts.save(conn, tenant_id=tenant_id, request_id=request_id, actor_id=actor.user_id,
                                   operation="conversation.start", resource_id=conversation["id"], status_code=201,
                                   response={"conversation_id": conversation["id"], "greeting": GREETING,
                                             "suggestions": list(SUGGESTIONS)})
    return WriteResult(body, 201)


async def get_conversation(pool, actor: SupportActor, conversation_id: UUID) -> dict:
    tenant_id = actor.require_tenant()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        conversation = await _owned_conversation(conn, conversation_id, actor, for_update=False)
        messages = await store.fetch_messages(conn, conversation_id, tenant_id)
    return {"conversation_id": conversation["id"], "status": conversation["status"],
            "messages": [projection.bot_message(m) for m in messages],
            "escalation": conversation.get("escalation")}


def _reply_diagnostics(turn: BotTurn) -> dict:
    return {"findings": [store.finding_to_json(f) for f in turn.findings], "account": turn.account,
            "category": turn.category, "error": turn.error, "resolution_prompt": turn.resolution_prompt,
            "needs_clarification": turn.needs_clarification}


async def _finish(conn, *, actor: SupportActor, conversation: dict, user_message: dict, request_id: UUID,
                  content: str, turn: BotTurn | None, handover: str | None, now: datetime) -> dict:
    """Store the reply (bot answer or fixed hand-over text), the decision and the receipt."""
    offer = None
    if handover:
        offer = await _offer_escalation(conn, conversation, actor, handover, now)
    reply = await store.insert_message(
        conn, conversation_id=conversation["id"], tenant_id=actor.tenant_id, role="assistant", content=content,
        now=now, sources=[{"slug": s.slug, "title": s.title, "url": s.url} for s in (turn.sources if turn else ())],
        tool_calls=[{"name": t.name, "summary": t.summary} for t in (turn.tool_calls if turn else ())],
        diagnostics=_reply_diagnostics(turn) if turn else {}, model=turn.model if turn else None,
        latency_ms=turn.latency_ms if turn else None, in_reply_to=user_message["id"])
    if not handover:
        await store.update_conversation(conn, conversation["id"], actor.tenant_id, now=now)
    response = {"message": projection.bot_message(reply),
                "resolution_prompt": bool(turn and turn.resolution_prompt and not offer),
                "escalation": offer}
    return await receipts.save(conn, tenant_id=actor.tenant_id, request_id=request_id, actor_id=actor.user_id,
                               operation="conversation.message", resource_id=reply["id"], status_code=200,
                               response=response)


async def post_message(pool, redis, actor: SupportActor, conversation_id: UUID, *, request_id: UUID,
                       content: str, settings: SupportSettings,
                       client_factory: Callable | None = None) -> WriteResult:
    tenant_id = actor.require_tenant()
    now = _now()
    # Step 1: store the customer's message, apply limits and the always-a-person topics.
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        replay = await receipts.claim(conn, tenant_id=tenant_id, request_id=request_id,
                                      actor_id=actor.user_id, operation="conversation.message")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        conversation = await _owned_conversation(conn, conversation_id, actor, for_update=True)
        user_message = await store.find_user_message(conn, tenant_id=tenant_id, request_id=request_id)
        if user_message is not None:
            if user_message["conversation_id"] != conversation_id or user_message["content"] != content:
                raise RequestIdConflict()
            if now - user_message["created_at"] < RESUME_AFTER:
                raise RequestInProgress()
            history_cutoff = user_message["created_at"]
        else:
            _require_active(conversation)
            history_cutoff = now
            limit = None
            if await store.count_user_turns(conn, conversation_id, tenant_id) >= settings.max_turns:
                limit = "limit_turns"
            else:
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if await store.count_tenant_messages_since(conn, tenant_id, day_start) \
                        >= settings.daily_message_cap_per_tenant:
                    limit = "limit_daily"
            user_message = await store.insert_message(conn, conversation_id=conversation_id, tenant_id=tenant_id,
                                                      role="user", content=content, request_id=request_id, now=now)
            topic = escalation.reason_for_text(content)
            if limit or topic or not settings.bot_enabled:
                reason = "limit_reached" if limit else (topic or "bot_unavailable")
                text = HANDOVER_TEXT.get(limit or "", DEFAULT_HANDOVER) if (limit or topic) \
                    else HANDOVER_TEXT["bot_unavailable"]
                body = await _finish(conn, actor=actor, conversation=conversation, user_message=user_message,
                                     request_id=request_id, content=text, turn=None, handover=reason, now=now)
                return WriteResult(body, 200)
        history = tuple((m["role"], m["content"]) for m in await store.fetch_messages(conn, conversation_id, tenant_id)
                        if m["id"] != user_message["id"] and m["created_at"] <= history_cutoff)[-HISTORY_LIMIT:]
        page_context = conversation.get("page_context") or {}

    # Step 2: the model call, outside any transaction.
    request = BotRequest(tenant_id=tenant_id, user_id=actor.user_id, user_text=content, history=history,
                         page_context=page_context, model=settings.bot_model)
    executor = ToolExecutor(pool=pool, redis=redis, tenant_id=tenant_id, user_id=actor.user_id)
    kwargs = {"client_factory": client_factory} if client_factory else {}
    turn = await run_turn(request, executor=executor, **kwargs)
    metrics.bot_turn()
    if turn.error:
        metrics.bot_error(turn.error)
        logger.warning("support_bot_turn_failed kind=%s latency_ms=%s", turn.error, turn.latency_ms)

    # Step 3: store the answer and decide whether to hand over.
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        replay = await receipts.claim(conn, tenant_id=tenant_id, request_id=request_id,
                                      actor_id=actor.user_id, operation="conversation.message")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        conversation = await _owned_conversation(conn, conversation_id, actor, for_update=True)
        handover = escalation.reason_for_turn(turn)
        text = HANDOVER_TEXT["bot_unavailable"] if turn.error else turn.answer
        body = await _finish(conn, actor=actor, conversation=conversation, user_message=user_message,
                             request_id=request_id, content=text[:4000], turn=turn, handover=handover, now=_now())
    return WriteResult(body, 200)


async def feedback(pool, actor: SupportActor, conversation_id: UUID, *, request_id: UUID, solved: bool) -> WriteResult:
    tenant_id = actor.require_tenant()
    now = _now()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        replay = await receipts.claim(conn, tenant_id=tenant_id, request_id=request_id,
                                      actor_id=actor.user_id, operation="conversation.feedback")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        conversation = await _owned_conversation(conn, conversation_id, actor, for_update=True)
        _require_active(conversation)
        message, offer, status = None, None, "active"
        if solved:
            status = "resolved_by_bot"
            await store.update_conversation(conn, conversation_id, tenant_id, now=now, status=status, ended_at=now)
        else:
            count = conversation["not_solved_count"] + 1
            await store.update_conversation(conn, conversation_id, tenant_id, now=now, not_solved_count=count)
            reason = escalation.reason_for_feedback(solved=False, not_solved_count=count)
            if reason:
                offer = await _offer_escalation(conn, conversation, actor, reason, now)
            row = await store.insert_message(conn, conversation_id=conversation_id, tenant_id=tenant_id,
                                             role="assistant", content=HANDOVER_TEXT["not_solved"] if offer
                                             else ASK_MORE, now=now)
            message = projection.bot_message(row)
        body = await receipts.save(conn, tenant_id=tenant_id, request_id=request_id, actor_id=actor.user_id,
                                   operation="conversation.feedback", resource_id=conversation_id, status_code=200,
                                   response={"status": status, "message": message, "escalation": offer})
    if solved:
        metrics.bot_resolved()
    return WriteResult(body, 200)


async def escalate(pool, actor: SupportActor, conversation_id: UUID, *, request_id: UUID) -> WriteResult:
    """The customer asks for a person: always allowed until a ticket exists."""
    tenant_id = actor.require_tenant()
    now = _now()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        replay = await receipts.claim(conn, tenant_id=tenant_id, request_id=request_id,
                                      actor_id=actor.user_id, operation="conversation.escalate")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        conversation = await _owned_conversation(conn, conversation_id, actor, for_update=True)
        if conversation["ticket_id"] is not None:
            raise SupportConflict("A ticket was already opened from this conversation.",
                                  code="conversation_already_escalated")
        offer = await _offer_escalation(conn, conversation, actor, "customer_requested", now)
        body = await receipts.save(conn, tenant_id=tenant_id, request_id=request_id, actor_id=actor.user_id,
                                   operation="conversation.escalate", resource_id=conversation_id, status_code=200,
                                   response={"escalation": offer})
    return WriteResult(body, 200)

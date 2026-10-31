"""Bot conversation SQL. Every statement names ``tenant_id`` explicitly."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.services.support.bot.types import Finding
from app.domain.services.support.receipts import to_json

CONVERSATION_COLUMNS = """id, tenant_id, user_id, status, page_context, not_solved_count, escalation,
    ticket_id, created_at, updated_at, ended_at"""
MESSAGE_COLUMNS = """id, conversation_id, role, content, sources, tool_calls, diagnostics, model,
    latency_ms, in_reply_to, request_id, created_at"""

_UPDATABLE = frozenset({"status", "not_solved_count", "escalation", "ticket_id", "ended_at"})
_JSON_COLUMNS = ("page_context", "escalation", "sources", "tool_calls", "diagnostics")


def _decode(row) -> dict | None:
    if row is None:
        return None
    record = dict(row)
    for name in _JSON_COLUMNS:
        if isinstance(record.get(name), str):
            record[name] = json.loads(record[name])
    return record


async def insert_conversation(conn, *, tenant_id: str, user_id: str, page_context: dict,
                              request_id: UUID, now: datetime) -> dict:
    row = await conn.fetchrow(
        f"""INSERT INTO support_conversations (tenant_id, user_id, page_context, request_id, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, $4, $5, $5) RETURNING {CONVERSATION_COLUMNS}""",
        tenant_id, user_id, json.dumps(to_json(page_context)), request_id, now,
    )
    return _decode(row)


async def fetch_conversation(conn, conversation_id: UUID, tenant_id: str, *, for_update: bool = False) -> dict | None:
    lock = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        f"SELECT {CONVERSATION_COLUMNS} FROM support_conversations WHERE id = $1 AND tenant_id = $2{lock}",
        conversation_id, tenant_id,
    )
    return _decode(row)


async def update_conversation(conn, conversation_id: UUID, tenant_id: str, *, now: datetime, **fields: Any) -> None:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    values = [json.dumps(to_json(value)) if name == "escalation" and value is not None else value
              for name, value in fields.items()]
    assignments = [f"{name} = ${index + 3}" + ("::jsonb" if name == "escalation" else "")
                   for index, name in enumerate(fields)]
    assignments.append(f"updated_at = ${len(fields) + 3}")
    await conn.execute(
        f"UPDATE support_conversations SET {', '.join(assignments)} WHERE id = $1 AND tenant_id = $2",
        conversation_id, tenant_id, *values, now,
    )


async def insert_message(conn, *, conversation_id: UUID, tenant_id: str, role: str, content: str,
                         now: datetime, sources: list | None = None, tool_calls: list | None = None,
                         diagnostics: dict | None = None, model: str | None = None,
                         latency_ms: int | None = None, in_reply_to: UUID | None = None,
                         request_id: UUID | None = None) -> dict:
    row = await conn.fetchrow(
        f"""INSERT INTO support_conversation_messages
               (conversation_id, tenant_id, role, content, sources, tool_calls, diagnostics, model,
                latency_ms, in_reply_to, request_id, created_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8, $9, $10, $11, $12)
            RETURNING {MESSAGE_COLUMNS}""",
        conversation_id, tenant_id, role, content, json.dumps(to_json(sources or [])),
        json.dumps(to_json(tool_calls or [])), json.dumps(to_json(diagnostics or {})), model, latency_ms,
        in_reply_to, request_id, now,
    )
    return _decode(row)


async def fetch_messages(conn, conversation_id: UUID, tenant_id: str) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT {MESSAGE_COLUMNS} FROM support_conversation_messages
            WHERE conversation_id = $1 AND tenant_id = $2 ORDER BY created_at, id""",
        conversation_id, tenant_id,
    )
    return [_decode(row) for row in rows]


async def find_user_message(conn, *, tenant_id: str, request_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        f"""SELECT {MESSAGE_COLUMNS} FROM support_conversation_messages
            WHERE tenant_id = $1 AND request_id = $2 AND role = 'user'""",
        tenant_id, request_id,
    )
    return _decode(row)


async def find_reply(conn, *, tenant_id: str, user_message_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT {MESSAGE_COLUMNS} FROM support_conversation_messages WHERE tenant_id = $1 AND in_reply_to = $2",
        tenant_id, user_message_id,
    )
    return _decode(row)


async def count_user_turns(conn, conversation_id: UUID, tenant_id: str) -> int:
    return await conn.fetchval(
        """SELECT count(*) FROM support_conversation_messages
           WHERE conversation_id = $1 AND tenant_id = $2 AND role = 'user'""",
        conversation_id, tenant_id,
    )


async def count_tenant_messages_since(conn, tenant_id: str, since: datetime) -> int:
    return await conn.fetchval(
        """SELECT count(*) FROM support_conversation_messages
           WHERE tenant_id = $1 AND role = 'user' AND created_at >= $2""",
        tenant_id, since,
    )


def finding_to_json(finding: Finding) -> dict:
    return {"code": finding.code, "title": finding.title, "detail": finding.detail,
            "staff_only": finding.staff_only, "account_wide_block": finding.account_wide_block,
            "related_campaign_id": finding.related_campaign_id, "related_call_id": finding.related_call_id}


def finding_from_json(data: dict) -> Finding:
    return Finding(code=str(data.get("code", "")), title=str(data.get("title", "")),
                   detail=str(data.get("detail", "")), staff_only=bool(data.get("staff_only")),
                   account_wide_block=bool(data.get("account_wide_block")),
                   related_campaign_id=data.get("related_campaign_id"),
                   related_call_id=data.get("related_call_id"))


def diagnostics_snapshot(conversation: dict, messages: list[dict]) -> dict:
    """What the bot checked and found, frozen onto a ticket made from the chat."""
    checks: list[dict] = []
    findings: list[dict] = []
    account = None
    for message in messages:
        if message["role"] != "assistant":
            continue
        for trace in message.get("tool_calls") or []:
            if trace not in checks:
                checks.append(trace)
        diagnostics = message.get("diagnostics") or {}
        for finding in diagnostics.get("findings") or []:
            if finding not in findings:
                findings.append(finding)
        account = diagnostics.get("account") or account
    escalation = conversation.get("escalation") or {}
    page = conversation.get("page_context") or {}
    return {"source": "support_bot", "checks": checks[:20], "findings": findings[:20], "account": account,
            "page_route": page.get("route"), "escalation_reason": escalation.get("reason")}

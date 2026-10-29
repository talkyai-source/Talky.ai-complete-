"""Idempotent writes: one stored response per (tenant, request_id).

Call ``claim`` first inside the write transaction. It takes a transaction
advisory lock on the request identity, so a concurrent duplicate waits and
then sees the committed receipt. Reusing a request_id for a different action
or by a different person is refused (409), never answered with someone
else's result.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.services.support.errors import RequestIdConflict


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def to_json(value: Any) -> Any:
    """The exact JSON shape the client receives, first time and on replay."""
    return json.loads(json.dumps(value, default=_default))


async def claim(conn, *, tenant_id: str | None, request_id: UUID, actor_id: str, operation: str) -> dict | None:
    """Lock the request identity; return the stored receipt when it already ran."""
    if not conn.is_in_transaction():
        raise RuntimeError("Receipts require an open transaction")
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        f"support-request:{tenant_id or '-'}:{request_id}",
    )
    row = await conn.fetchrow(
        """SELECT actor_user_id, operation, status_code, response
           FROM support_request_receipts
           WHERE request_id = $2 AND tenant_id IS NOT DISTINCT FROM $1::uuid""",
        tenant_id, request_id,
    )
    if row is None:
        return None
    if str(row["actor_user_id"]) != str(actor_id) or row["operation"] != operation:
        raise RequestIdConflict()
    response = row["response"]
    return {"status_code": row["status_code"],
            "response": json.loads(response) if isinstance(response, str) else response}


async def save(conn, *, tenant_id: str | None, request_id: UUID, actor_id: str, operation: str,
               resource_id: Any, status_code: int, response: dict) -> dict:
    """Store the response in the caller's transaction and return it as sent."""
    body = to_json(response)
    await conn.execute(
        """INSERT INTO support_request_receipts
               (tenant_id, request_id, actor_user_id, operation, resource_id, status_code, response)
           VALUES ($1::uuid, $2, $3::uuid, $4, $5::uuid, $6, $7::jsonb)""",
        tenant_id, request_id, str(actor_id), operation,
        str(resource_id) if resource_id is not None else None, status_code, json.dumps(body),
    )
    return body

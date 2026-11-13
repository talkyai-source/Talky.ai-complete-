"""Idempotent writes: one stored response per (tenant, request_id).

Call ``claim`` first inside the write transaction. It takes a transaction
advisory lock on the request identity, so a concurrent duplicate waits and
then sees the committed receipt. Reusing a request_id for a different action
or by a different person is refused (409), never answered with someone
else's result.

A write that passes ``fingerprint`` (see ``body_fingerprint``) also has its
content checked: the same request_id with other content is refused (409)
instead of replaying the first response and silently dropping the new content.
The fingerprint is kept inside the stored response JSON under a reserved key
(the table has no column for it) and is never returned to the client. A
receipt stored without one still replays whatever the content.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.services.support.errors import RequestIdConflict

_FINGERPRINT_KEY = "_request_fingerprint"


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


def body_fingerprint(**content: Any) -> str:
    """SHA-256 of a request's content (everything but its request_id).

    Same recipe as ``ContactEnquiryInput.payload_hash``: canonical JSON, sorted
    keys, then SHA-256. Ascii-escaped so no input text can fail to encode.
    """
    canonical = json.dumps(content, default=_default, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


async def claim(conn, *, tenant_id: str | None, request_id: UUID, actor_id: str, operation: str,
                fingerprint: str | None = None) -> dict | None:
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
    stored = json.loads(response) if isinstance(response, str) else response
    stored_fingerprint = stored.get(_FINGERPRINT_KEY)
    if fingerprint is not None and stored_fingerprint is not None and stored_fingerprint != fingerprint:
        raise RequestIdConflict("This request_id was already used with different content. Generate a new one.")
    return {"status_code": row["status_code"],
            "response": {key: value for key, value in stored.items() if key != _FINGERPRINT_KEY}}


async def save(conn, *, tenant_id: str | None, request_id: UUID, actor_id: str, operation: str,
               resource_id: Any, status_code: int, response: dict, fingerprint: str | None = None) -> dict:
    """Store the response in the caller's transaction and return it as sent."""
    body = to_json(response)
    stored = {**body, _FINGERPRINT_KEY: fingerprint} if fingerprint is not None else body
    await conn.execute(
        """INSERT INTO support_request_receipts
               (tenant_id, request_id, actor_user_id, operation, resource_id, status_code, response)
           VALUES ($1::uuid, $2, $3::uuid, $4, $5::uuid, $6, $7::jsonb)""",
        tenant_id, request_id, str(actor_id), operation,
        str(resource_id) if resource_id is not None else None, status_code, json.dumps(stored),
    )
    return body

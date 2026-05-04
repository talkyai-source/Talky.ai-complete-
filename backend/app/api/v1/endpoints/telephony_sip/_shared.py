"""Cross-cutting infrastructure for /telephony/sip endpoints.

Lives here:
  - RFC 9457 problem-response builder
  - tenant gate
  - request canonicalisation helpers (`_canonical_domain`, `_stable_hash`)
  - idempotency-key claim/store helpers (`tenant_telephony_idempotency` table)
  - rate-limiter wiring (`_enforce_ws_i_quota`)

Resource-specific helpers (row-to-response, fetch-by-id, set-active-state)
live in the resource module (trunks.py / codec_policies.py / route_policies.py)
to keep this file narrow.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional
from uuid import UUID

import asyncpg
from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import CurrentUser
from app.core.container import get_container
from app.domain.services.telephony_rate_limiter import (
    RateLimitAction,
    TelephonyRateLimiter,
)

PROBLEM_BASE = "https://talky.ai/problems"
IDEMPOTENCY_WINDOW_SECONDS = 24 * 60 * 60


def _problem(
    request: Request,
    status_code: int,
    title: str,
    detail: str,
    type_suffix: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"{PROBLEM_BASE}/{type_suffix}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": str(request.url.path),
        },
    )


def _canonical_domain(domain: str) -> str:
    return domain.strip().lower()


def _stable_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_tenant(request: Request, current_user: CurrentUser) -> Optional[JSONResponse]:
    if current_user.tenant_id:
        return None
    return _problem(
        request=request,
        status_code=403,
        title="Tenant Context Required",
        detail="Authenticated user is not associated with a tenant.",
        type_suffix="tenant-context-required",
    )


async def _claim_idempotency(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    operation: str,
    idempotency_key: str,
    request_hash: str,
) -> tuple[str, Optional[Dict[str, Any]], Optional[int]]:
    inserted = await conn.fetchrow(
        """
        INSERT INTO tenant_telephony_idempotency (
            tenant_id,
            operation,
            idempotency_key,
            request_hash,
            expires_at
        )
        VALUES ($1, $2, $3, $4, NOW() + ($5::int * INTERVAL '1 second'))
        ON CONFLICT (tenant_id, operation, idempotency_key) DO NOTHING
        RETURNING id
        """,
        tenant_id,
        operation,
        idempotency_key,
        request_hash,
        IDEMPOTENCY_WINDOW_SECONDS,
    )
    if inserted:
        return "new", None, None

    existing = await conn.fetchrow(
        """
        SELECT request_hash, response_body, status_code
        FROM tenant_telephony_idempotency
        WHERE tenant_id = $1
          AND operation = $2
          AND idempotency_key = $3
        """,
        tenant_id,
        operation,
        idempotency_key,
    )
    if not existing:
        return "new", None, None

    if existing["request_hash"] != request_hash:
        return "hash_mismatch", None, None

    response_body = existing["response_body"]
    status_code = existing["status_code"]
    if response_body is not None and status_code is not None:
        return "replay", response_body, int(status_code)
    return "in_progress", None, None


async def _store_idempotency_result(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    operation: str,
    idempotency_key: str,
    response_body: Dict[str, Any],
    status_code: int,
    resource_type: str,
    resource_id: Optional[UUID],
) -> None:
    await conn.execute(
        """
        UPDATE tenant_telephony_idempotency
        SET response_body = $4::jsonb,
            status_code = $5,
            resource_type = $6,
            resource_id = $7
        WHERE tenant_id = $1
          AND operation = $2
          AND idempotency_key = $3
        """,
        tenant_id,
        operation,
        idempotency_key,
        json.dumps(response_body),
        status_code,
        resource_type,
        resource_id,
    )


async def _store_error_idempotency_result(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    operation: str,
    idempotency_key: str,
    response: JSONResponse,
) -> None:
    await _store_idempotency_result(
        conn,
        tenant_id=tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        response_body=json.loads(response.body.decode("utf-8")),
        status_code=response.status_code,
        resource_type="telephony_rate_limit_error",
        resource_id=None,
    )


def _get_rate_limiter() -> TelephonyRateLimiter:
    try:
        container = get_container()
        redis_client = container.redis if container.is_initialized else None
    except Exception:
        redis_client = None
    return TelephonyRateLimiter(redis_client=redis_client)


async def _enforce_ws_i_quota(
    *,
    conn: asyncpg.Connection,
    request: Request,
    tenant_id: str,
    user_id: str,
    policy_scope: str,
    metric_key: str,
    request_id: Optional[str],
) -> Optional[JSONResponse]:
    limiter = _get_rate_limiter()
    decision = await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_id,
        policy_scope=policy_scope,
        metric_key=metric_key,
        request_id=request_id,
        created_by=user_id,
        details={"path": str(request.url.path), "method": request.method},
    )
    if decision.action in {RateLimitAction.ALLOW, RateLimitAction.WARN}:
        return None

    retry_after = (
        decision.block_ttl_seconds
        if decision.action == RateLimitAction.BLOCK
        else max(decision.policy.throttle_retry_seconds, 1)
    )
    title = "Temporarily Blocked" if decision.action == RateLimitAction.BLOCK else "Rate Limited"
    detail = (
        "Tenant mutation policy is temporarily blocked due to abuse threshold."
        if decision.action == RateLimitAction.BLOCK
        else "Tenant mutation policy exceeded soft throttle threshold."
    )
    response = _problem(
        request=request,
        status_code=429,
        title=title,
        detail=detail,
        type_suffix="telephony-rate-limited",
    )
    response.headers["Retry-After"] = str(retry_after)
    return response

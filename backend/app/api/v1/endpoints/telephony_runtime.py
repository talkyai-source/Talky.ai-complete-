"""
WS-G runtime policy endpoints.

Implements:
- deterministic compile preview
- activate flow: precheck -> apply -> verify -> commit
- rollback to prior version
- version history listing
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.container import get_container
from app.core.tenant_rls import apply_tenant_rls_context
from app.domain.services.telephony_rate_limiter import (
    RateLimitAction,
    TelephonyRateLimiter,
)
from app.domain.services.telephony_runtime_policy import (
    PolicyCompilationError,
    compile_tenant_runtime_policy,
)
from app.infrastructure.telephony.runtime_policy_adapter import (
    RuntimeCommandError,
    RuntimePolicyAdapter,
)

router = APIRouter(prefix="/telephony/sip/runtime", tags=["Telephony SIP Runtime"])

PROBLEM_BASE = "https://talky.ai/problems"
IDEMPOTENCY_WINDOW_SECONDS = 24 * 60 * 60


class RuntimeActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Optional[str] = Field(default=None, max_length=500)


class RuntimeRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_version: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=500)


class RuntimeCompilePreviewResponse(BaseModel):
    schema_version: str
    source_hash: str
    active_trunks: int
    active_codecs: int
    active_routes: int
    active_trust_policies: int
    artifact: Dict[str, Any]


class RuntimeActivationResponse(BaseModel):
    policy_version: int
    source_hash: str
    build_status: str
    apply_result: Dict[str, Any]
    verify_result: Dict[str, Any]


class RuntimeRollbackResponse(BaseModel):
    from_version: int
    to_version: int
    status: str
    apply_result: Dict[str, Any]
    verify_result: Dict[str, Any]


class RuntimeVersionResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    policy_version: int
    source_hash: str
    schema_version: str
    build_status: str
    is_active: bool
    is_last_good: bool
    validation_report: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    activated_at: Optional[datetime]


class RuntimeActivationMetricsResponse(BaseModel):
    tenant_id: UUID
    window_hours: int
    generated_at: datetime
    activation_success_count: int
    activation_failure_count: int
    activation_success_rate_pct: float
    rollback_success_count: int
    rollback_failure_count: int
    rollback_latency_p50_ms: float
    rollback_latency_p95_ms: float
    rollback_latency_max_ms: float


def get_runtime_policy_adapter() -> RuntimePolicyAdapter:
    return RuntimePolicyAdapter()


def _get_rate_limiter() -> TelephonyRateLimiter:
    try:
        container = get_container()
        redis_client = container.redis if container.is_initialized else None
    except Exception:
        redis_client = None
    return TelephonyRateLimiter(redis_client=redis_client)


def _problem(
    request: Request,
    *,
    status_code: int,
    title: str,
    detail: str,
    type_suffix: str,
    extras: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    payload: Dict[str, Any] = {
        "type": f"{PROBLEM_BASE}/{type_suffix}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": str(request.url.path),
    }
    if extras:
        payload.update(extras)
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content=payload,
    )


def _stable_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_tenant(request: Request, current_user: CurrentUser) -> Optional[JSONResponse]:
    if current_user.tenant_id:
        return None
    return _problem(
        request,
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

    if existing["response_body"] is not None and existing["status_code"] is not None:
        return "replay", existing["response_body"], int(existing["status_code"])
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
        resource_type="runtime_policy_error",
        resource_id=None,
    )


async def _enforce_ws_i_quota(
    *,
    conn: asyncpg.Connection,
    request: Request,
    tenant_id: str,
    user_id: str,
    metric_key: str,
    request_id: Optional[str],
) -> Optional[JSONResponse]:
    limiter = _get_rate_limiter()
    decision = await limiter.evaluate(
        conn=conn,
        tenant_id=tenant_id,
        policy_scope="runtime_mutation",
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
        "Tenant runtime activation is temporarily blocked due to abuse threshold."
        if decision.action == RateLimitAction.BLOCK
        else "Tenant runtime activation exceeded soft throttle threshold."
    )
    response = _problem(
        request,
        status_code=429,
        title=title,
        detail=detail,
        type_suffix="telephony-rate-limited",
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


async def _load_active_snapshot(conn: asyncpg.Connection, tenant_id: str) -> Dict[str, List[Dict[str, Any]]]:
    trunks = await conn.fetch(
        """
        SELECT
            id,
            trunk_name,
            sip_domain,
            port,
            transport,
            direction,
            is_active,
            metadata
        FROM tenant_sip_trunks
        WHERE tenant_id = $1
          AND is_active = TRUE
        ORDER BY lower(trunk_name), id
        """,
        tenant_id,
    )
    codecs = await conn.fetch(
        """
        SELECT
            id,
            policy_name,
            allowed_codecs,
            preferred_codec,
            sample_rate_hz,
            ptime_ms,
            max_bitrate_kbps,
            jitter_buffer_ms,
            is_active,
            metadata
        FROM tenant_codec_policies
        WHERE tenant_id = $1
          AND is_active = TRUE
        ORDER BY lower(policy_name), id
        """,
        tenant_id,
    )
    routes = await conn.fetch(
        """
        SELECT
            id,
            policy_name,
            route_type,
            priority,
            match_pattern,
            target_trunk_id,
            codec_policy_id,
            strip_digits,
            prepend_digits,
            is_active,
            metadata
        FROM tenant_route_policies
        WHERE tenant_id = $1
          AND is_active = TRUE
        ORDER BY route_type, priority, lower(policy_name), id
        """,
        tenant_id,
    )
    trust_policies = await conn.fetch(
        """
        SELECT
            id,
            policy_name,
            allowed_source_cidrs,
            blocked_source_cidrs,
            kamailio_group,
            priority,
            is_active,
            metadata
        FROM tenant_sip_trust_policies
        WHERE tenant_id = $1
          AND is_active = TRUE
        ORDER BY priority, kamailio_group, lower(policy_name), id
        """,
        tenant_id,
    )
    return {
        "trunks": [dict(row) for row in trunks],
        "codecs": [dict(row) for row in codecs],
        "routes": [dict(row) for row in routes],
        "trust_policies": [dict(row) for row in trust_policies],
    }


async def _log_runtime_event(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    policy_version_id: UUID,
    action: str,
    stage: str,
    status: str,
    details: Dict[str, Any],
    request_id: Optional[str],
    created_by: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO tenant_runtime_policy_events (
            tenant_id,
            policy_version_id,
            action,
            stage,
            status,
            details,
            request_id,
            created_by
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
        """,
        tenant_id,
        policy_version_id,
        action,
        stage,
        status,
        json.dumps(details),
        request_id,
        created_by,
    )


@router.post("/compile/preview", response_model=RuntimeCompilePreviewResponse)
async def preview_runtime_policy(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        snapshot = await _load_active_snapshot(conn, current_user.tenant_id)

    try:
        compiled = compile_tenant_runtime_policy(
            tenant_id=current_user.tenant_id,
            trunks=snapshot["trunks"],
            codec_policies=snapshot["codecs"],
            route_policies=snapshot["routes"],
            trust_policies=snapshot["trust_policies"],
        )
    except PolicyCompilationError as exc:
        return _problem(
            request,
            status_code=422,
            title="Runtime Policy Validation Failed",
            detail="Active policy data failed compilation validation.",
            type_suffix="runtime-policy-validation-failed",
            extras={"errors": [issue.to_dict() for issue in exc.issues]},
        )

    return RuntimeCompilePreviewResponse(
        schema_version=compiled.schema_version,
        source_hash=compiled.source_hash,
        active_trunks=len(snapshot["trunks"]),
        active_codecs=len(snapshot["codecs"]),
        active_routes=len(snapshot["routes"]),
        active_trust_policies=len(snapshot["trust_policies"]),
        artifact=compiled.artifact,
    )


@router.post("/activate", response_model=RuntimeActivationResponse)
async def activate_runtime_policy(
    payload: RuntimeActivateRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    adapter: RuntimePolicyAdapter = Depends(get_runtime_policy_adapter),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    if not idempotency_key:
        return _problem(
            request,
            status_code=400,
            title="Idempotency Key Required",
            detail="Mutating operations require Idempotency-Key header.",
            type_suffix="idempotency-key-required",
        )

    operation = "runtime_policy:activate"
    request_hash = _stable_hash({"note": payload.note or ""})

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        async with conn.transaction():
            state, cached_body, cached_code = await _claim_idempotency(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if state == "hash_mismatch":
                return _problem(
                    request,
                    status_code=409,
                    title="Idempotency Conflict",
                    detail="Idempotency-Key was reused with a different payload.",
                    type_suffix="idempotency-conflict",
                )
            if state == "in_progress":
                return _problem(
                    request,
                    status_code=409,
                    title="Request In Progress",
                    detail="A request with this Idempotency-Key is still processing.",
                    type_suffix="idempotency-in-progress",
                )
            if state == "replay":
                return JSONResponse(status_code=cached_code or 200, content=cached_body)

            quota_problem = await _enforce_ws_i_quota(
                conn=conn,
                request=request,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                metric_key="runtime_policy:activate",
                request_id=x_request_id,
            )
            if quota_problem:
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=current_user.tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=quota_problem,
                )
                return quota_problem

            snapshot = await _load_active_snapshot(conn, current_user.tenant_id)
            try:
                compiled = compile_tenant_runtime_policy(
                    tenant_id=current_user.tenant_id,
                    trunks=snapshot["trunks"],
                    codec_policies=snapshot["codecs"],
                    route_policies=snapshot["routes"],
                    trust_policies=snapshot["trust_policies"],
                )
            except PolicyCompilationError as exc:
                response = _problem(
                    request,
                    status_code=422,
                    title="Runtime Policy Validation Failed",
                    detail="Invalid policy cannot be activated.",
                    type_suffix="runtime-policy-validation-failed",
                    extras={"errors": [issue.to_dict() for issue in exc.issues]},
                )
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=current_user.tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=response,
                )
                return response

            next_version = await conn.fetchval(
                """
                SELECT COALESCE(MAX(policy_version), 0) + 1
                FROM tenant_runtime_policy_versions
                WHERE tenant_id = $1
                """,
                current_user.tenant_id,
            )
            version_row = await conn.fetchrow(
                """
                INSERT INTO tenant_runtime_policy_versions (
                    tenant_id,
                    policy_version,
                    source_hash,
                    schema_version,
                    input_snapshot,
                    compiled_artifact,
                    validation_report,
                    build_status,
                    created_by
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, 'compiled', $8)
                RETURNING id, policy_version, source_hash
                """,
                current_user.tenant_id,
                next_version,
                compiled.source_hash,
                compiled.schema_version,
                json.dumps(compiled.input_snapshot),
                json.dumps(compiled.artifact),
                json.dumps({"issues": []}),
                current_user.id,
            )
            version_id = version_row["id"]

            await _log_runtime_event(
                conn,
                tenant_id=current_user.tenant_id,
                policy_version_id=version_id,
                action="activate",
                stage="precheck",
                status="succeeded",
                details={"source_hash": compiled.source_hash},
                request_id=x_request_id,
                created_by=current_user.id,
            )

    try:
        apply_result = await adapter.apply(compiled.artifact)
    except RuntimeCommandError as exc:
        response = _problem(
            request,
            status_code=502,
            title="Runtime Apply Failed",
            detail="Failed to apply compiled policy to runtime services.",
            type_suffix="runtime-apply-failed",
            extras={"runtime_error": exc.to_dict()},
        )
        async with db_pool.acquire() as conn:
            await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE tenant_runtime_policy_versions
                    SET build_status = 'failed',
                        validation_report = $3::jsonb
                    WHERE tenant_id = $1
                      AND id = $2
                    """,
                    current_user.tenant_id,
                    version_id,
                    json.dumps({"runtime_error": exc.to_dict()}),
                )
                await _log_runtime_event(
                    conn,
                    tenant_id=current_user.tenant_id,
                    policy_version_id=version_id,
                    action="activate",
                    stage="apply",
                    status="failed",
                    details=exc.to_dict(),
                    request_id=x_request_id,
                    created_by=current_user.id,
                )
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=current_user.tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=response,
                )
        return response

    try:
        verify_result = await adapter.verify(compiled.artifact)
    except RuntimeCommandError as exc:
        response = _problem(
            request,
            status_code=502,
            title="Runtime Verify Failed",
            detail="Runtime verification failed after apply.",
            type_suffix="runtime-verify-failed",
            extras={"runtime_error": exc.to_dict()},
        )
        async with db_pool.acquire() as conn:
            await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE tenant_runtime_policy_versions
                    SET build_status = 'failed',
                        validation_report = $3::jsonb
                    WHERE tenant_id = $1
                      AND id = $2
                    """,
                    current_user.tenant_id,
                    version_id,
                    json.dumps({"runtime_error": exc.to_dict()}),
                )
                await _log_runtime_event(
                    conn,
                    tenant_id=current_user.tenant_id,
                    policy_version_id=version_id,
                    action="activate",
                    stage="verify",
                    status="failed",
                    details=exc.to_dict(),
                    request_id=x_request_id,
                    created_by=current_user.id,
                )
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=current_user.tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=response,
                )
        return response

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        async with conn.transaction():
            await _log_runtime_event(
                conn,
                tenant_id=current_user.tenant_id,
                policy_version_id=version_id,
                action="activate",
                stage="apply",
                status="succeeded",
                details=apply_result,
                request_id=x_request_id,
                created_by=current_user.id,
            )
            await _log_runtime_event(
                conn,
                tenant_id=current_user.tenant_id,
                policy_version_id=version_id,
                action="activate",
                stage="verify",
                status="succeeded",
                details=verify_result,
                request_id=x_request_id,
                created_by=current_user.id,
            )

            await conn.execute(
                """
                UPDATE tenant_runtime_policy_versions
                SET is_active = FALSE,
                    is_last_good = FALSE,
                    build_status = CASE WHEN is_active THEN 'superseded' ELSE build_status END,
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND id <> $2
                  AND is_active = TRUE
                """,
                current_user.tenant_id,
                version_id,
            )
            await conn.execute(
                """
                UPDATE tenant_runtime_policy_versions
                SET build_status = 'active',
                    is_active = TRUE,
                    is_last_good = TRUE,
                    activated_at = NOW(),
                    activated_by = $3,
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND id = $2
                """,
                current_user.tenant_id,
                version_id,
                current_user.id,
            )
            await _log_runtime_event(
                conn,
                tenant_id=current_user.tenant_id,
                policy_version_id=version_id,
                action="activate",
                stage="commit",
                status="succeeded",
                details={"build_status": "active"},
                request_id=x_request_id,
                created_by=current_user.id,
            )

            response_body = RuntimeActivationResponse(
                policy_version=int(version_row["policy_version"]),
                source_hash=str(version_row["source_hash"]),
                build_status="active",
                apply_result=apply_result,
                verify_result=verify_result,
            ).model_dump()
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_body,
                status_code=200,
                resource_type="runtime_policy_version",
                resource_id=version_id,
            )
    return response_body


@router.post("/rollback", response_model=RuntimeRollbackResponse)
async def rollback_runtime_policy(
    payload: RuntimeRollbackRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    adapter: RuntimePolicyAdapter = Depends(get_runtime_policy_adapter),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    if not idempotency_key:
        return _problem(
            request,
            status_code=400,
            title="Idempotency Key Required",
            detail="Mutating operations require Idempotency-Key header.",
            type_suffix="idempotency-key-required",
        )

    operation = "runtime_policy:rollback"
    request_hash = _stable_hash(
        {"target_version": payload.target_version, "reason": payload.reason or ""}
    )

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        async with conn.transaction():
            state, cached_body, cached_code = await _claim_idempotency(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if state == "hash_mismatch":
                return _problem(
                    request,
                    status_code=409,
                    title="Idempotency Conflict",
                    detail="Idempotency-Key was reused with a different payload.",
                    type_suffix="idempotency-conflict",
                )
            if state == "in_progress":
                return _problem(
                    request,
                    status_code=409,
                    title="Request In Progress",
                    detail="A request with this Idempotency-Key is still processing.",
                    type_suffix="idempotency-in-progress",
                )
            if state == "replay":
                return JSONResponse(status_code=cached_code or 200, content=cached_body)

            quota_problem = await _enforce_ws_i_quota(
                conn=conn,
                request=request,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                metric_key="runtime_policy:rollback",
                request_id=x_request_id,
            )
            if quota_problem:
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=current_user.tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=quota_problem,
                )
                return quota_problem

            active_row = await conn.fetchrow(
                """
                SELECT id, policy_version, compiled_artifact
                FROM tenant_runtime_policy_versions
                WHERE tenant_id = $1
                  AND is_active = TRUE
                ORDER BY policy_version DESC
                LIMIT 1
                """,
                current_user.tenant_id,
            )
            if not active_row:
                response = _problem(
                    request,
                    status_code=409,
                    title="No Active Policy",
                    detail="No active runtime policy version to rollback.",
                    type_suffix="runtime-no-active-policy",
                )
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=current_user.tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=response,
                )
                return response

            if payload.target_version is not None:
                target_row = await conn.fetchrow(
                    """
                    SELECT id, policy_version, compiled_artifact
                    FROM tenant_runtime_policy_versions
                    WHERE tenant_id = $1
                      AND policy_version = $2
                    LIMIT 1
                    """,
                    current_user.tenant_id,
                    payload.target_version,
                )
            else:
                target_row = await conn.fetchrow(
                    """
                    SELECT id, policy_version, compiled_artifact
                    FROM tenant_runtime_policy_versions
                    WHERE tenant_id = $1
                      AND policy_version < $2
                      AND build_status IN ('active', 'superseded', 'rolled_back')
                    ORDER BY policy_version DESC
                    LIMIT 1
                    """,
                    current_user.tenant_id,
                    int(active_row["policy_version"]),
                )

            if not target_row:
                response = _problem(
                    request,
                    status_code=409,
                    title="Rollback Target Not Found",
                    detail="No eligible runtime policy version available for rollback.",
                    type_suffix="runtime-rollback-target-not-found",
                )
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=current_user.tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=response,
                )
                return response

            await _log_runtime_event(
                conn,
                tenant_id=current_user.tenant_id,
                policy_version_id=target_row["id"],
                action="rollback",
                stage="rollback",
                status="started",
                details={"target_version": int(target_row["policy_version"])},
                request_id=x_request_id,
                created_by=current_user.id,
            )

    try:
        apply_result = await adapter.apply(target_row["compiled_artifact"])
        verify_result = await adapter.verify(target_row["compiled_artifact"])
    except RuntimeCommandError as exc:
        response = _problem(
            request,
            status_code=502,
            title="Rollback Failed",
            detail="Failed to apply rollback runtime policy version.",
            type_suffix="runtime-rollback-failed",
            extras={"runtime_error": exc.to_dict()},
        )
        async with db_pool.acquire() as conn:
            await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
            async with conn.transaction():
                await _log_runtime_event(
                    conn,
                    tenant_id=current_user.tenant_id,
                    policy_version_id=target_row["id"],
                    action="rollback",
                    stage="rollback",
                    status="failed",
                    details=exc.to_dict(),
                    request_id=x_request_id,
                    created_by=current_user.id,
                )
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=current_user.tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=response,
                )
        return response

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE tenant_runtime_policy_versions
                SET is_active = FALSE,
                    is_last_good = FALSE,
                    build_status = CASE WHEN id = $2 THEN 'rolled_back' ELSE build_status END,
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND is_active = TRUE
                """,
                current_user.tenant_id,
                active_row["id"],
            )
            await conn.execute(
                """
                UPDATE tenant_runtime_policy_versions
                SET is_active = TRUE,
                    is_last_good = TRUE,
                    build_status = 'active',
                    activated_by = $3,
                    activated_at = NOW(),
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND id = $2
                """,
                current_user.tenant_id,
                target_row["id"],
                current_user.id,
            )
            await _log_runtime_event(
                conn,
                tenant_id=current_user.tenant_id,
                policy_version_id=target_row["id"],
                action="rollback",
                stage="rollback",
                status="succeeded",
                details={"apply_result": apply_result, "verify_result": verify_result},
                request_id=x_request_id,
                created_by=current_user.id,
            )
            response_body = RuntimeRollbackResponse(
                from_version=int(active_row["policy_version"]),
                to_version=int(target_row["policy_version"]),
                status="rolled_back",
                apply_result=apply_result,
                verify_result=verify_result,
            ).model_dump()
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_body,
                status_code=200,
                resource_type="runtime_policy_version",
                resource_id=target_row["id"],
            )
    return response_body


@router.get("/versions", response_model=List[RuntimeVersionResponse])
async def list_runtime_policy_versions(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        rows = await conn.fetch(
            """
            SELECT
                id,
                tenant_id,
                policy_version,
                source_hash,
                schema_version,
                build_status,
                is_active,
                is_last_good,
                validation_report,
                created_at,
                updated_at,
                activated_at
            FROM tenant_runtime_policy_versions
            WHERE tenant_id = $1
            ORDER BY policy_version DESC
            """,
            current_user.tenant_id,
        )
    return [RuntimeVersionResponse(**dict(row)) for row in rows]


@router.get("/metrics/activation", response_model=RuntimeActivationMetricsResponse)
async def get_runtime_activation_metrics(
    request: Request,
    window_hours: int = Query(default=24, ge=1, le=168),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(
            conn,
            current_user.tenant_id,
            current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
        metrics = await conn.fetchrow(
            """
            WITH scoped_events AS (
                SELECT action, stage, status, request_id, created_at
                FROM tenant_runtime_policy_events
                WHERE tenant_id = $1
                  AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')
            ),
            counts AS (
                SELECT
                    COUNT(*) FILTER (
                        WHERE action = 'activate'
                          AND stage = 'commit'
                          AND status = 'succeeded'
                    ) AS activation_success_count,
                    COUNT(*) FILTER (
                        WHERE action = 'activate'
                          AND status = 'failed'
                    ) AS activation_failure_count,
                    COUNT(*) FILTER (
                        WHERE action = 'rollback'
                          AND stage = 'rollback'
                          AND status = 'succeeded'
                    ) AS rollback_success_count,
                    COUNT(*) FILTER (
                        WHERE action = 'rollback'
                          AND status = 'failed'
                    ) AS rollback_failure_count
                FROM scoped_events
            ),
            rollback_latencies AS (
                SELECT
                    EXTRACT(EPOCH FROM (done.created_at - started.created_at)) * 1000.0 AS latency_ms
                FROM scoped_events started
                JOIN scoped_events done
                  ON done.action = 'rollback'
                 AND done.stage = 'rollback'
                 AND done.status IN ('succeeded', 'failed')
                 AND started.action = 'rollback'
                 AND started.stage = 'rollback'
                 AND started.status = 'started'
                 AND started.request_id IS NOT NULL
                 AND started.request_id = done.request_id
                 AND done.created_at >= started.created_at
            ),
            rollback_stats AS (
                SELECT
                    percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                    MAX(latency_ms) AS max_ms
                FROM rollback_latencies
            )
            SELECT
                counts.activation_success_count,
                counts.activation_failure_count,
                counts.rollback_success_count,
                counts.rollback_failure_count,
                COALESCE(rollback_stats.p50_ms, 0)::float8 AS rollback_p50_ms,
                COALESCE(rollback_stats.p95_ms, 0)::float8 AS rollback_p95_ms,
                COALESCE(rollback_stats.max_ms, 0)::float8 AS rollback_max_ms
            FROM counts
            CROSS JOIN rollback_stats
            """,
            current_user.tenant_id,
            window_hours,
        )

    activation_success_count = int(metrics["activation_success_count"] or 0)
    activation_failure_count = int(metrics["activation_failure_count"] or 0)
    activation_attempts = activation_success_count + activation_failure_count
    activation_success_rate_pct = (
        round((activation_success_count / activation_attempts) * 100.0, 2)
        if activation_attempts > 0
        else 0.0
    )

    return RuntimeActivationMetricsResponse(
        tenant_id=current_user.tenant_id,
        window_hours=window_hours,
        generated_at=datetime.now(timezone.utc),
        activation_success_count=activation_success_count,
        activation_failure_count=activation_failure_count,
        activation_success_rate_pct=activation_success_rate_pct,
        rollback_success_count=int(metrics["rollback_success_count"] or 0),
        rollback_failure_count=int(metrics["rollback_failure_count"] or 0),
        rollback_latency_p50_ms=float(metrics["rollback_p50_ms"] or 0.0),
        rollback_latency_p95_ms=float(metrics["rollback_p95_ms"] or 0.0),
        rollback_latency_max_ms=float(metrics["rollback_max_ms"] or 0.0),
    )

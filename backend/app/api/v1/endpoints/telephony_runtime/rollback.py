"""POST /telephony/sip/runtime/rollback — revert to a prior policy version."""
from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.tenant_rls import apply_tenant_rls_context
from app.infrastructure.telephony.runtime_policy_adapter import (
    RuntimeCommandError,
    RuntimePolicyAdapter,
)

from ._shared import (
    _claim_idempotency,
    _enforce_ws_i_quota,
    _log_runtime_event,
    _problem,
    _require_tenant,
    _stable_hash,
    _store_error_idempotency_result,
    _store_idempotency_result,
    get_runtime_policy_adapter,
)
from .schemas import RuntimeRollbackRequest, RuntimeRollbackResponse

router = APIRouter(tags=["Telephony SIP Runtime"])


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

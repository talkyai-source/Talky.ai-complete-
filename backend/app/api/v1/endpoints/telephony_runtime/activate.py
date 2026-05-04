"""POST /telephony/sip/runtime/activate — precheck → apply → verify → commit."""
from __future__ import annotations

import json
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.tenant_rls import apply_tenant_rls_context
from app.domain.services.telephony_runtime_policy import (
    PolicyCompilationError,
    compile_tenant_runtime_policy,
)
from app.infrastructure.telephony.runtime_policy_adapter import (
    RuntimeCommandError,
    RuntimePolicyAdapter,
)

from ._shared import (
    _claim_idempotency,
    _enforce_ws_i_quota,
    _load_active_snapshot,
    _log_runtime_event,
    _problem,
    _require_tenant,
    _stable_hash,
    _store_error_idempotency_result,
    _store_idempotency_result,
    get_runtime_policy_adapter,
)
from .schemas import RuntimeActivateRequest, RuntimeActivationResponse

router = APIRouter(tags=["Telephony SIP Runtime"])


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

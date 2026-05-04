"""Codec policy endpoints — list / create / update / activate / deactivate."""
from __future__ import annotations

import json
import logging
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.tenant_rls import apply_tenant_rls_context

from ._shared import (
    _claim_idempotency,
    _enforce_ws_i_quota,
    _problem,
    _require_tenant,
    _stable_hash,
    _store_error_idempotency_result,
    _store_idempotency_result,
)
from .schemas import (
    CodecPolicyCreateRequest,
    CodecPolicyResponse,
    CodecPolicyUpdateRequest,
    _normalize_codec,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Telephony SIP"])


# --- helpers (codec-policy-specific) ----------------------------------

def _codec_row_to_response(row: asyncpg.Record) -> CodecPolicyResponse:
    return CodecPolicyResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        policy_name=row["policy_name"],
        allowed_codecs=row["allowed_codecs"] or [],
        preferred_codec=row["preferred_codec"],
        sample_rate_hz=row["sample_rate_hz"],
        ptime_ms=row["ptime_ms"],
        max_bitrate_kbps=row["max_bitrate_kbps"],
        jitter_buffer_ms=row["jitter_buffer_ms"],
        is_active=row["is_active"],
        metadata=row["metadata"] or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _get_tenant_codec_policy(
    conn: asyncpg.Connection,
    tenant_id: str,
    policy_id: UUID,
) -> Optional[asyncpg.Record]:
    return await conn.fetchrow(
        """
        SELECT
            id,
            tenant_id,
            policy_name,
            allowed_codecs,
            preferred_codec,
            sample_rate_hz,
            ptime_ms,
            max_bitrate_kbps,
            jitter_buffer_ms,
            is_active,
            metadata,
            created_at,
            updated_at
        FROM tenant_codec_policies
        WHERE tenant_id = $1
          AND id = $2
        """,
        tenant_id,
        policy_id,
    )


# --- endpoints --------------------------------------------------------

@router.get("/codec-policies", response_model=list[CodecPolicyResponse])
async def list_codec_policies(
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
                policy_name,
                allowed_codecs,
                preferred_codec,
                sample_rate_hz,
                ptime_ms,
                max_bitrate_kbps,
                jitter_buffer_ms,
                is_active,
                metadata,
                created_at,
                updated_at
            FROM tenant_codec_policies
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            """,
            current_user.tenant_id,
        )
    return [_codec_row_to_response(row) for row in rows]


@router.post("/codec-policies", response_model=CodecPolicyResponse, status_code=201)
async def create_codec_policy(
    payload: CodecPolicyCreateRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    if not idempotency_key:
        return _problem(
            request=request,
            status_code=400,
            title="Idempotency Key Required",
            detail="Mutating operations require Idempotency-Key header.",
            type_suffix="idempotency-key-required",
        )

    canonical_payload = payload.model_dump()
    canonical_payload["allowed_codecs"] = [_normalize_codec(c) for c in payload.allowed_codecs]
    canonical_payload["preferred_codec"] = _normalize_codec(payload.preferred_codec)
    request_hash = _stable_hash(canonical_payload)
    operation = "codec_policies:create"

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        async with conn.transaction():
            state, cached_response, cached_code = await _claim_idempotency(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if state == "hash_mismatch":
                return _problem(
                    request=request,
                    status_code=409,
                    title="Idempotency Conflict",
                    detail="Idempotency-Key was reused with a different payload.",
                    type_suffix="idempotency-conflict",
                )
            if state == "in_progress":
                return _problem(
                    request=request,
                    status_code=409,
                    title="Request In Progress",
                    detail="A request with this Idempotency-Key is still processing.",
                    type_suffix="idempotency-in-progress",
                )
            if state == "replay":
                return JSONResponse(status_code=cached_code or 200, content=cached_response)

            quota_problem = await _enforce_ws_i_quota(
                conn=conn,
                request=request,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                policy_scope="api_mutation",
                metric_key="codec_policies:create",
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

            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenant_codec_policies (
                        tenant_id,
                        policy_name,
                        allowed_codecs,
                        preferred_codec,
                        sample_rate_hz,
                        ptime_ms,
                        max_bitrate_kbps,
                        jitter_buffer_ms,
                        metadata,
                        created_by,
                        updated_by
                    )
                    VALUES (
                        $1, $2, $3::text[], $4, $5, $6, $7, $8, $9::jsonb, $10, $10
                    )
                    RETURNING
                        id,
                        tenant_id,
                        policy_name,
                        allowed_codecs,
                        preferred_codec,
                        sample_rate_hz,
                        ptime_ms,
                        max_bitrate_kbps,
                        jitter_buffer_ms,
                        is_active,
                        metadata,
                        created_at,
                        updated_at
                    """,
                    current_user.tenant_id,
                    payload.policy_name.strip(),
                    canonical_payload["allowed_codecs"],
                    canonical_payload["preferred_codec"],
                    payload.sample_rate_hz,
                    payload.ptime_ms,
                    payload.max_bitrate_kbps,
                    payload.jitter_buffer_ms,
                    json.dumps(payload.metadata),
                    current_user.id,
                )
            except asyncpg.UniqueViolationError:
                return _problem(
                    request=request,
                    status_code=409,
                    title="Duplicate Codec Policy",
                    detail="A codec policy with this name already exists for the tenant.",
                    type_suffix="duplicate-codec-policy",
                )

            response_model = _codec_row_to_response(row)
            response_payload = response_model.model_dump(mode="json")
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_payload,
                status_code=201,
                resource_type="codec_policy",
                resource_id=response_model.id,
            )
            return JSONResponse(status_code=201, content=response_payload)


@router.patch("/codec-policies/{policy_id}", response_model=CodecPolicyResponse)
async def update_codec_policy(
    policy_id: UUID,
    payload: CodecPolicyUpdateRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    if not idempotency_key:
        return _problem(
            request=request,
            status_code=400,
            title="Idempotency Key Required",
            detail="Mutating operations require Idempotency-Key header.",
            type_suffix="idempotency-key-required",
        )

    patch_payload = payload.model_dump(exclude_unset=True)
    if not patch_payload:
        return _problem(
            request=request,
            status_code=400,
            title="Empty Update",
            detail="No fields provided to update.",
            type_suffix="empty-update",
        )

    request_hash = _stable_hash(patch_payload)
    operation = f"codec_policies:update:{policy_id}"

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        async with conn.transaction():
            state, cached_response, cached_code = await _claim_idempotency(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if state == "hash_mismatch":
                return _problem(
                    request=request,
                    status_code=409,
                    title="Idempotency Conflict",
                    detail="Idempotency-Key was reused with a different payload.",
                    type_suffix="idempotency-conflict",
                )
            if state == "in_progress":
                return _problem(
                    request=request,
                    status_code=409,
                    title="Request In Progress",
                    detail="A request with this Idempotency-Key is still processing.",
                    type_suffix="idempotency-in-progress",
                )
            if state == "replay":
                return JSONResponse(status_code=cached_code or 200, content=cached_response)

            quota_problem = await _enforce_ws_i_quota(
                conn=conn,
                request=request,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                policy_scope="api_mutation",
                metric_key="codec_policies:update",
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

            existing = await _get_tenant_codec_policy(conn, current_user.tenant_id, policy_id)
            if not existing:
                return _problem(
                    request=request,
                    status_code=404,
                    title="Codec Policy Not Found",
                    detail="Requested codec policy does not exist for tenant.",
                    type_suffix="codec-policy-not-found",
                )

            policy_name = patch_payload.get("policy_name", existing["policy_name"])
            allowed_codecs = patch_payload.get("allowed_codecs", existing["allowed_codecs"])
            preferred_codec = patch_payload.get("preferred_codec", existing["preferred_codec"])
            sample_rate_hz = patch_payload.get("sample_rate_hz", existing["sample_rate_hz"])
            ptime_ms = patch_payload.get("ptime_ms", existing["ptime_ms"])
            max_bitrate_kbps = patch_payload.get("max_bitrate_kbps", existing["max_bitrate_kbps"])
            jitter_buffer_ms = patch_payload.get("jitter_buffer_ms", existing["jitter_buffer_ms"])
            metadata = patch_payload.get("metadata", existing["metadata"] or {})

            allowed_codecs = [_normalize_codec(c) for c in allowed_codecs]
            preferred_codec = _normalize_codec(preferred_codec)

            if preferred_codec not in allowed_codecs:
                return _problem(
                    request=request,
                    status_code=400,
                    title="Invalid Codec Policy",
                    detail="preferred_codec must be present in allowed_codecs.",
                    type_suffix="invalid-codec-policy",
                )
            if sample_rate_hz not in {8000, 16000, 24000, 48000}:
                return _problem(
                    request=request,
                    status_code=400,
                    title="Invalid Codec Policy",
                    detail="sample_rate_hz must be one of 8000,16000,24000,48000.",
                    type_suffix="invalid-codec-policy",
                )
            if ptime_ms not in {10, 20, 30, 40, 60}:
                return _problem(
                    request=request,
                    status_code=400,
                    title="Invalid Codec Policy",
                    detail="ptime_ms must be one of 10,20,30,40,60.",
                    type_suffix="invalid-codec-policy",
                )

            try:
                row = await conn.fetchrow(
                    """
                    UPDATE tenant_codec_policies
                    SET policy_name = $3,
                        allowed_codecs = $4::text[],
                        preferred_codec = $5,
                        sample_rate_hz = $6,
                        ptime_ms = $7,
                        max_bitrate_kbps = $8,
                        jitter_buffer_ms = $9,
                        metadata = $10::jsonb,
                        updated_by = $11,
                        updated_at = NOW()
                    WHERE tenant_id = $1
                      AND id = $2
                    RETURNING
                        id,
                        tenant_id,
                        policy_name,
                        allowed_codecs,
                        preferred_codec,
                        sample_rate_hz,
                        ptime_ms,
                        max_bitrate_kbps,
                        jitter_buffer_ms,
                        is_active,
                        metadata,
                        created_at,
                        updated_at
                    """,
                    current_user.tenant_id,
                    policy_id,
                    policy_name,
                    allowed_codecs,
                    preferred_codec,
                    sample_rate_hz,
                    ptime_ms,
                    max_bitrate_kbps,
                    jitter_buffer_ms,
                    json.dumps(metadata),
                    current_user.id,
                )
            except asyncpg.UniqueViolationError:
                return _problem(
                    request=request,
                    status_code=409,
                    title="Duplicate Codec Policy",
                    detail="A codec policy with this name already exists for the tenant.",
                    type_suffix="duplicate-codec-policy",
                )

            response_model = _codec_row_to_response(row)
            response_payload = response_model.model_dump(mode="json")
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_payload,
                status_code=200,
                resource_type="codec_policy",
                resource_id=response_model.id,
            )
            return response_model


async def _set_codec_policy_active_state(
    *,
    policy_id: UUID,
    active_state: bool,
    request: Request,
    idempotency_key: Optional[str],
    request_id: Optional[str],
    current_user: CurrentUser,
    db_pool: asyncpg.Pool,
) -> JSONResponse | CodecPolicyResponse:
    if not idempotency_key:
        return _problem(
            request=request,
            status_code=400,
            title="Idempotency Key Required",
            detail="Mutating operations require Idempotency-Key header.",
            type_suffix="idempotency-key-required",
        )
    operation = (
        f"codec_policies:activate:{policy_id}"
        if active_state
        else f"codec_policies:deactivate:{policy_id}"
    )
    request_hash = _stable_hash({"policy_id": str(policy_id), "active_state": active_state})

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        async with conn.transaction():
            state, cached_response, cached_code = await _claim_idempotency(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if state == "hash_mismatch":
                return _problem(
                    request=request,
                    status_code=409,
                    title="Idempotency Conflict",
                    detail="Idempotency-Key was reused with a different payload.",
                    type_suffix="idempotency-conflict",
                )
            if state == "in_progress":
                return _problem(
                    request=request,
                    status_code=409,
                    title="Request In Progress",
                    detail="A request with this Idempotency-Key is still processing.",
                    type_suffix="idempotency-in-progress",
                )
            if state == "replay":
                return JSONResponse(status_code=cached_code or 200, content=cached_response)

            quota_problem = await _enforce_ws_i_quota(
                conn=conn,
                request=request,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                policy_scope="api_mutation",
                metric_key="codec_policies:activate" if active_state else "codec_policies:deactivate",
                request_id=request_id,
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

            row = await conn.fetchrow(
                """
                UPDATE tenant_codec_policies
                SET is_active = $3,
                    updated_by = $4,
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND id = $2
                RETURNING
                    id,
                    tenant_id,
                    policy_name,
                    allowed_codecs,
                    preferred_codec,
                    sample_rate_hz,
                    ptime_ms,
                    max_bitrate_kbps,
                    jitter_buffer_ms,
                    is_active,
                    metadata,
                    created_at,
                    updated_at
                """,
                current_user.tenant_id,
                policy_id,
                active_state,
                current_user.id,
            )
            if not row:
                return _problem(
                    request=request,
                    status_code=404,
                    title="Codec Policy Not Found",
                    detail="Requested codec policy does not exist for tenant.",
                    type_suffix="codec-policy-not-found",
                )

            response_model = _codec_row_to_response(row)
            response_payload = response_model.model_dump(mode="json")
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_payload,
                status_code=200,
                resource_type="codec_policy",
                resource_id=response_model.id,
            )
            return response_model


@router.post("/codec-policies/{policy_id}/activate", response_model=CodecPolicyResponse)
async def activate_codec_policy(
    policy_id: UUID,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    return await _set_codec_policy_active_state(
        policy_id=policy_id,
        active_state=True,
        request=request,
        idempotency_key=idempotency_key,
        request_id=x_request_id,
        current_user=current_user,
        db_pool=db_pool,
    )


@router.post("/codec-policies/{policy_id}/deactivate", response_model=CodecPolicyResponse)
async def deactivate_codec_policy(
    policy_id: UUID,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    return await _set_codec_policy_active_state(
        policy_id=policy_id,
        active_state=False,
        request=request,
        idempotency_key=idempotency_key,
        request_id=x_request_id,
        current_user=current_user,
        db_pool=db_pool,
    )

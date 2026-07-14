"""Route policy endpoints — list / create / update / activate / deactivate."""
from __future__ import annotations

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
    RoutePolicyCreateRequest,
    RoutePolicyResponse,
    RoutePolicyUpdateRequest,
    SIPRouteType,
    _validate_match_pattern,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Telephony SIP"])


# --- helpers (route-policy-specific) ----------------------------------

def _route_row_to_response(row: asyncpg.Record) -> RoutePolicyResponse:
    return RoutePolicyResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        policy_name=row["policy_name"],
        route_type=row["route_type"],
        priority=row["priority"],
        match_pattern=row["match_pattern"],
        target_trunk_id=row["target_trunk_id"],
        codec_policy_id=row["codec_policy_id"],
        strip_digits=row["strip_digits"],
        prepend_digits=row["prepend_digits"],
        is_active=row["is_active"],
        metadata=row["metadata"] or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _get_tenant_route_policy(
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
            route_type,
            priority,
            match_pattern,
            target_trunk_id,
            codec_policy_id,
            strip_digits,
            prepend_digits,
            is_active,
            metadata,
            created_at,
            updated_at
        FROM tenant_route_policies
        WHERE tenant_id = $1
          AND id = $2
        """,
        tenant_id,
        policy_id,
    )


# --- endpoints --------------------------------------------------------

@router.get("/route-policies", response_model=list[RoutePolicyResponse])
async def list_route_policies(
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
                route_type,
                priority,
                match_pattern,
                target_trunk_id,
                codec_policy_id,
                strip_digits,
                prepend_digits,
                is_active,
                metadata,
                created_at,
                updated_at
            FROM tenant_route_policies
            WHERE tenant_id = $1
            ORDER BY priority ASC, created_at DESC
            """,
            current_user.tenant_id,
        )
    return [_route_row_to_response(row) for row in rows]


@router.post("/route-policies", response_model=RoutePolicyResponse, status_code=201)
async def create_route_policy(
    payload: RoutePolicyCreateRequest,
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

    _validate_match_pattern(payload.match_pattern)
    canonical_payload = payload.model_dump(mode="json")
    request_hash = _stable_hash(canonical_payload)
    operation = "route_policies:create"

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
                metric_key="route_policies:create",
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
                    INSERT INTO tenant_route_policies (
                        tenant_id,
                        policy_name,
                        route_type,
                        priority,
                        match_pattern,
                        target_trunk_id,
                        codec_policy_id,
                        strip_digits,
                        prepend_digits,
                        is_active,
                        metadata,
                        created_by,
                        updated_by
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $12
                    )
                    RETURNING
                        id,
                        tenant_id,
                        policy_name,
                        route_type,
                        priority,
                        match_pattern,
                        target_trunk_id,
                        codec_policy_id,
                        strip_digits,
                        prepend_digits,
                        is_active,
                        metadata,
                        created_at,
                        updated_at
                    """,
                    current_user.tenant_id,
                    payload.policy_name.strip(),
                    payload.route_type.value,
                    payload.priority,
                    payload.match_pattern,
                    payload.target_trunk_id,
                    payload.codec_policy_id,
                    payload.strip_digits,
                    payload.prepend_digits,
                    payload.is_active,
                    # Raw dict — the pool's jsonb codec (app.core.db) encodes
                    # via json.dumps on write; a pre-dumped string here would
                    # be double-encoded into a JSON string scalar and break
                    # every later read (metadata: Dict[str, Any] pydantic field).
                    payload.metadata,
                    current_user.id,
                )
            except asyncpg.UniqueViolationError:
                return _problem(
                    request=request,
                    status_code=409,
                    title="Duplicate Route Policy",
                    detail="A route policy with this name already exists for the tenant.",
                    type_suffix="duplicate-route-policy",
                )
            except asyncpg.ForeignKeyViolationError:
                return _problem(
                    request=request,
                    status_code=400,
                    title="Invalid Route References",
                    detail="target_trunk_id or codec_policy_id is invalid for this tenant.",
                    type_suffix="invalid-route-references",
                )

            response_model = _route_row_to_response(row)
            response_payload = response_model.model_dump(mode="json")
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_payload,
                status_code=201,
                resource_type="route_policy",
                resource_id=response_model.id,
            )
            return JSONResponse(status_code=201, content=response_payload)


@router.patch("/route-policies/{policy_id}", response_model=RoutePolicyResponse)
async def update_route_policy(
    policy_id: UUID,
    payload: RoutePolicyUpdateRequest,
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
    if "match_pattern" in patch_payload:
        _validate_match_pattern(patch_payload["match_pattern"])

    request_hash = _stable_hash(patch_payload)
    operation = f"route_policies:update:{policy_id}"

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
                metric_key="route_policies:update",
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

            existing = await _get_tenant_route_policy(conn, current_user.tenant_id, policy_id)
            if not existing:
                return _problem(
                    request=request,
                    status_code=404,
                    title="Route Policy Not Found",
                    detail="Requested route policy does not exist for tenant.",
                    type_suffix="route-policy-not-found",
                )

            policy_name = patch_payload.get("policy_name", existing["policy_name"])
            route_type = patch_payload.get("route_type", existing["route_type"])
            priority = patch_payload.get("priority", existing["priority"])
            match_pattern = patch_payload.get("match_pattern", existing["match_pattern"])
            target_trunk_id = patch_payload.get("target_trunk_id", existing["target_trunk_id"])
            codec_policy_id = patch_payload.get("codec_policy_id", existing["codec_policy_id"])
            strip_digits = patch_payload.get("strip_digits", existing["strip_digits"])
            prepend_digits = patch_payload.get("prepend_digits", existing["prepend_digits"])
            is_active = patch_payload.get("is_active", existing["is_active"])
            metadata = patch_payload.get("metadata", existing["metadata"] or {})

            try:
                row = await conn.fetchrow(
                    """
                    UPDATE tenant_route_policies
                    SET policy_name = $3,
                        route_type = $4,
                        priority = $5,
                        match_pattern = $6,
                        target_trunk_id = $7,
                        codec_policy_id = $8,
                        strip_digits = $9,
                        prepend_digits = $10,
                        is_active = $11,
                        metadata = $12::jsonb,
                        updated_by = $13,
                        updated_at = NOW()
                    WHERE tenant_id = $1
                      AND id = $2
                    RETURNING
                        id,
                        tenant_id,
                        policy_name,
                        route_type,
                        priority,
                        match_pattern,
                        target_trunk_id,
                        codec_policy_id,
                        strip_digits,
                        prepend_digits,
                        is_active,
                        metadata,
                        created_at,
                        updated_at
                    """,
                    current_user.tenant_id,
                    policy_id,
                    policy_name,
                    route_type.value if isinstance(route_type, SIPRouteType) else route_type,
                    priority,
                    match_pattern,
                    target_trunk_id,
                    codec_policy_id,
                    strip_digits,
                    prepend_digits,
                    is_active,
                    # Raw dict — see create-path comment above; the jsonb
                    # codec handles JSON encoding on write.
                    metadata,
                    current_user.id,
                )
            except asyncpg.UniqueViolationError:
                return _problem(
                    request=request,
                    status_code=409,
                    title="Duplicate Route Policy",
                    detail="A route policy with this name already exists for the tenant.",
                    type_suffix="duplicate-route-policy",
                )
            except asyncpg.ForeignKeyViolationError:
                return _problem(
                    request=request,
                    status_code=400,
                    title="Invalid Route References",
                    detail="target_trunk_id or codec_policy_id is invalid for this tenant.",
                    type_suffix="invalid-route-references",
                )

            response_model = _route_row_to_response(row)
            response_payload = response_model.model_dump(mode="json")
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_payload,
                status_code=200,
                resource_type="route_policy",
                resource_id=response_model.id,
            )
            return response_model


async def _set_route_policy_active_state(
    *,
    policy_id: UUID,
    active_state: bool,
    request: Request,
    idempotency_key: Optional[str],
    request_id: Optional[str],
    current_user: CurrentUser,
    db_pool: asyncpg.Pool,
) -> JSONResponse | RoutePolicyResponse:
    if not idempotency_key:
        return _problem(
            request=request,
            status_code=400,
            title="Idempotency Key Required",
            detail="Mutating operations require Idempotency-Key header.",
            type_suffix="idempotency-key-required",
        )
    operation = (
        f"route_policies:activate:{policy_id}"
        if active_state
        else f"route_policies:deactivate:{policy_id}"
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
                metric_key="route_policies:activate" if active_state else "route_policies:deactivate",
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
                UPDATE tenant_route_policies
                SET is_active = $3,
                    updated_by = $4,
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND id = $2
                RETURNING
                    id,
                    tenant_id,
                    policy_name,
                    route_type,
                    priority,
                    match_pattern,
                    target_trunk_id,
                    codec_policy_id,
                    strip_digits,
                    prepend_digits,
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
                    title="Route Policy Not Found",
                    detail="Requested route policy does not exist for tenant.",
                    type_suffix="route-policy-not-found",
                )

            response_model = _route_row_to_response(row)
            response_payload = response_model.model_dump(mode="json")
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_payload,
                status_code=200,
                resource_type="route_policy",
                resource_id=response_model.id,
            )
            return response_model


@router.post("/route-policies/{policy_id}/activate", response_model=RoutePolicyResponse)
async def activate_route_policy(
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
    return await _set_route_policy_active_state(
        policy_id=policy_id,
        active_state=True,
        request=request,
        idempotency_key=idempotency_key,
        request_id=x_request_id,
        current_user=current_user,
        db_pool=db_pool,
    )


@router.post("/route-policies/{policy_id}/deactivate", response_model=RoutePolicyResponse)
async def deactivate_route_policy(
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
    return await _set_route_policy_active_state(
        policy_id=policy_id,
        active_state=False,
        request=request,
        idempotency_key=idempotency_key,
        request_id=x_request_id,
        current_user=current_user,
        db_pool=db_pool,
    )

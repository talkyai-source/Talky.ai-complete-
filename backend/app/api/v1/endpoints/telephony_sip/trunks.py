"""SIP trunk endpoints — list / create / update / activate / deactivate."""
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
from app.infrastructure.connectors.encryption import get_encryption_service

from ._shared import (
    _canonical_domain,
    _claim_idempotency,
    _enforce_ws_i_quota,
    _problem,
    _require_tenant,
    _stable_hash,
    _store_error_idempotency_result,
    _store_idempotency_result,
)
from .schemas import (
    SIPDirection,
    SIPTransport,
    SIPTrunkCreateRequest,
    SIPTrunkResponse,
    SIPTrunkTestResponse,
    SIPTrunkUpdateRequest,
)
from .trunk_probe import probe_sip_endpoint

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Telephony SIP"])


# --- helpers (trunk-specific) ------------------------------------------

def _coerce_jsonb(raw):
    """asyncpg returns JSONB as dict on the modern codec and as str otherwise.

    Tolerate both so the row->response mapping works regardless of pool
    configuration.
    """
    if raw is None or isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None
    return None


def _row_to_response(row: asyncpg.Record) -> SIPTrunkResponse:
    keys = row.keys()
    return SIPTrunkResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        trunk_name=row["trunk_name"],
        sip_domain=row["sip_domain"],
        port=row["port"],
        transport=row["transport"],
        direction=row["direction"],
        is_active=row["is_active"],
        auth_username=row["auth_username"],
        auth_configured=bool(row["auth_password_encrypted"]),
        metadata=row["metadata"] or {},
        last_tested_at=row["last_tested_at"] if "last_tested_at" in keys else None,
        last_test_result=(
            _coerce_jsonb(row["last_test_result"]) if "last_test_result" in keys else None
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _get_tenant_trunk(
    conn: asyncpg.Connection,
    tenant_id: str,
    trunk_id: UUID,
) -> Optional[asyncpg.Record]:
    return await conn.fetchrow(
        """
        SELECT
            id,
            tenant_id,
            trunk_name,
            sip_domain,
            port,
            transport,
            direction,
            is_active,
            auth_username,
            auth_password_encrypted,
            metadata,
            last_tested_at,
            last_test_result,
            created_at,
            updated_at
        FROM tenant_sip_trunks
        WHERE tenant_id = $1
          AND id = $2
        """,
        tenant_id,
        trunk_id,
    )


# --- endpoints ---------------------------------------------------------

@router.get("/trunks", response_model=list[SIPTrunkResponse])
async def list_sip_trunks(
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
                trunk_name,
                sip_domain,
                port,
                transport,
                direction,
                is_active,
                auth_username,
                auth_password_encrypted,
                metadata,
                last_tested_at,
                last_test_result,
                created_at,
                updated_at
            FROM tenant_sip_trunks
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            """,
            current_user.tenant_id,
        )
    return [_row_to_response(row) for row in rows]


@router.post("/trunks", response_model=SIPTrunkResponse, status_code=201)
async def create_sip_trunk(
    payload: SIPTrunkCreateRequest,
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

    canonical_payload = {
        "trunk_name": payload.trunk_name.strip(),
        "sip_domain": _canonical_domain(payload.sip_domain),
        "port": payload.port,
        "transport": payload.transport.value,
        "direction": payload.direction.value,
        "auth_username": payload.auth_username,
        "auth_password": payload.auth_password,
        "metadata": payload.metadata,
    }
    request_hash = _stable_hash(canonical_payload)
    operation = "sip_trunks:create"
    encryption = get_encryption_service()
    encrypted_password = (
        encryption.encrypt(payload.auth_password) if payload.auth_password else None
    )

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
                metric_key="sip_trunks:create",
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
                    INSERT INTO tenant_sip_trunks (
                        tenant_id,
                        trunk_name,
                        sip_domain,
                        port,
                        transport,
                        direction,
                        auth_username,
                        auth_password_encrypted,
                        metadata,
                        created_by,
                        updated_by
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $10)
                    RETURNING
                        id,
                        tenant_id,
                        trunk_name,
                        sip_domain,
                        port,
                        transport,
                        direction,
                        is_active,
                        auth_username,
                        auth_password_encrypted,
                        metadata,
                        last_tested_at,
                        last_test_result,
                        created_at,
                        updated_at
                    """,
                    current_user.tenant_id,
                    canonical_payload["trunk_name"],
                    canonical_payload["sip_domain"],
                    canonical_payload["port"],
                    canonical_payload["transport"],
                    canonical_payload["direction"],
                    canonical_payload["auth_username"],
                    encrypted_password,
                    json.dumps(canonical_payload["metadata"]),
                    current_user.id,
                )
            except asyncpg.UniqueViolationError:
                return _problem(
                    request=request,
                    status_code=409,
                    title="Duplicate Trunk",
                    detail="A trunk with this name already exists for the tenant.",
                    type_suffix="duplicate-trunk",
                )

            response_model = _row_to_response(row)
            response_payload = response_model.model_dump(mode="json")
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_payload,
                status_code=201,
                resource_type="sip_trunk",
                resource_id=response_model.id,
            )
            return JSONResponse(status_code=201, content=response_payload)


@router.patch("/trunks/{trunk_id}", response_model=SIPTrunkResponse)
async def update_sip_trunk(
    trunk_id: UUID,
    payload: SIPTrunkUpdateRequest,
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
    operation = f"sip_trunks:update:{trunk_id}"

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
                metric_key="sip_trunks:update",
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

            existing = await _get_tenant_trunk(conn, current_user.tenant_id, trunk_id)
            if not existing:
                return _problem(
                    request=request,
                    status_code=404,
                    title="Trunk Not Found",
                    detail="Requested trunk does not exist for tenant.",
                    type_suffix="trunk-not-found",
                )

            existing_auth_user = existing["auth_username"]
            if payload.clear_auth and ("auth_username" in patch_payload or "auth_password" in patch_payload):
                return _problem(
                    request=request,
                    status_code=400,
                    title="Invalid Authentication Patch",
                    detail="clear_auth cannot be combined with auth_username/auth_password fields.",
                    type_suffix="invalid-auth-patch",
                )

            trunk_name = patch_payload.get("trunk_name", existing["trunk_name"])
            sip_domain = _canonical_domain(patch_payload.get("sip_domain", existing["sip_domain"]))
            port = patch_payload.get("port", existing["port"])
            transport = patch_payload.get("transport", existing["transport"])
            direction = patch_payload.get("direction", existing["direction"])
            metadata = patch_payload.get("metadata", existing["metadata"] or {})

            auth_username = existing_auth_user
            auth_password_encrypted = existing["auth_password_encrypted"]

            if payload.clear_auth:
                auth_username = None
                auth_password_encrypted = None
            else:
                if "auth_username" in patch_payload:
                    auth_username = patch_payload["auth_username"] or None
                if "auth_password" in patch_payload:
                    if patch_payload["auth_password"]:
                        auth_password_encrypted = get_encryption_service().encrypt(
                            patch_payload["auth_password"]
                        )
                    else:
                        auth_password_encrypted = None

            if bool(auth_username) != bool(auth_password_encrypted):
                return _problem(
                    request=request,
                    status_code=400,
                    title="Invalid Authentication Configuration",
                    detail="auth_username and auth_password must be set or cleared together.",
                    type_suffix="invalid-auth-configuration",
                )

            try:
                row = await conn.fetchrow(
                    """
                    UPDATE tenant_sip_trunks
                    SET trunk_name = $3,
                        sip_domain = $4,
                        port = $5,
                        transport = $6,
                        direction = $7,
                        auth_username = $8,
                        auth_password_encrypted = $9,
                        metadata = $10::jsonb,
                        updated_by = $11,
                        updated_at = NOW()
                    WHERE tenant_id = $1
                      AND id = $2
                    RETURNING
                        id,
                        tenant_id,
                        trunk_name,
                        sip_domain,
                        port,
                        transport,
                        direction,
                        is_active,
                        auth_username,
                        auth_password_encrypted,
                        metadata,
                        last_tested_at,
                        last_test_result,
                        created_at,
                        updated_at
                    """,
                    current_user.tenant_id,
                    trunk_id,
                    trunk_name,
                    sip_domain,
                    port,
                    transport.value if isinstance(transport, SIPTransport) else transport,
                    direction.value if isinstance(direction, SIPDirection) else direction,
                    auth_username,
                    auth_password_encrypted,
                    json.dumps(metadata),
                    current_user.id,
                )
            except asyncpg.UniqueViolationError:
                return _problem(
                    request=request,
                    status_code=409,
                    title="Duplicate Trunk",
                    detail="A trunk with this name already exists for the tenant.",
                    type_suffix="duplicate-trunk",
                )

            response_model = _row_to_response(row)
            response_payload = response_model.model_dump(mode="json")
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_payload,
                status_code=200,
                resource_type="sip_trunk",
                resource_id=response_model.id,
            )
            return response_model


async def _set_trunk_active_state(
    *,
    trunk_id: UUID,
    active_state: bool,
    request: Request,
    idempotency_key: Optional[str],
    request_id: Optional[str],
    current_user: CurrentUser,
    db_pool: asyncpg.Pool,
) -> JSONResponse | SIPTrunkResponse:
    if not idempotency_key:
        return _problem(
            request=request,
            status_code=400,
            title="Idempotency Key Required",
            detail="Mutating operations require Idempotency-Key header.",
            type_suffix="idempotency-key-required",
        )

    operation = (
        f"sip_trunks:activate:{trunk_id}" if active_state else f"sip_trunks:deactivate:{trunk_id}"
    )
    request_hash = _stable_hash({"trunk_id": str(trunk_id), "active_state": active_state})

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
                metric_key="sip_trunks:activate" if active_state else "sip_trunks:deactivate",
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

            # Activation gate: refuse to flip a trunk live until a probe
            # has actually proven the host is reachable. Deactivation is
            # always allowed (you need to be able to disable a broken
            # trunk fast). Tenants whose last_test_result is missing or
            # has ok=false must run /trunks/{id}/test first.
            if active_state:
                test_row = await conn.fetchrow(
                    """
                    SELECT last_tested_at, last_test_result
                    FROM tenant_sip_trunks
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    trunk_id, current_user.tenant_id,
                )
                if test_row:
                    raw = test_row["last_test_result"]
                    if isinstance(raw, str):
                        try:
                            raw = json.loads(raw)
                        except Exception:
                            raw = None
                    last_ok = bool(raw and raw.get("ok"))
                    if not last_ok:
                        return _problem(
                            request=request,
                            status_code=400,
                            title="Activation Blocked",
                            detail=(
                                "Run a successful connectivity test before activating "
                                "this trunk. POST /trunks/{id}/test."
                            ),
                            type_suffix="trunk-not-verified",
                        )

            row = await conn.fetchrow(
                """
                UPDATE tenant_sip_trunks
                SET is_active = $3,
                    updated_by = $4,
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND id = $2
                RETURNING
                    id,
                    tenant_id,
                    trunk_name,
                    sip_domain,
                    port,
                    transport,
                    direction,
                    is_active,
                    auth_username,
                    auth_password_encrypted,
                    metadata,
                    last_tested_at,
                    last_test_result,
                    created_at,
                    updated_at
                """,
                current_user.tenant_id,
                trunk_id,
                active_state,
                current_user.id,
            )
            if not row:
                return _problem(
                    request=request,
                    status_code=404,
                    title="Trunk Not Found",
                    detail="Requested trunk does not exist for tenant.",
                    type_suffix="trunk-not-found",
                )

            response_model = _row_to_response(row)
            response_payload = response_model.model_dump(mode="json")
            await _store_idempotency_result(
                conn,
                tenant_id=current_user.tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=response_payload,
                status_code=200,
                resource_type="sip_trunk",
                resource_id=response_model.id,
            )
            return response_model


@router.post("/trunks/{trunk_id}/activate", response_model=SIPTrunkResponse)
async def activate_sip_trunk(
    trunk_id: UUID,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    return await _set_trunk_active_state(
        trunk_id=trunk_id,
        active_state=True,
        request=request,
        idempotency_key=idempotency_key,
        request_id=x_request_id,
        current_user=current_user,
        db_pool=db_pool,
    )


@router.post("/trunks/{trunk_id}/deactivate", response_model=SIPTrunkResponse)
async def deactivate_sip_trunk(
    trunk_id: UUID,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    return await _set_trunk_active_state(
        trunk_id=trunk_id,
        active_state=False,
        request=request,
        idempotency_key=idempotency_key,
        request_id=x_request_id,
        current_user=current_user,
        db_pool=db_pool,
    )


@router.post("/trunks/{trunk_id}/test", response_model=SIPTrunkTestResponse)
async def test_sip_trunk(
    trunk_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Probe a tenant's SIP trunk for reachability and persist the result.

    The probe runs a real network handshake (TCP/TLS) or sends a SIP
    OPTIONS datagram (UDP). The full result dict is stored on the trunk
    row in last_test_result so the activate endpoint's gate can read
    .ok back without re-running the probe.
    """
    from datetime import datetime, timezone

    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(
            conn, current_user.tenant_id, current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
        row = await conn.fetchrow(
            "SELECT sip_domain, port, transport FROM tenant_sip_trunks "
            "WHERE id = $1 AND tenant_id = $2",
            trunk_id, current_user.tenant_id,
        )
    if not row:
        return _problem(
            request=request,
            status_code=404,
            title="Trunk Not Found",
            detail=f"No SIP trunk {trunk_id} for this tenant.",
            type_suffix="trunk-not-found",
        )

    result = await probe_sip_endpoint(
        host=row["sip_domain"], port=row["port"], transport=row["transport"],
    )
    tested_at = datetime.now(timezone.utc)

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(
            conn, current_user.tenant_id, current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
        await conn.execute(
            """
            UPDATE tenant_sip_trunks
            SET last_tested_at = $1,
                last_test_result = $2::jsonb,
                updated_at = NOW()
            WHERE id = $3 AND tenant_id = $4
            """,
            tested_at,
            json.dumps(result),
            trunk_id,
            current_user.tenant_id,
        )

    return SIPTrunkTestResponse(
        ok=bool(result.get("ok")),
        latency_ms=int(result.get("latency_ms", 0) or 0),
        transport=row["transport"],
        target=f'{row["sip_domain"]}:{row["port"]}',
        error=result.get("error"),
        detail=result.get("detail"),
        tested_at=tested_at,
    )

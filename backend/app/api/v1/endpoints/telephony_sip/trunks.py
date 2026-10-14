"""SIP trunk endpoints — list / create / update / activate / deactivate."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import socket
import time
from collections import deque
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.container import get_container
from app.core.db_utils import acquire_with_tenant
from app.core.tenant_rls import apply_tenant_rls_context
from app.domain.services.telephony.trunk_runtime import (
    evaluate_trunk_runtime,
    trunk_status_freshness_seconds,
)
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
from .trunk_probe import run_trunk_test

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Telephony SIP"])


class _AbandonClaim(Exception):
    """Raised inside the idempotency-claim transaction to roll the claim back.

    An early error return commits the transaction, which would persist a claim
    with no stored result and block every retry with that Idempotency-Key
    (409 'in progress' / 'different payload'). Raising rolls the claim back so
    the client can retry; ``_abandons_claim`` turns it into the response.
    """

    def __init__(self, response: JSONResponse) -> None:
        super().__init__("idempotency claim abandoned")
        self.response = response


def _abandons_claim(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except _AbandonClaim as abandoned:
            return abandoned.response

    return wrapper


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
    runtime = evaluate_trunk_runtime(dict(row), require_inbound=False)
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
        metadata=_coerce_jsonb(row["metadata"]) or {},
        last_tested_at=row["last_tested_at"] if "last_tested_at" in keys else None,
        last_test_result=(
            _coerce_jsonb(row["last_test_result"]) if "last_test_result" in keys else None
        ),
        live_registration_status=(
            row["live_registration_status"] if "live_registration_status" in keys else None
        ),
        live_status_detail=(row["live_status_detail"] if "live_status_detail" in keys else None),
        live_status_checked_at=(
            row["live_status_checked_at"] if "live_status_checked_at" in keys else None
        ),
        runtime_ready=runtime.ready,
        inbound_runtime_ready=evaluate_trunk_runtime(dict(row), require_inbound=True).ready,
        runtime_status_code=runtime.code,
        runtime_status_detail=runtime.detail,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _requires_confirmed_pjsip_apply() -> bool:
    return (
        os.getenv("ENVIRONMENT", "development").strip().lower() == "production"
        and os.getenv("TELEPHONY_ADAPTER", "auto").strip().lower() == "asterisk"
    )


def _pjsip_auto_reload_enabled() -> bool:
    return os.getenv("TELEPHONY_PJSIP_AUTO_RELOAD", "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }


async def _sync_trunk_pjsip_config(
    row: asyncpg.Record,
    *,
    active: bool,
    previous_row: Optional[asyncpg.Record] = None,
) -> None:
    """Render/apply or remove the per-tenant namespaced PJSIP config for a
    trunk after an activate / deactivate / update (Phase B).

    In production Asterisk mode, activation and edits to an active trunk are
    fail-closed: the enclosing database transaction rolls back unless a live
    reload command is acknowledged by Asterisk. A pending reload is not an
    acknowledgement, and command acknowledgement is not trunk health. A failed
    edit restores the previous file projection before surfacing HTTP 503.

    Deactivation stays logically authoritative even if file cleanup fails:
    the database becomes inactive immediately, admission rejects the route,
    and the root status-updater retries stale file removal.  Development mode
    remains fail-soft so offline API tests do not require a local Asterisk.
    """
    try:
        # The shared platform-default upstream is hand-managed
        # (blazedigitel-endpoint); never emit a generated file for it.
        from app.domain.services.telephony.trunk_resolver import (
            platform_default_trunk_name,
        )

        name = (row["trunk_name"] or "").strip().lower()
        if name and name == platform_default_trunk_name().strip().lower():
            return

        from app.infrastructure.telephony.pjsip_config_generator import (
            apply_trunk_config,
            remove_trunk_config,
            request_pjsip_reload,
        )

        require_live_apply = _requires_confirmed_pjsip_apply() and active
        if require_live_apply and not _pjsip_auto_reload_enabled():
            raise RuntimeError(
                "TELEPHONY_PJSIP_AUTO_RELOAD must be enabled for production trunk changes"
            )
        if active:
            if require_live_apply:
                # Resolve once under the same public-target policy as the
                # probe before handing a tenant-controlled host to Asterisk.
                from .trunk_probe import resolve_sip_target

                await resolve_sip_target(
                    host=row["sip_domain"],
                    port=int(row["port"]),
                    socktype=(
                        socket.SOCK_STREAM
                        if str(row["transport"]).lower() in {"tcp", "tls"}
                        else socket.SOCK_DGRAM
                    ),
                )
            decrypted = None
            enc = row["auth_password_encrypted"]
            if enc:
                decrypted = get_encryption_service().decrypt(enc)
            await apply_trunk_config(
                row,
                decrypted_password=decrypted,
                require_reload=require_live_apply,
            )
        else:
            await remove_trunk_config(str(row["id"]))
    except Exception as exc:  # noqa: BLE001
        strict_failure = _requires_confirmed_pjsip_apply() and active
        logger.error(
            "pjsip_config_sync_failed trunk=%s active=%s strict=%s err_type=%s",
            str(row["id"])[:8] if row is not None else "?",
            active,
            strict_failure,
            type(exc).__name__,
        )
        if strict_failure:
            # The database transaction will roll back.  Restore the file to
            # that same previous desired state so a later unrelated reload
            # cannot apply an uncommitted activation/edit.
            try:
                from app.infrastructure.telephony.pjsip_config_generator import (
                    apply_trunk_config,
                    remove_trunk_config,
                    request_pjsip_reload,
                )

                previous_active = bool(previous_row and previous_row["is_active"])
                if previous_active:
                    previous_decrypted = None
                    previous_enc = previous_row["auth_password_encrypted"]
                    if previous_enc:
                        previous_decrypted = get_encryption_service().decrypt(previous_enc)
                    await apply_trunk_config(
                        previous_row,
                        decrypted_password=previous_decrypted,
                        reload=False,
                    )
                else:
                    await remove_trunk_config(str(row["id"]), reload=False)
                await request_pjsip_reload()
            except Exception as compensation_exc:  # noqa: BLE001
                logger.critical(
                    "pjsip_config_compensation_failed trunk=%s err_type=%s",
                    str(row["id"])[:8],
                    type(compensation_exc).__name__,
                )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Asterisk did not confirm the SIP configuration reload; "
                    "the trunk change was not committed."
                ),
            ) from exc
        logger.warning(
            "pjsip_config_sync_deferred trunk=%s active=%s",
            str(row["id"])[:8] if row is not None else "?",
            active,
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
          AND deleted_at IS NULL
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

    async with acquire_with_tenant(
        db_pool,
        current_user.tenant_id,
        user_id=current_user.id,
        request_id=request.headers.get("x-request-id"),
    ) as conn:
        await apply_tenant_rls_context(
            conn,
            current_user.tenant_id,
            current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
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
                live_registration_status,
                live_status_detail,
                live_status_checked_at,
                created_at,
                updated_at
            FROM tenant_sip_trunks
            WHERE tenant_id = $1
              AND deleted_at IS NULL
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

    async with acquire_with_tenant(
        db_pool,
        current_user.tenant_id,
        user_id=current_user.id,
        request_id=request.headers.get("x-request-id"),
    ) as conn:
        await apply_tenant_rls_context(
            conn,
            current_user.tenant_id,
            current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
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
                    # Raw dict — the pool's jsonb codec (app.core.db) encodes
                    # via json.dumps on write; a pre-dumped string here would
                    # be double-encoded into a JSON string scalar.
                    canonical_payload["metadata"],
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
@_abandons_claim
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

    async with acquire_with_tenant(
        db_pool,
        current_user.tenant_id,
        user_id=current_user.id,
        request_id=request.headers.get("x-request-id"),
    ) as conn:
        await apply_tenant_rls_context(
            conn,
            current_user.tenant_id,
            current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
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
                raise _AbandonClaim(_problem(
                    request=request,
                    status_code=404,
                    title="Trunk Not Found",
                    detail="Requested trunk does not exist for tenant.",
                    type_suffix="trunk-not-found",
                ))

            existing_auth_user = existing["auth_username"]
            if payload.clear_auth and (
                "auth_username" in patch_payload or "auth_password" in patch_payload
            ):
                raise _AbandonClaim(_problem(
                    request=request,
                    status_code=400,
                    title="Invalid Authentication Patch",
                    detail="clear_auth cannot be combined with auth_username/auth_password fields.",
                    type_suffix="invalid-auth-patch",
                ))

            trunk_name = patch_payload.get("trunk_name", existing["trunk_name"])
            from app.domain.services.telephony.trunk_resolver import platform_default_trunk_name

            reserved = platform_default_trunk_name().strip().lower()
            previous_name = str(existing["trunk_name"]).strip().lower()
            proposed_name = str(trunk_name).strip().lower()
            if proposed_name != previous_name and reserved in {previous_name, proposed_name}:
                raise _AbandonClaim(_problem(request, 400, "Reserved Trunk Name", "The platform trunk name cannot be claimed or changed through tenant settings.", "reserved-trunk-name"))
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

            metadata = _coerce_jsonb(metadata) or {}
            if metadata.get("pool") != (_coerce_jsonb(existing["metadata"]) or {}).get("pool"):
                raise _AbandonClaim(_problem(request, 400, "Platform Metadata Reserved", "Shared-pool membership is managed by the platform operator.", "reserved-trunk-metadata"))
            if payload.clear_auth:
                metadata = {**metadata, "register": False}
            if metadata.get("register") and not (auth_username and auth_password_encrypted):
                raise _AbandonClaim(_problem(request, 400, "Invalid Registration", "REGISTER requires credentials. IP authentication uses register=false.", "invalid-registration"))

            if bool(auth_username) != bool(auth_password_encrypted):
                raise _AbandonClaim(_problem(
                    request=request,
                    status_code=400,
                    title="Invalid Authentication Configuration",
                    detail="auth_username and auth_password must be set or cleared together.",
                    type_suffix="invalid-auth-configuration",
                ))

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
                        live_registration_status = CASE
                            WHEN is_active THEN 'checking'
                            ELSE live_registration_status
                        END,
                        live_status_detail = CASE
                            WHEN is_active THEN 'Awaiting Asterisk proof after configuration change'
                            ELSE live_status_detail
                        END,
                        live_status_checked_at = CASE
                            WHEN is_active THEN NOW()
                            ELSE live_status_checked_at
                        END,
                        updated_by = $11,
                        updated_at = NOW()
                    WHERE tenant_id = $1
                      AND id = $2
                      AND deleted_at IS NULL
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
                        live_registration_status,
                        live_status_detail,
                        live_status_checked_at,
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
                    # Raw dict — see create-path comment above.
                    metadata,
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
            if not row:
                # Deleted by someone else between the read above and this write.
                raise _AbandonClaim(_problem(
                    request=request,
                    status_code=404,
                    title="Trunk Not Found",
                    detail="Requested trunk does not exist for tenant.",
                    type_suffix="trunk-not-found",
                ))

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
            # Phase B — an edit to an already-active trunk (host / auth /
            # caller-ID / register) must re-render its config so Asterisk
            # picks up the change on the next reload. Inactive trunks have no
            # file to update. Fail-soft.
            if row["is_active"]:
                await _sync_trunk_pjsip_config(
                    row,
                    active=True,
                    previous_row=existing,
                )
            return response_model


@_abandons_claim
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

    async with acquire_with_tenant(
        db_pool,
        current_user.tenant_id,
        user_id=current_user.id,
        request_id=request.headers.get("x-request-id"),
    ) as conn:
        await apply_tenant_rls_context(
            conn,
            current_user.tenant_id,
            current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
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

            # Activation is NO LONGER gated on the reachability probe. That probe
            # is unreliable (carriers that ignore OPTIONS; it can also throw inside
            # the sandboxed api service) and the gate created a trap: a trunk you
            # turned off couldn't be turned back on. The REAL verification now is
            # the real-time registration status (live_registration_status, refreshed
            # ~10s by the trunk-status updater): activate → config applied → the card
            # shows Registered / Rejected / Unregistered live. Deactivation was, and
            # remains, always allowed.

            existing = await _get_tenant_trunk(
                conn,
                current_user.tenant_id,
                trunk_id,
            )
            if not existing:
                raise _AbandonClaim(_problem(
                    request=request,
                    status_code=404,
                    title="Trunk Not Found",
                    detail="Requested trunk does not exist for tenant.",
                    type_suffix="trunk-not-found",
                ))

            row = await conn.fetchrow(
                """
                UPDATE tenant_sip_trunks
                SET is_active = $3,
                    live_registration_status = CASE WHEN $3 THEN 'checking' ELSE 'inactive' END,
                    live_status_detail = CASE
                        WHEN $3 THEN 'Awaiting Asterisk runtime proof'
                        ELSE NULL
                    END,
                    live_status_checked_at = NOW(),
                    updated_by = $4,
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND id = $2
                  AND deleted_at IS NULL
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
                    live_registration_status,
                    live_status_detail,
                    live_status_checked_at,
                    created_at,
                    updated_at
                """,
                current_user.tenant_id,
                trunk_id,
                active_state,
                current_user.id,
            )
            if not row:
                raise _AbandonClaim(_problem(
                    request=request,
                    status_code=404,
                    title="Trunk Not Found",
                    detail="Requested trunk does not exist for tenant.",
                    type_suffix="trunk-not-found",
                ))

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
            # Multi-active (2026-07-07): a tenant may run SEVERAL trunks at once
            # (removed the old single-active invariant that deactivated the
            # others on activate). Each active trunk keeps its own live Asterisk
            # config; the outbound resolver picks among a tenant's active trunks.
            # Phase B — sync THIS trunk's namespaced PJSIP config: activate →
            # render+write trunk-<id>.conf; deactivate → remove it. Fail-soft.
            await _sync_trunk_pjsip_config(
                row,
                active=active_state,
                previous_row=existing,
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


# --- POST /trunks/{id}/test: rate limit ---------------------------------
# Providers ban an IP after a few failed sign-ins (fail2ban), and that would also
# take down the tenant's live registration, so one line can be tested at most
# once every 10 seconds and 10 times in any 15 minutes. The 10-second gap reads
# last_tested_at (shared by every worker); the 15-minute budget lives in Redis
# when it is available and in this process otherwise.

_TEST_MIN_GAP_SECONDS = 10
_TEST_WINDOW_SECONDS = 15 * 60
_TEST_MAX_PER_WINDOW = 10
_test_history: dict[str, deque] = {}  # in-process fallback: key -> test times (oldest first)


def _clock() -> float:
    return time.time()


def _trunk_test_redis():
    try:
        container = get_container()
        return container.redis if container.is_initialized else None
    except Exception:  # noqa: BLE001 - no Redis is a supported configuration
        return None


async def _count_trunk_test(tenant_id: str, trunk_id: UUID) -> int:
    """Count one test against the line's 15-minute budget.

    Returns 0 when the test may run (it is now counted), otherwise the number of
    seconds until the oldest counted test ages out. Refused clicks are not counted.
    """
    now = _clock()
    cutoff = now - _TEST_WINDOW_SECONDS
    key = f"telephony:sip_trunk_test:{tenant_id}:{trunk_id}"
    redis = _trunk_test_redis()
    if redis is not None:
        try:
            await redis.zremrangebyscore(key, "-inf", cutoff)
            if await redis.zcard(key) >= _TEST_MAX_PER_WINDOW:
                oldest = await redis.zrange(key, 0, 0, withscores=True)
                return max(1, int(oldest[0][1] + _TEST_WINDOW_SECONDS - now) + 1) if oldest else _TEST_WINDOW_SECONDS
            await redis.zadd(key, {f"{now:.6f}:{uuid4().hex[:8]}": now})
            await redis.expire(key, _TEST_WINDOW_SECONDS + 60)
            return 0
        except Exception as exc:  # noqa: BLE001 - a Redis outage must not stop tests
            logger.warning("trunk_test_limit_redis_unavailable err_type=%s", type(exc).__name__)
    if len(_test_history) > 1024:  # forget lines nobody has tested for a window
        for stale in [k for k, v in _test_history.items() if not v or v[-1] <= cutoff]:
            del _test_history[stale]
    history = _test_history.setdefault(key, deque())
    while history and history[0] <= cutoff:
        history.popleft()
    if len(history) >= _TEST_MAX_PER_WINDOW:
        return max(1, int(history[0] + _TEST_WINDOW_SECONDS - now) + 1)
    history.append(now)
    return 0


def _test_refused(request: Request, *, title: str, detail: str, retry_after: int, type_suffix: str) -> JSONResponse:
    response = _problem(request=request, status_code=429, title=title, detail=detail, type_suffix=type_suffix)
    response.headers["Retry-After"] = str(retry_after)
    return response


@router.post("/trunks/{trunk_id}/test", response_model=SIPTrunkTestResponse)
async def test_sip_trunk(
    trunk_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Report a line's real state: address, reachability, credentials, registration.

    The first two checks are an address lookup and one OPTIONS (or a TCP/TLS
    connect). The third proves the saved username and password with one REGISTER
    round that cannot disturb the live registration (see trunk_probe), and is
    skipped when Asterisk already holds a fresh registration for the line. The
    fourth reads what the status updater last saw Asterisk do. The full result,
    steps included, is stored in last_test_result; the password is never part of
    it. A deleted line is a 404; testing too often is a 429.
    """
    from datetime import datetime, timezone

    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with acquire_with_tenant(
        db_pool,
        current_user.tenant_id,
        user_id=current_user.id,
        request_id=request.headers.get("x-request-id"),
    ) as conn:
        await apply_tenant_rls_context(
            conn,
            current_user.tenant_id,
            current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
        row = await conn.fetchrow(
            "SELECT sip_domain, port, transport, auth_username, auth_password_encrypted, metadata, "
            "is_active, live_registration_status, live_status_detail, live_status_checked_at, last_tested_at "
            "FROM tenant_sip_trunks "
            "WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
            trunk_id,
            current_user.tenant_id,
        )
    if not row:
        return _problem(
            request=request,
            status_code=404,
            title="Trunk Not Found",
            detail=f"No SIP trunk {trunk_id} for this tenant.",
            type_suffix="trunk-not-found",
        )

    last_tested_at = row["last_tested_at"]
    if last_tested_at is not None:
        if last_tested_at.tzinfo is None:
            last_tested_at = last_tested_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last_tested_at).total_seconds()
        if 0 <= age < _TEST_MIN_GAP_SECONDS:
            wait = max(1, int(_TEST_MIN_GAP_SECONDS - age) + 1)
            return _test_refused(
                request, title="Test Too Soon", retry_after=wait, type_suffix="trunk-test-too-soon",
                detail=f"This line was tested a moment ago. Wait {wait} seconds and try again.",
            )
    wait = await _count_trunk_test(str(current_user.tenant_id), trunk_id)
    if wait:
        return _test_refused(
            request, title="Too Many Tests", retry_after=wait, type_suffix="trunk-test-rate-limited",
            detail=(
                f"This line has already been tested {_TEST_MAX_PER_WINDOW} times in the last 15 minutes. "
                "Wait a few minutes before testing again — repeated sign-in attempts can get your server "
                "blocked by your provider."
            ),
        )

    password = None
    credentials_problem = None
    if row["auth_username"] and row["auth_password_encrypted"]:
        try:
            password = get_encryption_service().decrypt(row["auth_password_encrypted"])
        except Exception as exc:  # noqa: BLE001 - never echo the cause; it can name the key
            logger.warning(
                "trunk_test_password_unreadable trunk=%s err_type=%s", str(trunk_id)[:8], type(exc).__name__
            )
            credentials_problem = "The saved password couldn't be read. Re-enter the password and save the line."

    result = await run_trunk_test(
        host=row["sip_domain"],
        port=row["port"],
        transport=row["transport"],
        auth_username=row["auth_username"],
        auth_password=password,
        metadata=_coerce_jsonb(row["metadata"]) or {},
        is_active=bool(row["is_active"]),
        live_status=row["live_registration_status"],
        live_detail=row["live_status_detail"],
        live_checked_at=row["live_status_checked_at"],
        freshness_seconds=trunk_status_freshness_seconds(),
        credentials_problem=credentials_problem,
    )
    password = None
    tested_at = datetime.now(timezone.utc)

    async with acquire_with_tenant(
        db_pool,
        current_user.tenant_id,
        user_id=current_user.id,
        request_id=request.headers.get("x-request-id"),
    ) as conn:
        await apply_tenant_rls_context(
            conn,
            current_user.tenant_id,
            current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
        await conn.execute(
            """
            UPDATE tenant_sip_trunks
            SET last_tested_at = $1,
                last_test_result = $2::jsonb
            WHERE id = $3 AND tenant_id = $4 AND deleted_at IS NULL
            """,
            tested_at,
            # Raw dict — see create-path comment above.
            result,
            trunk_id,
            current_user.tenant_id,
        )

    return SIPTrunkTestResponse(
        ok=result["ok"],
        latency_ms=result["latency_ms"],
        transport=row["transport"],
        target=result["target"],
        error=result["error"],
        detail=result["detail"],
        tested_at=tested_at,
        sip_code=result["sip_code"],
        timeout_code=result["timeout_code"],
        steps=result["steps"],
        verdict=result["verdict"],
        status=result["status"],
    )


# ── Shared trunk pool: list available accounts + per-tenant assignment ──────
# The 4 Blaze accounts (150001-150004) are registered once as pool trunks
# (metadata.pool=true). A tenant is "allotted" one by storing a snapshot
# (id/endpoint/caller_id/label) on tenants.calling_rules.pool_trunk — the
# outbound resolver then dials on that pool account with no own trunk needed.

from pydantic import BaseModel  # noqa: E402


class PoolTrunkItem(BaseModel):
    id: str
    label: str
    caller_id: Optional[str] = None
    registration_status: Optional[str] = None
    runtime_ready: bool = False
    runtime_status_detail: str = "Asterisk runtime status is unavailable."


class PoolAssignmentBody(BaseModel):
    pool_trunk_id: Optional[UUID] = None  # null clears the assignment


class PoolAssignmentResponse(BaseModel):
    pool_trunk_id: Optional[str] = None
    label: Optional[str] = None
    caller_id: Optional[str] = None


async def _current_assignment_projection(db_pool, tenant_id: str, trunk_id: str):
    """Display the current assigned trunk, not a stale assignment-time number."""
    async with acquire_with_tenant(db_pool, tenant_id) as conn:
        return await conn.fetchrow(
            "SELECT trunk_name, metadata->>'caller_id' AS caller_id FROM tenant_sip_trunks "
            "WHERE id=$1::uuid AND tenant_id=$2::uuid AND deleted_at IS NULL",
            trunk_id,
            str(tenant_id),
        )


async def _fetch_pool_trunk(conn, pool_trunk_id: str, tenant_id: str):
    """Read and lock an active pool-labeled trunk owned by this tenant."""
    return await conn.fetchrow(
        """
        SELECT id, auth_username, metadata->>'caller_id' AS caller_id,
               live_registration_status, live_status_checked_at,
               live_status_detail, is_active, direction, metadata
        FROM tenant_sip_trunks
        WHERE id = $1::uuid
          AND tenant_id = $3::uuid
          AND deleted_at IS NULL
          AND is_active = TRUE
          AND direction IN ('outbound', 'both')
          AND metadata->>'pool' = 'true'
          AND live_registration_status IN ('registered', 'reachable')
          AND live_status_checked_at >= NOW() - ($2 * INTERVAL '1 second')
        FOR SHARE
        """,
        pool_trunk_id,
        trunk_status_freshness_seconds(),
        str(tenant_id),
    )


@router.get("/trunks/pool", response_model=list[PoolTrunkItem])
async def list_pool_trunks(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """List the shared-pool SIP accounts a tenant can be allotted."""
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    async with acquire_with_tenant(
        db_pool, current_user.tenant_id, user_id=current_user.id
    ) as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, auth_username, metadata->>'caller_id' AS caller_id,
                       live_registration_status, live_status_detail,
                       live_status_checked_at, is_active, direction, metadata
                FROM tenant_sip_trunks
                WHERE metadata->>'pool' = 'true' AND is_active = TRUE AND tenant_id=$1::uuid
                ORDER BY auth_username
                """, current_user.tenant_id,
            )
    result: list[PoolTrunkItem] = []
    for row in rows:
        runtime = evaluate_trunk_runtime(dict(row), require_inbound=False)
        outbound_direction = row["direction"] in {"outbound", "both"}
        result.append(
            PoolTrunkItem(
                id=str(row["id"]),
                label=row["auth_username"],
                caller_id=row["caller_id"],
                registration_status=row["live_registration_status"],
                runtime_ready=runtime.ready and outbound_direction,
                runtime_status_detail=(
                    runtime.detail
                    if outbound_direction
                    else "Shared trunk is not outbound-capable."
                ),
            )
        )
    return result


@router.get("/trunks/pool-assignment", response_model=PoolAssignmentResponse)
async def get_pool_assignment(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    async with acquire_with_tenant(
        db_pool, current_user.tenant_id, user_id=current_user.id
    ) as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id)
        raw = await conn.fetchval(
            "SELECT calling_rules->'pool_trunk' FROM tenants WHERE id = $1",
            current_user.tenant_id,
        )
    if not raw:
        return PoolAssignmentResponse()
    pt = raw if isinstance(raw, dict) else json.loads(raw)
    current = await _current_assignment_projection(db_pool, current_user.tenant_id, pt.get("id"))
    return PoolAssignmentResponse(
        pool_trunk_id=pt.get("id"),
        label=current["trunk_name"] if current else None,
        caller_id=current["caller_id"] if current else None,
    )


class OutboundRouteResponse(BaseModel):
    ready: bool
    reason_code: str
    reason: Optional[str] = None
    caller_id: Optional[str] = None
    trunk_id: Optional[str] = None
    trunk_name: Optional[str] = None


@router.get("/trunks/outbound-route", response_model=OutboundRouteResponse)
async def get_outbound_route(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """The number and line this tenant's outbound calls go out from now.

    Settings showed each trunk's stored caller ID, while the dialer picks the
    line and number by its own rules (pool account, newest own trunk, verified
    DID, calling-rules number), so the page could show a number no call used.
    This answers from the same read-only preflight as campaign launch and
    redial, for a campaign with no line of its own.
    """
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    from app.domain.services.telephony.outbound_readiness import evaluate_outbound_readiness

    tenant_id = str(current_user.tenant_id)
    try:
        async with acquire_with_tenant(db_pool, tenant_id, user_id=current_user.id) as conn:
            calling_rules = await conn.fetchval(
                "SELECT calling_rules FROM tenants WHERE id = $1::uuid", tenant_id
            )
        readiness = await evaluate_outbound_readiness(
            db_pool, tenant_id=tenant_id, campaign_id=None, campaign={}, calling_rules=calling_rules,
        )
        trunk_name = None
        if readiness.get("trunk_id"):
            async with acquire_with_tenant(db_pool, tenant_id, user_id=current_user.id) as conn:
                trunk_name = await conn.fetchval(
                    "SELECT trunk_name FROM tenant_sip_trunks WHERE id = $1::uuid AND tenant_id = $2::uuid",
                    readiness["trunk_id"], tenant_id,
                )
    except Exception as exc:  # noqa: BLE001 - a status read must not 500
        logger.warning("outbound_route_unavailable tenant=%s error=%s", tenant_id[:8], type(exc).__name__)
        return _problem(
            request=request,
            status_code=503,
            title="Outbound Route Unavailable",
            detail="The outbound line could not be checked. Retry shortly.",
            type_suffix="outbound-route-unavailable",
        )
    return OutboundRouteResponse(
        ready=bool(readiness.get("ready")),
        reason_code=str(readiness.get("reason_code") or ""),
        reason=readiness.get("reason"),
        caller_id=readiness.get("caller_id"),
        trunk_id=readiness.get("trunk_id"),
        trunk_name=trunk_name,
    )


@router.put("/trunks/pool-assignment", response_model=PoolAssignmentResponse)
async def set_pool_assignment(
    body: PoolAssignmentBody,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Allot a shared-pool account to this tenant (or clear it with null)."""
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    pid = str(body.pool_trunk_id) if body.pool_trunk_id is not None else None

    if pid is None:
        async with acquire_with_tenant(
            db_pool, current_user.tenant_id, user_id=current_user.id
        ) as conn:
            await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id)
            await conn.execute(
                "UPDATE tenants SET calling_rules = (COALESCE(calling_rules,'{}'::jsonb) - 'pool_trunk'), "
                "updated_at = NOW() WHERE id = $1",
                current_user.tenant_id,
            )
        return PoolAssignmentResponse()

    # Keep the trunk share lock through assignment so deletion cannot leave
    # behind a new dangling JSON reference after checking existing users.
    async with acquire_with_tenant(db_pool, current_user.tenant_id, user_id=current_user.id) as conn:
        pool = await _fetch_pool_trunk(conn, pid, current_user.tenant_id)
        if pool is None or not evaluate_trunk_runtime(dict(pool), require_inbound=False).ready:
            return _problem(
                request=request, status_code=400, title="Invalid Pool Account",
                detail="That shared-pool account is unavailable or its live status is stale.",
                type_suffix="invalid-pool-trunk",
            )
        snapshot = {
            "id": str(pool["id"]), "endpoint": f"trunk-{pool['id']}",
            "caller_id": pool["caller_id"], "label": pool["auth_username"],
        }
        await conn.execute(
            "UPDATE tenants SET calling_rules = COALESCE(calling_rules,'{}'::jsonb) "
            "|| jsonb_build_object('pool_trunk', $2::jsonb), updated_at = NOW() WHERE id = $1",
            current_user.tenant_id, snapshot,
        )
    return PoolAssignmentResponse(
        pool_trunk_id=snapshot["id"], label=snapshot["label"], caller_id=snapshot["caller_id"]
    )


# ── Per-CAMPAIGN trunk allotment ──────────────────────────────────────────
# Lets two campaigns of the same tenant dial out on different PBX accounts
# (different caller-IDs). Snapshot lives on campaigns.calling_config.trunk,
# same {"id","endpoint","caller_id","label"} shape as the tenant pool
# allotment; trunk_resolver._resolve_campaign_trunk gives it top precedence.


class CampaignTrunkBody(BaseModel):
    campaign_id: UUID
    trunk_id: Optional[UUID] = None  # null clears the assignment


class CampaignTrunkResponse(BaseModel):
    campaign_id: str
    trunk_id: Optional[str] = None
    label: Optional[str] = None
    caller_id: Optional[str] = None


_CAMPAIGN_ASSIGNMENT_DB_TIMEOUT_S = 5.0


def _campaign_assignment_boundary_problem(request: Request, campaign) -> Optional[JSONResponse]:
    """Translate an owned campaign snapshot into the public boundary contract."""
    if campaign is None:
        return _problem(
            request=request,
            status_code=404,
            title="Campaign Not Found",
            detail="No such campaign under this tenant.",
            type_suffix="campaign-not-found",
        )
    if str(campaign["direction"] or "").strip().lower() != "outbound":
        return _problem(
            request=request,
            status_code=409,
            title="Inbound Campaign Managed Separately",
            detail="SIP trunk assignment on this route is only valid for outbound campaigns.",
            type_suffix="inbound-campaign-managed-separately",
        )
    return None


async def _fetch_assignable_trunk(conn, trunk_id: str, tenant_id):
    """Read and lock an active trunk owned by this tenant."""
    return await conn.fetchrow(
        """
        SELECT id, trunk_name, auth_username,
               metadata->>'caller_id' AS caller_id,
               is_active, direction, metadata,
               live_registration_status, live_status_detail,
               live_status_checked_at
        FROM tenant_sip_trunks
        WHERE id = $1::uuid AND is_active = TRUE
          AND tenant_id = $2::uuid
          AND deleted_at IS NULL
        FOR SHARE
        """,
        trunk_id,
        str(tenant_id),
    )


@router.get("/trunks/campaign-assignment", response_model=CampaignTrunkResponse)
async def get_campaign_trunk_assignment(
    campaign_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    try:
        campaign_id = str(UUID(str(campaign_id)))
    except (TypeError, ValueError, AttributeError):
        return _problem(
            request=request,
            status_code=422,
            title="Invalid Campaign ID",
            detail="campaign_id must be a valid UUID.",
            type_suffix="invalid-campaign-id",
        )
    try:
        async with asyncio.timeout(_CAMPAIGN_ASSIGNMENT_DB_TIMEOUT_S):
            async with acquire_with_tenant(
                db_pool,
                current_user.tenant_id,
                user_id=current_user.id,
                timeout=_CAMPAIGN_ASSIGNMENT_DB_TIMEOUT_S,
            ) as conn:
                await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id)
                campaign = await conn.fetchrow(
                    "SELECT id, direction, calling_config->'trunk' AS trunk_config "
                    "FROM campaigns "
                    "WHERE id = $1::uuid AND tenant_id = $2::uuid",
                    campaign_id,
                    current_user.tenant_id,
                )
    except Exception as exc:  # noqa: BLE001 - absence cannot be inferred on DB failure
        logger.error("campaign_trunk_assignment_lookup_failed: %s", exc)
        return _problem(
            request=request,
            status_code=503,
            title="Campaign Lookup Unavailable",
            detail="Campaign ownership could not be verified. Retry later.",
            type_suffix="campaign-lookup-unavailable",
        )
    boundary_problem = _campaign_assignment_boundary_problem(request, campaign)
    if boundary_problem:
        return boundary_problem
    raw = campaign["trunk_config"]
    if not raw:
        return CampaignTrunkResponse(campaign_id=campaign_id)
    ct = raw if isinstance(raw, dict) else json.loads(raw)
    current = await _current_assignment_projection(db_pool, current_user.tenant_id, ct.get("id"))
    return CampaignTrunkResponse(
        campaign_id=campaign_id,
        trunk_id=ct.get("id"),
        label=current["trunk_name"] if current else None,
        caller_id=current["caller_id"] if current else None,
    )


@router.put("/trunks/campaign-assignment", response_model=CampaignTrunkResponse)
async def set_campaign_trunk_assignment(
    body: CampaignTrunkBody,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Allot a specific trunk to one campaign (or clear it with null).

    The campaign then dials on that trunk with its caller-ID, regardless of
    the tenant-level pool allotment or own-trunk resolution."""
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    campaign_id = str(body.campaign_id)
    tid = str(body.trunk_id) if body.trunk_id is not None else None

    try:
        # One tenant-scoped transaction holds row locks through mutation.
        # A pool label is not permission to use another tenant's carrier account.
        async with asyncio.timeout(_CAMPAIGN_ASSIGNMENT_DB_TIMEOUT_S):
            async with acquire_with_tenant(
                db_pool,
                current_user.tenant_id,
                user_id=current_user.id,
                timeout=_CAMPAIGN_ASSIGNMENT_DB_TIMEOUT_S,
            ) as conn:
                campaign = await conn.fetchrow(
                    "SELECT id, direction FROM campaigns "
                    "WHERE id = $1::uuid AND tenant_id = $2::uuid FOR UPDATE",
                    campaign_id,
                    current_user.tenant_id,
                )
                boundary_problem = _campaign_assignment_boundary_problem(request, campaign)
                if boundary_problem:
                    return boundary_problem

                if tid is None:
                    updated = await conn.execute(
                        "UPDATE campaigns SET calling_config = "
                        "(COALESCE(calling_config,'{}'::jsonb) - 'trunk'), "
                        "updated_at = NOW() "
                        "WHERE id = $1::uuid AND tenant_id = $2::uuid "
                        "AND direction = 'outbound'",
                        campaign_id,
                        current_user.tenant_id,
                    )
                    snapshot = None
                else:
                    trunk = await _fetch_assignable_trunk(conn, tid, current_user.tenant_id)
                    runtime = (
                        evaluate_trunk_runtime(dict(trunk), require_inbound=False)
                        if trunk is not None
                        else None
                    )
                    outbound_direction = bool(
                        trunk is not None and trunk["direction"] in {"outbound", "both"}
                    )
                    if trunk is None or not runtime or not runtime.ready or not outbound_direction:
                        runtime_detail = (
                            "The trunk is not outbound-capable."
                            if trunk is not None and not outbound_direction
                            else runtime.detail if runtime else "The trunk is unavailable."
                        )
                        return _problem(
                            request=request,
                            status_code=400,
                            title="Invalid Trunk",
                            detail=f"That trunk is not safe to assign: {runtime_detail}",
                            type_suffix="invalid-campaign-trunk",
                        )
                    from app.domain.services.telephony.trunk_resolver import (
                        env_default_endpoint,
                        platform_default_trunk_name,
                    )

                    is_platform_default = (
                        str(trunk["trunk_name"] or "").strip().lower()
                        == platform_default_trunk_name().strip().lower()
                    )
                    snapshot = {
                        "id": str(trunk["id"]),
                        "endpoint": (
                            env_default_endpoint()
                            if is_platform_default
                            else f"trunk-{trunk['id']}"
                        ),
                        "caller_id": trunk["caller_id"],
                        "label": trunk["trunk_name"] or trunk["auth_username"],
                    }
                    updated = await conn.execute(
                        "UPDATE campaigns SET calling_config = "
                        "COALESCE(calling_config,'{}'::jsonb) "
                        "|| jsonb_build_object('trunk', $3::jsonb), updated_at = NOW() "
                        "WHERE id = $1::uuid AND tenant_id = $2::uuid "
                        "AND direction = 'outbound'",
                        campaign_id,
                        current_user.tenant_id,
                        # Raw dict — see create-path comment above.
                        snapshot,
                    )
    except Exception as exc:  # noqa: BLE001 - DB uncertainty is not not-found
        logger.error("campaign_trunk_assignment_write_failed: %s", exc)
        return _problem(
            request=request,
            status_code=503,
            title="Campaign Assignment Unavailable",
            detail="Campaign ownership and direction could not be verified. Retry later.",
            type_suffix="campaign-assignment-unavailable",
        )

    if updated != "UPDATE 1":
        return _problem(
            request=request,
            status_code=409,
            title="Campaign Assignment Conflict",
            detail="The campaign changed while its trunk assignment was being updated.",
            type_suffix="campaign-assignment-conflict",
        )
    if snapshot is None:
        return CampaignTrunkResponse(campaign_id=campaign_id)
    return CampaignTrunkResponse(
        campaign_id=campaign_id,
        trunk_id=snapshot["id"],
        label=snapshot["label"],
        caller_id=snapshot["caller_id"],
    )


# ── Delete a line ───────────────────────────────────────────────────────────
# A tenant can always delete its own line (owner report 2026-10-14: the UI
# deactivated a line and then got 409 "Trunk In Use" for every line that had ever
# carried an inbound number). What pointed at the line is cleaned up, and the row
# is removed unless inbound call history still references it, in which case it is
# retired in place (deleted_at, migration 0070) and disappears from every API.

# A call row that never reached a terminal status must not block deletion forever.
_DELETE_OPEN_CALL_HOURS = 4

# Another tenant's allotment / campaign pin is invisible under RLS and is not ours
# to edit; this only answers "does anyone else still use the line". It runs with the
# RLS bypass on and returns a boolean, never their identities.
_SHARED_LINE_IN_USE_SQL = """
SELECT EXISTS (
    SELECT 1 FROM tenants
     WHERE id <> $2::uuid AND calling_rules->'pool_trunk'->>'id' = $1
    UNION ALL
    SELECT 1 FROM campaigns
     WHERE tenant_id <> $2::uuid AND calling_config->'trunk'->>'id' = $1
)
"""
_OPEN_CALL_SQL = """
SELECT EXISTS (
    SELECT 1 FROM calls
     WHERE tenant_id = $1::uuid
       AND (status IS NULL OR status <> ALL($2::text[]))
       AND created_at >= NOW() - make_interval(hours => $3::int)
)
"""
_UNPIN_CAMPAIGNS_SQL = """
UPDATE campaigns
   SET calling_config = (COALESCE(calling_config, '{}'::jsonb) - 'trunk'), updated_at = NOW()
 WHERE tenant_id = $1::uuid AND calling_config->'trunk'->>'id' = $2
RETURNING id
"""
_CLEAR_POOL_ASSIGNMENT_SQL = """
UPDATE tenants
   SET calling_rules = (COALESCE(calling_rules, '{}'::jsonb) - 'pool_trunk'), updated_at = NOW()
 WHERE id = $1::uuid AND calling_rules->'pool_trunk'->>'id' = $2
"""
_DELETE_ROUTE_POLICIES_SQL = """
DELETE FROM tenant_route_policies WHERE tenant_id = $1::uuid AND target_trunk_id = $2::uuid
"""
# An inbound campaign whose number is archived below can no longer answer calls, and
# its lifecycle refuses an archived assignment ("assignment_archived"), so leaving it
# "active" strands it. Pause it first, as an approved number reassignment does.
# Configs are locked before their assignments: the order every inbound path uses.
_PAUSE_INBOUND_CONFIGS_SQL = """
UPDATE inbound_campaign_configs
   SET status = 'paused', version = version + 1, updated_by = $3, updated_at = NOW()
 WHERE tenant_id = $1::uuid AND status = 'active'
   AND id IN (SELECT config_id FROM inbound_did_assignments
               WHERE tenant_id = $1::uuid AND sip_trunk_id = $2::uuid AND status <> 'archived')
RETURNING id, campaign_id
"""
_PAUSE_INBOUND_CAMPAIGNS_SQL = """
UPDATE campaigns
   SET status = 'paused', updated_at = NOW()
 WHERE tenant_id = $1::uuid AND id = ANY($2::uuid[]) AND direction = 'inbound'
   AND status IN ('running', 'active')
"""
_AUDIT_INBOUND_PAUSE_SQL = """
INSERT INTO inbound_audit_events
    (tenant_id, event_type, actor_id, actor_role, resource_type, resource_id, reason, metadata)
SELECT $1::uuid, 'inbound_campaign_paused', $2::uuid, $3, 'inbound_campaign', config_id,
       'Its phone line was deleted', jsonb_build_object('sip_trunk_id', $4::text)
  FROM unnest($5::uuid[]) AS config_id
"""
# History is archived, never deleted. An assignment that already ended earlier keeps
# its earlier end; valid_to must stay after valid_from (inbound_assignments_valid_window).
_ARCHIVE_INBOUND_ASSIGNMENTS_SQL = """
UPDATE inbound_did_assignments
   SET status = 'archived',
       valid_to = GREATEST(LEAST(COALESCE(valid_to, NOW()), NOW()), valid_from + INTERVAL '1 millisecond'),
       version = version + 1, updated_by = $3, updated_at = NOW()
 WHERE tenant_id = $1::uuid AND sip_trunk_id = $2::uuid AND status <> 'archived'
RETURNING id
"""
_INBOUND_HISTORY_REMAINS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM inbound_did_assignments WHERE tenant_id = $1::uuid AND sip_trunk_id = $2::uuid
)
"""
_RETIRE_LINE_SQL = """
UPDATE tenant_sip_trunks
   SET deleted_at = NOW(), is_active = FALSE, live_registration_status = 'inactive',
       live_status_detail = NULL, live_status_checked_at = NOW(),
       updated_by = $3, updated_at = NOW()
 WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
"""


async def _delete_blocker(
    conn, request: Request, *, tenant_id: str, trunk_id: UUID
) -> Optional[JSONResponse]:
    """Why this tenant's line still cannot be deleted right now; None when it can."""
    from app.domain.services.call_status import TERMINAL_CALL_STATUSES

    await conn.execute("SET LOCAL app.bypass_rls = 'on'")
    shared = await conn.fetchval(_SHARED_LINE_IN_USE_SQL, str(trunk_id), tenant_id)
    # Back under this tenant's own policies before anything else is read or written.
    await conn.execute("SET LOCAL app.bypass_rls = 'off'")
    if shared:
        return _problem(
            request,
            409,
            "Trunk In Use",
            "This shared line is still assigned to another account, so it can't be deleted from here. "
            "Ask the platform operator to release it first.",
            "trunk-in-use",
        )
    if await conn.fetchval(_OPEN_CALL_SQL, tenant_id, list(TERMINAL_CALL_STATUSES), _DELETE_OPEN_CALL_HOURS):
        return _problem(
            request,
            409,
            "Call In Progress",
            "A call on your account is still in progress. Delete this line once it has finished.",
            "trunk-call-in-progress",
        )
    return None


async def _restore_trunk_config_file(row) -> None:
    """Best effort after a refused delete: the row stays active, so its file goes back."""
    try:
        from app.infrastructure.telephony.pjsip_config_generator import (
            apply_trunk_config,
            request_pjsip_reload,
        )

        decrypted = (
            get_encryption_service().decrypt(row["auth_password_encrypted"])
            if row["auth_password_encrypted"] else None
        )
        await apply_trunk_config(row, decrypted_password=decrypted, reload=False)
        await request_pjsip_reload()
    except Exception as exc:  # noqa: BLE001
        logger.critical(
            "pjsip_config_compensation_failed trunk=%s err_type=%s", str(row["id"])[:8], type(exc).__name__
        )


@router.delete("/trunks/{trunk_id}")
async def delete_sip_trunk(
    trunk_id: UUID,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Delete one of the tenant's own lines. Switching it off first is not required.

    In one transaction: this tenant's campaign pins, pool allotment and route
    policies that point at the line are cleared, inbound numbers on it are
    archived, and the line is removed (``mode: "removed"``) or, when inbound call
    history still references it, retired in place (``mode: "archived"``). Refused
    only for the platform line (409), a shared line another account still uses
    (409), or an unfinished call of this account created in the last four hours
    (409). A missing or already-deleted line is a 404. The PJSIP file is removed
    first; if Asterisk does not confirm that in production the call fails (503)
    and nothing is changed.
    """
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem
    if not idempotency_key:
        return _problem(
            request,
            400,
            "Idempotency Key Required",
            "Mutating operations require Idempotency-Key header.",
            "idempotency-key-required",
        )
    operation = f"sip_trunks:delete:{trunk_id}"
    tenant_id = current_user.tenant_id
    async with acquire_with_tenant(
        db_pool, tenant_id, user_id=current_user.id
    ) as conn:
        async with conn.transaction():
            state, cached, code = await _claim_idempotency(
                conn,
                tenant_id=tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=_stable_hash({"trunk_id": str(trunk_id)}),
            )
            if state == "replay":
                return JSONResponse(status_code=code or 200, content=cached)
            if state != "new":
                return _problem(
                    request,
                    409,
                    "Idempotency Conflict",
                    "This request is already in progress or conflicts with an earlier request.",
                    "idempotency-conflict",
                )
            quota_problem = await _enforce_ws_i_quota(
                conn=conn,
                request=request,
                tenant_id=tenant_id,
                user_id=current_user.id,
                policy_scope="api_mutation",
                metric_key="sip_trunks:delete",
                request_id=request.headers.get("x-request-id"),
            )
            if quota_problem:
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=quota_problem,
                )
                return quota_problem
            # Holding the row lock until commit also makes an in-flight pin or pool
            # assignment (which share-locks the line) finish first, so the cleanup
            # below sees it and nothing is left pointing at a removed line.
            row = await conn.fetchrow(
                "SELECT * FROM tenant_sip_trunks "
                "WHERE tenant_id=$1 AND id=$2 AND deleted_at IS NULL FOR UPDATE",
                tenant_id,
                trunk_id,
            )
            problem = None
            if not row:
                problem = _problem(
                    request,
                    404,
                    "Trunk Not Found",
                    "Requested trunk does not exist for tenant.",
                    "trunk-not-found",
                )
            else:
                from app.domain.services.telephony.trunk_resolver import platform_default_trunk_name

                if row["trunk_name"].strip().lower() == platform_default_trunk_name().strip().lower():
                    problem = _problem(
                        request,
                        409,
                        "Managed Platform Trunk",
                        "This shared platform trunk is managed by the platform operator.",
                        "platform-trunk-managed",
                    )
                else:
                    problem = await _delete_blocker(conn, request, tenant_id=tenant_id, trunk_id=trunk_id)
            if problem:
                await _store_error_idempotency_result(
                    conn,
                    tenant_id=tenant_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    response=problem,
                )
                return problem

            from app.infrastructure.telephony.pjsip_config_generator import remove_trunk_config

            try:
                await remove_trunk_config(
                    str(trunk_id), require_reload=_requires_confirmed_pjsip_apply()
                )
            except Exception as exc:
                if row["is_active"]:
                    await _restore_trunk_config_file(row)
                raise HTTPException(
                    503,
                    "Asterisk configuration cleanup was not confirmed; the trunk was not deleted.",
                ) from exc

            unpinned = await conn.fetch(_UNPIN_CAMPAIGNS_SQL, tenant_id, str(trunk_id))
            await conn.execute(_CLEAR_POOL_ASSIGNMENT_SQL, tenant_id, str(trunk_id))
            await conn.execute(_DELETE_ROUTE_POLICIES_SQL, tenant_id, trunk_id)
            paused = await conn.fetch(
                _PAUSE_INBOUND_CONFIGS_SQL, tenant_id, trunk_id, current_user.id
            )
            if paused:
                await conn.execute(
                    _PAUSE_INBOUND_CAMPAIGNS_SQL, tenant_id, [r["campaign_id"] for r in paused]
                )
                await conn.execute(
                    _AUDIT_INBOUND_PAUSE_SQL, tenant_id, current_user.id,
                    getattr(current_user, "role", None), str(trunk_id), [r["id"] for r in paused],
                )
            archived = await conn.fetch(
                _ARCHIVE_INBOUND_ASSIGNMENTS_SQL, tenant_id, trunk_id, current_user.id
            )
            if await conn.fetchval(_INBOUND_HISTORY_REMAINS_SQL, tenant_id, trunk_id):
                await conn.execute(_RETIRE_LINE_SQL, tenant_id, trunk_id, current_user.id)
                mode = "archived"
            else:
                await conn.execute(
                    "DELETE FROM tenant_sip_trunks WHERE tenant_id=$1 AND id=$2",
                    tenant_id,
                    trunk_id,
                )
                mode = "removed"
            result = {
                "id": str(trunk_id),
                "deleted": True,
                "mode": mode,
                "archived_inbound_numbers": len(archived),
                "paused_inbound_campaigns": len(paused),
                "unpinned_campaigns": len(unpinned),
            }
            await _store_idempotency_result(
                conn,
                tenant_id=tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_body=result,
                status_code=200,
                resource_type="sip_trunk",
                resource_id=trunk_id,
            )
            return result

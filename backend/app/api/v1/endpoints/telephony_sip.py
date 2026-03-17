"""
Tenant SIP onboarding endpoints (Phase 2 / WS-F).

Scope in this iteration:
- Tenant-scoped SIP trunk CRUD-lite (list/create/update/activate/deactivate)
- Idempotency-key enforcement for mutating endpoints
- RFC 9457-style problem responses (application/problem+json)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict, model_validator

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.container import get_container
from app.core.tenant_rls import apply_tenant_rls_context
from app.domain.services.telephony_rate_limiter import (
    RateLimitAction,
    TelephonyRateLimiter,
)
from app.infrastructure.connectors.encryption import get_encryption_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telephony/sip", tags=["Telephony SIP"])

PROBLEM_BASE = "https://talky.ai/problems"
IDEMPOTENCY_WINDOW_SECONDS = 24 * 60 * 60


class SIPTransport(str, Enum):
    UDP = "udp"
    TCP = "tcp"
    TLS = "tls"


class SIPDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class SIPTrunkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trunk_name: str = Field(min_length=3, max_length=100)
    sip_domain: str = Field(min_length=3, max_length=255)
    port: int = Field(default=5060, ge=1, le=65535)
    transport: SIPTransport = SIPTransport.UDP
    direction: SIPDirection = SIPDirection.BOTH
    auth_username: Optional[str] = Field(default=None, max_length=255)
    auth_password: Optional[str] = Field(default=None, max_length=255)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_auth_pair(self) -> "SIPTrunkCreateRequest":
        if bool(self.auth_username) != bool(self.auth_password):
            raise ValueError("auth_username and auth_password must both be provided")
        return self


class SIPTrunkUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trunk_name: Optional[str] = Field(default=None, min_length=3, max_length=100)
    sip_domain: Optional[str] = Field(default=None, min_length=3, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    transport: Optional[SIPTransport] = None
    direction: Optional[SIPDirection] = None
    auth_username: Optional[str] = Field(default=None, max_length=255)
    auth_password: Optional[str] = Field(default=None, max_length=255)
    clear_auth: bool = False
    metadata: Optional[Dict[str, Any]] = None


class SIPTrunkResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    trunk_name: str
    sip_domain: str
    port: int
    transport: SIPTransport
    direction: SIPDirection
    is_active: bool
    auth_username: Optional[str]
    auth_configured: bool
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SIPRouteType(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CodecPolicyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: str = Field(min_length=3, max_length=100)
    allowed_codecs: List[str] = Field(default_factory=lambda: ["PCMU", "PCMA"], min_length=1)
    preferred_codec: str = Field(default="PCMU", min_length=1, max_length=20)
    sample_rate_hz: int = Field(default=8000)
    ptime_ms: int = Field(default=20)
    max_bitrate_kbps: Optional[int] = Field(default=None, gt=0)
    jitter_buffer_ms: int = Field(default=60, ge=0, le=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> "CodecPolicyCreateRequest":
        allowed = [_normalize_codec(c) for c in self.allowed_codecs]
        preferred = _normalize_codec(self.preferred_codec)
        if preferred not in allowed:
            raise ValueError("preferred_codec must be present in allowed_codecs")
        if self.sample_rate_hz not in {8000, 16000, 24000, 48000}:
            raise ValueError("sample_rate_hz must be one of 8000,16000,24000,48000")
        if self.ptime_ms not in {10, 20, 30, 40, 60}:
            raise ValueError("ptime_ms must be one of 10,20,30,40,60")
        self.allowed_codecs = allowed
        self.preferred_codec = preferred
        return self


class CodecPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: Optional[str] = Field(default=None, min_length=3, max_length=100)
    allowed_codecs: Optional[List[str]] = Field(default=None, min_length=1)
    preferred_codec: Optional[str] = Field(default=None, min_length=1, max_length=20)
    sample_rate_hz: Optional[int] = None
    ptime_ms: Optional[int] = None
    max_bitrate_kbps: Optional[int] = Field(default=None, gt=0)
    jitter_buffer_ms: Optional[int] = Field(default=None, ge=0, le=1000)
    metadata: Optional[Dict[str, Any]] = None


class CodecPolicyResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    policy_name: str
    allowed_codecs: List[str]
    preferred_codec: str
    sample_rate_hz: int
    ptime_ms: int
    max_bitrate_kbps: Optional[int]
    jitter_buffer_ms: int
    is_active: bool
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class RoutePolicyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: str = Field(min_length=3, max_length=100)
    route_type: SIPRouteType = SIPRouteType.OUTBOUND
    priority: int = Field(default=100, ge=1, le=10000)
    match_pattern: str = Field(min_length=1, max_length=512)
    target_trunk_id: UUID
    codec_policy_id: Optional[UUID] = None
    strip_digits: int = Field(default=0, ge=0, le=15)
    prepend_digits: Optional[str] = Field(default=None, max_length=20)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_pattern(self) -> "RoutePolicyCreateRequest":
        _validate_match_pattern(self.match_pattern)
        return self


class RoutePolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: Optional[str] = Field(default=None, min_length=3, max_length=100)
    route_type: Optional[SIPRouteType] = None
    priority: Optional[int] = Field(default=None, ge=1, le=10000)
    match_pattern: Optional[str] = Field(default=None, min_length=1, max_length=512)
    target_trunk_id: Optional[UUID] = None
    codec_policy_id: Optional[UUID] = None
    strip_digits: Optional[int] = Field(default=None, ge=0, le=15)
    prepend_digits: Optional[str] = Field(default=None, max_length=20)
    metadata: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class RoutePolicyResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    policy_name: str
    route_type: SIPRouteType
    priority: int
    match_pattern: str
    target_trunk_id: UUID
    codec_policy_id: Optional[UUID]
    strip_digits: int
    prepend_digits: Optional[str]
    is_active: bool
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TelephonyQuotaStatusItem(BaseModel):
    policy_id: Optional[str]
    policy_name: str
    policy_scope: str
    metric_key: str
    window_seconds: int
    warn_threshold: int
    throttle_threshold: int
    block_threshold: int
    block_duration_seconds: int
    throttle_retry_seconds: int
    counter_value: int
    window_ttl_seconds: int
    block_ttl_seconds: int
    current_action: str
    metadata: Dict[str, Any]


class TelephonyQuotaStatusResponse(BaseModel):
    tenant_id: str
    policy_scope: str
    metrics: List[TelephonyQuotaStatusItem]
    generated_at: datetime


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


def _normalize_codec(codec: str) -> str:
    return codec.strip().upper()


def _validate_match_pattern(pattern: str) -> None:
    # Keep regex validation strict to avoid runtime route-compile failures.
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid match_pattern regex: {exc}") from exc


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


def _row_to_response(row: asyncpg.Record) -> SIPTrunkResponse:
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
            created_at,
            updated_at
        FROM tenant_sip_trunks
        WHERE tenant_id = $1
          AND id = $2
        """,
        tenant_id,
        trunk_id,
    )


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
                created_at,
                updated_at
            FROM tenant_sip_trunks
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            """,
            current_user.tenant_id,
        )
    return [_row_to_response(row) for row in rows]


@router.get("/quotas/status", response_model=TelephonyQuotaStatusResponse)
async def get_telephony_quota_status(
    request: Request,
    policy_scope: str = "api_mutation",
    metric_key: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    limiter = _get_rate_limiter()
    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        statuses = await limiter.get_status(
            conn=conn,
            tenant_id=current_user.tenant_id,
            policy_scope=policy_scope,
            metric_key=metric_key,
        )

    return TelephonyQuotaStatusResponse(
        tenant_id=current_user.tenant_id,
        policy_scope=policy_scope,
        metrics=[TelephonyQuotaStatusItem(**status.to_dict()) for status in statuses],
        generated_at=datetime.now(timezone.utc),
    )


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
                    json.dumps(payload.metadata),
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
                    json.dumps(metadata),
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

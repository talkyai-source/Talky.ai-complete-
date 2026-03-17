"""
Day 9 tenant telephony concurrency endpoints.

Provides tenant-scoped lease acquisition/release/status for call and transfer
runtime controls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.container import get_container
from app.core.tenant_rls import apply_tenant_rls_context
from app.domain.services.telephony_concurrency_limiter import (
    LeaseKind,
    TelephonyConcurrencyLimiter,
)

router = APIRouter(prefix="/telephony/sip/runtime/concurrency", tags=["Telephony SIP Runtime"])

PROBLEM_BASE = "https://talky.ai/problems"


class LeaseAcquireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: UUID
    talklee_call_id: str = Field(min_length=3, max_length=64)
    lease_kind: LeaseKind
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LeaseAcquireResponse(BaseModel):
    accepted: bool
    lease_id: Optional[UUID]
    lease_kind: LeaseKind
    reason: str
    active_calls: int
    active_transfers: int
    max_active_calls: int
    max_transfer_inflight: int
    generated_at: datetime


class LeaseReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=2, max_length=64)


class LeaseReleaseResponse(BaseModel):
    released: bool
    lease_id: UUID
    reason: str
    generated_at: datetime


class LeaseHeartbeatResponse(BaseModel):
    updated: bool
    lease_id: UUID
    generated_at: datetime


class LeaseExpireResponse(BaseModel):
    tenant_id: str
    expired_count: int
    generated_at: datetime


class ConcurrencyStatusResponse(BaseModel):
    tenant_id: str
    active_calls: int
    active_transfers: int
    max_active_calls: int
    max_transfer_inflight: int
    lease_ttl_seconds: int
    heartbeat_grace_seconds: int
    policy_name: str
    policy_id: Optional[str]
    metadata: Dict[str, Any]
    generated_at: datetime


def _problem(
    request: Request,
    *,
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


def _get_concurrency_limiter() -> TelephonyConcurrencyLimiter:
    try:
        container = get_container()
        redis_client = container.redis if container.is_initialized else None
    except Exception:
        redis_client = None
    return TelephonyConcurrencyLimiter(redis_client=redis_client)


@router.post("/leases/acquire", response_model=LeaseAcquireResponse)
async def acquire_concurrency_lease(
    payload: LeaseAcquireRequest,
    request: Request,
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    limiter: TelephonyConcurrencyLimiter = Depends(_get_concurrency_limiter),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=x_request_id)
        async with conn.transaction():
            decision = await limiter.acquire_lease(
                conn,
                tenant_id=current_user.tenant_id,
                call_id=str(payload.call_id),
                talklee_call_id=payload.talklee_call_id,
                lease_kind=payload.lease_kind,
                request_id=x_request_id,
                created_by=current_user.id,
                metadata=payload.metadata,
            )

    return LeaseAcquireResponse(
        accepted=decision.accepted,
        lease_id=decision.lease_id,
        lease_kind=decision.lease_kind,
        reason=decision.reason,
        active_calls=decision.active_calls,
        active_transfers=decision.active_transfers,
        max_active_calls=decision.policy.max_active_calls,
        max_transfer_inflight=decision.policy.max_transfer_inflight,
        generated_at=datetime.now(timezone.utc),
    )


@router.post("/leases/{lease_id}/release", response_model=LeaseReleaseResponse)
async def release_concurrency_lease(
    lease_id: UUID,
    payload: LeaseReleaseRequest,
    request: Request,
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    limiter: TelephonyConcurrencyLimiter = Depends(_get_concurrency_limiter),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=x_request_id)
        async with conn.transaction():
            released = await limiter.release_lease(
                conn,
                tenant_id=current_user.tenant_id,
                lease_id=lease_id,
                reason=payload.reason,
                request_id=x_request_id,
                created_by=current_user.id,
            )
    if not released:
        return _problem(
            request,
            status_code=404,
            title="Lease Not Found",
            detail="No active lease found for release.",
            type_suffix="lease-not-found",
        )

    return LeaseReleaseResponse(
        released=True,
        lease_id=lease_id,
        reason=payload.reason,
        generated_at=datetime.now(timezone.utc),
    )


@router.post("/leases/{lease_id}/heartbeat", response_model=LeaseHeartbeatResponse)
async def heartbeat_concurrency_lease(
    lease_id: UUID,
    request: Request,
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    limiter: TelephonyConcurrencyLimiter = Depends(_get_concurrency_limiter),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=x_request_id)
        async with conn.transaction():
            updated = await limiter.heartbeat_lease(
                conn,
                tenant_id=current_user.tenant_id,
                lease_id=lease_id,
                request_id=x_request_id,
                created_by=current_user.id,
            )
    if not updated:
        return _problem(
            request,
            status_code=404,
            title="Lease Not Found",
            detail="No active lease found for heartbeat.",
            type_suffix="lease-not-found",
        )

    return LeaseHeartbeatResponse(
        updated=True,
        lease_id=lease_id,
        generated_at=datetime.now(timezone.utc),
    )


@router.post("/leases/expire", response_model=LeaseExpireResponse)
async def expire_stale_concurrency_leases(
    request: Request,
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    limiter: TelephonyConcurrencyLimiter = Depends(_get_concurrency_limiter),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=x_request_id)
        async with conn.transaction():
            expired = await limiter.expire_stale_leases(
                conn,
                tenant_id=current_user.tenant_id,
                request_id=x_request_id,
                created_by=current_user.id,
            )

    return LeaseExpireResponse(
        tenant_id=current_user.tenant_id,
        expired_count=expired,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/status", response_model=ConcurrencyStatusResponse)
async def get_concurrency_status(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    limiter: TelephonyConcurrencyLimiter = Depends(_get_concurrency_limiter),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        status = await limiter.get_status(conn, tenant_id=current_user.tenant_id)

    return ConcurrencyStatusResponse(
        tenant_id=status["tenant_id"],
        active_calls=int(status["active_calls"]),
        active_transfers=int(status["active_transfers"]),
        max_active_calls=int(status["max_active_calls"]),
        max_transfer_inflight=int(status["max_transfer_inflight"]),
        lease_ttl_seconds=int(status["lease_ttl_seconds"]),
        heartbeat_grace_seconds=int(status["heartbeat_grace_seconds"]),
        policy_name=str(status["policy_name"]),
        policy_id=status.get("policy_id"),
        metadata=status.get("metadata") or {},
        generated_at=datetime.now(timezone.utc),
    )

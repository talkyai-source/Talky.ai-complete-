"""Do-Not-Call list CRUD endpoints (T2.1).

Surface for tenants to manage their DNC list. CallGuard already
consults `dnc_entries` on every origination — adding a row here
immediately blocks that number for subsequent calls.

Endpoints
---------
  GET    /api/v1/dnc/              list current tenant's DNC entries
  POST   /api/v1/dnc/              add a number (manual_admin)
  POST   /api/v1/dnc/bulk-import   upload many numbers at once
  POST   /api/v1/dnc/caller-opt-out record an in-call opt-out (used
                                    by the voice pipeline)
  DELETE /api/v1/dnc/{entry_id}    remove an entry the tenant owns
  GET    /api/v1/dnc/check?e164=…  check if a number is on the list
                                   (includes global entries)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.domain.services.dnc_service import (
    KNOWN_SOURCES,
    SOURCE_BULK_IMPORT,
    SOURCE_CALLER_OPT_OUT,
    SOURCE_MANUAL_ADMIN,
    DNCEntry,
    DNCService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dnc", tags=["Do-Not-Call"])


# ────────────────────────────────────────────────────────────────────────────
# Request / response models
# ────────────────────────────────────────────────────────────────────────────

class DNCEntryResponse(BaseModel):
    id: str
    tenant_id: Optional[str] = None
    normalized_number: str
    source: str
    reason: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: Optional[str] = None


class DNCAddRequest(BaseModel):
    e164: str = Field(..., description="Phone number — any format; normalised server-side.")
    source: str = Field(default=SOURCE_MANUAL_ADMIN, max_length=64)
    reason: Optional[str] = Field(default=None, max_length=512)
    expires_at: Optional[datetime] = None


class DNCBulkImportRequest(BaseModel):
    numbers: List[str] = Field(..., min_length=1, max_length=10_000)
    source: str = Field(default=SOURCE_BULK_IMPORT, max_length=64)
    reason: Optional[str] = Field(default=None, max_length=512)


class DNCBulkImportResponse(BaseModel):
    accepted_count: int
    skipped_count: int
    invalid_count: int
    accepted: List[str]
    invalid: List[str]


class DNCCallerOptOutRequest(BaseModel):
    e164: str = Field(..., description="Number the caller asked us not to dial again.")
    call_id: Optional[str] = Field(default=None, description="Telephony call UUID for audit.")


class DNCCheckResponse(BaseModel):
    e164: str
    on_dnc: bool


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _require_tenant(user: CurrentUser) -> str:
    if not user.tenant_id:
        raise HTTPException(status_code=403, detail="Not associated with a tenant")
    return str(user.tenant_id)


def _to_response(entry: DNCEntry) -> DNCEntryResponse:
    return DNCEntryResponse(
        id=entry.id,
        tenant_id=entry.tenant_id,
        normalized_number=entry.normalized_number,
        source=entry.source,
        reason=entry.reason,
        expires_at=entry.expires_at.isoformat() if entry.expires_at else None,
        created_at=entry.created_at.isoformat() if entry.created_at else None,
    )


# ────────────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[DNCEntryResponse])
async def list_dnc_entries(
    include_global: bool = Query(default=False, description="Include cross-tenant DNC entries."),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[DNCEntryResponse]:
    tenant_id = _require_tenant(current_user)
    svc = DNCService(db_pool)
    entries = await svc.list_for_tenant(tenant_id, include_global=include_global, limit=limit)
    return [_to_response(e) for e in entries]


@router.post("/", response_model=DNCEntryResponse, status_code=201)
async def add_dnc_entry(
    payload: DNCAddRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> DNCEntryResponse:
    """Add a number to the tenant's DNC list. Idempotent — adding
    the same number twice just refreshes `updated_at`."""
    tenant_id = _require_tenant(current_user)
    if payload.source not in KNOWN_SOURCES:
        logger.info("dnc_add_unknown_source source=%s — accepting", payload.source)
    svc = DNCService(db_pool)
    try:
        entry = await svc.add(
            tenant_id=tenant_id,
            e164=payload.e164,
            source=payload.source,
            reason=payload.reason,
            expires_at=payload.expires_at,
            added_by=str(current_user.user_id) if current_user.user_id else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "dnc_entry_added tenant=%s number=%s source=%s",
        tenant_id, entry.normalized_number, entry.source,
    )
    return _to_response(entry)


@router.post("/bulk-import", response_model=DNCBulkImportResponse)
async def bulk_import(
    payload: DNCBulkImportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> DNCBulkImportResponse:
    tenant_id = _require_tenant(current_user)
    svc = DNCService(db_pool)
    result = await svc.bulk_import(
        tenant_id=tenant_id,
        numbers=payload.numbers,
        source=payload.source,
        reason=payload.reason,
    )
    logger.info(
        "dnc_bulk_import tenant=%s source=%s accepted=%d skipped=%d invalid=%d",
        tenant_id, payload.source,
        result["accepted_count"], result["skipped_count"], result["invalid_count"],
    )
    return DNCBulkImportResponse(**result)


@router.post("/caller-opt-out", response_model=DNCEntryResponse, status_code=201)
async def caller_opt_out(
    payload: DNCCallerOptOutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> DNCEntryResponse:
    """Record an in-call opt-out. Called by the voice pipeline when
    it detects a stop request (DTMF opt-out, "stop calling me"
    keyword, etc.). Permanent by default."""
    tenant_id = _require_tenant(current_user)
    svc = DNCService(db_pool)
    try:
        entry = await svc.add_caller_opt_out(
            tenant_id=tenant_id,
            e164=payload.e164,
            call_id=payload.call_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.warning(
        "dnc_caller_opt_out tenant=%s number=%s call=%s",
        tenant_id, entry.normalized_number, payload.call_id,
    )
    return _to_response(entry)


@router.get("/check", response_model=DNCCheckResponse)
async def check_dnc(
    e164: str = Query(..., description="Number to check (any format)."),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> DNCCheckResponse:
    """Pre-flight check used by the UI / campaign loader to flag DNC
    numbers BEFORE they hit CallGuard at origination time."""
    tenant_id = _require_tenant(current_user)
    svc = DNCService(db_pool)
    on_list = await svc.is_on_dnc(tenant_id=tenant_id, e164=e164)
    return DNCCheckResponse(e164=e164, on_dnc=on_list)


@router.delete("/{entry_id}", status_code=204, response_class=Response)
async def remove_dnc_entry(
    entry_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Remove a DNC entry. Only entries owned by the current tenant
    can be deleted — global (tenant_id=NULL) entries are managed by
    the operator via direct DB access."""
    tenant_id = _require_tenant(current_user)
    svc = DNCService(db_pool)
    removed = await svc.remove(tenant_id=tenant_id, entry_id=entry_id)
    logger.info(
        "dnc_entry_removed tenant=%s id=%s removed=%s",
        tenant_id, entry_id, removed,
    )
    return Response(status_code=204)

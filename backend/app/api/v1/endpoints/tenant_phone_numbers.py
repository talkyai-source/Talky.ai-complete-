"""Tenant phone-number (verified DID) CRUD + verification endpoints.

Used by the admin UI to:
  - register a number the tenant claims to own,
  - mark it verified once proof is received (SMS code, carrier API,
    letter of authorization, or manual admin override),
  - list / revoke numbers.

The enforcement layer in telephony_bridge.make_call gates outbound
origination on `status='verified'` here, so a row in this table is the
only thing that lets a tenant dial with a given caller_id.
"""
from __future__ import annotations

import logging
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.api.v1.dependencies import (
    CurrentUser,
    get_current_user,
    get_db_pool,
    require_admin_tenant,
    require_platform_admin,
)
from app.core.security.rbac import UserRole, normalize_role
from app.domain.services.phone_number_normalizer import (
    _STRICT_E164_RE,
    validate_e164_strict,
)
from app.domain.services.telephony.inbound_router import redact_did
from app.domain.models.tenant_phone_number import (
    TenantPhoneNumber,
    VerificationMethod,
)
from app.domain.services.tenant_phone_number_service import (
    TenantPhoneNumberError,
    TenantPhoneNumberService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant-phone-numbers", tags=["Tenant Phone Numbers"])


# ────────────────────────────────────────────────────────────────────────────
# Request / response models
# ────────────────────────────────────────────────────────────────────────────

# Kept as an alias so any existing importer of this name still sees the same
# pattern; the check itself now lives in the canonical normalizer module.
_E164_RE = _STRICT_E164_RE


class PhoneNumberRegisterRequest(BaseModel):
    """Admin registers a number the tenant claims to own."""
    e164: str = Field(..., description="Number in strict E.164 form, e.g. +14155551234")
    provider: str = Field(default="manual_admin", max_length=64)
    label: Optional[str] = Field(default=None, max_length=128)
    metadata: dict = Field(default_factory=dict)

    @field_validator("e164")
    @classmethod
    def _e164_format(cls, v: str) -> str:
        return validate_e164_strict(v)


class PhoneNumberVerifyRequest(BaseModel):
    """Privileged verification of proof reviewed outside this endpoint.

    Tenant users cannot use this model to attest their own ownership.  The
    proof reference identifies a server/provider challenge or reviewed LOA in
    the audit trail without persisting the proof itself in application logs.
    """
    method: VerificationMethod
    proof_reference: str = Field(
        ...,
        min_length=3,
        max_length=128,
        description="Opaque provider challenge, carrier case, or reviewed LOA reference.",
    )
    stir_shaken_token: Optional[str] = Field(
        default=None,
        description="Attestation token from the upstream provider. Required for production dialing.",
        max_length=1024,
    )
    notes: Optional[str] = Field(default=None, max_length=512)

    @field_validator("proof_reference")
    @classmethod
    def _proof_reference_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("proof_reference cannot be blank")
        return cleaned


# ────────────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────────────

def _require_tenant(current_user: CurrentUser) -> str:
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Not associated with a tenant")
    return str(current_user.tenant_id)


@router.get("/", response_model=list[TenantPhoneNumber])
async def list_phone_numbers(
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> list[TenantPhoneNumber]:
    """List every number registered under the current tenant."""
    tenant_id = _require_tenant(current_user)
    svc = TenantPhoneNumberService(db_pool)
    return await svc.list_for_tenant(tenant_id)


@router.post("/", response_model=TenantPhoneNumber, status_code=201)
async def register_phone_number(
    payload: PhoneNumberRegisterRequest,
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> TenantPhoneNumber:
    """Register a pending number. Not dial-able until `/verify`."""
    tenant_id = _require_tenant(current_user)
    svc = TenantPhoneNumberService(db_pool)
    row = await svc.create_pending(
        tenant_id=tenant_id,
        e164=payload.e164,
        provider=payload.provider,
        label=payload.label,
        metadata=payload.metadata,
        actor_id=current_user.id,
        actor_role=current_user.role,
    )
    logger.info(
        "tenant_phone_number_registered tenant=%s e164=%s provider=%s",
        tenant_id, redact_did(payload.e164), payload.provider,
    )
    return row


@router.post("/{did_id}/verify", response_model=TenantPhoneNumber)
async def verify_phone_number(
    did_id: str,
    payload: PhoneNumberVerifyRequest,
    target_tenant_id: Optional[str] = Query(default=None, alias="tenant_id"),
    current_user: CurrentUser = Depends(require_platform_admin),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> TenantPhoneNumber:
    """Privileged pending -> verified transition after independent proof.

    Tenant administrators may register a pending DID, but they cannot assert
    that SMS, carrier, LOA, or manual proof succeeded.  Until a durable
    server-side challenge verifier exists, every verification method is
    restricted to a platform administrator.
    """
    if normalize_role(current_user.role) != UserRole.PLATFORM_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="DID verification requires a platform administrator",
        )
    tenant_id = str(target_tenant_id or current_user.tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=422, detail="tenant_id is required")
    svc = TenantPhoneNumberService(db_pool)
    try:
        row = await svc.mark_verified(
            tenant_id=tenant_id,
            did_id=did_id,
            method=payload.method,
            verified_by=current_user.email or current_user.id,
            stir_shaken_token=payload.stir_shaken_token,
            proof_reference=payload.proof_reference,
            proof_notes=payload.notes,
            actor_id=current_user.id,
            actor_role=current_user.role,
        )
    except TenantPhoneNumberError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    logger.info(
        "tenant_phone_number_verified tenant=%s did=%s method=%s attestation_set=%s",
        tenant_id, did_id, payload.method.value, bool(payload.stir_shaken_token),
    )
    return row


@router.delete("/{did_id}", status_code=204, response_class=__import__("fastapi").Response)
async def revoke_phone_number(
    did_id: str,
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Revoke a number. The row is kept for audit; status is set to `revoked`
    so it can no longer be used as a caller_id."""
    from fastapi import Response
    tenant_id = _require_tenant(current_user)
    svc = TenantPhoneNumberService(db_pool)
    try:
        await svc.revoke(
            tenant_id=tenant_id,
            did_id=did_id,
            actor_id=current_user.id,
            actor_role=current_user.role,
        )
    except TenantPhoneNumberError as exc:
        code = 409 if "assignment" in str(exc).lower() else 404
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    logger.info("tenant_phone_number_revoked tenant=%s did=%s", tenant_id, did_id)
    return Response(status_code=204)

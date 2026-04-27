"""Tenant AI credential CRUD endpoints (T1.1).

Lets a tenant admin register their own API keys for each provider so
the call pipeline resolves per-tenant instead of falling back to the
shared env vars. Plaintext never leaves the wire in responses — only
the last four characters and a label.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.domain.services.credential_resolver import env_var_for_provider
from app.infrastructure.connectors.encryption import get_encryption_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant-ai-credentials", tags=["Tenant AI Credentials"])


# ────────────────────────────────────────────────────────────────────────────
# Request / response models
# ────────────────────────────────────────────────────────────────────────────

class CredentialCreateRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    api_key: str = Field(..., min_length=8, max_length=8192)
    credential_kind: str = Field(default="api_key", max_length=64)
    label: Optional[str] = Field(default=None, max_length=128)

    @field_validator("provider")
    @classmethod
    def _lc_provider(cls, v: str) -> str:
        v = v.strip().lower()
        # Warn (but don't reject) unknown providers — allows early
        # registration for providers we haven't wired factories for yet.
        if not env_var_for_provider(v):
            logger.info("tenant_credential_unknown_provider provider=%s", v)
        return v


class CredentialResponse(BaseModel):
    """Safe-to-expose representation. Never includes plaintext."""
    id: str
    tenant_id: str
    provider: str
    credential_kind: str
    last4: Optional[str] = None
    label: Optional[str] = None
    status: str
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _require_tenant(user: CurrentUser) -> str:
    if not user.tenant_id:
        raise HTTPException(status_code=403, detail="Not associated with a tenant")
    return str(user.tenant_id)


def _row_to_response(row) -> CredentialResponse:
    return CredentialResponse(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        provider=row["provider"],
        credential_kind=row["credential_kind"],
        last4=row["last4"],
        label=row["label"],
        status=row["status"],
        created_at=row["created_at"].isoformat() if row.get("created_at") else None,
        last_used_at=row["last_used_at"].isoformat() if row.get("last_used_at") else None,
    )


# ────────────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[CredentialResponse])
async def list_credentials(
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[CredentialResponse]:
    tenant_id = _require_tenant(current_user)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, tenant_id, provider, credential_kind, last4, label,
                   status, created_at, last_used_at
            FROM tenant_ai_credentials
            WHERE tenant_id = $1
            ORDER BY provider, created_at DESC
            """,
            tenant_id,
        )
    return [_row_to_response(r) for r in rows]


@router.post("/", response_model=CredentialResponse, status_code=201)
async def create_credential(
    payload: CredentialCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> CredentialResponse:
    """Register (or rotate) a tenant's API key for a provider.

    Idempotent on (tenant_id, provider, credential_kind): a second POST
    disables the existing active row (audit-preserving) and inserts the
    new one."""
    tenant_id = _require_tenant(current_user)
    encryption = get_encryption_service()
    encrypted = encryption.encrypt(payload.api_key)
    last4 = payload.api_key[-4:] if len(payload.api_key) >= 4 else None

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Disable any existing active row for this tuple.
            await conn.execute(
                """
                UPDATE tenant_ai_credentials
                SET status = 'disabled', rotated_at = NOW()
                WHERE tenant_id = $1 AND provider = $2
                  AND credential_kind = $3 AND status = 'active'
                """,
                tenant_id,
                payload.provider,
                payload.credential_kind,
            )
            # Insert the new active row.
            row = await conn.fetchrow(
                """
                INSERT INTO tenant_ai_credentials
                    (tenant_id, provider, credential_kind, encrypted_key,
                     last4, label, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'active')
                RETURNING id, tenant_id, provider, credential_kind, last4, label,
                          status, created_at, last_used_at
                """,
                tenant_id,
                payload.provider,
                payload.credential_kind,
                encrypted,
                last4,
                payload.label,
            )

    logger.info(
        "tenant_credential_created tenant=%s provider=%s kind=%s",
        tenant_id, payload.provider, payload.credential_kind,
    )
    return _row_to_response(row)


@router.delete("/{credential_id}", status_code=204, response_class=Response)
async def disable_credential(
    credential_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Disable a credential. Row kept for audit; resolver ignores
    disabled rows."""
    tenant_id = _require_tenant(current_user)
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE tenant_ai_credentials
            SET status = 'disabled'
            WHERE tenant_id = $1 AND id = $2
            """,
            tenant_id,
            credential_id,
        )
    logger.info("tenant_credential_disabled tenant=%s id=%s", tenant_id, credential_id)
    return Response(status_code=204)

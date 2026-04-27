"""DB access for tenant_phone_numbers (verified DIDs).

Thin wrapper around the `tenant_phone_numbers` table. Keeps SQL out of
the endpoint layer so enforcement and admin CRUD share one path.

Ownership check is the hot path — called once per outbound origination —
so it uses the covering partial index on (tenant_id, e164) WHERE status='verified'.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from app.domain.models.tenant_phone_number import (
    PhoneNumberStatus,
    TenantPhoneNumber,
    VerificationMethod,
)

logger = logging.getLogger(__name__)


class TenantPhoneNumberError(Exception):
    """Base exception for DID operations."""


class NumberNotOwnedError(TenantPhoneNumberError):
    """Raised when a tenant tries to use a caller_id they have not
    verified. Pre-dials only — once a call is in-flight, origination
    has already succeeded."""


class TenantPhoneNumberService:
    """Persistence for verified DIDs."""

    def __init__(self, db_pool: Any):
        # asyncpg pool.
        self._db_pool = db_pool

    # ──────────────────────────────────────────────────────────────────
    # Enforcement (hot path)
    # ──────────────────────────────────────────────────────────────────

    async def is_verified_for_tenant(
        self,
        tenant_id: str,
        e164: str,
        *,
        require_attestation: bool = False,
    ) -> bool:
        """True iff the tenant owns this number AND it is in `verified`
        status. When `require_attestation=True`, also requires a non-NULL
        `stir_shaken_token` — set this in production so un-attested
        numbers (test-only) cannot dial real carriers.

        Safe to call on a cold DB: falls through to False on any error so
        origination gets a clean 403 instead of a 500.
        """
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT status, stir_shaken_token
                    FROM tenant_phone_numbers
                    WHERE tenant_id = $1 AND e164 = $2
                    LIMIT 1
                    """,
                    tenant_id,
                    e164,
                )
        except Exception as exc:
            logger.error(
                "tenant_phone_number_lookup_failed tenant=%s e164=%s err=%s",
                tenant_id, e164, exc,
            )
            return False

        if row is None:
            return False
        if row["status"] != PhoneNumberStatus.VERIFIED.value:
            return False
        if require_attestation and not row["stir_shaken_token"]:
            return False
        return True

    # ──────────────────────────────────────────────────────────────────
    # CRUD (admin path)
    # ──────────────────────────────────────────────────────────────────

    async def list_for_tenant(self, tenant_id: str) -> list[TenantPhoneNumber]:
        async with self._db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM tenant_phone_numbers
                WHERE tenant_id = $1
                ORDER BY created_at DESC
                """,
                tenant_id,
            )
        return [_row_to_model(r) for r in rows]

    async def get(self, tenant_id: str, did_id: str) -> Optional[TenantPhoneNumber]:
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tenant_phone_numbers WHERE tenant_id=$1 AND id=$2",
                tenant_id,
                did_id,
            )
        return _row_to_model(row) if row else None

    async def create_pending(
        self,
        *,
        tenant_id: str,
        e164: str,
        provider: str = "manual_admin",
        label: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TenantPhoneNumber:
        """Insert a new number in `pending_verification` state. Idempotent
        on (tenant_id, e164) — returns the existing row if it already
        exists (admins often re-submit the same number)."""
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tenant_phone_numbers
                    (tenant_id, e164, provider, label, metadata, status)
                VALUES ($1, $2, $3, $4, $5::jsonb, 'pending_verification')
                ON CONFLICT (tenant_id, e164) DO UPDATE
                    SET label = COALESCE(EXCLUDED.label, tenant_phone_numbers.label),
                        metadata = tenant_phone_numbers.metadata || EXCLUDED.metadata
                RETURNING *
                """,
                tenant_id,
                e164,
                provider,
                label,
                __import__("json").dumps(metadata or {}),
            )
        model = _row_to_model(row)
        assert model is not None
        return model

    async def mark_verified(
        self,
        *,
        tenant_id: str,
        did_id: str,
        method: VerificationMethod,
        verified_by: Optional[str],
        stir_shaken_token: Optional[str] = None,
    ) -> TenantPhoneNumber:
        """Transition pending → verified. No-op if already verified."""
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE tenant_phone_numbers
                SET status = 'verified',
                    verification_method = $3,
                    verified_by = $4,
                    verified_at = NOW(),
                    stir_shaken_token = COALESCE($5, stir_shaken_token)
                WHERE tenant_id = $1 AND id = $2
                RETURNING *
                """,
                tenant_id,
                did_id,
                method.value,
                verified_by,
                stir_shaken_token,
            )
        if row is None:
            raise TenantPhoneNumberError(f"DID {did_id} not found for tenant {tenant_id}")
        return _row_to_model(row)  # type: ignore[return-value]

    async def revoke(self, *, tenant_id: str, did_id: str) -> None:
        """Move to `revoked`. Kept for audit — row is not deleted."""
        async with self._db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tenant_phone_numbers
                SET status = 'revoked'
                WHERE tenant_id = $1 AND id = $2
                """,
                tenant_id,
                did_id,
            )


def _row_to_model(row: Any) -> Optional[TenantPhoneNumber]:
    if row is None:
        return None
    data = dict(row)
    # asyncpg returns JSONB as dict already; be defensive.
    md = data.get("metadata")
    if isinstance(md, str):
        import json as _json
        try:
            data["metadata"] = _json.loads(md)
        except Exception:
            data["metadata"] = {}
    # Cast UUIDs to strings for the Pydantic model.
    for key in ("id", "tenant_id"):
        if key in data and data[key] is not None:
            data[key] = str(data[key])
    return TenantPhoneNumber(**data)

"""DB access for tenant_phone_numbers (verified DIDs).

Thin wrapper around the `tenant_phone_numbers` table. Keeps SQL out of
the endpoint layer so enforcement and admin CRUD share one path.

Ownership check is the hot path — called once per outbound origination —
so it uses the covering partial index on (tenant_id, e164) WHERE status='verified'.
"""
from __future__ import annotations

import logging
import json
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
        # tenant_phone_numbers has an RLS policy that filters on
        # `current_setting('app.current_tenant_id')`. The bridge endpoint that
        # calls this isn't running under TenantMiddleware (the dialer worker
        # hits it with a query-param tenant_id and no JWT), so a raw
        # pool.acquire() would return zero rows and every caller_id appears
        # unverified. `acquire_with_tenant()` sets the GUC for the scope of
        # this query — see app/core/db_utils.py.
        from app.core.db_utils import acquire_with_tenant

        # NORMALISE BEFORE COMPARING (2026-08-06).
        #
        # The query is an exact string match, and registration
        # (`tenant_phone_numbers._e164_format`) enforces a leading '+', so the
        # stored side is always true E.164. The CALLER side was not normalised,
        # and production shipped `DEFAULT_CALLER_ID=17789249977` — the same
        # number WITHOUT the '+'. `'17789249977' = '+17789249977'` is false, so
        # every campaign that fell back to that default was told its caller ID
        # was unverified and the dial was refused with `caller_id_not_verified`.
        #
        # That is a one-character difference between "the dialer works" and
        # "this tenant can never place a call". It accounted for the entire
        # blocked state of newly-signed-up tenants, whose campaigns carry no
        # explicit `calling_config.caller_id` and therefore always take the
        # default path (`dialer_worker.py:878`).
        #
        # Normalising here rather than at the one call site fixes every caller
        # at once and is strictly narrowing: a non-phone value like the
        # Asterisk extension "1001" normalises to something that still matches
        # no row, so it stays unverified exactly as before.
        from app.domain.services.dnc_service import normalize_e164

        lookup_e164 = normalize_e164(e164) or e164

        try:
            async with acquire_with_tenant(self._db_pool, str(tenant_id)) as conn:
                row = await conn.fetchrow(
                    """
                    SELECT status, stir_shaken_token
                    FROM tenant_phone_numbers
                    WHERE tenant_id = $1 AND e164 = $2
                    LIMIT 1
                    """,
                    tenant_id,
                    lookup_e164,
                )
        except ValueError:
            # acquire_with_tenant raises ValueError for non-UUID tenant ids.
            return False
        except Exception as exc:
            logger.error(
                "tenant_phone_number_lookup_failed tenant=%s e164=%s err=%s",
                tenant_id, lookup_e164, exc,
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
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._db_pool, str(tenant_id)) as conn:
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
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._db_pool, str(tenant_id)) as conn:
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
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None,
    ) -> TenantPhoneNumber:
        """Insert a new number in `pending_verification` state. Idempotent
        on (tenant_id, e164) — returns the existing row if it already
        exists (admins often re-submit the same number)."""
        from app.core.db_utils import acquire_with_tenant
        from app.domain.services.telephony.inbound_router import redact_did

        async with acquire_with_tenant(self._db_pool, str(tenant_id)) as conn:
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
            if actor_id:
                await conn.execute(
                    """
                    INSERT INTO inbound_audit_events (
                        tenant_id,event_type,actor_id,actor_role,resource_type,
                        resource_id,after_state,metadata
                    ) VALUES (
                        $1,'tenant_phone_number_registered',$2,$3,
                        'tenant_phone_number',$4,$5::jsonb,$6::jsonb
                    )
                    """,
                    tenant_id,
                    actor_id,
                    actor_role,
                    row["id"],
                    json.dumps({"status": row["status"], "provider": row["provider"]}),
                    json.dumps({"did_ref": redact_did(row["e164"])}),
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
        proof_reference: str,
        stir_shaken_token: Optional[str] = None,
        proof_notes: Optional[str] = None,
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None,
    ) -> TenantPhoneNumber:
        """Transition pending -> verified after privileged proof review."""
        from app.core.db_utils import acquire_with_tenant
        from app.core.security.rbac import UserRole, normalize_role
        from app.domain.services.telephony.inbound_router import redact_did

        if normalize_role(actor_role or "") != UserRole.PLATFORM_ADMIN:
            raise TenantPhoneNumberError(
                "DID verification requires a platform administrator"
            )
        proof_ref = str(proof_reference or "").strip()
        if not proof_ref:
            raise TenantPhoneNumberError("A proof reference is required")

        async with acquire_with_tenant(self._db_pool, str(tenant_id)) as conn:
            before = await conn.fetchrow(
                "SELECT * FROM tenant_phone_numbers WHERE tenant_id=$1 AND id=$2 FOR UPDATE",
                tenant_id,
                did_id,
            )
            if before is None:
                raise TenantPhoneNumberError(f"DID {did_id} not found for tenant {tenant_id}")
            if before["status"] == "revoked":
                raise TenantPhoneNumberError("A revoked DID cannot be self-restored; register new proof")
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
            if actor_id:
                await conn.execute(
                    """
                    INSERT INTO inbound_audit_events (
                        tenant_id,event_type,actor_id,actor_role,resource_type,
                        resource_id,before_state,after_state,metadata
                    ) VALUES (
                        $1,'tenant_phone_number_verified',$2,$3,
                        'tenant_phone_number',$4,$5::jsonb,$6::jsonb,$7::jsonb
                    )
                    """,
                    tenant_id,
                    actor_id,
                    actor_role,
                    row["id"],
                    json.dumps({"status": before["status"]}),
                    json.dumps(
                        {
                            "status": row["status"],
                            "verification_method": row["verification_method"],
                        }
                    ),
                    json.dumps(
                        {
                            "did_ref": redact_did(row["e164"]),
                            "proof_reference": proof_ref,
                            "proof_notes_present": bool(str(proof_notes or "").strip()),
                            "attestation_set": bool(stir_shaken_token),
                        }
                    ),
                )
        return _row_to_model(row)  # type: ignore[return-value]

    async def revoke(
        self,
        *,
        tenant_id: str,
        did_id: str,
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None,
    ) -> None:
        """Move to `revoked`. Kept for audit — row is not deleted."""
        from app.core.db_utils import acquire_with_tenant
        from app.domain.services.telephony.inbound_router import redact_did

        async with acquire_with_tenant(self._db_pool, str(tenant_id)) as conn:
            before = await conn.fetchrow(
                "SELECT * FROM tenant_phone_numbers WHERE tenant_id=$1 AND id=$2 FOR UPDATE",
                tenant_id,
                did_id,
            )
            if not before:
                raise TenantPhoneNumberError(f"DID {did_id} not found for tenant {tenant_id}")
            assigned = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM inbound_did_assignments
                    WHERE tenant_id=$1 AND phone_number_id=$2 AND status <> 'archived'
                )
                """,
                tenant_id,
                did_id,
            )
            if assigned:
                raise TenantPhoneNumberError(
                    "Archive the inbound DID assignment before revoking this number"
                )
            row = await conn.fetchrow(
                """
                UPDATE tenant_phone_numbers
                SET status = 'revoked', updated_at=NOW()
                WHERE tenant_id = $1 AND id = $2
                RETURNING *
                """,
                tenant_id,
                did_id,
            )
            if actor_id:
                await conn.execute(
                    """
                    INSERT INTO inbound_audit_events (
                        tenant_id,event_type,actor_id,actor_role,resource_type,
                        resource_id,before_state,after_state,metadata
                    ) VALUES (
                        $1,'tenant_phone_number_revoked',$2,$3,
                        'tenant_phone_number',$4,$5::jsonb,$6::jsonb,$7::jsonb
                    )
                    """,
                    tenant_id,
                    actor_id,
                    actor_role,
                    row["id"],
                    json.dumps({"status": before["status"]}),
                    json.dumps({"status": row["status"]}),
                    json.dumps({"did_ref": redact_did(row["e164"])}),
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

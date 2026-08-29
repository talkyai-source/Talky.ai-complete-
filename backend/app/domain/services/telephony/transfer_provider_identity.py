"""Durable provider-channel identity for supervised inbound transfer legs.

Asterisk normally honours the ``channelId`` supplied to ARI create.  Some
deployments can nevertheless return a different authoritative channel ID.  A
live transfer may use that replacement only after this module atomically binds
it to the exact tenant, parent call, and pre-created transfer child.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.core.db_utils import acquire_with_tenant


_ASTERISK_CHANNEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")


class TransferProviderIdentityError(RuntimeError):
    """A fail-closed provider identity validation or persistence failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PersistedTransferProviderIdentity:
    tenant_id: str
    call_id: str
    leg_id: str
    original_provider_leg_id: str
    provider_leg_id: str
    rebound: bool


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _required(value: Any, *, code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise TransferProviderIdentityError(code)
    return normalized


def _provider_id(value: Any, *, code: str) -> str:
    raw = str(value or "")
    normalized = raw.strip()
    if raw != normalized or not _ASTERISK_CHANNEL_ID.fullmatch(normalized):
        raise TransferProviderIdentityError(code)
    return normalized


def _identity_from_row(
    row: Any,
    *,
    tenant_id: str,
    expected_original_provider_leg_id: str,
) -> PersistedTransferProviderIdentity:
    metadata = _json_object(row.get("metadata"))
    current = _provider_id(
        row.get("provider_leg_id"),
        code="transfer_provider_identity_invalid_durable_id",
    )
    original = str(metadata.get("provider_leg_id_original") or current).strip()
    rebound = current != expected_original_provider_leg_id
    if rebound and (
        metadata.get("provider_leg_id_rebound") is not True
        or original != expected_original_provider_leg_id
        or str(metadata.get("provider_leg_id_authoritative") or "").strip()
        != current
    ):
        raise TransferProviderIdentityError(
            "transfer_provider_identity_unproved_rebind"
        )
    if not rebound and original != expected_original_provider_leg_id:
        raise TransferProviderIdentityError(
            "transfer_provider_identity_original_mismatch"
        )
    return PersistedTransferProviderIdentity(
        tenant_id=tenant_id,
        call_id=str(row["call_id"]),
        leg_id=str(row["id"]),
        original_provider_leg_id=original,
        provider_leg_id=current,
        rebound=rebound,
    )


async def persist_asterisk_transfer_provider_identity(
    pool: Any,
    *,
    tenant_id: str,
    durable_call_id: str,
    parent_provider_call_id: str,
    planned_provider_leg_id: str,
    actual_provider_leg_id: str,
) -> PersistedTransferProviderIdentity:
    """Rebind one pre-created child to Asterisk's returned channel ID.

    The exact tenant, durable parent, provider parent, and original child ID
    must all agree.  Provider IDs already used by another call/leg, parent IDs,
    and previous alias identities are rejected.  The event and child update
    commit in the same transaction; callers must not dial or publish success
    before this function returns.
    """

    tenant = _required(tenant_id, code="transfer_provider_identity_tenant_missing")
    call_id = _required(
        durable_call_id,
        code="transfer_provider_identity_parent_missing",
    )
    parent_provider = _provider_id(
        parent_provider_call_id,
        code="transfer_provider_identity_invalid_parent_id",
    )
    planned = _provider_id(
        planned_provider_leg_id,
        code="transfer_provider_identity_invalid_planned_id",
    )
    actual = _provider_id(
        actual_provider_leg_id,
        code="transfer_provider_identity_invalid_actual_id",
    )
    if actual == planned:
        return await load_persisted_transfer_provider_identity(
            pool,
            tenant_id=tenant,
            durable_call_id=call_id,
            leg_id=None,
            expected_original_provider_leg_id=planned,
            parent_provider_call_id=parent_provider,
        )
    if actual == parent_provider:
        raise TransferProviderIdentityError("transfer_provider_identity_parent_alias")

    async with acquire_with_tenant(pool, None) as conn:
        parent = await conn.fetchrow(
            """
            SELECT id, tenant_id, talklee_call_id, status,
                   processing_status, billing_status, ended_at
            FROM calls
            WHERE id=$1::uuid AND tenant_id=$2::uuid
              AND (provider_call_id=$3 OR external_call_uuid=$3)
            FOR UPDATE
            """,
            call_id,
            tenant,
            parent_provider,
        )
        if not parent:
            raise TransferProviderIdentityError(
                "transfer_provider_identity_parent_scope_mismatch"
            )
        if (
            parent.get("ended_at") is not None
            or str(parent.get("processing_status") or "") != "active"
            or str(parent.get("billing_status") or "") != "reserved"
            or str(parent.get("status") or "") == "termination_pending"
        ):
            raise TransferProviderIdentityError(
                "transfer_provider_identity_parent_not_active"
            )

        rows = await conn.fetch(
            """
            SELECT id, call_id, provider, provider_leg_id, status,
                   billing_status, reserved_seconds, metadata, created_at
            FROM call_legs
            WHERE call_id=$1::uuid AND leg_type='transfer'
              AND (
                  provider_leg_id=$2
                  OR provider_leg_id=$3
                  OR metadata->>'provider_leg_id_original'=$2
              )
            ORDER BY created_at, id
            FOR UPDATE
            """,
            call_id,
            planned,
            actual,
        )
        if not rows:
            raise TransferProviderIdentityError(
                "transfer_provider_identity_leg_missing"
            )
        if len(rows) != 1:
            raise TransferProviderIdentityError(
                "transfer_provider_identity_child_collision"
            )
        leg = rows[0]
        metadata = _json_object(leg.get("metadata"))
        current = str(leg.get("provider_leg_id") or "").strip()

        # Exact replay after commit is harmless; a different second alias for
        # the same planned ID is never accepted.
        if current == actual:
            identity = _identity_from_row(
                leg,
                tenant_id=tenant,
                expected_original_provider_leg_id=planned,
            )
            if not identity.rebound:
                raise TransferProviderIdentityError(
                    "transfer_provider_identity_replay_conflict"
                )
            return identity
        if current != planned or (
            metadata.get("provider_leg_id_rebound") is True
            and str(metadata.get("provider_leg_id_authoritative") or "") != actual
        ):
            raise TransferProviderIdentityError(
                "transfer_provider_identity_alias_conflict"
            )
        if (
            str(leg.get("status") or "") not in {"initiated", "ringing"}
            or str(leg.get("billing_status") or "") != "reserved"
            or int(leg.get("reserved_seconds") or 0) <= 0
        ):
            raise TransferProviderIdentityError(
                "transfer_provider_identity_leg_not_active"
            )

        call_alias = await conn.fetchrow(
            """
            SELECT id
            FROM calls
            WHERE provider_call_id=$1 OR external_call_uuid=$1
            ORDER BY created_at, id
            LIMIT 1
            FOR UPDATE
            """,
            actual,
        )
        if call_alias:
            raise TransferProviderIdentityError(
                "transfer_provider_identity_call_alias"
            )
        leg_alias = await conn.fetchrow(
            """
            SELECT id
            FROM call_legs
            WHERE id<>$1::uuid
              AND (
                  provider_leg_id=$2
                  OR metadata->>'provider_leg_id_original'=$2
              )
            ORDER BY created_at, id
            LIMIT 1
            FOR UPDATE
            """,
            leg["id"],
            actual,
        )
        if leg_alias:
            raise TransferProviderIdentityError(
                "transfer_provider_identity_leg_alias"
            )

        event_payload = {
            "planned_provider_leg_id": planned,
            "provider_leg_id": actual,
            "provider_leg_id_rebound": True,
        }
        updated = await conn.fetchrow(
            """
            UPDATE call_legs
            SET provider_leg_id=$3,
                metadata=COALESCE(metadata,'{}'::jsonb) || $4::jsonb,
                updated_at=NOW()
            WHERE id=$1::uuid AND call_id=$2::uuid
              AND provider_leg_id=$5
              AND status IN ('initiated','ringing')
              AND billing_status='reserved'
              AND reserved_seconds > 0
            RETURNING id, call_id, provider_leg_id, metadata
            """,
            leg["id"],
            parent["id"],
            actual,
            json.dumps(
                {
                    "provider_leg_id_original": planned,
                    "provider_leg_id_authoritative": actual,
                    "provider_leg_id_rebound": True,
                }
            ),
            planned,
        )
        if not updated:
            raise TransferProviderIdentityError(
                "transfer_provider_identity_projection_conflict"
            )
        await conn.execute(
            """
            INSERT INTO call_events (
                call_id, talklee_call_id, leg_id, event_type, source,
                event_data, previous_state, new_state, created_at
            ) VALUES (
                $1::uuid,$2,$3::uuid,'transfer_provider_identity_rebound',
                'asterisk',$4::jsonb,$5,$5,NOW()
            )
            """,
            parent["id"],
            parent.get("talklee_call_id"),
            leg["id"],
            json.dumps(event_payload),
            str(leg.get("status") or "initiated"),
        )
        return _identity_from_row(
            updated,
            tenant_id=tenant,
            expected_original_provider_leg_id=planned,
        )


async def load_persisted_transfer_provider_identity(
    pool: Any,
    *,
    tenant_id: str,
    durable_call_id: str,
    leg_id: str | None,
    expected_original_provider_leg_id: str,
    parent_provider_call_id: str | None = None,
) -> PersistedTransferProviderIdentity:
    """Load and verify the DB identity used by completion/cleanup paths."""

    tenant = _required(tenant_id, code="transfer_provider_identity_tenant_missing")
    call_id = _required(
        durable_call_id,
        code="transfer_provider_identity_parent_missing",
    )
    expected = _provider_id(
        expected_original_provider_leg_id,
        code="transfer_provider_identity_invalid_planned_id",
    )
    normalized_leg_id = str(leg_id or "").strip()
    parent_provider = str(parent_provider_call_id or "").strip()

    async with acquire_with_tenant(pool, None) as conn:
        rows = await conn.fetch(
            """
            SELECT l.id, l.call_id, l.provider_leg_id, l.metadata
            FROM call_legs l
            JOIN calls c ON c.id=l.call_id
            WHERE c.id=$1::uuid AND c.tenant_id=$2::uuid
              AND l.leg_type='transfer'
              AND ($3::uuid IS NULL OR l.id=$3::uuid)
              AND (
                  l.provider_leg_id=$4
                  OR l.metadata->>'provider_leg_id_original'=$4
              )
              AND (
                  $5 = ''
                  OR c.provider_call_id=$5
                  OR c.external_call_uuid=$5
              )
            ORDER BY l.created_at DESC
            LIMIT 2
            """,
            call_id,
            tenant,
            normalized_leg_id or None,
            expected,
            parent_provider,
        )
        if not rows:
            raise TransferProviderIdentityError(
                "transfer_provider_identity_leg_missing"
            )
        if len(rows) != 1:
            raise TransferProviderIdentityError(
                "transfer_provider_identity_child_collision"
            )
        return _identity_from_row(
            rows[0],
            tenant_id=tenant,
            expected_original_provider_leg_id=expected,
        )

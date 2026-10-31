"""Durable takeover claims for orphaned inbound transfer parents.

This module deliberately does not control the PBX or settle usage.  Its only
job is to let the process that has *already* proved exclusive telephony
ownership turn PostgreSQL into a durable retry marker before attempting
all-leg termination elsewhere.

The normal fast path discovers crashed calls from the Redis session ledger.
PostgreSQL is the fallback when that cache entry was lost or expired.  A
current owner must pass every locally-owned inbound provider call id (including
after-hours admissions, which intentionally have no ``VoiceSession``) so this
fallback cannot fence a call the same process is still serving.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.core.db_utils import acquire_with_tenant
from app.domain.services.call_status import TERMINAL_CALL_STATUSES


_ACTIVE_TRANSFER_STATUSES = (
    "initiated",
    "ringing",
    "answered",
    "in_progress",
    "in_call",
    "active",
)


@dataclass(frozen=True, slots=True)
class InboundTransferTakeoverClaim:
    """One parent durably fenced for proof-aware restart teardown."""

    call_id: str
    tenant_id: str
    provider: str
    provider_call_id: str
    provider_leg_ids: tuple[str, ...]
    previous_status: str


def _normalized_exclusions(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(normalized for value in values if (normalized := str(value or "").strip()))
    )


def _usable_parent_provider_id(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or any(ord(char) < 32 for char in normalized):
        return None
    return normalized


def _usable_child_provider_id(value: Any) -> str | None:
    normalized = _usable_parent_provider_id(value)
    if normalized is None or normalized.startswith("transfer-"):
        # ``transfer-*`` was the legacy synthetic placeholder.  It was never
        # an addressable PBX channel and therefore cannot participate in an
        # all-leg absence proof.
        return None
    return normalized


async def claim_inbound_transfer_takeovers(
    pool: Any,
    *,
    exclusive_owner_confirmed: bool,
    excluded_provider_call_ids: Iterable[str] = (),
    limit: int = 100,
    timeout_s: float = 5.0,
) -> list[InboundTransferTakeoverClaim]:
    """Fence DB-only inbound transfer orphans for an exclusive owner.

    The caller must already hold the single telephony/ARI ownership lease.
    Under the cross-tenant service RLS bypass, this function locks candidate
    parents, locks every active reserved transfer child, and changes the parent
    to ``termination_pending`` in the same transaction.  Returning from the
    function therefore means PostgreSQL contains a durable retry marker before
    any caller can request PBX deletion.

    A second or concurrent invocation is idempotent: ``termination_pending``
    parents are no longer candidates, and ``SKIP LOCKED`` prevents two owners
    of this worker operation from claiming the same parent concurrently.

    Parents are skipped rather than partially returned if either the parent or
    any active reserved child lacks a usable provider channel id.  Settling a
    subset would be less safe than leaving the row untouched for investigation.
    """

    if exclusive_owner_confirmed is not True:
        raise RuntimeError("exclusive telephony ownership is required for transfer takeover")

    exclusions = _normalized_exclusions(excluded_provider_call_ids)
    batch_limit = max(1, min(int(limit), 500))
    bounded_timeout = max(0.1, min(float(timeout_s), 5.0))
    claims: list[InboundTransferTakeoverClaim] = []

    async with acquire_with_tenant(
        pool,
        None,
        timeout=bounded_timeout,
    ) as conn:
        # Lock only parents first.  Transfer authorization and terminal paths
        # lock this same row before mutating children, so the subsequent child
        # snapshot cannot race a newly-created target into existence.
        parents = list(
            await conn.fetch(
                """
                SELECT c.id, c.tenant_id, c.provider, c.status,
                       COALESCE(
                           NULLIF(BTRIM(c.provider_call_id),''),
                           NULLIF(BTRIM(c.external_call_uuid),'')
                       ) AS provider_call_id
                FROM calls c
                WHERE c.direction='inbound'
                  AND c.status <> 'termination_pending'
                  AND NOT (c.status = ANY($1::text[]))
                  AND c.billing_status IN ('reserved','held')
                  AND COALESCE(
                        NULLIF(BTRIM(c.provider_call_id),''),
                        NULLIF(BTRIM(c.external_call_uuid),'')
                      ) IS NOT NULL
                  AND NOT (
                        COALESCE(
                            NULLIF(BTRIM(c.provider_call_id),''),
                            NULLIF(BTRIM(c.external_call_uuid),'')
                        ) = ANY($2::text[])
                      )
                  AND EXISTS (
                        SELECT 1
                        FROM call_legs eligible_leg
                        WHERE eligible_leg.call_id=c.id
                          AND eligible_leg.leg_type='transfer'
                          AND eligible_leg.billing_status='reserved'
                          AND eligible_leg.status = ANY($3::text[])
                          AND eligible_leg.provider_leg_id IS NOT NULL
                          AND BTRIM(eligible_leg.provider_leg_id) <> ''
                          AND eligible_leg.provider_leg_id NOT LIKE 'transfer-%'
                          AND eligible_leg.provider_leg_id <> COALESCE(
                                NULLIF(BTRIM(c.provider_call_id),''),
                                NULLIF(BTRIM(c.external_call_uuid),'')
                              )
                      )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM call_legs unusable_leg
                        WHERE unusable_leg.call_id=c.id
                          AND unusable_leg.leg_type='transfer'
                          AND unusable_leg.billing_status='reserved'
                          AND unusable_leg.status = ANY($3::text[])
                          AND (
                               unusable_leg.provider_leg_id IS NULL
                            OR BTRIM(unusable_leg.provider_leg_id) = ''
                            OR unusable_leg.provider_leg_id LIKE 'transfer-%'
                            OR unusable_leg.provider_leg_id = COALESCE(
                                NULLIF(BTRIM(c.provider_call_id),''),
                                NULLIF(BTRIM(c.external_call_uuid),'')
                            )
                          )
                      )
                ORDER BY c.updated_at, c.id
                LIMIT $4
                FOR UPDATE OF c SKIP LOCKED
                """,
                list(TERMINAL_CALL_STATUSES),
                list(exclusions),
                list(_ACTIVE_TRANSFER_STATUSES),
                batch_limit,
            )
        )

        for raw_parent in parents:
            parent = dict(raw_parent)
            parent_provider_id = _usable_parent_provider_id(parent.get("provider_call_id"))
            if parent_provider_id is None or parent_provider_id in exclusions:
                # Defense in depth for test/third-party connection adapters and
                # unexpected database collations.  Do not fence a partial or
                # explicitly excluded identity.
                continue

            child_rows = list(
                await conn.fetch(
                    """
                    SELECT id, provider_leg_id
                    FROM call_legs
                    WHERE call_id=$1::uuid
                      AND leg_type='transfer'
                      AND billing_status='reserved'
                      AND status = ANY($2::text[])
                    ORDER BY created_at, id
                    FOR UPDATE
                    """,
                    str(parent["id"]),
                    list(_ACTIVE_TRANSFER_STATUSES),
                )
            )
            provider_leg_ids: list[str] = []
            seen_provider_leg_ids: set[str] = set()
            unusable_child = not child_rows
            for raw_child in child_rows:
                child = dict(raw_child)
                provider_leg_id = _usable_child_provider_id(child.get("provider_leg_id"))
                if provider_leg_id is None:
                    unusable_child = True
                    break
                if (
                    provider_leg_id == parent_provider_id
                    or provider_leg_id in seen_provider_leg_ids
                ):
                    # One PBX identity cannot truthfully represent two billing
                    # children, and a child cannot alias its parent.  Do not
                    # hide either corruption by de-duplicating the result.
                    unusable_child = True
                    break
                seen_provider_leg_ids.add(provider_leg_id)
                provider_leg_ids.append(provider_leg_id)

            if unusable_child or not provider_leg_ids:
                continue

            updated = await conn.fetchrow(
                """
                UPDATE calls
                   SET status='termination_pending', updated_at=NOW()
                 WHERE id=$1::uuid
                   AND direction='inbound'
                   AND status=$2
                   AND status <> 'termination_pending'
                   AND NOT (status = ANY($3::text[]))
                RETURNING id
                """,
                str(parent["id"]),
                str(parent.get("status") or ""),
                list(TERMINAL_CALL_STATUSES),
            )
            if not updated:
                continue

            claims.append(
                InboundTransferTakeoverClaim(
                    call_id=str(parent["id"]),
                    tenant_id=str(parent["tenant_id"]),
                    provider=str(parent.get("provider") or "asterisk"),
                    provider_call_id=parent_provider_id,
                    provider_leg_ids=tuple(provider_leg_ids),
                    previous_status=str(parent.get("status") or ""),
                )
            )

    return claims

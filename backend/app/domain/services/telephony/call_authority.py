"""Current formal account authority for call admission and existing call policy.

These server-internal reads require the existing service RLS context because a
formal partner belongs to its own tenant. Every query still binds explicit
tenant IDs. This module never grants connection authority or caches verdicts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from app.domain.services.subscription_status import (
    TENANT_BLOCKED_STATUSES,
    canonical,
    is_blocked,
)


MAX_AUTHORITY_BATCH = 200


class CallAuthorityUnavailable(RuntimeError):
    """The current formal authority could not be established; never allow."""


class CallAuthorityPurpose(str, Enum):
    ADMISSION = "admission"
    EXISTING_CALL = "existing_call"
    PRINCIPAL = "principal"


@dataclass(frozen=True)
class CallAuthority:
    allowed: bool
    reason: str | None = None


# Project only the current tenant's policy state, not the suspension's private
# evidence or member list. The target/cutoff/audit proof come from DB columns.
_AUTHORITY_SELECT = """
SELECT t.id, t.status, t.subscription_status, t.suspended_at,
       t.suspended_by, t.suspension_reason, t.white_label_partner_id,
       p.id AS formal_partner_id, p.status AS formal_partner_status,
       COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
           AS authority_scope_ok,
       COALESCE((
           SELECT jsonb_agg(jsonb_build_object(
               'suspension_id', s.suspension_id,
               'target_type', s.target_type, 'target_id', s.target_id,
               'suspended_at', s.suspended_at,
               'audited', s.audit_log_id IS NOT NULL AND COALESCE(
                   a.metadata->'formal_account_hold_version' = '1'::jsonb, FALSE),
               'policy_version', s.evidence->'_formal_account_hold'->'version',
               'immediate', s.evidence->'_formal_account_hold'->'immediate',
               'affected', COALESCE(
                   (s.evidence->'_formal_account_hold'->'affected_tenant_ids') ? t.id::text,
                   FALSE),
               'tenant_state', s.evidence->'_formal_account_hold'->'tenant_state'->t.id::text
           ) ORDER BY s.suspended_at, s.suspension_id)
           FROM suspension_events s
           LEFT JOIN audit_logs a ON a.event_id = s.audit_log_id
           WHERE s.is_active = TRUE AND (
               (s.target_type = 'tenant' AND s.target_id = t.id)
               OR (s.target_type = 'partner' AND (
                   s.target_id = t.white_label_partner_id
                   OR (s.audit_log_id IS NOT NULL
                       AND a.metadata->'formal_account_hold_version' = '1'::jsonb
                       AND s.evidence->'_formal_account_hold'->'version' = '1'::jsonb
                       AND (s.evidence->'_formal_account_hold'->'affected_tenant_ids') ? t.id::text)
               ))
           )
       ), '[]'::jsonb) AS active_holds
FROM tenants t
LEFT JOIN white_label_partners p ON p.id = t.white_label_partner_id
"""


def _timestamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value


def _same_identity(a, b):
    if a is None or b is None:
        return a is b
    try:
        return UUID(str(a)) == UUID(str(b))
    except (ValueError, TypeError, AttributeError):
        return False


def _deferred_existing_call(row, hold, started_at):
    """Only an audited server-owned non-immediate tenant hold can exempt it."""
    cutoff = _timestamp(hold.get("suspended_at"))
    start = _timestamp(started_at)
    if (
        hold.get("target_type") != "tenant"
        or not _same_identity(hold.get("target_id"), row["id"])
        or hold.get("audited") is not True
        or type(hold.get("policy_version")) is not int
        or hold["policy_version"] != 1
        or hold.get("immediate") is not False
        or hold.get("affected") is not True
        or start is None
        or cutoff is None
        or start >= cutoff
    ):
        return False
    state = hold.get("tenant_state")
    if not isinstance(state, Mapping):
        return False
    prior, held = state.get("prior"), state.get("held")
    if not isinstance(prior, Mapping) or not isinstance(held, Mapping):
        return False
    keys = {"status", "suspended_at", "suspended_by", "suspension_reason"}
    held_at = _timestamp(held.get("suspended_at"))
    if (
        not keys.issubset(prior)
        or not keys.issubset(held)
        or prior["status"] != "active"
        or held["status"] != "suspended"
        or row["status"] != held["status"]
        or held_at is None
        or held_at > cutoff
        or _timestamp(row["suspended_at"]) != held_at
        or not _same_identity(row["suspended_by"], held["suspended_by"])
        or row["suspension_reason"] != held["suspension_reason"]
    ):
        return False
    return True


def evaluate_call_authority(
    row,
    *,
    purpose: CallAuthorityPurpose = CallAuthorityPurpose.ADMISSION,
    existing_call_started_at=None,
) -> CallAuthority:
    """Evaluate one current DB projection.

    Purpose is explicit: a missing timestamp never changes an active-owner check
    into fresh admission. Existing-call callers
    may supply only server-proven provider acknowledgement or answer time, never
    a planned call/queue creation time or a client-supplied timestamp.
    """
    purpose = CallAuthorityPurpose(purpose)
    if row is None:
        return CallAuthority(False, "tenant_not_found")
    required = {
        "id",
        "status",
        "subscription_status",
        "suspended_at",
        "suspended_by",
        "suspension_reason",
        "white_label_partner_id",
        "formal_partner_id",
        "formal_partner_status",
        "active_holds",
        "authority_scope_ok",
    }
    if not required.issubset(row.keys()) or row["authority_scope_ok"] is not True:
        raise CallAuthorityUnavailable(
            "Formal account authority requires a complete service-scoped read"
        )
    if row["white_label_partner_id"] is not None:
        if not _same_identity(row["white_label_partner_id"], row["formal_partner_id"]):
            return CallAuthority(False, "formal_partner_not_found")
        if row["formal_partner_status"] != "active":
            return CallAuthority(False, "formal_partner_inactive")
    status = canonical(row["subscription_status"] or "active")
    if purpose == CallAuthorityPurpose.ADMISSION and is_blocked(status, TENANT_BLOCKED_STATUSES):
        return CallAuthority(False, f"tenant_{status}")
    holds = row["active_holds"]
    if isinstance(holds, str):
        try:
            holds = json.loads(holds)
        except ValueError as error:
            raise CallAuthorityUnavailable("Malformed formal hold projection") from error
    if not isinstance(holds, list) or any(not isinstance(hold, Mapping) for hold in holds):
        raise CallAuthorityUnavailable("Malformed formal hold projection")
    if holds:
        # Duplicate/overlapping holds cannot be excused by one historical policy.
        if (
            purpose == CallAuthorityPurpose.EXISTING_CALL
            and len(holds) == 1
            and _deferred_existing_call(row, holds[0], existing_call_started_at)
        ):
            return CallAuthority(True)
        return CallAuthority(False, "formal_account_hold")
    # Preserve the pre-existing compatibility of a NULL legacy tenant status.
    if (
        purpose != CallAuthorityPurpose.EXISTING_CALL
        and row["status"] is not None
        and row["status"] != "active"
    ):
        return CallAuthority(False, f"tenant_{row['status']}")
    return CallAuthority(True)


async def read_call_authority(
    conn,
    tenant_id,
    *,
    purpose: CallAuthorityPurpose = CallAuthorityPurpose.ADMISSION,
    existing_call_started_at=None,
) -> CallAuthority:
    tenant = UUID(str(tenant_id))
    row = await conn.fetchrow(_AUTHORITY_SELECT + " WHERE t.id = $1::uuid", tenant)
    return evaluate_call_authority(
        row, purpose=purpose, existing_call_started_at=existing_call_started_at
    )


async def read_formal_account_authority(conn, tenant_id) -> CallAuthority:
    """Authentication authority: formal tenant/parent/holds, never billing."""
    return await read_call_authority(conn, tenant_id, purpose=CallAuthorityPurpose.PRINCIPAL)


async def read_call_authority_rows(conn, tenant_ids: Sequence[str]) -> dict[str, Mapping]:
    """One bounded current read for an owner loop; absent rows remain denied."""
    tenants = tuple(dict.fromkeys(UUID(str(value)) for value in tenant_ids))
    if len(tenants) > MAX_AUTHORITY_BATCH:
        raise ValueError(f"At most {MAX_AUTHORITY_BATCH} tenants per authority read")
    if not tenants:
        return {}
    rows = await conn.fetch(_AUTHORITY_SELECT + " WHERE t.id = ANY($1::uuid[])", tenants)
    return {str(row["id"]): row for row in rows}

"""Authoritative policy, attempt accounting, and leases for inbound transfers."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from app.core.db_utils import acquire_with_tenant
from app.domain.services.telephony.inbound_router import normalize_did
from app.domain.services.telephony_concurrency_limiter import (
    LeaseKind,
    TelephonyConcurrencyLimiter,
)


# Code-owned capability declaration, intentionally separate from the operator's
# live kill switch. Keep it closed until the release checklist's live-carrier
# two-leg, restart, hard-hangup, and settlement proofs are signed off. Policy
# remains readable for audit; tenant/admin write paths may only disable a
# legacy enabled value while every execution path fails closed.
CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE = False

_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})


def _staging_proof_scope() -> tuple[Optional[str], Optional[str]]:
    """Return the validated tenant/config UUIDs for the staging proof window."""

    values: list[Optional[str]] = []
    for name in (
        "INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID",
        "INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID",
    ):
        raw = os.getenv(name, "").strip()
        try:
            values.append(str(uuid.UUID(raw)) if raw else None)
        except (TypeError, ValueError, AttributeError):
            values.append(None)
    return values[0], values[1]


def inbound_transfer_runtime_available() -> bool:
    """Return whether the controlled transfer runtime may accept work.

    Production remains code-owned and closed until the live-carrier evidence is
    signed.  Staging can open a deliberately separate proof window so that the
    evidence required to change that declaration can actually be collected.
    Development, test, blank, and production environments never honor the
    staging switch.
    """

    if CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE:
        return True
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    staging_proof_enabled = (
        os.getenv("INBOUND_TRANSFER_STAGING_PROOF_ENABLED", "false").strip().lower()
        in _TRUTHY_ENV_VALUES
    )
    proof_tenant_id, proof_config_id = _staging_proof_scope()
    return bool(
        environment == "staging"
        and staging_proof_enabled
        and proof_tenant_id
        and proof_config_id
    )


def inbound_transfer_scope_available(*, tenant_id: Any, config_id: Any) -> bool:
    """Limit the proof-only runtime to one exact staging tenant/config pair.

    The code-owned production capability deliberately bypasses this temporary
    proof allowlist once it is explicitly released. Until then, malformed,
    missing, or mismatched identifiers fail closed.
    """

    if CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE:
        return True
    if not inbound_transfer_runtime_available():
        return False
    try:
        normalized_tenant_id = str(uuid.UUID(str(tenant_id)))
        normalized_config_id = str(uuid.UUID(str(config_id)))
    except (TypeError, ValueError, AttributeError):
        return False
    proof_tenant_id, proof_config_id = _staging_proof_scope()
    return (
        normalized_tenant_id == proof_tenant_id
        and normalized_config_id == proof_config_id
    )


class InboundTransferError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class InboundTransferAttempt:
    inbound: bool
    call_id: Optional[str] = None
    tenant_id: Optional[str] = None
    talklee_call_id: Optional[str] = None
    leg_id: Optional[str] = None
    provider_leg_id: Optional[str] = None
    lease_id: Optional[str] = None
    usage_reservation_id: Optional[str] = None
    reserved_seconds: int = 0
    destination: Optional[str] = None
    attempt_number: int = 0
    hop_number: int = 0
    failure_action: str = "hangup"
    idempotency_record_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    request_hash: Optional[str] = None
    actor_id: Optional[str] = None
    actor_role: Optional[str] = None
    actor_type: Optional[str] = None
    is_replay: bool = False
    replay_result: Optional[dict[str, Any]] = None


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


_TRANSFER_IDEMPOTENCY_OPERATION = "controlled_inbound_transfer"


def _transfer_scope_key(call_id: str) -> str:
    return f"inbound-transfer:{call_id}"


def _transfer_request_hash(
    *,
    call_id: str,
    destination: str,
    mode: str,
    source: str,
) -> str:
    canonical = json.dumps(
        {
            "call_id": str(call_id),
            "destination": str(destination),
            "mode": str(mode),
            "source": str(source),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalized_actor_id(value: Optional[str]) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    try:
        return str(uuid.UUID(str(value).strip()))
    except (TypeError, ValueError, AttributeError) as exc:
        raise InboundTransferError(
            "invalid_transfer_actor",
            "The authenticated transfer actor is invalid.",
            status_code=422,
        ) from exc


def _transfer_response_status_code(result: Mapping[str, Any]) -> int:
    status = str(result.get("status") or "").strip().lower()
    fallback_status = str(result.get("fallback_status") or "").strip().lower()
    if status in {
        "in_progress",
        "cleanup_pending",
        "reconciliation_required",
    } or fallback_status in {
        "termination_pending",
        "unconfirmed",
    }:
        return 202
    return 200


async def _load_idempotent_transfer_attempt(
    conn: Any,
    *,
    call_row: Mapping[str, Any],
    idempotency_key: str,
    request_hash: str,
) -> Optional[InboundTransferAttempt]:
    """Load a committed same-key operation without creating new PBX work."""

    operation = await conn.fetchrow(
        """
        SELECT id, request_hash, response_body, status_code,
               resource_type, resource_id, actor_id
        FROM inbound_operation_idempotency
        WHERE scope_key=$1 AND operation=$2 AND idempotency_key=$3
          AND expires_at > NOW()
        FOR UPDATE
        """,
        _transfer_scope_key(str(call_row["id"])),
        _TRANSFER_IDEMPOTENCY_OPERATION,
        idempotency_key,
    )
    if not operation:
        return None
    if str(operation["request_hash"] or "") != request_hash:
        raise InboundTransferError(
            "transfer_idempotency_conflict",
            "This idempotency key was already used for a different transfer request.",
            status_code=409,
        )

    resource_id = operation.get("resource_id")
    leg: Mapping[str, Any] = {}
    if resource_id:
        loaded_leg = await conn.fetchrow(
            """
            SELECT id, provider_leg_id, to_number, status, metadata,
                   reserved_seconds
            FROM call_legs
            WHERE id=$1::uuid AND call_id=$2::uuid AND leg_type='transfer'
            FOR UPDATE
            """,
            resource_id,
            call_row["id"],
        )
        if not loaded_leg:
            raise InboundTransferError(
                "transfer_idempotency_resource_missing",
                "The durable transfer attempt is unavailable.",
                status_code=503,
            )
        leg = loaded_leg

    metadata = _json_object(leg.get("metadata"))
    response_body = operation.get("response_body")
    replay_result = _json_object(response_body) if response_body is not None else None
    transfer_policy = _json_object(
        _json_object(_json_object(call_row.get("route_snapshot")).get("inbound_config")).get(
            "transfer_policy"
        )
    )
    failure_action = transfer_policy.get("failure_action")
    if failure_action not in {"voicemail", "return_to_agent", "hangup"}:
        failure_action = "hangup"
    return InboundTransferAttempt(
        inbound=True,
        call_id=str(call_row["id"]),
        tenant_id=str(call_row["tenant_id"]),
        talklee_call_id=str(call_row["talklee_call_id"]),
        leg_id=str(resource_id) if resource_id else None,
        provider_leg_id=(str(leg.get("provider_leg_id")) if leg.get("provider_leg_id") else None),
        lease_id=(str(metadata["lease_id"]) if metadata.get("lease_id") else None),
        usage_reservation_id=(
            str(metadata["usage_reservation_id"]) if metadata.get("usage_reservation_id") else None
        ),
        reserved_seconds=max(0, int(leg.get("reserved_seconds") or 0)),
        destination=(str(leg.get("to_number")) if leg.get("to_number") else None),
        attempt_number=max(0, int(metadata.get("attempt") or 0)),
        hop_number=max(0, int(metadata.get("hop") or 0)),
        failure_action=str(failure_action),
        idempotency_record_id=str(operation["id"]),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        actor_id=(str(operation["actor_id"]) if operation.get("actor_id") else None),
        actor_role=(str(metadata["actor_role"]) if metadata.get("actor_role") else None),
        actor_type=(str(metadata["actor_type"]) if metadata.get("actor_type") else None),
        is_replay=True,
        replay_result=replay_result,
    )


async def _store_transfer_idempotency_response(
    conn: Any,
    *,
    attempt: InboundTransferAttempt,
    result: Mapping[str, Any],
) -> None:
    """Store the exact public response in the same transaction as leg state."""

    if not attempt.idempotency_record_id or not attempt.request_hash:
        return
    response = dict(result)
    response.setdefault("attempt_id", attempt.leg_id)
    response.setdefault("idempotency_key", attempt.idempotency_key)
    updated = await conn.fetchrow(
        """
        UPDATE inbound_operation_idempotency
        SET response_body=$4::jsonb, status_code=$5,
            resource_type='call_leg', resource_id=$6::uuid,
            expires_at=GREATEST(expires_at,NOW()+INTERVAL '24 hours')
        WHERE id=$1::uuid AND tenant_id=$2::uuid AND request_hash=$3
        RETURNING id
        """,
        attempt.idempotency_record_id,
        attempt.tenant_id,
        attempt.request_hash,
        json.dumps(response),
        _transfer_response_status_code(response),
        attempt.leg_id,
    )
    if not updated:
        raise RuntimeError("transfer_idempotency_response_conflict")


async def _store_persisted_transfer_terminal_response(
    conn: Any,
    *,
    tenant_id: str,
    call_id: str,
    leg: Mapping[str, Any],
    terminal_status: str,
    reason: Optional[str],
) -> None:
    """Converge a missing/pending replay result during recovery settlement."""

    metadata = _json_object(leg.get("metadata"))
    record_id = metadata.get("idempotency_record_id")
    request_hash = metadata.get("request_hash")
    idempotency_key = metadata.get("idempotency_key")
    if not record_id or not request_hash or not idempotency_key:
        return
    response = {
        "status": terminal_status,
        "attempt_id": str(leg["id"]),
        "idempotency_key": str(idempotency_key),
        "provider_leg_id": (
            str(leg.get("provider_leg_id")) if leg.get("provider_leg_id") else None
        ),
        "terminal_reason": str(reason or "recovered_transfer_terminal")[:128],
    }
    if terminal_status == "reconciliation_required":
        response.update(
            {
                "billing_status": "held",
                "reconciliation_required": True,
            }
        )
    response_status_code = _transfer_response_status_code(response)
    updated = await conn.fetchrow(
        """
        UPDATE inbound_operation_idempotency
        SET response_body=CASE
                WHEN response_body IS NULL
                  OR LOWER(COALESCE(response_body->>'status','')) IN (
                      'in_progress','cleanup_pending','unconfirmed',
                      'termination_unconfirmed'
                  )
                  OR LOWER(COALESCE(response_body->>'fallback_status','')) IN (
                      'termination_pending','unconfirmed'
                  )
                THEN $5::jsonb
                ELSE response_body
            END,
            status_code=CASE
                WHEN response_body IS NULL
                  OR LOWER(COALESCE(response_body->>'status','')) IN (
                      'in_progress','cleanup_pending','unconfirmed',
                      'termination_unconfirmed'
                  )
                  OR LOWER(COALESCE(response_body->>'fallback_status','')) IN (
                      'termination_pending','unconfirmed'
                  )
                THEN $7
                ELSE status_code
            END,
            resource_type='call_leg', resource_id=$4::uuid,
            expires_at=GREATEST(expires_at,NOW()+INTERVAL '24 hours')
        WHERE id=$1::uuid AND tenant_id=$2::uuid AND request_hash=$3
          AND scope_key=$6
        RETURNING id
        """,
        record_id,
        tenant_id,
        str(request_hash),
        leg["id"],
        json.dumps(response),
        _transfer_scope_key(call_id),
        response_status_code,
    )
    if not updated:
        raise RuntimeError("transfer_idempotency_terminal_conflict")


async def fetch_tenant_accounted_usage_seconds(
    conn: Any,
    *,
    tenant_id: str,
    exclude_call_id: Optional[str] = None,
) -> int:
    """Return quota seconds including every independently billable leg.

    Parent calls remain the product-wide duration authority. Reserved or held
    inbound parents count at least their full reservation until settlement.
    Controlled transfer targets add either their live reservation or their
    settled actual duration because the inbound and outbound PSTN channels
    overlap and are independently billable. A held child is counted
    conservatively at the greater of its reservation and observed duration.

    Call admission and transfer authorization both invoke this while holding
    the concurrency limiter's tenant advisory transaction lock. Keeping the SQL
    in one function prevents either gate from forgetting the second PSTN leg.
    """

    value = await conn.fetchval(
        """
        SELECT
            COALESCE((
                SELECT SUM(
                    CASE
                        WHEN c.billing_status='reversed' THEN 0
                        WHEN c.direction='inbound'
                         AND c.billing_status='reserved'
                            THEN GREATEST(
                                COALESCE(c.reserved_seconds,0),
                                COALESCE(c.duration_seconds,0)
                            )
                        WHEN c.direction='inbound'
                         AND c.billing_status='held'
                            THEN GREATEST(
                                COALESCE(c.reserved_seconds,0),
                                COALESCE(c.duration_seconds,0)
                            )
                        ELSE COALESCE(c.duration_seconds,0)
                    END
                )
                FROM calls c
                WHERE c.tenant_id=$1
                  AND c.created_at >= date_trunc('month',NOW())
                  AND ($2::uuid IS NULL OR c.id <> $2::uuid)
                  AND NOT c.is_test
            ),0)
            + COALESCE((
                SELECT SUM(
                    CASE
                        WHEN leg.billing_status='reversed' THEN 0
                        WHEN leg.billing_status='reserved'
                            THEN COALESCE(leg.reserved_seconds,0)
                        WHEN leg.billing_status='held'
                            THEN GREATEST(
                                COALESCE(leg.reserved_seconds,0),
                                COALESCE(leg.duration_seconds,0)
                            )
                        WHEN leg.billing_status='finalized'
                            THEN COALESCE(leg.duration_seconds,0)
                        ELSE 0
                    END
                )
                FROM call_legs leg
                JOIN calls parent ON parent.id=leg.call_id
                WHERE parent.tenant_id=$1
                  AND parent.created_at >= date_trunc('month',NOW())
                  AND ($2::uuid IS NULL OR parent.id <> $2::uuid)
                  AND NOT parent.is_test
                  AND leg.leg_type='transfer'
            ),0)
        """,
        tenant_id,
        exclude_call_id,
    )
    return max(0, int(value or 0))


async def _settle_transfer_usage(
    conn: Any,
    *,
    leg: Mapping[str, Any],
    call_id: str,
    tenant_id: str,
    terminal_status: str,
    actual_seconds: int,
    release_only: bool,
    reason: str,
    provider_leg_id: str,
    metadata: Mapping[str, Any],
    answered: bool,
) -> bool:
    """Append one terminal child-ledger delta and project the leg atomically.

    The caller owns a transaction and row lock.  Returning ``False`` means a
    concurrent/idempotent terminal writer already won. Monetary cost remains
    NULL until a carrier CDR plus pinned tariff is an approved authority; NULL
    is intentionally different from a fabricated zero-cost leg.
    """

    billing_status = str(leg.get("billing_status") or "")
    if billing_status in {"finalized", "released", "reversed"}:
        return False
    if billing_status != "reserved":
        raise RuntimeError("transfer_usage_reservation_missing")

    reserved_seconds = max(0, int(leg.get("reserved_seconds") or 0))
    actual_seconds = max(0, int(actual_seconds or 0))
    if reserved_seconds <= 0:
        raise RuntimeError("transfer_usage_reservation_missing")
    if not release_only and actual_seconds > reserved_seconds:
        # The target shares the parent's hard deadline and must never outlive
        # the exact remaining seconds reserved before ARI channel creation.
        raise RuntimeError("transfer_usage_exceeded_reservation")

    if not release_only:
        controls = await conn.fetchrow(
            """
            SELECT inbound_settlement_enabled
            FROM platform_runtime_controls WHERE id=1
            """
        )
        if not controls or controls["inbound_settlement_enabled"] is not True:
            raise RuntimeError("transfer_settlement_held")

    reserve = await conn.fetchrow(
        """
        SELECT id, quantity_seconds
        FROM inbound_usage_transactions
        WHERE call_id=$1::uuid AND call_leg_id=$2::uuid
          AND transaction_type='reserve'
        """,
        call_id,
        leg["id"],
    )
    if not reserve or int(reserve["quantity_seconds"] or 0) != reserved_seconds:
        raise RuntimeError("transfer_usage_reservation_missing")

    transaction_type = "release" if release_only else "finalize"
    terminal_billing_status = "released" if release_only else "finalized"
    delta = -reserved_seconds if release_only else actual_seconds - reserved_seconds
    idempotency_key = f"inbound:transfer:{transaction_type}:{leg['id']}"
    usage = await conn.fetchrow(
        """
        INSERT INTO inbound_usage_transactions (
            tenant_id, call_id, call_leg_id, transaction_type,
            quantity_seconds, amount, currency, idempotency_key,
            related_transaction_id, policy_snapshot, metadata
        ) VALUES (
            $1,$2::uuid,$3::uuid,$4,$5,NULL,NULL,$6,$7,$8::jsonb,$9::jsonb
        )
        ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
        RETURNING id
        """,
        tenant_id,
        call_id,
        leg["id"],
        transaction_type,
        delta,
        idempotency_key,
        reserve["id"],
        json.dumps(
            {
                "reserved_seconds": reserved_seconds,
                "actual_seconds": 0 if release_only else actual_seconds,
                "billable_subject": "transfer_leg",
                "cost_authority": "carrier_cdr_unavailable",
            }
        ),
        json.dumps({"reason": reason, **dict(metadata)}),
    )
    if not usage:
        usage = await conn.fetchrow(
            """
            SELECT id, call_id, call_leg_id, transaction_type,
                   quantity_seconds, related_transaction_id
            FROM inbound_usage_transactions
            WHERE tenant_id=$1 AND idempotency_key=$2
            """,
            tenant_id,
            idempotency_key,
        )
        if (
            not usage
            or str(usage["call_id"]) != str(call_id)
            or str(usage["call_leg_id"]) != str(leg["id"])
            or str(usage["transaction_type"]) != transaction_type
            or int(usage["quantity_seconds"] or 0) != delta
            or str(usage["related_transaction_id"]) != str(reserve["id"])
        ):
            raise RuntimeError("transfer_usage_idempotency_conflict")

    updated = await conn.fetchrow(
        """
        UPDATE call_legs
        SET status=$3,
            answered_at=CASE
                WHEN $10 THEN COALESCE(answered_at,NOW())
                ELSE answered_at
            END,
            ended_at=COALESCE(ended_at,NOW()),
            duration_seconds=$4,
            billing_status=$5,
            -- Monetary truth is deliberately unavailable without carrier CDR
            -- and a pinned rate snapshot. Never turn unknown into zero.
            cost=NULL,
            currency=NULL,
            provider_leg_id=COALESCE(NULLIF($6,''),provider_leg_id),
            metadata=COALESCE(metadata,'{}'::jsonb) || $7::jsonb,
            updated_at=NOW()
        WHERE id=$1::uuid AND call_id=$2::uuid
          AND status=$8 AND billing_status=$9
        RETURNING id
        """,
        leg["id"],
        call_id,
        terminal_status,
        actual_seconds,
        terminal_billing_status,
        provider_leg_id,
        json.dumps(dict(metadata)),
        str(leg.get("status") or ""),
        billing_status,
        answered,
    )
    if not updated:
        # The caller locked this exact row before appending the terminal
        # ledger entry.  A failed projection therefore signals corruption or
        # a violated lock contract, not a benign replay.  Raising rolls the
        # ledger insert back with the projection and retains the reservation.
        raise RuntimeError("transfer_usage_projection_conflict")
    return True


def _bounded_policy_int(policy: Mapping[str, Any], key: str, default: int) -> int:
    value = policy.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if 1 <= parsed <= 5 else default


def normalized_inbound_transfer_destinations(
    transfer_policy: Mapping[str, Any],
) -> frozenset[str]:
    """Return only valid E.164 destinations from the explicit policy list.

    A scalar or otherwise malformed ``destinations`` value is not an
    allowlist.  Treating it as empty keeps readiness, pre-answer admission,
    and the final execution gate aligned and fail closed.
    """

    raw_destinations = transfer_policy.get("destinations")
    if not isinstance(raw_destinations, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        normalized
        for value in raw_destinations
        if (normalized := normalize_did(str(value))) is not None
    )


def inbound_transfer_destination_approved(
    transfer_policy: Mapping[str, Any],
    destination: Any,
) -> bool:
    """Return whether ``destination`` is explicitly present in policy."""

    normalized = normalize_did(str(destination or ""))
    return normalized is not None and normalized in normalized_inbound_transfer_destinations(
        transfer_policy
    )


async def authorize_inbound_transfer(
    pool,
    *,
    call_reference: str,
    destination: str,
    mode: str,
    source: str,
    redis_client: Any = None,
    idempotency_key: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    actor_type: Optional[str] = None,
) -> InboundTransferAttempt:
    """Atomically authorize and reserve one inbound transfer attempt.

    Outbound calls are returned unchanged and remain governed by their
    existing path. For inbound calls, the platform emergency switch, admitted
    allowlist, attempt/hop ceilings, and tenant transfer concurrency policy are
    all checked before the PBX sees a redirect.
    """

    limiter = TelephonyConcurrencyLimiter(redis_client)
    async with acquire_with_tenant(pool, None) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, talklee_call_id, direction, provider,
                       route_snapshot, status, admission_status,
                       processing_status, billing_status, reserved_seconds,
                       started_at, answered_at,
                       GREATEST(
                           0,
                           FLOOR(
                               COALESCE(reserved_seconds,0) - GREATEST(
                                   0,
                                   EXTRACT(EPOCH FROM (
                                       NOW()-COALESCE(answered_at,started_at,NOW())
                                   ))
                               )
                           )::int
                       ) AS transfer_reservation_seconds
                FROM calls
                WHERE external_call_uuid=$1 OR provider_call_id=$1
                ORDER BY created_at DESC
                LIMIT 1
                FOR UPDATE
                """,
                call_reference,
            )
            if not row:
                raise InboundTransferError(
                    "call_not_found",
                    "Call not found.",
                    status_code=404,
                )
            if (row["direction"] or "outbound") != "inbound":
                # This service was added only to harden inbound transfers.
                # Preserve the legacy outbound API contract exactly: internal
                # extensions and provider-specific destinations are valid on
                # that path and must not be subjected to inbound E.164 rules.
                return InboundTransferAttempt(inbound=False)

            from app.domain.services.telephony.transfer_validation import (
                validate_transfer_idempotency_key,
            )

            try:
                normalized_idempotency_key = validate_transfer_idempotency_key(idempotency_key)
            except ValueError as exc:
                raise InboundTransferError(
                    "transfer_idempotency_key_required",
                    str(exc),
                    status_code=400,
                ) from exc

            normalized_mode = str(mode or "").strip().lower()
            if normalized_mode != "blind":
                raise InboundTransferError(
                    "unsupported_inbound_transfer_mode",
                    "Controlled inbound transfer supports blind mode only.",
                    status_code=422,
                )

            requested = normalize_did(destination)
            if requested is None:
                raise InboundTransferError(
                    "invalid_transfer_destination",
                    "Transfer destination must be a valid E.164 number.",
                    status_code=422,
                )
            normalized_source = str(source or "api").strip().lower()[:50] or "api"
            normalized_actor_id = _normalized_actor_id(actor_id)
            normalized_actor_role = str(actor_role).strip()[:32] if actor_role is not None else None
            normalized_actor_type = str(actor_type).strip()[:32] if actor_type is not None else None
            request_hash = _transfer_request_hash(
                call_id=str(row["id"]),
                destination=requested,
                mode=normalized_mode,
                source=normalized_source,
            )
            replay = await _load_idempotent_transfer_attempt(
                conn,
                call_row=row,
                idempotency_key=normalized_idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay

            snapshot = _json_object(row["route_snapshot"])
            route_snapshot = _json_object(snapshot.get("route"))
            inbound_config = _json_object(snapshot.get("inbound_config"))

            from app.domain.services.call_status import TERMINAL_CALL_STATUSES

            call_status = str(row.get("status") or "")
            if call_status == "termination_pending" or call_status in TERMINAL_CALL_STATUSES:
                raise InboundTransferError(
                    "call_terminating",
                    "The call is ending and cannot start a new transfer.",
                    status_code=409,
                )
            if (
                str(row.get("admission_status") or "") != "allowed"
                or str(row.get("processing_status") or "") != "active"
                or str(row.get("billing_status") or "") != "reserved"
                or int(row.get("reserved_seconds") or 0) <= 0
            ):
                raise InboundTransferError(
                    "inbound_transfer_billing_unavailable",
                    "The parent call does not have a live usage reservation.",
                    status_code=409,
                )

            # New work fails closed before live-switch evaluation, lease
            # creation, call-leg insertion, or any PBX side effect. A durable
            # same-hash replay was returned above and never re-executes PBX
            # work, even if a later deploy closes this capability switch.
            if not inbound_transfer_runtime_available():
                raise InboundTransferError(
                    "transfer_runtime_unavailable",
                    (
                        "Inbound transfer is unavailable until the controlled "
                        "linked-leg runtime is verified."
                    ),
                    status_code=503,
                )
            if not inbound_transfer_scope_available(
                tenant_id=row["tenant_id"],
                config_id=route_snapshot.get("config_id"),
            ):
                raise InboundTransferError(
                    "transfer_staging_scope_mismatch",
                    "Inbound transfer is outside the approved staging proof scope.",
                    status_code=503,
                )

            live_enabled = await conn.fetchval(
                "SELECT inbound_transfer_enabled " "FROM platform_runtime_controls WHERE id=1"
            )
            if live_enabled is not True:
                raise InboundTransferError(
                    "inbound_transfer_disabled",
                    "Inbound transfers are disabled.",
                )

            transfer_policy = _json_object(inbound_config.get("transfer_policy"))
            called_did = normalize_did(
                str(route_snapshot.get("called_did") or "")
            )
            if called_did and requested == called_did:
                raise InboundTransferError(
                    "inbound_transfer_self_target",
                    "The inbound DID cannot be used as its own transfer destination.",
                )
            policy_enabled = transfer_policy.get("enabled") is True

            is_pinned_after_hours = (
                normalized_source == "after_hours"
                and inbound_config.get("selected_action") == "transfer"
            )
            if is_pinned_after_hours:
                pinned_destination = normalize_did(
                    str(inbound_config.get("selected_destination") or "")
                )
                if pinned_destination is None or requested != pinned_destination:
                    raise InboundTransferError(
                        "inbound_transfer_not_approved",
                        (
                            "This transfer destination does not match the "
                            "after-hours destination pinned at admission."
                        ),
                    )

            if not policy_enabled or not inbound_transfer_destination_approved(
                transfer_policy,
                requested,
            ):
                raise InboundTransferError(
                    "inbound_transfer_not_approved",
                    "This transfer destination is not approved by the pinned campaign policy.",
                )

            max_attempts = _bounded_policy_int(transfer_policy, "max_attempts", 1)
            max_hops = _bounded_policy_int(transfer_policy, "max_hops", 1)
            leg_stats = await conn.fetchrow(
                """
                SELECT COUNT(*) AS attempt_count,
                       COUNT(*) FILTER (WHERE status='completed') AS completed_hop_count,
                       COALESCE(
                           BOOL_OR(status IN ('initiated','ringing','answered')),
                           FALSE
                       ) AS transfer_active
                FROM call_legs
                WHERE call_id=$1 AND leg_type='transfer'
                """,
                row["id"],
            )
            attempt_count = int(leg_stats["attempt_count"] or 0)
            completed_hop_count = int(leg_stats["completed_hop_count"] or 0)
            if bool(leg_stats["transfer_active"]):
                raise InboundTransferError(
                    "inbound_transfer_already_active",
                    "Another transfer is already active for this call.",
                )
            attempt_number = attempt_count + 1
            # A failed retry is another attempt, not another completed hop.
            # Keeping these counters independent is what lets an operator allow
            # two tries to the same first-hop representative while still
            # preventing transfer chains.
            hop_number = completed_hop_count + 1
            if attempt_number > max_attempts:
                raise InboundTransferError(
                    "inbound_transfer_attempt_limit",
                    "The inbound transfer attempt limit has been reached.",
                )
            if hop_number > max_hops:
                raise InboundTransferError(
                    "inbound_transfer_hop_limit",
                    "The inbound transfer hop limit has been reached.",
                )

            # The parent row lock serializes claims for this call. Delete only
            # this operation's expired tombstone, then claim the key before
            # any lease, child ledger, or PBX-visible identity is created.
            scope_key = _transfer_scope_key(str(row["id"]))
            await conn.execute(
                """
                DELETE FROM inbound_operation_idempotency
                WHERE scope_key=$1 AND operation=$2 AND idempotency_key=$3
                  AND expires_at <= NOW()
                """,
                scope_key,
                _TRANSFER_IDEMPOTENCY_OPERATION,
                normalized_idempotency_key,
            )
            idempotency_record_id = uuid.uuid4()
            claimed = await conn.fetchrow(
                """
                INSERT INTO inbound_operation_idempotency (
                    id, tenant_id, scope_key, operation, idempotency_key,
                    request_hash, actor_id, expires_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,NOW()+INTERVAL '24 hours')
                ON CONFLICT (scope_key, operation, idempotency_key) DO NOTHING
                RETURNING id
                """,
                idempotency_record_id,
                row["tenant_id"],
                scope_key,
                _TRANSFER_IDEMPOTENCY_OPERATION,
                normalized_idempotency_key,
                request_hash,
                normalized_actor_id,
            )
            if not claimed:
                replay = await _load_idempotent_transfer_attempt(
                    conn,
                    call_row=row,
                    idempotency_key=normalized_idempotency_key,
                    request_hash=request_hash,
                )
                if replay is None:
                    raise RuntimeError("transfer_idempotency_claim_unavailable")
                return replay
            idempotency_record_id = claimed["id"]

            lease = await limiter.acquire_lease(
                conn,
                tenant_id=str(row["tenant_id"]),
                call_id=str(row["id"]),
                talklee_call_id=str(row["talklee_call_id"]),
                lease_kind=LeaseKind.TRANSFER,
                request_id=f"transfer:{request_hash[:32]}",
                created_by=normalized_actor_id,
                metadata={
                    "destination": requested,
                    "mode": normalized_mode,
                    "source": normalized_source,
                    "attempt": attempt_number,
                    "hop": hop_number,
                    "idempotency_key": normalized_idempotency_key,
                    "actor_id": normalized_actor_id,
                    "actor_role": normalized_actor_role,
                    "actor_type": normalized_actor_type,
                },
            )
            if not lease.accepted or lease.lease_id is None:
                raise InboundTransferError(
                    "inbound_transfer_concurrency_limit",
                    "The tenant transfer concurrency limit has been reached.",
                )

            reservation_seconds = max(
                0,
                int(row.get("transfer_reservation_seconds") or 0),
            )
            if reservation_seconds <= 0:
                raise InboundTransferError(
                    "inbound_transfer_deadline_exhausted",
                    "The parent call has no reserved runtime remaining.",
                )
            quota = await conn.fetchrow(
                "SELECT minutes_allocated FROM tenants WHERE id=$1 FOR UPDATE",
                row["tenant_id"],
            )
            if not quota:
                raise InboundTransferError(
                    "inbound_transfer_billing_unavailable",
                    "Tenant quota is unavailable.",
                    status_code=503,
                )
            allocated_minutes = int(quota["minutes_allocated"] or 0)
            accounted_seconds = await fetch_tenant_accounted_usage_seconds(
                conn,
                tenant_id=str(row["tenant_id"]),
            )
            if (
                allocated_minutes > 0
                and accounted_seconds + reservation_seconds > allocated_minutes * 60
            ):
                raise InboundTransferError(
                    "insufficient_transfer_minutes",
                    (
                        "There are not enough unreserved minutes for the "
                        "transfer leg's remaining hard deadline."
                    ),
                )

            leg_id = uuid.uuid4()
            # Preallocate the exact ARI channel id before the adapter creates
            # the target. A restart in the initiated/ringing window otherwise
            # loses the in-memory parent/child relationship and recovery can
            # only hang up the parent. ``call_legs.provider_leg_id`` is the
            # existing durable field; no schema change is required.
            provider_leg_id = f"talky-xfer-{uuid.uuid4().hex[:20]}"
            metadata = {
                "mode": normalized_mode,
                "source": normalized_source,
                "attempt": attempt_number,
                "hop": hop_number,
                "lease_id": str(lease.lease_id),
                "reserved_seconds": reservation_seconds,
                "idempotency_record_id": str(idempotency_record_id),
                "request_hash": request_hash,
                "idempotency_key": normalized_idempotency_key,
                "actor_id": normalized_actor_id,
                "actor_role": normalized_actor_role,
                "actor_type": normalized_actor_type,
            }
            await conn.execute(
                """
                INSERT INTO call_legs (
                    id, call_id, talklee_call_id, leg_type, direction,
                    provider, provider_leg_id, to_number, status, started_at,
                    billing_status, reserved_seconds, cost, currency,
                    metadata, created_at, updated_at
                ) VALUES (
                    $1,$2,$3,'transfer','outbound',$4,$5,$6,'initiated',NOW(),
                    'reserved',$7,NULL,NULL,$8::jsonb,NOW(),NOW()
                )
                """,
                leg_id,
                row["id"],
                row["talklee_call_id"],
                row["provider"] or "asterisk",
                provider_leg_id,
                requested,
                reservation_seconds,
                json.dumps(metadata),
            )
            reservation_key = f"inbound:transfer:reserve:{leg_id}"
            reservation = await conn.fetchrow(
                """
                INSERT INTO inbound_usage_transactions (
                    tenant_id, call_id, call_leg_id, transaction_type,
                    quantity_seconds, amount, currency, idempotency_key,
                    policy_snapshot, metadata
                ) VALUES (
                    $1,$2,$3,'reserve',$4,NULL,NULL,$5,$6::jsonb,$7::jsonb
                )
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING id
                """,
                row["tenant_id"],
                row["id"],
                leg_id,
                reservation_seconds,
                reservation_key,
                json.dumps(
                    {
                        "allocated_minutes": allocated_minutes,
                        "accounted_seconds_at_reservation": accounted_seconds,
                        "reservation_seconds": reservation_seconds,
                        "billable_subject": "transfer_leg",
                        "hard_deadline_source": "parent_remaining_reservation",
                        "cost_authority": "carrier_cdr_unavailable",
                    }
                ),
                json.dumps(
                    {
                        "provider": row["provider"] or "asterisk",
                        "provider_leg_id": provider_leg_id,
                        "destination": requested,
                        "source": normalized_source,
                    }
                ),
            )
            if not reservation:
                raise RuntimeError("failed to create transfer usage reservation")
            metadata["usage_reservation_id"] = str(reservation["id"])
            await conn.execute(
                """
                UPDATE call_legs
                SET metadata=metadata || $2::jsonb, updated_at=NOW()
                WHERE id=$1
                """,
                leg_id,
                json.dumps({"usage_reservation_id": str(reservation["id"])}),
            )
            bound = await conn.fetchrow(
                """
                UPDATE inbound_operation_idempotency
                SET resource_type='call_leg', resource_id=$2::uuid
                WHERE id=$1::uuid AND tenant_id=$3::uuid
                  AND request_hash=$4
                RETURNING id
                """,
                idempotency_record_id,
                leg_id,
                row["tenant_id"],
                request_hash,
            )
            if not bound:
                raise RuntimeError("transfer_idempotency_bind_conflict")
            await conn.execute(
                """
                INSERT INTO inbound_audit_events (
                    tenant_id, event_type, actor_id, actor_role,
                    resource_type, resource_id, reason,
                    before_state, after_state, metadata, idempotency_key
                ) VALUES (
                    $1,'inbound_transfer_requested',$2,$3,
                    'call_leg',$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9
                )
                """,
                row["tenant_id"],
                normalized_actor_id,
                normalized_actor_role,
                leg_id,
                normalized_source,
                json.dumps({"call_status": str(row.get("status") or "")}),
                json.dumps(
                    {
                        "leg_status": "initiated",
                        "destination": requested,
                        "mode": normalized_mode,
                    }
                ),
                json.dumps(
                    {
                        "actor_type": normalized_actor_type,
                        "provider_leg_id": provider_leg_id,
                        "attempt": attempt_number,
                        "hop": hop_number,
                        "request_hash": request_hash,
                    }
                ),
                normalized_idempotency_key,
            )
            await conn.execute(
                """
                INSERT INTO call_events (
                    call_id, talklee_call_id, leg_id, event_type, source,
                    event_data, new_state, created_at
                ) VALUES ($1,$2,$3,'transfer_requested',$4,$5::jsonb,'transferring',NOW())
                """,
                row["id"],
                row["talklee_call_id"],
                leg_id,
                normalized_source,
                json.dumps(metadata),
            )

            failure_action = transfer_policy.get("failure_action")
            if failure_action not in {"voicemail", "return_to_agent", "hangup"}:
                failure_action = "hangup"
            return InboundTransferAttempt(
                inbound=True,
                call_id=str(row["id"]),
                tenant_id=str(row["tenant_id"]),
                talklee_call_id=str(row["talklee_call_id"]),
                leg_id=str(leg_id),
                provider_leg_id=provider_leg_id,
                lease_id=str(lease.lease_id),
                usage_reservation_id=str(reservation["id"]),
                reserved_seconds=reservation_seconds,
                destination=requested,
                attempt_number=attempt_number,
                hop_number=hop_number,
                failure_action=str(failure_action),
                idempotency_record_id=str(idempotency_record_id),
                idempotency_key=normalized_idempotency_key,
                request_hash=request_hash,
                actor_id=normalized_actor_id,
                actor_role=normalized_actor_role,
                actor_type=normalized_actor_type,
            )


async def complete_inbound_transfer(
    pool,
    *,
    attempt: InboundTransferAttempt,
    succeeded: bool,
    result: Optional[Mapping[str, Any]] = None,
    redis_client: Any = None,
) -> None:
    """Persist the supervised target outcome.

    A successful answer keeps the transfer lease active: the human target is
    still using a live outbound leg. The parent call's terminal lifecycle owns
    the eventual leg completion and lease release. A pre-answer failure becomes
    terminal and releases capacity only after the adapter proves the target
    channel absent. Timeouts, cancellation, and provider uncertainty remain
    active ``cleanup_pending`` obligations for restart/watchdog recovery.
    """

    if not attempt.inbound or not attempt.call_id or not attempt.leg_id:
        return
    limiter = TelephonyConcurrencyLimiter(redis_client)
    async with acquire_with_tenant(pool, None) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            call_state = await conn.fetchrow(
                """
                SELECT tenant_id, talklee_call_id, status,
                       processing_status, ended_at
                FROM calls
                WHERE id=$1::uuid
                FOR UPDATE
                """,
                attempt.call_id,
            )
            if not call_state:
                return
            leg = await conn.fetchrow(
                """
                SELECT id, status, metadata, provider_leg_id,
                       billing_status, reserved_seconds,
                       started_at, answered_at, duration_seconds,
                       CASE
                           WHEN answered_at IS NOT NULL THEN GREATEST(
                               0,
                               FLOOR(EXTRACT(EPOCH FROM (NOW()-answered_at)))::int
                           )
                           ELSE 0
                       END AS actual_duration_seconds
                FROM call_legs
                WHERE id=$1::uuid AND call_id=$2::uuid
                  AND leg_type='transfer'
                FOR UPDATE
                """,
                attempt.leg_id,
                attempt.call_id,
            )
            if not leg:
                return

            from app.domain.services.call_status import TERMINAL_CALL_STATUSES

            parent_terminal = (
                bool(call_state["ended_at"])
                or str(call_state["processing_status"] or "") in {"completed", "failed", "released"}
                or str(call_state["status"] or "") in TERMINAL_CALL_STATUSES
            )
            result_status = str((result or {}).get("status") or "").strip().lower()
            cleanup_pending = result_status in {
                "cleanup_pending",
                "unconfirmed",
                "termination_unconfirmed",
            }
            # The ID authorized and persisted before ARI channel creation is
            # authoritative. Provider result payloads are observations only;
            # they may never replace the restart-safe planned channel id.
            provider_leg_id = str(
                attempt.provider_leg_id or leg.get("provider_leg_id") or ""
            ).strip()
            if cleanup_pending:
                # Request failure/timeout is not PBX absence proof. Preserve
                # the active status, exact usage reservation, and concurrency
                # lease until restart/watchdog recovery proves target absence.
                pending_payload = {
                    "destination": attempt.destination,
                    "attempt": attempt.attempt_number,
                    "hop": attempt.hop_number,
                    "result": dict(result or {}),
                    "cleanup_pending": True,
                }
                updated = await conn.fetchrow(
                    """
                    UPDATE call_legs
                    SET provider_leg_id=COALESCE(NULLIF($3,''),provider_leg_id),
                        metadata=COALESCE(metadata,'{}'::jsonb) || $4::jsonb,
                        updated_at=NOW()
                    WHERE id=$1::uuid AND call_id=$2::uuid
                      AND status IN ('initiated','ringing','answered')
                      AND billing_status='reserved'
                    RETURNING id, status
                    """,
                    attempt.leg_id,
                    attempt.call_id,
                    provider_leg_id,
                    json.dumps({"result": dict(result or {}), "cleanup_pending": True}),
                )
                if updated:
                    await conn.execute(
                        """
                        INSERT INTO call_events (
                            call_id, talklee_call_id, leg_id, event_type, source,
                            event_data, new_state, created_at
                        ) VALUES (
                            $1::uuid,$2,$3::uuid,'transfer_cleanup_pending',
                            'inbound_transfer',$4::jsonb,'transferring',NOW()
                        )
                        """,
                        attempt.call_id,
                        call_state["talklee_call_id"] or attempt.talklee_call_id,
                        attempt.leg_id,
                        json.dumps(pending_payload),
                    )
                    await _store_transfer_idempotency_response(
                        conn,
                        attempt=attempt,
                        result=dict(result or {}),
                    )
                return

            previous_status = str(leg.get("status") or "")
            if previous_status not in {"initiated", "ringing", "answered"}:
                return
            leg_metadata = _json_object(leg.get("metadata"))
            if succeeded and (
                previous_status != "answered"
                or leg_metadata.get("provider_answer_persisted") is not True
            ):
                raise RuntimeError("transfer_answer_proof_missing")
            payload = {
                "destination": attempt.destination,
                "attempt": attempt.attempt_number,
                "hop": attempt.hop_number,
                "result": dict(result or {}),
            }
            if succeeded and not parent_terminal:
                # Provider Answer must already be durable before the adapter
                # can expose success. This second transition records the
                # completed handoff/result exactly once while retaining the
                # live child reservation and lease until terminal proof.
                updated = await conn.fetchrow(
                    """
                    UPDATE call_legs
                    SET status='answered', answered_at=COALESCE(answered_at,NOW()),
                        provider_leg_id=COALESCE(NULLIF($3,''),provider_leg_id),
                        metadata=COALESCE(metadata,'{}'::jsonb) || $4::jsonb,
                        updated_at=NOW()
                    WHERE id=$1::uuid AND call_id=$2::uuid
                      AND status='answered'
                      AND billing_status='reserved'
                      AND COALESCE(metadata->>'handoff_persisted','false') <> 'true'
                    RETURNING id
                    """,
                    attempt.leg_id,
                    attempt.call_id,
                    provider_leg_id,
                    json.dumps(
                        {
                            "result": dict(result or {}),
                            "handoff_persisted": True,
                        }
                    ),
                )
                if not updated:
                    return
                await conn.execute(
                    """
                    INSERT INTO call_events (
                        call_id, talklee_call_id, leg_id, event_type, source,
                        event_data, new_state, created_at
                    ) VALUES (
                        $1::uuid,$2,$3::uuid,'transfer_connected',
                        'inbound_transfer',$4::jsonb,'transferred',NOW()
                    )
                    """,
                    attempt.call_id,
                    call_state["talklee_call_id"] or attempt.talklee_call_id,
                    attempt.leg_id,
                    json.dumps(payload),
                )
                await _store_transfer_idempotency_response(
                    conn,
                    attempt=attempt,
                    result=dict(result or {}),
                )
                return

            was_answered = previous_status == "answered" or succeeded
            if previous_status == "answered" and not leg.get("answered_at"):
                raise RuntimeError("transfer_answer_timestamp_missing")
            actual_seconds = (
                int(leg.get("actual_duration_seconds") or 0) if previous_status == "answered" else 0
            )
            terminal_status = "completed" if succeeded else "failed"
            event_type = "transfer_completed" if succeeded else "transfer_failed"
            terminal_payload = {
                **payload,
                "previous_status": previous_status,
                "terminal_status": terminal_status,
                "terminal_before_answer": not was_answered,
                "terminal_reason": (
                    "parent_already_terminal" if parent_terminal else result_status
                ),
            }
            lease_id = attempt.lease_id or _json_object(leg["metadata"]).get("lease_id")
            if not lease_id:
                raise RuntimeError("transfer_concurrency_lease_missing")
            settled = await _settle_transfer_usage(
                conn,
                leg=leg,
                call_id=attempt.call_id,
                tenant_id=str(call_state["tenant_id"]),
                terminal_status=terminal_status,
                actual_seconds=actual_seconds,
                release_only=not was_answered,
                reason=(
                    "parent_already_terminal"
                    if parent_terminal
                    else result_status or "provider_target_absent"
                ),
                provider_leg_id=provider_leg_id,
                metadata=terminal_payload,
                answered=was_answered,
            )
            if not settled:
                return
            await conn.execute(
                """
                INSERT INTO call_events (
                    call_id, talklee_call_id, leg_id, event_type, source,
                    event_data, new_state, created_at
                ) VALUES ($1::uuid,$2,$3::uuid,$4,'inbound_transfer',$5::jsonb,$6,NOW())
                """,
                attempt.call_id,
                call_state["talklee_call_id"] or attempt.talklee_call_id,
                attempt.leg_id,
                event_type,
                json.dumps(terminal_payload),
                terminal_status,
            )
            await limiter.release_lease(
                conn,
                tenant_id=str(call_state["tenant_id"]),
                lease_id=str(lease_id),
                reason=terminal_status,
                request_id=f"transfer-complete:{attempt.leg_id}",
            )
            await _store_transfer_idempotency_response(
                conn,
                attempt=attempt,
                result=dict(result or {}),
            )


async def finalize_connected_inbound_transfers(
    pool,
    *,
    call_id: str,
    terminal_reason: Optional[str] = None,
    redis_client: Any = None,
    hold_ambiguous_transfer_legs: bool = False,
) -> int:
    """End connected target legs and release their leases with the parent.

    Idempotency comes from locking only still-active transfer rows. A duplicate
    parent terminal event sees zero rows and performs no second release/event.

    Restart recovery must not interpret a pre-Answer durable status as proof
    that the carrier target never answered: the process may have crashed after
    provider Answer but before the mandatory Answer transaction committed. In
    that narrow path, ``hold_ambiguous_transfer_legs`` preserves the complete
    reservation for carrier-CDR reconciliation while still releasing the
    concurrency lease after all-leg PBX absence has been proved.
    """
    limiter = TelephonyConcurrencyLimiter(redis_client)
    async with acquire_with_tenant(pool, None) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            call_row = await conn.fetchrow(
                "SELECT tenant_id, talklee_call_id FROM calls WHERE id=$1::uuid FOR UPDATE",
                call_id,
            )
            if not call_row:
                return 0
            rows = list(
                await conn.fetch(
                    """
                    SELECT id, status, metadata, provider_leg_id,
                           billing_status, reserved_seconds,
                           started_at, answered_at, duration_seconds,
                           CASE
                               WHEN answered_at IS NOT NULL THEN GREATEST(
                                   0,
                                   FLOOR(EXTRACT(EPOCH FROM (NOW()-answered_at)))::int
                               )
                               ELSE 0
                           END AS actual_duration_seconds
                    FROM call_legs
                    WHERE call_id=$1::uuid AND leg_type='transfer'
                      AND status IN ('initiated','ringing','answered')
                    ORDER BY created_at, id
                    FOR UPDATE
                    """,
                    call_id,
                )
            )
            finalized = 0
            for row in rows:
                metadata = _json_object(row["metadata"])
                previous_status = str(row["status"] or "")
                was_answered = previous_status == "answered"
                if was_answered and not row.get("answered_at"):
                    # Never turn an unprovable answered duration into a
                    # fabricated zero-second settlement.
                    raise RuntimeError("transfer_answer_timestamp_missing")
                terminal_status = "completed" if was_answered else "failed"
                event_type = "transfer_completed" if was_answered else "transfer_failed"
                event_payload = {
                    "terminal_reason": terminal_reason,
                    "previous_status": previous_status,
                    "terminal_status": terminal_status,
                    "terminal_before_answer": not was_answered,
                }
                lease_id = metadata.get("lease_id")
                if not lease_id:
                    raise RuntimeError("transfer_concurrency_lease_missing")
                if hold_ambiguous_transfer_legs and not was_answered:
                    event_payload.update(
                        {
                            "terminal_status": "reconciliation_required",
                            "terminal_reason": (terminal_reason or "process_restart_recovery"),
                            "restart_answer_state_ambiguous": True,
                            "provider_absence_proven": True,
                            "reservation_preserved": True,
                            "cost_authority": "carrier_cdr_required",
                        }
                    )
                    held = await conn.fetchrow(
                        """
                        UPDATE call_legs
                        SET status='reconciliation_required',
                            billing_status='held',
                            ended_at=COALESCE(ended_at,NOW()),
                            cost=NULL, currency=NULL,
                            metadata=COALESCE(metadata,'{}'::jsonb) || $3::jsonb,
                            updated_at=NOW()
                        WHERE id=$1::uuid AND call_id=$2::uuid
                          AND status=$4 AND billing_status='reserved'
                        RETURNING id
                        """,
                        row["id"],
                        call_id,
                        json.dumps(event_payload),
                        previous_status,
                    )
                    if not held:
                        raise RuntimeError("transfer_reconciliation_hold_conflict")
                    await conn.execute(
                        """
                        INSERT INTO call_events (
                            call_id, talklee_call_id, leg_id, event_type, source,
                            event_data, new_state, created_at
                        ) VALUES (
                            $1::uuid,$2,$3::uuid,'transfer_billing_held',
                            'inbound_transfer',$4::jsonb,
                            'reconciliation_required',NOW()
                        )
                        """,
                        call_id,
                        call_row["talklee_call_id"],
                        row["id"],
                        json.dumps(event_payload),
                    )
                    await limiter.release_lease(
                        conn,
                        tenant_id=str(call_row["tenant_id"]),
                        lease_id=str(lease_id),
                        reason="restart_answer_state_ambiguous",
                        request_id=f"transfer-terminal:{row['id']}",
                    )
                    await _store_persisted_transfer_terminal_response(
                        conn,
                        tenant_id=str(call_row["tenant_id"]),
                        call_id=call_id,
                        leg=row,
                        terminal_status="reconciliation_required",
                        reason=terminal_reason,
                    )
                    finalized += 1
                    continue
                settled = await _settle_transfer_usage(
                    conn,
                    leg=row,
                    call_id=call_id,
                    tenant_id=str(call_row["tenant_id"]),
                    terminal_status=terminal_status,
                    actual_seconds=(
                        int(row.get("actual_duration_seconds") or 0) if was_answered else 0
                    ),
                    release_only=not was_answered,
                    reason=(
                        str(terminal_reason or "parent_terminated")
                        if was_answered
                        else "parent_terminated_before_transfer_answer"
                    ),
                    provider_leg_id=str(row.get("provider_leg_id") or ""),
                    metadata=event_payload,
                    answered=was_answered,
                )
                if not settled:
                    continue
                await conn.execute(
                    """
                    INSERT INTO call_events (
                        call_id, talklee_call_id, leg_id, event_type, source,
                        event_data, new_state, created_at
                    ) VALUES (
                        $1::uuid,$2,$3::uuid,$4,'inbound_transfer',
                        $5::jsonb,$6,NOW()
                    )
                    """,
                    call_id,
                    call_row["talklee_call_id"],
                    row["id"],
                    event_type,
                    json.dumps(event_payload),
                    terminal_status,
                )
                await limiter.release_lease(
                    conn,
                    tenant_id=str(call_row["tenant_id"]),
                    lease_id=str(lease_id),
                    reason=(
                        "completed" if was_answered else "parent_terminated_before_transfer_answer"
                    ),
                    request_id=f"transfer-terminal:{row['id']}",
                )
                await _store_persisted_transfer_terminal_response(
                    conn,
                    tenant_id=str(call_row["tenant_id"]),
                    call_id=call_id,
                    leg=row,
                    terminal_status=terminal_status,
                    reason=terminal_reason,
                )
                finalized += 1

            unsettled = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM call_legs
                WHERE call_id=$1::uuid AND leg_type='transfer'
                  AND (
                      status IN ('initiated','ringing','answered')
                      OR billing_status NOT IN (
                          'finalized','released','reversed'
                      )
                  )
                  AND NOT (
                      status='reconciliation_required'
                      AND billing_status='held'
                      AND LOWER(COALESCE(
                          metadata->>'restart_answer_state_ambiguous',''
                      ))='true'
                  )
                """,
                call_id,
            )
            if int(unsettled or 0) > 0:
                # This exception rolls back every child ledger/projection and
                # lease mutation in this transaction. The durable parent
                # cleanup obligation remains retryable and cannot advance to
                # parent settlement while any child cost reservation is live.
                raise RuntimeError("transfer_usage_nonterminal")
            return finalized


async def mark_inbound_transfer_answered(
    pool: Any,
    *,
    parent_call_id: str,
    provider_leg_id: str,
) -> int:
    """Persist provider Answer before the adapter exposes handoff success.

    This is intentionally a small, mandatory transaction on the Asterisk
    answer path. If it cannot prove and commit the exact active child, the
    adapter must fail the handoff and clean both human legs. A crash after this
    commit can no longer misclassify an answered, billable target as a
    zero-second pre-answer release.
    """

    normalized_parent = str(parent_call_id or "").strip()
    normalized_leg = str(provider_leg_id or "").strip()
    if not normalized_parent or not normalized_leg:
        raise ValueError("parent_call_id and provider_leg_id are required")

    async with acquire_with_tenant(pool, None) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            row = await conn.fetchrow(
                """
                SELECT l.id, l.call_id, l.status, l.answered_at,
                       l.billing_status, l.reserved_seconds, l.metadata,
                       c.talklee_call_id, c.status AS parent_status,
                       c.processing_status AS parent_processing_status,
                       c.billing_status AS parent_billing_status
                FROM call_legs l
                JOIN calls c ON c.id=l.call_id
                WHERE (c.provider_call_id=$1 OR c.external_call_uuid=$1)
                  AND l.leg_type='transfer'
                  AND l.provider_leg_id=$2
                ORDER BY l.created_at DESC
                LIMIT 1
                FOR UPDATE OF c,l
                """,
                normalized_parent,
                normalized_leg,
            )
            if not row:
                raise RuntimeError("transfer_answer_leg_missing")

            from app.domain.services.call_status import TERMINAL_CALL_STATUSES

            if (
                str(row.get("parent_status") or "") == "termination_pending"
                or str(row.get("parent_status") or "") in TERMINAL_CALL_STATUSES
                or str(row.get("parent_processing_status") or "") != "active"
                or str(row.get("parent_billing_status") or "") != "reserved"
            ):
                raise RuntimeError("transfer_answer_parent_not_active")
            if (
                str(row.get("billing_status") or "") != "reserved"
                or int(row.get("reserved_seconds") or 0) <= 0
            ):
                raise RuntimeError("transfer_answer_reservation_missing")

            previous_status = str(row.get("status") or "")
            metadata = _json_object(row.get("metadata"))
            if previous_status == "answered":
                if not row.get("answered_at"):
                    raise RuntimeError("transfer_answer_timestamp_missing")
                if metadata.get("provider_answer_persisted") is not True:
                    raise RuntimeError("transfer_answer_proof_missing")
                return 0
            if previous_status not in {"initiated", "ringing"}:
                raise RuntimeError("transfer_answer_leg_not_active")

            answer_payload = {
                "provider_answer_persisted": True,
                "provider_leg_id": normalized_leg,
                "previous_status": previous_status,
            }
            updated = await conn.fetchrow(
                """
                UPDATE call_legs
                SET status='answered', answered_at=COALESCE(answered_at,NOW()),
                    metadata=COALESCE(metadata,'{}'::jsonb) || $3::jsonb,
                    updated_at=NOW()
                WHERE id=$1::uuid AND call_id=$2::uuid
                  AND status IN ('initiated','ringing')
                  AND billing_status='reserved'
                  AND reserved_seconds > 0
                RETURNING id
                """,
                row["id"],
                row["call_id"],
                json.dumps(answer_payload),
            )
            if not updated:
                raise RuntimeError("transfer_answer_projection_conflict")
            await conn.execute(
                """
                INSERT INTO call_events (
                    call_id, talklee_call_id, leg_id, event_type, source,
                    event_data, previous_state, new_state, created_at
                ) VALUES (
                    $1,$2,$3,'transfer_target_answered','asterisk',
                    $4::jsonb,$5,'answered',NOW()
                )
                """,
                row["call_id"],
                row["talklee_call_id"],
                row["id"],
                json.dumps(answer_payload),
                previous_status,
            )
            return 1


async def finalize_proven_inbound_transfer_cleanup(
    pool,
    *,
    parent_call_id: str,
    provider_leg_id: str,
    reason: Optional[str] = None,
    redis_client: Any = None,
) -> int:
    """Converge a cleanup-pending child after target-only absence proof.

    A bounded transfer API request can return ``cleanup_pending`` while the
    adapter keeps proving that its preallocated target channel is gone. The
    parent AI call remains live, so waiting for parent teardown would retain a
    tenant transfer slot unnecessarily. The adapter invokes this callback only
    after target absence proof and must retain/retry its cleanup index if this
    function raises. The active-row lock makes duplicate proof callbacks
    idempotent.
    """

    normalized_parent = str(parent_call_id or "").strip()
    normalized_leg = str(provider_leg_id or "").strip()
    if not normalized_parent or not normalized_leg:
        raise ValueError("parent_call_id and provider_leg_id are required")

    limiter = TelephonyConcurrencyLimiter(redis_client)
    async with acquire_with_tenant(pool, None) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            row = await conn.fetchrow(
                """
                SELECT l.id, l.call_id, l.status, l.metadata,
                       l.provider_leg_id, l.billing_status,
                       l.reserved_seconds, l.started_at, l.answered_at,
                       l.duration_seconds,
                       CASE
                           WHEN l.answered_at IS NOT NULL THEN GREATEST(
                               0,
                               FLOOR(EXTRACT(EPOCH FROM (
                                   NOW()-l.answered_at
                               )))::int
                           )
                           ELSE 0
                       END AS actual_duration_seconds,
                       c.tenant_id, c.talklee_call_id
                FROM call_legs l
                JOIN calls c ON c.id=l.call_id
                WHERE (c.provider_call_id=$1 OR c.external_call_uuid=$1)
                  AND l.leg_type='transfer'
                  AND l.provider_leg_id=$2
                  AND l.status IN ('initiated','ringing','answered')
                ORDER BY l.created_at DESC
                LIMIT 1
                FOR UPDATE OF c,l
                """,
                normalized_parent,
                normalized_leg,
            )
            if not row:
                # The parent terminal path or an earlier proof callback already
                # converged the leg. Treat that as idempotent success.
                return 0

            metadata = _json_object(row["metadata"])
            lease_id = metadata.get("lease_id")
            if not lease_id:
                raise RuntimeError("transfer_concurrency_lease_missing")
            proof_payload = {
                "cleanup_confirmed": True,
                "cleanup_reason": str(reason or "target_absent")[:128],
                "provider_leg_id": normalized_leg,
                "previous_status": str(row["status"] or ""),
                "terminal_status": "failed",
            }
            was_answered = str(row["status"] or "") == "answered"
            if was_answered and not row.get("answered_at"):
                raise RuntimeError("transfer_answer_timestamp_missing")
            settled = await _settle_transfer_usage(
                conn,
                leg=row,
                call_id=str(row["call_id"]),
                tenant_id=str(row["tenant_id"]),
                terminal_status="failed",
                actual_seconds=(
                    int(row.get("actual_duration_seconds") or 0) if was_answered else 0
                ),
                release_only=not was_answered,
                reason=str(reason or "target_absent")[:128],
                provider_leg_id=normalized_leg,
                metadata=proof_payload,
                answered=was_answered,
            )
            if not settled:
                return 0

            await conn.execute(
                """
                INSERT INTO call_events (
                    call_id, talklee_call_id, leg_id, event_type, source,
                    event_data, new_state, created_at
                ) VALUES (
                    $1,$2,$3,'transfer_failed','inbound_transfer',
                    $4::jsonb,'in_call',NOW()
                )
                """,
                row["call_id"],
                row["talklee_call_id"],
                row["id"],
                json.dumps(proof_payload),
            )
            await limiter.release_lease(
                conn,
                tenant_id=str(row["tenant_id"]),
                lease_id=str(lease_id),
                reason="transfer_target_absent",
                request_id=f"transfer-cleanup:{row['id']}",
            )
            await _store_persisted_transfer_terminal_response(
                conn,
                tenant_id=str(row["tenant_id"]),
                call_id=str(row["call_id"]),
                leg=row,
                terminal_status="failed",
                reason=str(reason or "target_absent")[:128],
            )
            return 1


def _isoformat_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


async def get_inbound_transfer_attempt(
    pool: Any,
    *,
    attempt_id: str,
    tenant_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return one tenant-scoped durable transfer status projection."""

    try:
        normalized_attempt_id = str(uuid.UUID(str(attempt_id or "").strip()))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("attempt_id must be a UUID") from exc
    normalized_tenant_id: Optional[str] = None
    if tenant_id is not None:
        try:
            normalized_tenant_id = str(uuid.UUID(str(tenant_id).strip()))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("tenant_id must be a UUID") from exc

    async with acquire_with_tenant(pool, normalized_tenant_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT l.id, l.call_id, l.talklee_call_id, l.provider_leg_id,
                   l.to_number, l.status, l.billing_status,
                   l.reserved_seconds, l.duration_seconds, l.cost, l.currency,
                   l.started_at, l.answered_at, l.ended_at, l.metadata,
                   c.tenant_id,
                   idem.idempotency_key, idem.response_body, idem.status_code
            FROM call_legs l
            JOIN calls c ON c.id=l.call_id
            LEFT JOIN LATERAL (
                SELECT idempotency_key, response_body, status_code
                FROM inbound_operation_idempotency
                WHERE resource_type='call_leg'
                  AND resource_id=l.id
                  AND operation=$3
                ORDER BY created_at DESC
                LIMIT 1
            ) idem ON TRUE
            WHERE l.id=$1::uuid AND l.leg_type='transfer'
              AND ($2::uuid IS NULL OR c.tenant_id=$2::uuid)
            LIMIT 1
            """,
            normalized_attempt_id,
            normalized_tenant_id,
            _TRANSFER_IDEMPOTENCY_OPERATION,
        )
    if not row:
        return None

    metadata = _json_object(row.get("metadata"))
    stored_response = (
        _json_object(row.get("response_body")) if row.get("response_body") is not None else None
    )
    raw_status = str(row.get("status") or "initiated").strip().lower()
    if raw_status in {"initiated", "ringing"}:
        operation_status = "cleanup_pending" if metadata.get("cleanup_pending") else "in_progress"
    elif raw_status == "answered":
        operation_status = "transferred"
    else:
        operation_status = raw_status
    if stored_response and stored_response.get("status"):
        operation_status = str(stored_response["status"]).strip().lower()

    return {
        "attempt_id": str(row["id"]),
        "call_id": str(row["call_id"]),
        "talklee_call_id": row.get("talklee_call_id"),
        "tenant_id": str(row["tenant_id"]),
        "provider_leg_id": row.get("provider_leg_id"),
        "destination": row.get("to_number"),
        "mode": metadata.get("mode"),
        "source": metadata.get("source"),
        "status": operation_status,
        "leg_status": raw_status,
        "terminal": raw_status in {"completed", "failed", "cancelled"},
        "reconciliation_required": (
            raw_status == "reconciliation_required"
            or metadata.get("restart_answer_state_ambiguous") is True
        ),
        "billing_status": str(row.get("billing_status") or "none"),
        "reserved_seconds": max(0, int(row.get("reserved_seconds") or 0)),
        "duration_seconds": (
            max(0, int(row["duration_seconds"]))
            if row.get("duration_seconds") is not None
            else None
        ),
        "cost": (str(row["cost"]) if row.get("cost") is not None else None),
        "currency": row.get("currency"),
        "started_at": _isoformat_or_none(row.get("started_at")),
        "answered_at": _isoformat_or_none(row.get("answered_at")),
        "ended_at": _isoformat_or_none(row.get("ended_at")),
        "idempotency_key": (row.get("idempotency_key") or metadata.get("idempotency_key")),
        "http_status": row.get("status_code"),
        "result": stored_response or _json_object(metadata.get("result")) or None,
    }

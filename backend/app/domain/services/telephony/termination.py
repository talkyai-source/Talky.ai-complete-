"""Confirmation-aware telephony termination primitives.

Provider/PBX control APIs commonly acknowledge a hangup request before the
channel is actually gone.  Call-row finalization, concurrency release, and
billing settlement must therefore consume an explicit absence proof rather
than infer termination from a successful request dispatch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.db_utils import acquire_with_tenant


_ACTIVE_PROVIDER_LEG_STATUSES = (
    "initiated",
    "ringing",
    "answered",
    "in_progress",
    "in_call",
    "active",
)


@dataclass(frozen=True, slots=True)
class HangupProof:
    """Truthful result of one bounded provider hangup attempt."""

    requested: bool
    confirmed: bool
    code: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class TerminationContext:
    """Durable call identity and the linked legs fenced for termination."""

    call_id: str
    tenant_id: str | None
    provider_call_id: str | None
    previous_status: str
    provider_leg_ids: tuple[str, ...]
    provider: str | None = None
    direction: str = "outbound"
    campaign_id: str | None = None
    answered_at: Any = None


def _safe_error(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:300]


async def request_confirmed_hangup(
    adapter: Any,
    call_id: str,
    *,
    provider_leg_ids: Iterable[str] = (),
    timeout_s: float = 5.0,
) -> HangupProof:
    """Request a hangup and return proof only when the adapter can provide it.

    ``hangup_confirmed`` is an optional capability.  Legacy adapters that only
    expose ``hangup`` may still receive the request, but their acknowledgement
    is deliberately returned as unconfirmed so no caller can settle billing or
    project a terminal database state prematurely.
    """

    call_id = str(call_id or "").strip()
    if not call_id:
        return HangupProof(False, False, "missing_provider_call_id")
    if adapter is None:
        return HangupProof(False, False, "adapter_unavailable")

    timeout_s = max(0.1, min(5.0, float(timeout_s)))
    linked_ids = tuple(
        dict.fromkeys(
            normalized
            for value in provider_leg_ids
            if (normalized := str(value or "").strip())
            and normalized != call_id
        )
    )
    if linked_ids:
        confirmed_many = getattr(adapter, "hangup_many_confirmed", None)
        if not callable(confirmed_many):
            # Proving the parent alone is insufficient once PostgreSQL says a
            # linked provider leg may still be live. Do not silently fall back
            # to the single-leg capability and manufacture a terminal state.
            return HangupProof(
                False,
                False,
                "linked_leg_confirmation_unsupported",
            )
        try:
            confirmed = bool(
                await asyncio.wait_for(
                    confirmed_many((call_id, *linked_ids)),
                    timeout=timeout_s,
                )
            )
        except (asyncio.TimeoutError, TimeoutError):
            return HangupProof(True, False, "confirmation_timeout")
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return HangupProof(True, False, "provider_error", _safe_error(exc))
        return HangupProof(
            True,
            confirmed,
            "confirmed" if confirmed else "hangup_unconfirmed",
        )

    confirmed_hangup = getattr(adapter, "hangup_confirmed", None)
    if callable(confirmed_hangup):
        try:
            confirmed = bool(
                await asyncio.wait_for(confirmed_hangup(call_id), timeout=timeout_s)
            )
        except (asyncio.TimeoutError, TimeoutError):
            return HangupProof(True, False, "confirmation_timeout")
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return HangupProof(True, False, "provider_error", _safe_error(exc))
        return HangupProof(
            True,
            confirmed,
            "confirmed" if confirmed else "hangup_unconfirmed",
        )

    hangup = getattr(adapter, "hangup", None)
    if not callable(hangup):
        return HangupProof(False, False, "hangup_unsupported")
    try:
        await asyncio.wait_for(hangup(call_id), timeout=timeout_s)
    except (asyncio.TimeoutError, TimeoutError):
        return HangupProof(True, False, "confirmation_timeout")
    except Exception as exc:  # noqa: BLE001 - provider boundary
        return HangupProof(True, False, "provider_error", _safe_error(exc))
    return HangupProof(True, False, "confirmation_unsupported")


async def load_active_provider_leg_ids(
    pool: Any,
    *,
    call_reference: str,
    tenant_id: str | None = None,
    timeout_s: float = 5.0,
) -> tuple[str, ...]:
    """Load every durable, potentially-live provider leg for one call.

    ``call_reference`` may be the durable UUID, Talky call ID, parent provider
    ID, or external provider UUID. This intentionally reads PostgreSQL rather
    than adapter memory: administrative and restart teardown commonly run in a
    process whose transfer indexes are empty. Query/dependency failures escape
    so callers fail closed instead of proving only the parent.

    The returned tuple excludes the old synthetic ``transfer-*`` placeholders,
    which were never real provider channel IDs. Callers should pass it to
    :func:`request_confirmed_hangup`; that primitive de-duplicates the parent
    and requires the adapter's one-deadline multi-leg proof capability.
    """

    normalized_reference = str(call_reference or "").strip()
    if not normalized_reference:
        raise ValueError("call_reference is required")
    bounded_timeout = max(0.1, min(5.0, float(timeout_s)))
    async with acquire_with_tenant(
        pool,
        str(tenant_id) if tenant_id is not None else None,
        timeout=bounded_timeout,
    ) as conn:
        return await fetch_active_provider_leg_ids(
            conn,
            call_reference=normalized_reference,
        )


async def fetch_active_provider_leg_ids(
    conn: Any,
    *,
    call_reference: str,
) -> tuple[str, ...]:
    """Connection-level form used when a caller already holds an RLS scope."""

    normalized_reference = str(call_reference or "").strip()
    if not normalized_reference:
        raise ValueError("call_reference is required")
    rows = list(
        await conn.fetch(
            """
            SELECT l.provider_leg_id
            FROM call_legs l
            JOIN calls c ON c.id=l.call_id
            WHERE (
                   c.id::text=$1
                OR c.talklee_call_id=$1
                OR c.provider_call_id=$1
                OR c.external_call_uuid=$1
            )
              AND l.status IN (
                  'initiated','ringing','answered',
                  'in_progress','in_call','active'
              )
              AND l.provider_leg_id IS NOT NULL
              AND BTRIM(l.provider_leg_id) <> ''
              AND l.provider_leg_id NOT LIKE 'transfer-%'
            ORDER BY l.created_at, l.id
            """,
            normalized_reference,
        )
    )
    return tuple(
        dict.fromkeys(
            normalized
            for row in rows
            if (normalized := str(row["provider_leg_id"] or "").strip())
        )
    )


async def mark_termination_pending_and_load_context(
    pool: Any,
    *,
    call_reference: str,
    tenant_id: str | None = None,
    timeout_s: float = 5.0,
) -> TerminationContext:
    """Fence one call against new work and snapshot every persisted live leg.

    The call row is locked before its non-terminal status becomes
    ``termination_pending``. Transfer authorization locks the same row, so a
    transfer cannot be authorized after this fence. The linked-leg snapshot is
    taken in the same transaction. Terminal rows remain terminal, but callers
    still receive their provider identities so a stale database state is never
    mistaken for PBX absence.

    The pending marker is deliberately non-terminal: it releases no billing,
    quota, or concurrency resources. If the immediate provider proof fails,
    the recovery loop owns the retry within its normal <=30-second cadence.
    """

    normalized_reference = str(call_reference or "").strip()
    if not normalized_reference:
        raise ValueError("call_reference is required")
    bounded_timeout = max(0.1, min(5.0, float(timeout_s)))
    async with acquire_with_tenant(
        pool,
        str(tenant_id) if tenant_id is not None else None,
        timeout=bounded_timeout,
    ) as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text AS call_id,
                   tenant_id::text AS tenant_id,
                   COALESCE(provider_call_id, external_call_uuid)
                       AS provider_call_id,
                   status, provider, direction, campaign_id::text AS campaign_id,
                   answered_at
            FROM calls
            WHERE (
                   id::text=$1
                OR talklee_call_id=$1
                OR provider_call_id=$1
                OR external_call_uuid=$1
            )
            ORDER BY created_at DESC
            LIMIT 1
            FOR UPDATE
            """,
            normalized_reference,
        )
        if not row:
            raise LookupError("call not found")

        previous_status = str(row["status"] or "")
        from app.domain.services.call_status import TERMINAL_CALL_STATUSES

        if previous_status not in TERMINAL_CALL_STATUSES:
            await conn.execute(
                """
                UPDATE calls
                   SET status='termination_pending', updated_at=NOW()
                 WHERE id=$1::uuid
                   AND status <> ALL($2::text[])
                """,
                str(row["call_id"]),
                list(TERMINAL_CALL_STATUSES),
            )

        leg_rows = list(
            await conn.fetch(
                """
                SELECT provider_leg_id
                FROM call_legs
                WHERE call_id=$1::uuid
                  AND status = ANY($2::text[])
                  AND provider_leg_id IS NOT NULL
                  AND BTRIM(provider_leg_id) <> ''
                  AND provider_leg_id NOT LIKE 'transfer-%'
                ORDER BY created_at, id
                """,
                str(row["call_id"]),
                list(_ACTIVE_PROVIDER_LEG_STATUSES),
            )
        )

    return TerminationContext(
        call_id=str(row["call_id"]),
        tenant_id=(str(row["tenant_id"]) if row["tenant_id"] else None),
        provider_call_id=(
            str(row["provider_call_id"]).strip()
            if row["provider_call_id"]
            else None
        ),
        previous_status=previous_status,
        provider_leg_ids=tuple(
            dict.fromkeys(
                normalized
                for leg_row in leg_rows
                if (
                    normalized := str(leg_row["provider_leg_id"] or "").strip()
                )
            )
        ),
        provider=(str(row["provider"]) if row["provider"] else None),
        direction=str(row["direction"] or "outbound"),
        campaign_id=(str(row["campaign_id"]) if row["campaign_id"] else None),
        answered_at=row["answered_at"],
    )


async def _load_persisted_inbound_terminal_duration(
    pool: Any,
    *,
    durable_call_id: str,
    provider_call_id: str,
    tenant_id: str | None,
) -> int | None:
    """Return only a duration fenced by durable answer and terminal proof.

    ``ended_at`` predates the proof contract and may be a delayed projection,
    so it is deliberately not accepted. Operator/admin paths call this after
    the adapter confirms absence; a missing marker leaves the durable cleanup
    obligation in an explicit carrier/CDR hold instead of guessing from
    wall-clock time.  ``None`` means that the locked-after-proof snapshot is
    still ambiguous; it never means a proven zero-second release.
    """

    normalized_call_id = str(durable_call_id or "").strip()
    normalized_provider_id = str(provider_call_id or "").strip()
    if not normalized_call_id or not normalized_provider_id:
        raise ValueError("durable and provider call identities are required")
    async with acquire_with_tenant(
        pool,
        str(tenant_id) if tenant_id is not None else None,
        timeout=5.0,
    ) as conn:
        row = await conn.fetchrow(
            """
            SELECT answered_at, provider_terminated_at,
                   duration_seconds, reserved_seconds
            FROM calls
            WHERE id=$1::uuid
              AND direction='inbound'
              AND (provider_call_id=$2 OR external_call_uuid=$2)
            """,
            normalized_call_id,
            normalized_provider_id,
        )
    if row is None:
        raise RuntimeError("durable inbound call identity was not found")
    if row.get("answered_at") is None or row.get("provider_terminated_at") is None:
        return None
    duration = row.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        raise RuntimeError("durable provider terminal duration is invalid")
    reserved = row.get("reserved_seconds")
    if isinstance(reserved, int) and not isinstance(reserved, bool):
        if reserved < 0 or duration > reserved:
            raise RuntimeError("durable provider terminal duration exceeds reservation")
    return duration


async def finalize_proven_inbound_termination(
    pool: Any,
    *,
    provider_call_id: str,
    durable_call_id: str | None,
    tenant_id: str | None,
    terminal_status: str,
    provider: str = "asterisk",
    outcome: str | None = None,
    reason: str | None = None,
    redis_client: Any = None,
    campaign_id: str | None = None,
    acknowledge_ledger: bool = True,
) -> None:
    """Commit all inbound cleanup after the caller has PBX absence proof.

    The ordering is the contract shared by tenant/admin/raw termination:

    1. persist a same-owner retry ledger before any settlement mutation;
    2. close active transfer legs and release their transfer leases;
    3. settle the parent from a fresh durable fact read, holding any ambiguous
       answer race for carrier/CDR reconciliation;
    4. strictly release the cluster-wide call slot; and
    5. acknowledge the retry ledger last.

    Any exception escapes before acknowledgement. The entry remains
    ``termination_pending`` and the <=30-second recovery loop can replay every
    operation idempotently. This function deliberately does not request a PBX
    hangup; its name documents that callers must already hold all-leg proof.
    """

    normalized_provider_id = str(provider_call_id or "").strip()
    if not normalized_provider_id:
        raise ValueError("provider_call_id is required")
    normalized_provider = str(provider or "asterisk").strip().lower()

    from app.domain.services.global_concurrency import release_lease_strict
    from app.domain.services.telephony.inbound_admission import (
        InboundAdmissionService,
        InboundFinalizationRequest,
    )
    from app.domain.services.telephony.inbound_transfer import (
        finalize_connected_inbound_transfers,
    )
    from app.domain.services.telephony.state_backend import get_state_backend

    state_backend = get_state_backend()
    await state_backend.register_cleanup_obligation(
        normalized_provider_id,
        tenant_id=(str(tenant_id) if tenant_id else None),
        campaign_id=(str(campaign_id) if campaign_id else None),
        state="termination_pending",
    )

    normalized_durable_id = str(durable_call_id or "").strip()
    if normalized_durable_id:
        authoritative_duration = await _load_persisted_inbound_terminal_duration(
            pool,
            durable_call_id=normalized_durable_id,
            provider_call_id=normalized_provider_id,
            tenant_id=tenant_id,
        )
        effective_reason = reason
        if authoritative_duration is None:
            authoritative_duration = 0
            effective_reason = "process_restart_answer_ambiguous"
        await finalize_connected_inbound_transfers(
            pool,
            call_id=normalized_durable_id,
            terminal_reason=effective_reason,
            redis_client=redis_client,
        )
        service = InboundAdmissionService(pool)
        await service.finalize(
            InboundFinalizationRequest(
                call_id=normalized_durable_id,
                provider=normalized_provider,
                provider_call_id=normalized_provider_id,
                terminal_status=terminal_status,
                duration_seconds=authoritative_duration,
                outcome=outcome,
                reason=effective_reason,
                request_id=f"proven-termination:{normalized_provider_id}",
            )
        )

    await release_lease_strict(redis_client, call_id=normalized_provider_id)
    if acknowledge_ledger:
        await state_backend.acknowledge_orphan_recovery(normalized_provider_id)

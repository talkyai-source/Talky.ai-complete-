"""Real-PostgreSQL proof for evidence-backed inbound hold adjudication."""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal

import asyncpg
import pytest

from app.domain.services.telephony.inbound_admission import (
    InboundAdmissionService,
    InboundHoldResolutionConflictError,
    InboundHoldResolutionRequest,
)
from app.domain.services.telephony.inbound_transfer import (
    fetch_tenant_accounted_usage_seconds,
)


class _SameConnectionAcquire:
    def __init__(self, connection: asyncpg.Connection):
        self._connection = connection

    async def __aenter__(self) -> asyncpg.Connection:
        return self._connection

    async def __aexit__(self, *_args) -> bool:
        return False


class _NestedTransactionPool:
    """Let the service use a savepoint inside the test's rollback wrapper."""

    def __init__(self, connection: asyncpg.Connection):
        self._connection = connection

    def acquire(self):
        return _SameConnectionAcquire(self._connection)


def _request(
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    actor_id: uuid.UUID,
    hold_reason: str,
    decision: str,
    request_id: str,
    duration: int | None = None,
    cost: Decimal | None = None,
    currency: str | None = None,
    approval_action: str | None = None,
    approval_request_id: uuid.UUID | str | None = None,
) -> InboundHoldResolutionRequest:
    return InboundHoldResolutionRequest(
        call_id=str(call_id),
        tenant_id=str(tenant_id),
        hold_reason=hold_reason,
        decision=decision,
        evidence_type=(
            "carrier_cdr" if hold_reason == "provider_answer_ambiguous" else "provider_usage_record"
        ),
        evidence_reference=f"integration-evidence-{call_id}",
        evidence_sha256=call_id.hex * 2,
        adjudication_reason="Integration fixture evidence was independently reviewed",
        authoritative_duration_seconds=duration,
        authoritative_cost=cost,
        authoritative_currency=currency,
        actor_id=str(actor_id),
        actor_role="super_admin",
        request_id=request_id,
        approval_action=approval_action,
        approval_request_id=(str(approval_request_id) if approval_request_id is not None else None),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manual_hold_resolution_is_atomic_idempotent_and_quota_exact():
    dsn = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    try:
        connection = await asyncpg.connect(dsn, timeout=3)
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable: {exc}")

    outer = connection.transaction()
    await outer.start()
    try:
        await connection.execute("SET LOCAL app.bypass_rls = 'on'")
        ready = await connection.fetchval(
            """
            SELECT to_regclass('public.inbound_usage_transactions') IS NOT NULL
               AND to_regclass(
                    'public.inbound_billing_hold_finalize_approvals'
               ) IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='calls'
                      AND column_name='billing_hold_reason'
               )
            """
        )
        if not ready:
            pytest.skip("database is not at the current inbound billing schema")

        tenant_id = uuid.uuid4()
        actor_id = uuid.uuid4()
        approver_id = uuid.uuid4()
        release_call_id = uuid.uuid4()
        finalize_call_id = uuid.uuid4()
        await connection.execute(
            """
            INSERT INTO tenants (id,business_name,subscription_status,status)
            VALUES ($1,'Hold resolution integration','active','active')
            """,
            tenant_id,
        )
        await connection.execute(
            """
            INSERT INTO user_profiles (id,email,role)
            VALUES ($1,$2,'platform_admin'),($3,$4,'platform_admin')
            """,
            actor_id,
            f"hold-resolution-{actor_id}@example.test",
            approver_id,
            f"hold-approval-{approver_id}@example.test",
        )
        await connection.execute(
            """
            INSERT INTO calls (
                id,tenant_id,phone_number,status,talklee_call_id,direction,
                provider,provider_call_id,admission_status,processing_status,
                billing_status,billing_hold_reason,reserved_seconds,
                duration_seconds,cost,ended_at
            ) VALUES
                ($1,$3,'+15550001001','completed',$4,'inbound','asterisk',$6,
                 'allowed','completed','held','provider_answer_ambiguous',60,
                 37,1.25,NOW()),
                ($2,$3,'+15550001002','completed',$5,'inbound','asterisk',$7,
                 'allowed','completed','held','usage_exceeded_reservation',60,
                 91,NULL,NOW())
            """,
            release_call_id,
            finalize_call_id,
            tenant_id,
            f"IN{release_call_id.hex[:18]}",
            f"IN{finalize_call_id.hex[:18]}",
            f"pbx-{release_call_id}",
            f"pbx-{finalize_call_id}",
        )
        await connection.execute(
            """
            INSERT INTO inbound_usage_transactions (
                tenant_id,call_id,call_leg_id,transaction_type,
                quantity_seconds,idempotency_key,policy_snapshot,metadata
            ) VALUES
                ($1,$2,NULL,'reserve',60,$4,'{}','{}'),
                ($1,$3,NULL,'reserve',60,$5,'{}','{}')
            """,
            tenant_id,
            release_call_id,
            finalize_call_id,
            f"integration-reserve-{release_call_id}",
            f"integration-reserve-{finalize_call_id}",
        )
        await connection.execute(
            "UPDATE platform_runtime_controls " "SET inbound_settlement_enabled=FALSE WHERE id=1"
        )
        # Exercise real tenant RLS during every service read/write.
        await connection.execute("SET LOCAL app.bypass_rls = 'off'")
        await connection.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")

        service = InboundAdmissionService(_NestedTransactionPool(connection))
        release_request = _request(
            tenant_id=tenant_id,
            call_id=release_call_id,
            actor_id=actor_id,
            hold_reason="provider_answer_ambiguous",
            decision="release_unanswered",
            request_id=f"integration-release-{release_call_id}",
        )
        before_release = await fetch_tenant_accounted_usage_seconds(
            connection,
            tenant_id=str(tenant_id),
            exclude_call_id=str(finalize_call_id),
        )
        assert before_release == 60
        released = await service.resolve_billing_hold(release_request)
        assert released.billing_status == "released"
        assert released.duration_seconds == 0
        assert released.is_replay is False
        replay = await service.resolve_billing_hold(release_request)
        assert replay.is_replay is True
        assert replay.usage_transaction_id == released.usage_transaction_id
        after_release = await fetch_tenant_accounted_usage_seconds(
            connection,
            tenant_id=str(tenant_id),
            exclude_call_id=str(finalize_call_id),
        )
        assert after_release == 0

        finalize_request = _request(
            tenant_id=tenant_id,
            call_id=finalize_call_id,
            actor_id=actor_id,
            hold_reason="usage_exceeded_reservation",
            decision="finalize",
            duration=91,
            cost=Decimal("12.3456"),
            currency="usd",
            request_id=f"integration-finalize-{finalize_call_id}",
        )
        pending = await service.resolve_billing_hold(finalize_request)
        assert pending.workflow_status == "pending_approval"
        assert pending.billing_status == "held"
        assert pending.usage_transaction_id is None
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM inbound_usage_transactions "
                "WHERE tenant_id=$1 AND call_id=$2 "
                "AND transaction_type='finalize'",
                tenant_id,
                finalize_call_id,
            )
            == 0
        )
        trigger_contract = await connection.fetchrow(
            """
            SELECT tgtype,tgenabled::text AS tgenabled
            FROM pg_trigger
            WHERE tgrelid=
                'public.inbound_billing_hold_finalize_approvals'::regclass
              AND tgname='inbound_hold_finalize_approval_transition'
              AND NOT tgisinternal
            """
        )
        assert trigger_contract["tgtype"] == 27
        assert trigger_contract["tgenabled"] == "A"
        tamper = connection.transaction()
        await tamper.start()
        try:
            await connection.execute("SET LOCAL session_replication_role='replica'")
            with pytest.raises(
                asyncpg.PostgresError,
                match="invalid inbound billing-hold approval transition",
            ):
                await connection.execute(
                    "UPDATE inbound_billing_hold_finalize_approvals "
                    "SET evidence_reference='tampered-after-review' WHERE id=$1",
                    uuid.UUID(pending.approval_request_id),
                )
        finally:
            await tamper.rollback()
        self_approval = _request(
            tenant_id=tenant_id,
            call_id=finalize_call_id,
            actor_id=actor_id,
            hold_reason="usage_exceeded_reservation",
            decision="finalize",
            duration=91,
            cost=Decimal("12.3456"),
            currency="usd",
            request_id=f"integration-self-approve-{finalize_call_id}",
            approval_action="approve",
            approval_request_id=pending.approval_request_id,
        )
        with pytest.raises(InboundHoldResolutionConflictError, match="cannot approve"):
            await service.resolve_billing_hold(self_approval)

        approval_request = _request(
            tenant_id=tenant_id,
            call_id=finalize_call_id,
            actor_id=approver_id,
            hold_reason="usage_exceeded_reservation",
            decision="finalize",
            duration=91,
            cost=Decimal("12.3456"),
            currency="usd",
            request_id=f"integration-approve-{finalize_call_id}",
            approval_action="approve",
            approval_request_id=pending.approval_request_id,
        )
        with pytest.raises(
            InboundHoldResolutionConflictError,
            match="settlement is disabled",
        ):
            await service.resolve_billing_hold(approval_request)
        assert (
            await connection.fetchval(
                "SELECT status FROM inbound_billing_hold_finalize_approvals " "WHERE id=$1",
                uuid.UUID(pending.approval_request_id),
            )
            == "pending"
        )
        await connection.execute(
            "UPDATE platform_runtime_controls " "SET inbound_settlement_enabled=TRUE WHERE id=1"
        )
        finalized = await service.resolve_billing_hold(approval_request)
        assert finalized.billing_status == "finalized"
        assert finalized.duration_seconds == 91
        assert finalized.requested_by == str(actor_id)
        assert finalized.approved_by == str(approver_id)
        finalized_replay = await service.resolve_billing_hold(approval_request)
        assert finalized_replay.is_replay is True
        approval_row = await connection.fetchrow(
            "SELECT status,requested_by,approved_by,approval_idempotency_key "
            "FROM inbound_billing_hold_finalize_approvals WHERE id=$1",
            uuid.UUID(pending.approval_request_id),
        )
        assert approval_row["status"] == "approved"
        assert approval_row["requested_by"] == actor_id
        assert approval_row["approved_by"] == approver_id
        assert approval_row["approval_idempotency_key"] == approval_request.request_id

        ledger = await connection.fetch(
            """
            SELECT id,call_id,transaction_type,quantity_seconds,amount,currency,
                   related_transaction_id,metadata
            FROM inbound_usage_transactions
            WHERE tenant_id=$1 AND transaction_type IN ('release','finalize')
            ORDER BY call_id
            """,
            tenant_id,
        )
        assert len(ledger) == 2
        by_call = {row["call_id"]: row for row in ledger}
        metadata_by_call = {
            row["call_id"]: (
                json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
            )
            for row in ledger
        }
        assert by_call[release_call_id]["transaction_type"] == "release"
        assert by_call[release_call_id]["quantity_seconds"] == -60
        assert by_call[release_call_id]["amount"] is None
        assert by_call[release_call_id]["currency"] is None
        assert by_call[finalize_call_id]["transaction_type"] == "finalize"
        assert by_call[finalize_call_id]["quantity_seconds"] == 31
        assert by_call[finalize_call_id]["amount"] == Decimal("12.345600")
        assert by_call[finalize_call_id]["currency"] == "USD"
        assert metadata_by_call[finalize_call_id]["authoritative_currency"] == "USD"
        assert await connection.fetchval(
            "SELECT cost FROM calls WHERE id=$1",
            finalize_call_id,
        ) == Decimal("12.3456")
        assert await connection.fetchval(
            "SELECT cost FROM calls WHERE id=$1",
            release_call_id,
        ) == Decimal("1.2500")
        assert all(row["related_transaction_id"] is not None for row in ledger)
        assert all(metadata["manual_hold_resolution"] for metadata in metadata_by_call.values())
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM inbound_audit_events "
                "WHERE tenant_id=$1 AND event_type='inbound_billing_hold_resolved'",
                tenant_id,
            )
            == 2
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM inbound_audit_events "
                "WHERE tenant_id=$1 "
                "AND event_type='inbound_billing_hold_finalize_requested'",
                tenant_id,
            )
            == 1
        )
        assert (
            await fetch_tenant_accounted_usage_seconds(
                connection,
                tenant_id=str(tenant_id),
                exclude_call_id=str(release_call_id),
            )
            == 91
        )

        reversed_usage = await service.reverse_finalized_usage(
            call_id=str(finalize_call_id),
            provider="asterisk",
            provider_call_id=f"pbx-{finalize_call_id}",
            reason="Integration reversal proves monetary cancellation",
            request_id=f"integration-reverse-{finalize_call_id}",
        )
        assert reversed_usage.billing_status == "reversed"
        reversal = await connection.fetchrow(
            """
            SELECT quantity_seconds,amount,currency,related_transaction_id
            FROM inbound_usage_transactions
            WHERE tenant_id=$1 AND call_id=$2 AND transaction_type='reverse'
            """,
            tenant_id,
            finalize_call_id,
        )
        assert reversal["quantity_seconds"] == -91
        assert reversal["amount"] == Decimal("-12.345600")
        assert reversal["currency"] == "USD"
        assert reversal["related_transaction_id"] == by_call[finalize_call_id]["id"]
        assert (
            await connection.fetchval(
                """
            SELECT COALESCE(SUM(amount),0)
            FROM inbound_usage_transactions
            WHERE tenant_id=$1 AND call_id=$2
              AND transaction_type IN ('finalize','reverse')
            """,
                tenant_id,
                finalize_call_id,
            )
            == Decimal("0.000000")
        )
        assert (
            await fetch_tenant_accounted_usage_seconds(
                connection,
                tenant_id=str(tenant_id),
                exclude_call_id=str(release_call_id),
            )
            == 0
        )

        opposite = _request(
            tenant_id=tenant_id,
            call_id=release_call_id,
            actor_id=actor_id,
            hold_reason="provider_answer_ambiguous",
            decision="finalize",
            duration=12,
            request_id=f"integration-opposite-{release_call_id}",
        )
        with pytest.raises(InboundHoldResolutionConflictError):
            await service.resolve_billing_hold(opposite)
        assert (
            await connection.fetchval(
                """
            SELECT count(*) FROM inbound_usage_transactions
            WHERE tenant_id=$1 AND call_id=$2
              AND transaction_type IN ('release','finalize')
            """,
                tenant_id,
                release_call_id,
            )
            == 1
        )
    finally:
        await outer.rollback()
        await connection.close()

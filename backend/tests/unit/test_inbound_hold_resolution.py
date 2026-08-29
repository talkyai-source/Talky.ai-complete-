"""Manual inbound billing-hold adjudication is tenant-safe and exactly once."""

from __future__ import annotations

import copy
import json
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.v1.endpoints.admin.inbound as admin_inbound
from app.api.v1.dependencies import CurrentUser, require_platform_admin
from app.api.v1.schemas.inbound_campaigns import (
    AdminInboundBillingHoldResolutionRequest,
)
from app.domain.services.telephony import inbound_admission as admission_module
from app.domain.services.telephony.inbound_admission import (
    InboundAdmissionService,
    InboundHoldResolutionConflictError,
    InboundHoldResolutionNotFoundError,
    InboundHoldResolutionRequest,
    InboundHoldResolutionResult,
)


TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "22222222-2222-2222-2222-222222222222"
CALL_ID = "33333333-3333-3333-3333-333333333333"
ACTOR_ID = "44444444-4444-4444-4444-444444444444"
APPROVER_ID = "88888888-8888-4888-8888-888888888888"
THIRD_ADMIN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
RESERVE_ID = "55555555-5555-5555-5555-555555555555"
USAGE_ID = "66666666-6666-6666-6666-666666666666"
OPERATION_ID = "77777777-7777-7777-7777-777777777777"
APPROVAL_ID = "99999999-9999-4999-8999-999999999999"
EVIDENCE_HASH = "a" * 64


def _request(
    *,
    hold_reason: str = "provider_answer_ambiguous",
    decision: str = "release_unanswered",
    evidence_type: str | None = None,
    evidence_reference: str = "carrier-cdr-987654",
    evidence_sha256: str = EVIDENCE_HASH,
    duration: int | None = None,
    cost: Decimal | None = None,
    currency: str | None = None,
    tenant_id: str = TENANT_ID,
    actor_id: str = ACTOR_ID,
    actor_role: str = "platform_admin",
    request_id: str = "hold-resolution-key-001",
    approval_action: str | None = None,
    approval_request_id: str | None = None,
) -> InboundHoldResolutionRequest:
    if evidence_type is None:
        evidence_type = (
            "carrier_cdr" if hold_reason == "provider_answer_ambiguous" else "provider_usage_record"
        )
    return InboundHoldResolutionRequest(
        call_id=CALL_ID,
        tenant_id=tenant_id,
        hold_reason=hold_reason,
        decision=decision,
        evidence_type=evidence_type,
        evidence_reference=evidence_reference,
        evidence_sha256=evidence_sha256,
        adjudication_reason="Carrier evidence reviewed by billing operations",
        authoritative_duration_seconds=duration,
        authoritative_cost=cost,
        authoritative_currency=currency,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=request_id,
        approval_action=approval_action,
        approval_request_id=approval_request_id,
    )


class _Transaction(AbstractAsyncContextManager):
    def __init__(self, conn: "_ResolutionConn", tenant_id: str):
        self.conn = conn
        self.tenant_id = tenant_id
        self.snapshot = None

    async def __aenter__(self):
        self.conn.acquired_tenants.append(self.tenant_id)
        self.snapshot = self.conn.snapshot()
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.conn.restore(self.snapshot)
        return False


class _ResolutionConn:
    def __init__(
        self,
        *,
        hold_reason: str,
        reserved_seconds: int = 60,
        duration_seconds: int = 37,
        cost: Decimal | None = None,
        reservation_currency: str | None = None,
    ):
        self.call = {
            "id": CALL_ID,
            "tenant_id": TENANT_ID,
            "provider": "asterisk",
            "provider_call_id": "pbx-inbound-1",
            "direction": "inbound",
            "status": "completed",
            "outcome": "answered",
            "ended_at": "2026-08-28T12:00:00+00:00",
            "duration_seconds": duration_seconds,
            "cost": cost,
            "reserved_seconds": reserved_seconds,
            "billing_status": "held",
            "billing_hold_reason": hold_reason,
        }
        self.reserve = {
            "id": RESERVE_ID,
            "transaction_type": "reserve",
            "quantity_seconds": reserved_seconds,
            "currency": reservation_currency,
        }
        self.settlements: list[dict] = []
        self.audits: list[dict] = []
        self.operations: dict[tuple[str, str, str], dict] = {}
        self.approvals: list[dict] = []
        self.events: list[tuple[str, str]] = []
        self.acquired_tenants: list[str] = []
        self.unsettled_transfer = False
        self.fail_cas = False
        self.settlement_enabled: bool | None = True
        self.settlement_control_reads = 0

    def snapshot(self):
        return copy.deepcopy(
            (
                self.call,
                self.settlements,
                self.audits,
                self.operations,
                self.approvals,
                self.events,
            )
        )

    def restore(self, snapshot):
        (
            self.call,
            self.settlements,
            self.audits,
            self.operations,
            self.approvals,
            self.events,
        ) = copy.deepcopy(snapshot)

    @staticmethod
    def _sql(query: str) -> str:
        return " ".join(str(query).split())

    async def execute(self, query: str, *args):
        sql = self._sql(query)
        if "pg_advisory_xact_lock" in sql:
            self.events.append(("lock", sql))
            return "SELECT 1"
        if sql.startswith("INSERT INTO inbound_audit_events"):
            self.audits.append(
                {
                    "event_type": (
                        "inbound_billing_hold_finalize_requested"
                        if "inbound_billing_hold_finalize_requested" in sql
                        else "inbound_billing_hold_resolved"
                    ),
                    "tenant_id": args[0],
                    "actor_id": args[1],
                    "actor_role": args[2],
                    "call_id": args[3],
                    "reason": args[4],
                    "before": json.loads(args[5]),
                    "after": json.loads(args[6]),
                    "metadata": json.loads(args[7]),
                    "idempotency_key": args[8],
                }
            )
            return "INSERT 0 1"
        if sql.startswith("UPDATE inbound_operation_idempotency"):
            key = (args[1], args[2], args[3])
            operation = self.operations.get(key)
            if operation and operation["tenant_id"] == args[0]:
                operation.update(
                    response_body=json.loads(args[4]),
                    status_code=200,
                    resource_id=args[5],
                )
                return "UPDATE 1"
            return "UPDATE 0"
        raise AssertionError(f"unexpected execute: {sql}")

    async def fetchval(self, query: str, *args):
        sql = self._sql(query)
        if "SELECT EXISTS" in sql and "FROM call_legs" in sql:
            assert "transfer_leg.status IN ('initiated','ringing','answered')" in sql
            assert args == (CALL_ID, TENANT_ID)
            return self.unsettled_transfer
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, query: str, *args):
        sql = self._sql(query)
        if sql.startswith("INSERT INTO inbound_operation_idempotency"):
            key = (args[1], args[2], args[3])
            if key in self.operations:
                return None
            self.operations[key] = {
                "id": OPERATION_ID,
                "tenant_id": args[0],
                "request_hash": args[4],
                "actor_id": args[5],
                "response_body": None,
                "status_code": None,
            }
            return {"id": OPERATION_ID}
        if sql.startswith("SELECT request_hash, response_body, status_code"):
            operation = self.operations.get((args[1], args[2], args[3]))
            if operation and operation["tenant_id"] == args[0]:
                return dict(operation)
            return None
        if "FROM calls" in sql and "FOR UPDATE" in sql:
            self.events.append(("call_for_update", sql))
            if args[0] != self.call["id"] or args[1] != self.call["tenant_id"]:
                return None
            return dict(self.call)
        if "FROM platform_runtime_controls" in sql:
            self.settlement_control_reads += 1
            self.events.append(("settlement_controls", sql))
            if self.settlement_enabled is None:
                return None
            return {"inbound_settlement_enabled": self.settlement_enabled}
        if sql.startswith("INSERT INTO inbound_billing_hold_finalize_approvals"):
            if self.approvals:
                return None
            approval = {
                "id": APPROVAL_ID,
                "tenant_id": args[0],
                "call_id": args[1],
                "hold_reason": args[2],
                "evidence_type": args[3],
                "evidence_reference": args[4],
                "evidence_sha256": args[5],
                "adjudication_reason": args[6],
                "authoritative_duration_seconds": args[7],
                "authoritative_cost": args[8],
                "authoritative_currency": args[9],
                "resolution_hash": args[10],
                "requested_by": args[11],
                "request_id": args[12],
                "status": "pending",
                "approved_by": None,
                "approval_idempotency_key": None,
                "approved_at": None,
            }
            self.approvals.append(approval)
            self.events.append(("approval_request", sql))
            return dict(approval)
        if "FROM inbound_billing_hold_finalize_approvals" in sql:
            if not self.approvals:
                return None
            approval = self.approvals[0]
            if "WHERE id=$1::uuid" in sql:
                if (
                    args[0] != approval["id"]
                    or args[1] != approval["tenant_id"]
                    or args[2] != approval["call_id"]
                ):
                    return None
            elif args[0] != approval["tenant_id"] or args[1] != approval["call_id"]:
                return None
            return dict(approval)
        if sql.startswith("UPDATE inbound_billing_hold_finalize_approvals"):
            if not self.approvals:
                return None
            approval = self.approvals[0]
            if (
                args[0] != approval["id"]
                or args[1] != approval["tenant_id"]
                or args[2] != approval["call_id"]
                or approval["status"] != "pending"
                or args[3] == approval["requested_by"]
                or args[5] != approval["resolution_hash"]
            ):
                return None
            approval.update(
                status="approved",
                approved_by=args[3],
                approval_idempotency_key=args[4],
                approved_at="2026-08-28T12:01:00+00:00",
            )
            self.events.append(("approval_cas", sql))
            return {
                "requested_by": approval["requested_by"],
                "approved_by": approval["approved_by"],
            }
        if (
            "FROM inbound_usage_transactions" in sql
            and "transaction_type IN ('release','finalize')" in sql
        ):
            return dict(self.settlements[0]) if self.settlements else None
        if "FROM inbound_usage_transactions" in sql and "transaction_type='reserve'" in sql:
            if args[0] != self.call["tenant_id"] or args[1] != self.call["id"]:
                return None
            return dict(self.reserve)
        if sql.startswith("INSERT INTO inbound_usage_transactions"):
            if self.settlements:
                return None
            settlement = {
                "id": USAGE_ID,
                "tenant_id": args[0],
                "call_id": args[1],
                "transaction_type": args[2],
                "quantity_seconds": args[3],
                "amount": args[4],
                "currency": args[5],
                "idempotency_key": args[6],
                "related_transaction_id": args[7],
                "policy_snapshot": json.loads(args[8]),
                "metadata": json.loads(args[9]),
            }
            self.settlements.append(settlement)
            self.events.append(("ledger_insert", sql))
            return dict(settlement)
        if sql.startswith("UPDATE calls"):
            self.events.append(("call_cas", sql))
            if (
                self.fail_cas
                or args[0] != self.call["id"]
                or args[1] != self.call["tenant_id"]
                or args[2] != self.call["billing_hold_reason"]
                or self.call["billing_status"] != "held"
            ):
                return None
            self.call["duration_seconds"] = args[3]
            if args[4] is not None:
                self.call["cost"] = args[4]
            self.call["billing_status"] = args[5]
            self.call["billing_hold_reason"] = None
            return {
                "id": self.call["id"],
                "billing_status": self.call["billing_status"],
                "billing_hold_reason": None,
                "duration_seconds": self.call["duration_seconds"],
                "cost": self.call["cost"],
            }
        if sql.startswith("UPDATE inbound_operation_idempotency"):
            key = (args[1], args[2], args[3])
            operation = self.operations.get(key)
            if (
                not operation
                or operation["tenant_id"] != args[0]
                or operation["request_hash"] != args[6]
            ):
                return None
            operation.update(
                response_body=json.loads(args[4]),
                status_code=202 if "status_code=202" in sql else 200,
                resource_id=args[5],
            )
            return {"id": operation["id"]}
        raise AssertionError(f"unexpected fetchrow: {sql}")


def _wire(monkeypatch, conn: _ResolutionConn):
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda _pool, tenant_id: _Transaction(conn, tenant_id),
    )
    return InboundAdmissionService(object())


async def _request_and_approve(
    service: InboundAdmissionService,
    request: InboundHoldResolutionRequest,
) -> tuple[InboundHoldResolutionResult, InboundHoldResolutionResult]:
    pending = await service.resolve_billing_hold(replace(request, approval_action="request"))
    approved = await service.resolve_billing_hold(
        replace(
            request,
            actor_id=APPROVER_ID,
            request_id="hold-approval-key-002",
            approval_action="approve",
            approval_request_id=pending.approval_request_id,
        )
    )
    return pending, approved


@pytest.mark.asyncio
async def test_finalize_request_is_replay_safe_and_cannot_mutate_money(monkeypatch):
    conn = _ResolutionConn(hold_reason="usage_exceeded_reservation")
    before = copy.deepcopy(conn.call)
    service = _wire(monkeypatch, conn)
    request = _request(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        duration=73,
        cost=Decimal("2.5000"),
        currency="USD",
    )

    pending = await service.resolve_billing_hold(request)
    replay = await service.resolve_billing_hold(request)

    assert pending.workflow_status == "pending_approval"
    assert pending.approval_request_id == APPROVAL_ID
    assert pending.requested_by == ACTOR_ID
    assert pending.approved_by is None
    assert replay.is_replay is True
    assert replay.approval_request_id == pending.approval_request_id
    assert conn.call == before
    assert conn.settlements == []
    assert conn.settlement_control_reads == 0
    assert len(conn.approvals) == 1
    assert conn.approvals[0]["status"] == "pending"
    assert [row["event_type"] for row in conn.audits] == ["inbound_billing_hold_finalize_requested"]


@pytest.mark.asyncio
async def test_finalize_requester_cannot_self_approve_and_attempt_rolls_back(monkeypatch):
    conn = _ResolutionConn(hold_reason="usage_exceeded_reservation")
    service = _wire(monkeypatch, conn)
    request = _request(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        duration=73,
    )
    pending = await service.resolve_billing_hold(request)

    with pytest.raises(InboundHoldResolutionConflictError, match="cannot approve"):
        await service.resolve_billing_hold(
            replace(
                request,
                request_id="self-approval-key-002",
                approval_action="approve",
                approval_request_id=pending.approval_request_id,
            )
        )

    assert conn.approvals[0]["status"] == "pending"
    assert conn.approvals[0]["approved_by"] is None
    assert conn.settlements == []
    assert conn.call["billing_status"] == "held"
    assert len(conn.audits) == 1
    assert not any(key[1] == "approve_inbound_billing_hold_finalize" for key in conn.operations)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatched",
    [
        {"evidence_reference": "different-provider-record"},
        {"evidence_sha256": "b" * 64},
        {"authoritative_duration_seconds": 74},
        {"authoritative_cost": Decimal("2.6000")},
        {"authoritative_currency": "EUR"},
        {"adjudication_reason": "A different adjudication basis was supplied"},
    ],
)
async def test_finalize_approval_is_bound_to_every_immutable_field(
    monkeypatch,
    mismatched,
):
    conn = _ResolutionConn(
        hold_reason="usage_exceeded_reservation",
        reservation_currency="USD",
    )
    service = _wire(monkeypatch, conn)
    request = _request(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        duration=73,
        cost=Decimal("2.5000"),
        currency="USD",
    )
    pending = await service.resolve_billing_hold(request)

    with pytest.raises(InboundHoldResolutionConflictError, match="does not match"):
        await service.resolve_billing_hold(
            replace(
                request,
                **mismatched,
                actor_id=APPROVER_ID,
                request_id="mismatch-approval-key-002",
                approval_action="approve",
                approval_request_id=pending.approval_request_id,
            )
        )

    assert conn.approvals[0]["status"] == "pending"
    assert conn.settlements == []
    assert conn.call["billing_status"] == "held"
    assert len(conn.audits) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_id", "request_id"),
    [
        (THIRD_ADMIN_ID, "third-admin-approval-key"),
        (APPROVER_ID, "same-admin-different-key"),
    ],
)
async def test_consumed_approval_cannot_be_replayed_as_another_approver(
    monkeypatch,
    actor_id,
    request_id,
):
    conn = _ResolutionConn(hold_reason="usage_exceeded_reservation")
    service = _wire(monkeypatch, conn)
    request = _request(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        duration=73,
    )
    pending, resolved = await _request_and_approve(service, request)
    settled_metadata = copy.deepcopy(conn.settlements[0]["metadata"])

    with pytest.raises(InboundHoldResolutionConflictError, match="already consumed"):
        await service.resolve_billing_hold(
            replace(
                request,
                actor_id=actor_id,
                request_id=request_id,
                approval_action="approve",
                approval_request_id=pending.approval_request_id,
            )
        )

    assert resolved.approved_by == APPROVER_ID
    assert conn.approvals[0]["approved_by"] == APPROVER_ID
    assert conn.settlements[0]["metadata"] == settled_metadata
    assert conn.settlements[0]["metadata"]["approved_by"] == APPROVER_ID
    assert len(conn.settlements) == 1
    assert len(conn.audits) == 2


@pytest.mark.asyncio
async def test_release_ambiguous_answer_is_atomic_audited_and_quota_zero(monkeypatch):
    conn = _ResolutionConn(
        hold_reason="provider_answer_ambiguous",
        cost=Decimal("1.250000"),
    )
    result = await _wire(monkeypatch, conn).resolve_billing_hold(_request())

    assert result.billing_status == "released"
    assert result.duration_seconds == 0
    assert conn.call["status"] == "completed"
    assert conn.call["outcome"] == "answered"
    assert conn.call["cost"] == Decimal("1.250000")  # no synthetic zero
    assert conn.call["billing_hold_reason"] is None
    assert len(conn.settlements) == len(conn.audits) == 1
    settlement = conn.settlements[0]
    assert settlement["transaction_type"] == "release"
    assert settlement["quantity_seconds"] == -60
    assert settlement["amount"] is None
    assert settlement["related_transaction_id"] == RESERVE_ID
    assert settlement["metadata"]["evidence_sha256"] == EVIDENCE_HASH
    assert settlement["metadata"]["actor_id"] == ACTOR_ID
    assert 60 + settlement["quantity_seconds"] == 0
    assert conn.audits[0]["metadata"]["usage_transaction_id"] == USAGE_ID
    assert conn.acquired_tenants == [TENANT_ID]
    assert [event for event, _ in conn.events].index("lock") < [
        event for event, _ in conn.events
    ].index("call_for_update")


@pytest.mark.asyncio
async def test_finalize_over_reservation_accounts_authoritative_duration_once(monkeypatch):
    conn = _ResolutionConn(
        hold_reason="usage_exceeded_reservation",
        reserved_seconds=60,
        duration_seconds=91,
        cost=Decimal("1.2500"),
    )
    request = _request(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        duration=91,
        evidence_reference="provider-usage-4432",
    )
    service = _wire(monkeypatch, conn)
    pending = await service.resolve_billing_hold(request)

    assert pending.workflow_status == "pending_approval"
    assert pending.billing_status == "held"
    assert pending.usage_transaction_id is None
    assert conn.call["billing_status"] == "held"
    assert conn.settlements == []
    approval = replace(
        request,
        actor_id=APPROVER_ID,
        request_id="hold-approval-key-002",
        approval_action="approve",
        approval_request_id=pending.approval_request_id,
    )
    result = await service.resolve_billing_hold(approval)

    assert result.billing_status == "finalized"
    assert result.requested_by == ACTOR_ID
    assert result.approved_by == APPROVER_ID
    assert conn.call["duration_seconds"] == 91
    assert conn.call["cost"] == Decimal("1.2500")
    assert conn.settlements[0]["transaction_type"] == "finalize"
    assert conn.settlements[0]["quantity_seconds"] == 31
    assert conn.settlements[0]["amount"] is None
    assert conn.settlements[0]["currency"] is None
    assert 60 + conn.settlements[0]["quantity_seconds"] == 91

    replay = await InboundAdmissionService(object()).resolve_billing_hold(approval)
    assert replay.is_replay is True
    assert len(conn.settlements) == 1
    assert len(conn.audits) == 2
    assert 60 + sum(row["quantity_seconds"] for row in conn.settlements) == 91


@pytest.mark.asyncio
async def test_authoritative_cost_and_currency_are_canonical_and_persisted(monkeypatch):
    conn = _ResolutionConn(
        hold_reason="usage_exceeded_reservation",
        reservation_currency="USD",
    )
    request = _request(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        duration=37,
        cost=Decimal("12.34000"),
        currency=" usd ",
    )

    service = _wire(monkeypatch, conn)
    pending, result = await _request_and_approve(service, request)

    assert pending.workflow_status == "pending_approval"
    assert result.authoritative_currency == "USD"
    assert conn.call["cost"] == Decimal("12.3400")
    settlement = conn.settlements[0]
    assert settlement["amount"] == Decimal("12.3400")
    assert settlement["currency"] == "USD"
    assert settlement["metadata"]["authoritative_cost"] == "12.3400"
    assert settlement["metadata"]["authoritative_currency"] == "USD"
    assert conn.audits[-1]["metadata"]["authoritative_cost"] == "12.3400"
    assert conn.audits[-1]["metadata"]["authoritative_currency"] == "USD"

    replay = await InboundAdmissionService(object()).resolve_billing_hold(
        replace(
            request,
            actor_id=APPROVER_ID,
            request_id="hold-approval-key-002",
            approval_action="approve",
            approval_request_id=pending.approval_request_id,
        )
    )
    assert replay.is_replay is True
    assert len(conn.settlements) == 1
    assert len(conn.audits) == 2

    with pytest.raises(InboundHoldResolutionConflictError, match="Idempotency-Key"):
        await InboundAdmissionService(object()).resolve_billing_hold(
            replace(
                request,
                authoritative_cost=Decimal("12.34"),
                authoritative_currency="EUR",
                actor_id=APPROVER_ID,
                request_id="hold-approval-key-002",
                approval_action="approve",
                approval_request_id=pending.approval_request_id,
            )
        )


@pytest.mark.asyncio
async def test_authoritative_currency_must_match_reservation_currency(monkeypatch):
    conn = _ResolutionConn(
        hold_reason="usage_exceeded_reservation",
        reservation_currency="EUR",
    )

    with pytest.raises(InboundHoldResolutionConflictError, match="currency conflicts"):
        await _wire(monkeypatch, conn).resolve_billing_hold(
            _request(
                hold_reason="usage_exceeded_reservation",
                decision="finalize",
                duration=37,
                cost=Decimal("1.2500"),
                currency="USD",
            )
        )

    assert conn.call["billing_status"] == "held"
    assert conn.settlements == []
    assert conn.audits == []
    assert conn.operations == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("control_value", [False, None])
async def test_finalize_fails_closed_while_settlement_switch_is_disabled_or_missing(
    monkeypatch, control_value
):
    conn = _ResolutionConn(hold_reason="usage_exceeded_reservation")
    before = copy.deepcopy(conn.call)
    conn.settlement_enabled = control_value
    service = _wire(monkeypatch, conn)
    request = _request(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        duration=91,
    )
    pending = await service.resolve_billing_hold(request)

    assert pending.workflow_status == "pending_approval"
    assert conn.settlement_control_reads == 0
    assert conn.settlements == []

    with pytest.raises(InboundHoldResolutionConflictError, match="settlement is disabled"):
        await service.resolve_billing_hold(
            replace(
                request,
                actor_id=APPROVER_ID,
                request_id="hold-approval-key-002",
                approval_action="approve",
                approval_request_id=pending.approval_request_id,
            )
        )

    assert conn.call == before
    assert conn.settlements == []
    assert len(conn.audits) == 1
    assert conn.approvals[0]["status"] == "pending"
    assert conn.settlement_control_reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("control_value", [False, None])
async def test_release_remains_available_while_settlement_switch_is_off_or_missing(
    monkeypatch, control_value
):
    conn = _ResolutionConn(hold_reason="provider_answer_ambiguous")
    conn.settlement_enabled = control_value

    result = await _wire(monkeypatch, conn).resolve_billing_hold(_request())

    assert result.billing_status == "released"
    assert conn.settlements[0]["transaction_type"] == "release"
    assert conn.settlement_control_reads == 0


@pytest.mark.asyncio
async def test_opposite_or_mismatched_resolution_is_conflict_not_duplicate(monkeypatch):
    conn = _ResolutionConn(hold_reason="provider_answer_ambiguous")
    service = _wire(monkeypatch, conn)
    await service.resolve_billing_hold(_request())

    with pytest.raises(InboundHoldResolutionConflictError, match="immutable settlement"):
        await service.resolve_billing_hold(_request(decision="finalize", duration=12))
    with pytest.raises(InboundHoldResolutionConflictError, match="immutable settlement"):
        await service.resolve_billing_hold(
            _request(
                decision="finalize",
                duration=12,
                request_id="opposite-resolution-key",
            )
        )
    with pytest.raises(InboundHoldResolutionConflictError, match="Idempotency-Key"):
        await service.resolve_billing_hold(_request(evidence_reference="different-carrier-cdr"))
    assert len(conn.settlements) == len(conn.audits) == 1


@pytest.mark.asyncio
async def test_wrong_role_tenant_reason_and_evidence_fail_closed(monkeypatch):
    conn = _ResolutionConn(hold_reason="provider_answer_ambiguous")
    service = _wire(monkeypatch, conn)

    with pytest.raises(PermissionError):
        await service.resolve_billing_hold(_request(actor_role="tenant_admin"))
    with pytest.raises(InboundHoldResolutionNotFoundError):
        await service.resolve_billing_hold(
            _request(tenant_id=OTHER_TENANT_ID, request_id="wrong-tenant-key")
        )
    with pytest.raises(ValueError, match="unsupported"):
        await service.resolve_billing_hold(
            _request(
                hold_reason="settlement_switch_disabled",
                request_id="unsupported-reason-key",
            )
        )
    with pytest.raises(ValueError, match="requires carrier_cdr"):
        await service.resolve_billing_hold(_request(evidence_type="provider_usage_record"))
    assert not conn.settlements
    assert not conn.audits
    assert not conn.operations


@pytest.mark.asyncio
async def test_super_admin_alias_is_canonical_platform_admin(monkeypatch):
    conn = _ResolutionConn(hold_reason="provider_answer_ambiguous")
    result = await _wire(monkeypatch, conn).resolve_billing_hold(_request(actor_role="super_admin"))
    assert result.billing_status == "released"
    assert conn.settlements[0]["metadata"]["actor_role"] == "platform_admin"
    assert conn.audits[0]["actor_role"] == "platform_admin"


@pytest.mark.asyncio
async def test_transaction_rolls_back_ledger_and_idempotency_when_cas_fails(monkeypatch):
    conn = _ResolutionConn(hold_reason="provider_answer_ambiguous")
    conn.fail_cas = True

    with pytest.raises(InboundHoldResolutionConflictError, match="CAS"):
        await _wire(monkeypatch, conn).resolve_billing_hold(_request())

    assert conn.call["billing_status"] == "held"
    assert conn.call["billing_hold_reason"] == "provider_answer_ambiguous"
    assert conn.settlements == []
    assert conn.audits == []
    assert conn.operations == {}


def test_admin_route_is_platform_only_and_schema_requires_reason_specific_evidence():
    route = next(
        route
        for route in admin_inbound.router.routes
        if route.path == "/inbound/tenants/{tenant_id}/calls/{call_id}/billing-hold/resolve"
    )
    dependencies = {
        dependency.call.__name__
        for dependency in route.dependant.dependencies
        if dependency.call is not None
    }
    assert "require_platform_admin" in dependencies

    with pytest.raises(ValueError):
        AdminInboundBillingHoldResolutionRequest(
            hold_reason="provider_answer_ambiguous",
            decision="finalize",
            evidence_type="provider_usage_record",
            evidence_reference="wrong-evidence",
            evidence_sha256=EVIDENCE_HASH,
            adjudication_reason="Evidence was reviewed",
            authoritative_duration_seconds=5,
        )

    normalized = AdminInboundBillingHoldResolutionRequest(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        evidence_type="provider_usage_record",
        evidence_reference="provider-usage-1",
        evidence_sha256=EVIDENCE_HASH,
        adjudication_reason="Provider usage record was reviewed",
        authoritative_duration_seconds=5,
        authoritative_cost=Decimal("1.2300"),
        authoritative_currency=" usd ",
    )
    assert normalized.authoritative_currency == "USD"

    with pytest.raises(ValueError, match="approve requires approval_request_id"):
        AdminInboundBillingHoldResolutionRequest(
            hold_reason="usage_exceeded_reservation",
            decision="finalize",
            evidence_type="provider_usage_record",
            evidence_reference="provider-usage-1",
            evidence_sha256=EVIDENCE_HASH,
            adjudication_reason="Provider usage record was reviewed",
            authoritative_duration_seconds=5,
            approval_action="approve",
        )

    approval_payload = AdminInboundBillingHoldResolutionRequest(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        evidence_type="provider_usage_record",
        evidence_reference="provider-usage-1",
        evidence_sha256=EVIDENCE_HASH,
        adjudication_reason="Provider usage record was reviewed",
        authoritative_duration_seconds=5,
        approval_action="approve",
        approval_request_id=APPROVAL_ID.upper(),
    )
    assert approval_payload.approval_request_id == APPROVAL_ID.upper()

    with pytest.raises(ValueError, match="does not use finalize approval fields"):
        AdminInboundBillingHoldResolutionRequest(
            hold_reason="provider_answer_ambiguous",
            decision="release_unanswered",
            evidence_type="carrier_cdr",
            evidence_reference="carrier-cdr-1",
            evidence_sha256=EVIDENCE_HASH,
            adjudication_reason="Carrier CDR was reviewed",
            approval_action="request",
        )

    with pytest.raises(ValueError, match="supplied together"):
        AdminInboundBillingHoldResolutionRequest(
            hold_reason="usage_exceeded_reservation",
            decision="finalize",
            evidence_type="provider_usage_record",
            evidence_reference="provider-usage-1",
            evidence_sha256=EVIDENCE_HASH,
            adjudication_reason="Provider usage record was reviewed",
            authoritative_duration_seconds=5,
            authoritative_cost=Decimal("1.2300"),
        )


@pytest.mark.asyncio
async def test_admin_endpoint_forwards_tenant_actor_evidence_and_idempotency(monkeypatch):
    result = InboundHoldResolutionResult(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        hold_reason="provider_answer_ambiguous",
        decision="release_unanswered",
        billing_status="released",
        duration_seconds=0,
        usage_transaction_id=USAGE_ID,
        evidence_type="carrier_cdr",
        evidence_reference="carrier-cdr-987654",
        evidence_sha256=EVIDENCE_HASH,
        authoritative_currency="USD",
    )
    service = AsyncMock()
    service.resolve_billing_hold.return_value = result
    monkeypatch.setattr(admin_inbound, "InboundAdmissionService", lambda _pool: service)
    payload = AdminInboundBillingHoldResolutionRequest(
        hold_reason="provider_answer_ambiguous",
        decision="release_unanswered",
        evidence_type="carrier_cdr",
        evidence_reference="carrier-cdr-987654",
        evidence_sha256=EVIDENCE_HASH,
        adjudication_reason="Carrier CDR proves no answered event",
        authoritative_cost=Decimal("0"),
        authoritative_currency="usd",
    )
    user = CurrentUser(
        id=ACTOR_ID,
        email="platform@example.test",
        role="super_admin",
    )

    response = await admin_inbound.resolve_inbound_billing_hold(
        TENANT_ID,
        CALL_ID,
        payload,
        user=user,
        idempotency_key="hold-resolution-key-001",
        db_pool=object(),
    )
    forwarded = service.resolve_billing_hold.await_args.args[0]
    assert forwarded.tenant_id == TENANT_ID
    assert forwarded.actor_role == "super_admin"
    assert forwarded.evidence_sha256 == EVIDENCE_HASH
    assert forwarded.authoritative_cost == Decimal("0")
    assert forwarded.authoritative_currency == "USD"
    assert forwarded.request_id == "hold-resolution-key-001"
    assert response["billing_status"] == "released"

    tenant_admin = CurrentUser(
        id=ACTOR_ID,
        email="tenant@example.test",
        tenant_id=TENANT_ID,
        role="tenant_admin",
    )
    with pytest.raises(HTTPException) as exc:
        await require_platform_admin(tenant_admin)
    assert exc.value.status_code == 403
    assert await require_platform_admin(user) is user


@pytest.mark.asyncio
async def test_admin_endpoint_forwards_finalize_approval_identity(monkeypatch):
    result = InboundHoldResolutionResult(
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        billing_status="finalized",
        duration_seconds=91,
        usage_transaction_id=USAGE_ID,
        evidence_type="provider_usage_record",
        evidence_reference="provider-usage-1",
        evidence_sha256=EVIDENCE_HASH,
        approval_request_id=APPROVAL_ID,
        requested_by=ACTOR_ID,
        approved_by=APPROVER_ID,
    )
    service = AsyncMock()
    service.resolve_billing_hold.return_value = result
    monkeypatch.setattr(admin_inbound, "InboundAdmissionService", lambda _pool: service)
    payload = AdminInboundBillingHoldResolutionRequest(
        hold_reason="usage_exceeded_reservation",
        decision="finalize",
        evidence_type="provider_usage_record",
        evidence_reference="provider-usage-1",
        evidence_sha256=EVIDENCE_HASH,
        adjudication_reason="Provider usage record was reviewed",
        authoritative_duration_seconds=91,
        approval_action="approve",
        approval_request_id=APPROVAL_ID,
    )

    response = await admin_inbound.resolve_inbound_billing_hold(
        TENANT_ID,
        CALL_ID,
        payload,
        user=CurrentUser(
            id=APPROVER_ID,
            email="approver@example.test",
            role="platform_admin",
        ),
        idempotency_key="hold-approval-key-002",
        db_pool=object(),
    )

    forwarded = service.resolve_billing_hold.await_args.args[0]
    assert forwarded.actor_id == APPROVER_ID
    assert forwarded.approval_action == "approve"
    assert forwarded.approval_request_id == APPROVAL_ID
    assert forwarded.request_id == "hold-approval-key-002"
    assert response["approved_by"] == APPROVER_ID


@pytest.mark.parametrize(
    ("duration", "cost", "currency"),
    [
        (2_147_483_648, None, None),
        (1, Decimal("1000000"), "USD"),
        (1, Decimal("0.00001"), "USD"),
        (1, Decimal("0.123456"), "USD"),
        (1, Decimal("999999.99995"), "USD"),
    ],
)
@pytest.mark.asyncio
async def test_authoritative_values_must_fit_postgres_types(monkeypatch, duration, cost, currency):
    conn = _ResolutionConn(hold_reason="usage_exceeded_reservation")
    with pytest.raises(ValueError):
        await _wire(monkeypatch, conn).resolve_billing_hold(
            _request(
                hold_reason="usage_exceeded_reservation",
                decision="finalize",
                duration=duration,
                cost=cost,
                currency=currency,
            )
        )
    assert not conn.operations


@pytest.mark.parametrize(
    ("cost", "currency"),
    [
        (Decimal("1.0000"), None),
        (None, "USD"),
        (Decimal("1.0000"), "US1"),
        (Decimal("1.0000"), "ZZZ"),
    ],
)
@pytest.mark.asyncio
async def test_authoritative_cost_and_currency_are_an_atomic_pair(monkeypatch, cost, currency):
    conn = _ResolutionConn(hold_reason="usage_exceeded_reservation")
    with pytest.raises(ValueError):
        await _wire(monkeypatch, conn).resolve_billing_hold(
            _request(
                hold_reason="usage_exceeded_reservation",
                decision="finalize",
                duration=1,
                cost=cost,
                currency=currency,
            )
        )
    assert not conn.operations


@pytest.mark.parametrize("cost", [Decimal("0.0001"), Decimal("999999.9999")])
@pytest.mark.asyncio
async def test_shared_call_and_ledger_cost_boundaries_are_exact(monkeypatch, cost):
    conn = _ResolutionConn(hold_reason="usage_exceeded_reservation")
    _, result = await _request_and_approve(
        _wire(monkeypatch, conn),
        _request(
            hold_reason="usage_exceeded_reservation",
            decision="finalize",
            duration=1,
            cost=cost,
            currency="USD",
        ),
    )
    assert result.billing_status == "finalized"
    assert conn.call["cost"] == cost
    assert conn.settlements[0]["amount"] == cost
    assert conn.settlements[0]["currency"] == "USD"

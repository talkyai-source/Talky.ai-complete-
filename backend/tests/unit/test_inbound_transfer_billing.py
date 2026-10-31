"""Durability contracts for the independently billable inbound transfer leg."""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from uuid import UUID

import pytest

import app.domain.services.telephony.inbound_transfer as transfer_module


BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "Alembic" / "versions" / "0030_inbound_transfer_leg_usage.py"
SCHEMA = BACKEND / "database" / "complete_schema.sql"
TRANSFER = BACKEND / "app" / "domain" / "services" / "telephony" / "inbound_transfer.py"
ADMISSION = BACKEND / "app" / "domain" / "services" / "telephony" / "inbound_admission.py"


def _normalized(source: str) -> str:
    return re.sub(r"\s+", " ", source).strip().lower()


def test_0030_is_the_next_single_head_and_runtime_remains_closed() -> None:
    module = importlib.import_module("Alembic.versions.0030_inbound_transfer_leg_usage")
    transfer = TRANSFER.read_text(encoding="utf-8")

    assert module.revision == "0030_inbound_transfer_leg_usage"
    assert module.down_revision == "0029_trunk_runtime_status"
    assert len(module.revision) <= 32
    assert "CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE = False" in transfer


def test_0030_links_each_usage_subject_to_its_exact_parent_and_child() -> None:
    migration = _normalized(MIGRATION.read_text(encoding="utf-8")).replace('" "', "")

    for projection in (
        "billing_status varchar(16)",
        "reserved_seconds integer",
        "cost numeric(12, 6)",
        "currency varchar(3)",
    ):
        assert projection in migration
    assert "add column if not exists call_leg_id uuid" in migration
    assert "foreign key (call_leg_id, call_id)" in migration
    assert "references call_legs(id, call_id)" in migration
    assert "on delete restrict" in migration
    assert "uq_call_legs_id_call" in migration

    # Parent uniqueness is preserved while every child gets its own reserve
    # and terminal delta. The tenant idempotency key and immutable trigger
    # from 0022 remain additional once-only authorities.
    assert "uq_inbound_usage_reserve_per_call" in migration
    assert "transaction_type='reserve' and call_leg_id is null" in migration
    assert "uq_inbound_usage_settlement_per_call" in migration
    assert "transaction_type in ('finalize','release') and call_leg_id is null" in migration
    assert "uq_inbound_usage_reserve_per_leg" in migration
    assert "transaction_type='reserve' and call_leg_id is not null" in migration
    assert "uq_inbound_usage_settlement_per_leg" in migration
    assert "transaction_type in ('finalize','release') and call_leg_id is not null" in migration
    assert "0030 downgrade refused: transfer-leg usage ledger is non-empty" in migration
    assert "0030 downgrade refused: transfer-leg billing evidence is non-empty" in migration
    for evidence in (
        "billing_status <> 'none'",
        "reserved_seconds <> 0",
        "cost is not null",
        "currency is not null",
    ):
        assert evidence in migration
    downgrade = migration[migration.index("def downgrade") :]
    assert downgrade.index(
        "lock table call_legs, inbound_usage_transactions in access exclusive mode"
    ) < downgrade.index("if exists")


def test_complete_schema_has_the_typed_call_leg_billing_projection() -> None:
    schema = _normalized(SCHEMA.read_text(encoding="utf-8"))
    call_legs = schema[
        schema.index("create table if not exists call_legs") : schema.index("-- 3.2 call events")
    ]

    assert "billing_status varchar(16) not null default 'none'" in call_legs
    assert "reserved_seconds integer not null default 0" in call_legs
    assert "cost numeric(12, 6)" in call_legs
    assert "currency varchar(3)" in call_legs
    assert "uq_call_legs_id_call" in call_legs
    assert "cost is null or cost >= 0" in call_legs
    for constraint in (
        "call_legs_billing_status_valid",
        "call_legs_reserved_seconds_nonnegative",
        "call_legs_cost_nonnegative",
    ):
        assert constraint in call_legs


def test_parent_ledger_queries_cannot_select_a_transfer_child() -> None:
    admission = _normalized(ADMISSION.read_text(encoding="utf-8"))

    for expected in (
        "reserve.call_id=c.id and reserve.tenant_id=c.tenant_id and reserve.call_leg_id is null",
        "terminal.call_id=c.id and terminal.call_leg_id is null",
        (
            "where call_id=$1 and call_leg_id is null and "
            "transaction_type in ('finalize','release','reverse')"
        ),
        "where call_id=$1 and call_leg_id is null and transaction_type='reserve'",
        "where call_id=$1 and call_leg_id is null and transaction_type in ('finalize','release')",
        "where call_id=$1 and call_leg_id is null and transaction_type in ('reserve','finalize')",
    ):
        assert expected in admission
    assert admission.count("tenant_id, call_id, call_leg_id, transaction_type") >= 3
    assert admission.count("values ($1,$2,null") >= 3


def test_quota_authority_counts_parent_and_every_transfer_leg_state() -> None:
    transfer = _normalized(TRANSFER.read_text(encoding="utf-8"))

    assert "from calls c" in transfer
    assert "c.billing_status='reserved'" in transfer
    assert "c.billing_status='held'" in transfer
    assert "from call_legs leg join calls parent" in transfer
    assert "leg.leg_type='transfer'" in transfer
    for status in ("reserved", "held", "finalized", "reversed"):
        assert f"leg.billing_status='{status}'" in transfer
    held_projection = (
        "greatest( coalesce(leg.reserved_seconds,0), " "coalesce(leg.duration_seconds,0) )"
    )
    assert held_projection in transfer


def test_child_terminal_ledger_keeps_unknown_carrier_cost_null() -> None:
    transfer = _normalized(TRANSFER.read_text(encoding="utf-8"))

    assert "values ( $1,$2::uuid,$3::uuid,$4,$5,null,null" in transfer
    assert "cost_authority" in transfer
    assert "carrier_cdr_unavailable" in transfer
    assert "cost=null, currency=null" in transfer


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _TerminalConn:
    def __init__(
        self,
        *,
        leg_status: str = "initiated",
        parent_terminal: bool = False,
        settlement_enabled: bool = True,
        actual_seconds: int = 23,
    ) -> None:
        self.leg_status = leg_status
        self.billing_status = "reserved"
        self.parent_terminal = parent_terminal
        self.settlement_enabled = settlement_enabled
        self.actual_seconds = actual_seconds
        self.metadata = {
            "lease_id": "44444444-4444-4444-4444-444444444444",
            **({"provider_answer_persisted": True} if leg_status == "answered" else {}),
        }
        self.terminal_usage: list[tuple] = []
        self.executions: list[tuple] = []

    def transaction(self):
        return _Context(self)

    def _leg(self) -> dict:
        return {
            "id": UUID("33333333-3333-3333-3333-333333333333"),
            "status": self.leg_status,
            "metadata": dict(self.metadata),
            "provider_leg_id": "talky-xfer-0000000000000000000f",
            "billing_status": self.billing_status,
            "reserved_seconds": 90,
            "started_at": object(),
            "answered_at": object() if self.leg_status == "answered" else None,
            "duration_seconds": None,
            "actual_duration_seconds": (
                self.actual_seconds if self.leg_status == "answered" else 0
            ),
        }

    async def fetchrow(self, query, *args):
        if "FROM calls" in query:
            return {
                "tenant_id": "22222222-2222-2222-2222-222222222222",
                "talklee_call_id": "IN-billing-path",
                "status": "ended" if self.parent_terminal else "in_call",
                "processing_status": ("completed" if self.parent_terminal else "active"),
                "ended_at": object() if self.parent_terminal else None,
            }
        if "FROM platform_runtime_controls" in query:
            return {"inbound_settlement_enabled": self.settlement_enabled}
        if "transaction_type='reserve'" in query:
            return {"id": "usage-reserve", "quantity_seconds": 90}
        if "INSERT INTO inbound_usage_transactions" in query:
            self.terminal_usage.append(args)
            return {"id": f"usage-{args[3]}"}
        if "SET status='reconciliation_required'" in query:
            assert self.leg_status == args[3]
            assert self.billing_status == "reserved"
            self.leg_status = "reconciliation_required"
            self.billing_status = "held"
            self.metadata.update(json.loads(args[2]))
            return {"id": args[0]}
        if "UPDATE call_legs" in query:
            assert self.leg_status == args[7]
            assert self.billing_status == args[8]
            self.leg_status = args[2]
            self.billing_status = args[4]
            return {"id": args[0]}
        if "FROM call_legs" in query:
            return self._leg()
        raise AssertionError(query)

    async def fetch(self, query, *_args):
        if "FROM call_legs" not in query:
            raise AssertionError(query)
        if self.leg_status not in {"initiated", "ringing", "answered"}:
            return []
        return [self._leg()]

    async def fetchval(self, query, *_args):
        if "billing_status NOT IN" not in query:
            raise AssertionError(query)
        if (
            self.leg_status == "reconciliation_required"
            and self.billing_status == "held"
            and self.metadata.get("restart_answer_state_ambiguous") is True
        ):
            return 0
        return int(self.billing_status not in {"finalized", "released", "reversed"})

    async def execute(self, query, *args):
        self.executions.append((query, args))
        return None


class _Limiter:
    releases: list[dict] = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def release_lease(self, *_args, **kwargs):
        self.releases.append(kwargs)
        return True


def _attempt() -> transfer_module.InboundTransferAttempt:
    return transfer_module.InboundTransferAttempt(
        inbound=True,
        call_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        talklee_call_id="IN-billing-path",
        leg_id="33333333-3333-3333-3333-333333333333",
        provider_leg_id="talky-xfer-0000000000000000000f",
        lease_id="44444444-4444-4444-4444-444444444444",
        usage_reservation_id="usage-reserve",
        reserved_seconds=90,
        destination="+14155550123",
        attempt_number=1,
        hop_number=1,
    )


@pytest.mark.asyncio
async def test_provider_answer_is_durable_and_idempotent_before_handoff(
    monkeypatch,
) -> None:
    class AnswerConn:
        def __init__(self) -> None:
            self.status = "initiated"
            self.answered_at = None
            self.metadata = {"lease_id": "44444444-4444-4444-4444-444444444444"}
            self.executions: list[tuple] = []
            self.update_count = 0

        def transaction(self):
            return _Context(self)

        async def fetchrow(self, query, *args):
            if "SELECT l.id" in query:
                return {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "call_id": "11111111-1111-1111-1111-111111111111",
                    "status": self.status,
                    "answered_at": self.answered_at,
                    "billing_status": "reserved",
                    "reserved_seconds": 90,
                    "metadata": dict(self.metadata),
                    "talklee_call_id": "IN-answer-proof",
                    "parent_status": "in_call",
                    "parent_processing_status": "active",
                    "parent_billing_status": "reserved",
                }
            if "UPDATE call_legs" in query:
                self.update_count += 1
                self.status = "answered"
                self.answered_at = object()
                self.metadata.update({"provider_answer_persisted": True})
                return {"id": args[0]}
            raise AssertionError(query)

        async def execute(self, query, *args):
            self.executions.append((query, args))
            return None

    conn = AnswerConn()
    monkeypatch.setattr(
        transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )

    assert (
        await transfer_module.mark_inbound_transfer_answered(
            object(),
            parent_call_id="provider-parent",
            provider_leg_id="talky-xfer-0000000000000000000f",
        )
        == 1
    )
    assert conn.status == "answered"
    assert conn.metadata["provider_answer_persisted"] is True
    assert any("transfer_target_answered" in query for query, _ in conn.executions)

    assert (
        await transfer_module.mark_inbound_transfer_answered(
            object(),
            parent_call_id="provider-parent",
            provider_leg_id="talky-xfer-0000000000000000000f",
        )
        == 0
    )
    assert conn.update_count == 1


@pytest.mark.asyncio
async def test_provider_proved_preanswer_failure_releases_child_once(
    monkeypatch,
) -> None:
    conn = _TerminalConn()
    _Limiter.releases = []
    monkeypatch.setattr(
        transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )
    monkeypatch.setattr(transfer_module, "TelephonyConcurrencyLimiter", _Limiter)

    await transfer_module.complete_inbound_transfer(
        object(),
        attempt=_attempt(),
        succeeded=False,
        result={"status": "failed", "target_absent": True},
        redis_client=object(),
    )
    await transfer_module.complete_inbound_transfer(
        object(),
        attempt=_attempt(),
        succeeded=False,
        result={"status": "failed", "target_absent": True},
        redis_client=object(),
    )

    assert conn.leg_status == "failed"
    assert conn.billing_status == "released"
    assert len(conn.terminal_usage) == 1
    assert conn.terminal_usage[0][3:5] == ("release", -90)
    assert len(_Limiter.releases) == 1


@pytest.mark.asyncio
async def test_answer_racing_parent_terminal_finalizes_not_releases(
    monkeypatch,
) -> None:
    conn = _TerminalConn(leg_status="answered", parent_terminal=True)
    _Limiter.releases = []
    monkeypatch.setattr(
        transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )
    monkeypatch.setattr(transfer_module, "TelephonyConcurrencyLimiter", _Limiter)

    await transfer_module.complete_inbound_transfer(
        object(),
        attempt=_attempt(),
        succeeded=True,
        result={"status": "success"},
        redis_client=object(),
    )

    assert conn.leg_status == "completed"
    assert conn.billing_status == "finalized"
    assert conn.terminal_usage[0][3:5] == ("finalize", -67)
    assert len(_Limiter.releases) == 1


@pytest.mark.asyncio
async def test_provider_success_without_durable_answer_never_releases_zero_seconds(
    monkeypatch,
) -> None:
    conn = _TerminalConn(parent_terminal=True)
    _Limiter.releases = []
    monkeypatch.setattr(
        transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )
    monkeypatch.setattr(transfer_module, "TelephonyConcurrencyLimiter", _Limiter)

    with pytest.raises(RuntimeError, match="transfer_answer_proof_missing"):
        await transfer_module.complete_inbound_transfer(
            object(),
            attempt=_attempt(),
            succeeded=True,
            result={"status": "success"},
            redis_client=object(),
        )

    assert conn.leg_status == "initiated"
    assert conn.billing_status == "reserved"
    assert conn.terminal_usage == []
    assert _Limiter.releases == []


@pytest.mark.asyncio
async def test_answered_child_keeps_reservation_when_settlement_is_disabled(
    monkeypatch,
) -> None:
    conn = _TerminalConn(
        leg_status="answered",
        settlement_enabled=False,
        actual_seconds=23,
    )
    _Limiter.releases = []
    monkeypatch.setattr(
        transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )
    monkeypatch.setattr(transfer_module, "TelephonyConcurrencyLimiter", _Limiter)

    with pytest.raises(RuntimeError, match="transfer_settlement_held"):
        await transfer_module.finalize_connected_inbound_transfers(
            object(),
            call_id="11111111-1111-1111-1111-111111111111",
            terminal_reason="caller_hangup",
            redis_client=object(),
        )

    assert conn.leg_status == "answered"
    assert conn.billing_status == "reserved"
    assert conn.terminal_usage == []
    assert _Limiter.releases == []


@pytest.mark.asyncio
@pytest.mark.parametrize("leg_status", ["initiated", "ringing"])
async def test_restart_ambiguous_preanswer_child_is_held_not_released(
    monkeypatch,
    leg_status: str,
) -> None:
    conn = _TerminalConn(leg_status=leg_status)
    _Limiter.releases = []
    monkeypatch.setattr(
        transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )
    monkeypatch.setattr(transfer_module, "TelephonyConcurrencyLimiter", _Limiter)

    finalized = await transfer_module.finalize_connected_inbound_transfers(
        object(),
        call_id="11111111-1111-1111-1111-111111111111",
        terminal_reason="process_restart_recovery",
        redis_client=object(),
        hold_ambiguous_transfer_legs=True,
    )

    assert finalized == 1
    assert conn.leg_status == "reconciliation_required"
    assert conn.billing_status == "held"
    assert conn.terminal_usage == []
    assert conn.metadata["restart_answer_state_ambiguous"] is True
    assert conn.metadata["reservation_preserved"] is True
    assert conn.metadata["cost_authority"] == "carrier_cdr_required"
    assert _Limiter.releases == [
        {
            "tenant_id": "22222222-2222-2222-2222-222222222222",
            "lease_id": "44444444-4444-4444-4444-444444444444",
            "reason": "restart_answer_state_ambiguous",
            "request_id": "transfer-terminal:33333333-3333-3333-3333-333333333333",
        }
    ]
    events = [(query, args) for query, args in conn.executions if "transfer_billing_held" in query]
    assert len(events) == 1

    assert (
        await transfer_module.finalize_connected_inbound_transfers(
            object(),
            call_id="11111111-1111-1111-1111-111111111111",
            terminal_reason="duplicate_restart_recovery",
            redis_client=object(),
            hold_ambiguous_transfer_legs=True,
        )
        == 0
    )
    assert len(_Limiter.releases) == 1


def test_parent_settlement_allows_only_explicit_restart_reconciliation_holds() -> None:
    admission = _normalized(ADMISSION.read_text(encoding="utf-8"))

    assert "status='reconciliation_required'" in admission
    assert "billing_status='held'" in admission
    assert "metadata->>'restart_answer_state_ambiguous'" in admission

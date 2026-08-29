"""Inbound transfer endpoints enforce live and pinned controls before PBX."""

import json

from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.telephony_bridge import _enforce_inbound_transfer_policy
import app.domain.services.telephony.inbound_transfer as inbound_transfer_module


PROOF_TENANT_ID = "22222222-2222-2222-2222-222222222222"
PROOF_CONFIG_ID = "55555555-5555-5555-5555-555555555555"


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _Conn:
    def __init__(
        self,
        row,
        enabled=True,
        *,
        attempt_count=0,
        completed_hops=0,
        transfer_active=False,
        quota_minutes=100_000,
        accounted_seconds=0,
    ):
        self.row = row
        self.enabled = enabled
        self.attempt_count = attempt_count
        self.completed_hops = completed_hops
        self.transfer_active = transfer_active
        self.quota_minutes = quota_minutes
        self.accounted_seconds = accounted_seconds
        self.executions = []
        self.fetchrows = []
        self.fetchvals = []
        self.idempotency = None
        self.transfer_leg = None

    def transaction(self):
        return _Context(self)

    async def execute(self, *_args):
        self.executions.append(_args)
        query = str(_args[0]) if _args else ""
        if "INSERT INTO call_legs" in query:
            self.transfer_leg = {
                "id": _args[1],
                "provider_leg_id": _args[5],
                "to_number": _args[6],
                "status": "initiated",
                "metadata": json.loads(_args[8]),
                "reserved_seconds": _args[7],
            }
        if "SET metadata=metadata ||" in query and self.transfer_leg is not None:
            self.transfer_leg["metadata"].update(json.loads(_args[2]))
        return None

    async def fetchrow(self, *_args):
        self.fetchrows.append(_args)
        query = str(_args[0]) if _args else ""
        if "FROM inbound_operation_idempotency" in query:
            return self.idempotency
        if "INSERT INTO inbound_operation_idempotency" in query:
            self.idempotency = {
                "id": _args[1],
                "request_hash": _args[6],
                "response_body": None,
                "status_code": None,
                "resource_type": None,
                "resource_id": None,
                "actor_id": _args[7],
            }
            return {"id": _args[1]}
        if "UPDATE inbound_operation_idempotency" in query:
            if self.idempotency is not None and "resource_id=$2" in query:
                self.idempotency["resource_type"] = "call_leg"
                self.idempotency["resource_id"] = _args[2]
            return {"id": _args[1]}
        if (
            "FROM call_legs" in query
            and "WHERE id=$1::uuid" in query
            and "leg_type='transfer'" in query
        ):
            return self.transfer_leg
        if "AS attempt_count" in query:
            assert len(_args) == 2, "transfer aggregate query accepts only call_id"
            return {
                "attempt_count": self.attempt_count,
                "completed_hop_count": self.completed_hops,
                "transfer_active": self.transfer_active,
            }
        if "SELECT minutes_allocated FROM tenants" in query:
            return {"minutes_allocated": self.quota_minutes}
        if "INSERT INTO inbound_usage_transactions" in query:
            return {"id": UUID("44444444-4444-4444-4444-444444444444")}
        return self.row

    async def fetchval(self, *_args):
        self.fetchvals.append(_args)
        query = str(_args[0]) if _args else ""
        if "platform_runtime_controls" in query:
            return self.enabled
        if "FROM call_legs leg" in query:
            return self.accounted_seconds
        raise AssertionError(query)


class _Pool:
    def __init__(self, row, enabled=True, **conn_kwargs):
        self.conn = _Conn(row, enabled, **conn_kwargs)

    def acquire(self):
        return _Context(self.conn)


def _snapshot():
    return {
        "inbound_config": {
            "after_hours_action": "hangup",
            "transfer_number": None,
            "transfer_policy": {
                "enabled": True,
                "destinations": ["+14155550123"],
            },
        },
        "route": {
            "config_id": PROOF_CONFIG_ID,
            "called_did": "+15551234567",
        },
    }


async def _run(
    row,
    destination="+14155550123",
    enabled=True,
    runtime_available=True,
    *,
    mode="blind",
    source="api",
    connection_out=None,
    quota_minutes=100_000,
    accounted_seconds=0,
    idempotency_key="transfer-control-test-key",
    pool_override=None,
):
    if row is not None and pool_override is None:
        row = {
            "id": UUID("11111111-1111-1111-1111-111111111111"),
            "tenant_id": UUID("22222222-2222-2222-2222-222222222222"),
            "talklee_call_id": "IN-test-call",
            "provider": "asterisk",
            "status": "in_call",
            "admission_status": "allowed",
            "processing_status": "active",
            "billing_status": "reserved",
            "reserved_seconds": 3_600,
            "transfer_reservation_seconds": 1_800,
            **row,
        }
    pool = pool_override or _Pool(
        row,
        enabled,
        quota_minutes=quota_minutes,
        accounted_seconds=accounted_seconds,
    )
    container = SimpleNamespace(is_initialized=True, db_pool=pool)
    if connection_out is not None:
        connection_out.append(container.db_pool.conn)

    class _Limiter:
        def __init__(self, *_args, **_kwargs):
            pass

        async def acquire_lease(self, *_args, **_kwargs):
            return SimpleNamespace(
                accepted=True,
                lease_id=UUID("33333333-3333-3333-3333-333333333333"),
            )

    with (
        patch("app.core.container.get_container", return_value=container),
        patch(
            "app.domain.services.telephony.inbound_transfer.TelephonyConcurrencyLimiter",
            _Limiter,
        ),
        patch.object(
            inbound_transfer_module,
            "CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE",
            runtime_available,
        ),
    ):
        attempt = await _enforce_inbound_transfer_policy(
            "provider-call",
            destination,
            mode=mode,
            source=source,
            idempotency_key=idempotency_key,
        )
        return attempt, container.db_pool.conn


@pytest.mark.asyncio
async def test_outbound_transfer_behavior_is_unchanged():
    await _run({"direction": "outbound", "route_snapshot": {}})


@pytest.mark.asyncio
async def test_inbound_transfer_runtime_fails_closed_before_live_switch():
    with pytest.raises(HTTPException) as exc:
        await _run(
            {"direction": "inbound", "route_snapshot": _snapshot()},
            enabled=True,
            runtime_available=False,
        )

    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "transfer_runtime_unavailable"


@pytest.mark.parametrize("environment", ["development", "test", "", "production"])
def test_staging_transfer_proof_switch_is_environment_scoped(monkeypatch, environment):
    monkeypatch.setattr(
        inbound_transfer_module,
        "CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE",
        False,
    )
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_ENABLED", "true")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID", PROOF_TENANT_ID)
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID", PROOF_CONFIG_ID)

    assert inbound_transfer_module.inbound_transfer_runtime_available() is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on", "enabled"])
def test_staging_transfer_proof_requires_explicit_truthy_switch(monkeypatch, value):
    monkeypatch.setattr(
        inbound_transfer_module,
        "CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE",
        False,
    )
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_ENABLED", value)
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID", PROOF_TENANT_ID)
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID", PROOF_CONFIG_ID)

    assert inbound_transfer_module.inbound_transfer_runtime_available() is True


def test_staging_transfer_proof_defaults_closed(monkeypatch):
    monkeypatch.setattr(
        inbound_transfer_module,
        "CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE",
        False,
    )
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.delenv("INBOUND_TRANSFER_STAGING_PROOF_ENABLED", raising=False)
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID", PROOF_TENANT_ID)
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID", PROOF_CONFIG_ID)

    assert inbound_transfer_module.inbound_transfer_runtime_available() is False


@pytest.mark.parametrize(
    ("tenant_scope", "config_scope"),
    [
        ("", PROOF_CONFIG_ID),
        (PROOF_TENANT_ID, ""),
        ("not-a-uuid", PROOF_CONFIG_ID),
        (PROOF_TENANT_ID, "not-a-uuid"),
    ],
)
def test_staging_transfer_proof_requires_valid_complete_scope(
    monkeypatch,
    tenant_scope,
    config_scope,
):
    monkeypatch.setattr(
        inbound_transfer_module,
        "CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE",
        False,
    )
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_ENABLED", "true")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID", tenant_scope)
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID", config_scope)

    assert inbound_transfer_module.inbound_transfer_runtime_available() is False


def test_staging_transfer_scope_matches_only_exact_tenant_and_config(monkeypatch):
    monkeypatch.setattr(
        inbound_transfer_module,
        "CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE",
        False,
    )
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_ENABLED", "true")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID", PROOF_TENANT_ID)
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID", PROOF_CONFIG_ID)

    assert inbound_transfer_module.inbound_transfer_scope_available(
        tenant_id=PROOF_TENANT_ID,
        config_id=PROOF_CONFIG_ID,
    )
    assert not inbound_transfer_module.inbound_transfer_scope_available(
        tenant_id="99999999-9999-9999-9999-999999999999",
        config_id=PROOF_CONFIG_ID,
    )
    assert not inbound_transfer_module.inbound_transfer_scope_available(
        tenant_id=PROOF_TENANT_ID,
        config_id="99999999-9999-9999-9999-999999999999",
    )


@pytest.mark.asyncio
async def test_transfer_authorization_rejects_out_of_scope_staging_campaign(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_ENABLED", "true")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID", PROOF_TENANT_ID)
    monkeypatch.setenv(
        "INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID",
        "99999999-9999-9999-9999-999999999999",
    )

    with pytest.raises(HTTPException) as exc:
        await _run(
            {"direction": "inbound", "route_snapshot": _snapshot()},
            runtime_available=False,
        )

    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "transfer_staging_scope_mismatch"


@pytest.mark.asyncio
async def test_live_platform_switch_remains_a_second_gate():
    with pytest.raises(HTTPException) as exc:
        await _run(
            {"direction": "inbound", "route_snapshot": _snapshot()},
            enabled=False,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_transfer_disabled"


@pytest.mark.asyncio
async def test_pinned_allowlist_accepts_only_approved_destination():
    await _run(
        {"direction": "inbound", "route_snapshot": _snapshot()},
    )

    with pytest.raises(HTTPException) as exc:
        await _run(
            {"direction": "inbound", "route_snapshot": _snapshot()},
            destination="+14155550999",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_transfer_not_approved"


@pytest.mark.asyncio
async def test_non_blind_mode_is_rejected_before_attempt_is_persisted():
    connections = []
    with pytest.raises(HTTPException) as exc:
        await _run(
            {"direction": "inbound", "route_snapshot": _snapshot()},
            mode="attended",
            connection_out=connections,
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "unsupported_inbound_transfer_mode"
    assert not any("INSERT INTO call_legs" in str(args[0]) for args in connections[0].executions)


@pytest.mark.asyncio
async def test_after_hours_destination_must_already_be_in_policy_allowlist():
    snapshot = _snapshot()
    snapshot["inbound_config"].update(
        {
            "selected_action": "transfer",
            "selected_destination": "+14155550999",
        }
    )

    with pytest.raises(HTTPException) as exc:
        await _run(
            {"direction": "inbound", "route_snapshot": snapshot},
            destination="+14155550999",
            source="after_hours",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_transfer_not_approved"

    snapshot["inbound_config"]["transfer_policy"]["destinations"] = ["+1 (415) 555-0999"]
    attempt, _ = await _run(
        {"direction": "inbound", "route_snapshot": snapshot},
        destination="+14155550999",
        source="after_hours",
    )
    assert attempt.destination == "+14155550999"


@pytest.mark.asyncio
async def test_authorization_persists_exact_provider_target_before_adapter_use():
    attempt, conn = await _run(
        {"direction": "inbound", "route_snapshot": _snapshot()},
    )

    assert attempt.inbound is True
    assert attempt.provider_leg_id is not None
    assert attempt.provider_leg_id.startswith("talky-xfer-")
    assert len(attempt.provider_leg_id.removeprefix("talky-xfer-")) == 20
    leg_insert = next(args for args in conn.executions if "INSERT INTO call_legs" in str(args[0]))
    assert leg_insert[5] == attempt.provider_leg_id


@pytest.mark.asyncio
async def test_durable_same_key_replays_without_creating_a_second_leg():
    first, conn = await _run(
        {"direction": "inbound", "route_snapshot": _snapshot()},
        idempotency_key="durable-transfer-replay-key",
    )
    shared_pool = _Pool.__new__(_Pool)
    shared_pool.conn = conn

    replay, _ = await _run(
        None,
        idempotency_key="durable-transfer-replay-key",
        pool_override=shared_pool,
    )

    assert first.is_replay is False
    assert replay.is_replay is True
    assert replay.leg_id == first.leg_id
    assert replay.provider_leg_id == first.provider_leg_id
    assert sum("INSERT INTO call_legs" in str(args[0]) for args in conn.executions) == 1
    assert sum("INSERT INTO inbound_audit_events" in str(args[0]) for args in conn.executions) == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_with_changed_request_is_rejected():
    _first, conn = await _run(
        {"direction": "inbound", "route_snapshot": _snapshot()},
        idempotency_key="durable-transfer-conflict-key",
    )
    shared_pool = _Pool.__new__(_Pool)
    shared_pool.conn = conn

    with pytest.raises(HTTPException) as exc:
        await _run(
            None,
            destination="+14155550999",
            idempotency_key="durable-transfer-conflict-key",
            pool_override=shared_pool,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "transfer_idempotency_conflict"
    assert sum("INSERT INTO call_legs" in str(args[0]) for args in conn.executions) == 1


@pytest.mark.asyncio
async def test_completed_idempotency_replay_returns_exact_stored_result():
    first, conn = await _run(
        {"direction": "inbound", "route_snapshot": _snapshot()},
        idempotency_key="durable-transfer-result-key",
    )
    stored = {
        "status": "success",
        "attempt_id": first.leg_id,
        "handoff_confirmed": True,
    }
    conn.idempotency["response_body"] = stored
    conn.idempotency["status_code"] = 200
    shared_pool = _Pool.__new__(_Pool)
    shared_pool.conn = conn

    replay, _ = await _run(
        None,
        idempotency_key="durable-transfer-result-key",
        pool_override=shared_pool,
    )

    assert replay.is_replay is True
    assert replay.replay_result == stored


@pytest.mark.asyncio
async def test_transfer_status_projection_is_tenant_scoped_and_truthful(monkeypatch):
    class StatusConn:
        async def fetchrow(self, query, *args):
            assert "c.tenant_id=$2::uuid" in query
            assert args[1] == "22222222-2222-2222-2222-222222222222"
            return {
                "id": UUID("33333333-3333-3333-3333-333333333333"),
                "call_id": UUID("11111111-1111-1111-1111-111111111111"),
                "talklee_call_id": "IN-status",
                "provider_leg_id": "talky-xfer-status",
                "to_number": "+14155550123",
                "status": "initiated",
                "billing_status": "reserved",
                "reserved_seconds": 90,
                "duration_seconds": None,
                "cost": None,
                "currency": None,
                "started_at": "2026-08-27T10:00:00+00:00",
                "answered_at": None,
                "ended_at": None,
                "metadata": {
                    "mode": "blind",
                    "source": "api",
                    "cleanup_pending": True,
                    "idempotency_key": "status-key",
                },
                "tenant_id": UUID("22222222-2222-2222-2222-222222222222"),
                "idempotency_key": "status-key",
                "response_body": {
                    "status": "cleanup_pending",
                    "attempt_id": "33333333-3333-3333-3333-333333333333",
                },
                "status_code": 202,
            }

    monkeypatch.setattr(
        inbound_transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(StatusConn()),
    )
    result = await inbound_transfer_module.get_inbound_transfer_attempt(
        object(),
        attempt_id="33333333-3333-3333-3333-333333333333",
        tenant_id="22222222-2222-2222-2222-222222222222",
    )

    assert result["status"] == "cleanup_pending"
    assert result["leg_status"] == "initiated"
    assert result["terminal"] is False
    assert result["billing_status"] == "reserved"
    assert result["cost"] is None
    assert result["http_status"] == 202


@pytest.mark.asyncio
async def test_transfer_status_rejects_non_uuid_attempt_id():
    with pytest.raises(ValueError, match="attempt_id must be a UUID"):
        await inbound_transfer_module.get_inbound_transfer_attempt(
            object(),
            attempt_id="not-a-uuid",
        )


@pytest.mark.asyncio
async def test_transfer_status_exposes_restart_reconciliation_hold(monkeypatch):
    class StatusConn:
        async def fetchrow(self, _query, *_args):
            return {
                "id": UUID("33333333-3333-3333-3333-333333333333"),
                "call_id": UUID("11111111-1111-1111-1111-111111111111"),
                "talklee_call_id": "IN-held",
                "provider_leg_id": "talky-xfer-held",
                "to_number": "+14155550123",
                "status": "reconciliation_required",
                "billing_status": "held",
                "reserved_seconds": 90,
                "duration_seconds": None,
                "cost": None,
                "currency": None,
                "started_at": "2026-08-27T10:00:00+00:00",
                "answered_at": None,
                "ended_at": "2026-08-27T10:01:00+00:00",
                "metadata": {
                    "restart_answer_state_ambiguous": True,
                    "idempotency_key": "held-status-key",
                },
                "tenant_id": UUID("22222222-2222-2222-2222-222222222222"),
                "idempotency_key": "held-status-key",
                "response_body": {
                    "status": "reconciliation_required",
                    "billing_status": "held",
                    "reconciliation_required": True,
                },
                "status_code": 202,
            }

    monkeypatch.setattr(
        inbound_transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(StatusConn()),
    )
    result = await inbound_transfer_module.get_inbound_transfer_attempt(
        object(),
        attempt_id="33333333-3333-3333-3333-333333333333",
        tenant_id="22222222-2222-2222-2222-222222222222",
    )

    assert result["status"] == "reconciliation_required"
    assert result["leg_status"] == "reconciliation_required"
    assert result["terminal"] is False
    assert result["reconciliation_required"] is True
    assert result["billing_status"] == "held"
    assert result["http_status"] == 202


@pytest.mark.asyncio
async def test_authorization_reserves_exact_remaining_parent_deadline_per_leg():
    attempt, conn = await _run(
        {
            "direction": "inbound",
            "route_snapshot": _snapshot(),
            "transfer_reservation_seconds": 137,
        },
    )

    assert attempt.reserved_seconds == 137
    leg_insert = next(args for args in conn.executions if "INSERT INTO call_legs" in str(args[0]))
    reserve_insert = next(
        args for args in conn.fetchrows if "INSERT INTO inbound_usage_transactions" in str(args[0])
    )
    assert reserve_insert[3] == leg_insert[1]  # one durable call_leg subject
    assert reserve_insert[4] == 137
    assert reserve_insert[0].count("NULL") >= 2  # unknown cost/currency stay NULL
    assert any("FROM call_legs leg" in str(args[0]) for args in conn.fetchvals)


@pytest.mark.asyncio
async def test_authorization_rejects_child_reservation_over_remaining_quota():
    connections = []
    with pytest.raises(HTTPException) as exc:
        await _run(
            {
                "direction": "inbound",
                "route_snapshot": _snapshot(),
                "transfer_reservation_seconds": 120,
            },
            quota_minutes=2,
            accounted_seconds=1,
            connection_out=connections,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "insufficient_transfer_minutes"
    assert not any("INSERT INTO call_legs" in str(args[0]) for args in connections[0].executions)


@pytest.mark.asyncio
async def test_cleanup_pending_keeps_active_leg_and_transfer_lease(monkeypatch):
    """An adapter timeout is not evidence that the target stopped billing."""

    class CompletionConn:
        def __init__(self):
            self.executions = []
            self.fetches = []
            self.update_query = ""
            self.update_args = ()

        def transaction(self):
            return _Context(self)

        async def execute(self, *args):
            self.executions.append(args)
            return None

        async def fetchrow(self, query, *args):
            self.fetches.append((query, args))
            if "FROM calls" in query:
                return {
                    "tenant_id": "22222222-2222-2222-2222-222222222222",
                    "talklee_call_id": "IN-cleanup-pending",
                    "status": "in_call",
                    "processing_status": "active",
                    "ended_at": None,
                }
            if "FROM call_legs\n" in query:
                return {
                    "id": args[0],
                    "status": "initiated",
                    "metadata": {"lease_id": "44444444-4444-4444-4444-444444444444"},
                    "provider_leg_id": "talky-xfer-0000000000000000000c",
                    "billing_status": "reserved",
                    "reserved_seconds": 120,
                    "started_at": object(),
                    "answered_at": None,
                    "duration_seconds": None,
                    "actual_duration_seconds": 0,
                }
            if "UPDATE call_legs" in query:
                self.update_query = query
                self.update_args = args
                return {"id": args[0], "status": "initiated"}
            raise AssertionError(query)

    class Limiter:
        releases = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def release_lease(self, *_args, **kwargs):
            self.releases.append(kwargs)

    conn = CompletionConn()
    attempt = inbound_transfer_module.InboundTransferAttempt(
        inbound=True,
        call_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        talklee_call_id="IN-cleanup-pending",
        leg_id="33333333-3333-3333-3333-333333333333",
        provider_leg_id="talky-xfer-0000000000000000000c",
        lease_id="44444444-4444-4444-4444-444444444444",
        destination="+14155550123",
        attempt_number=1,
        hop_number=1,
    )
    monkeypatch.setattr(
        inbound_transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )
    monkeypatch.setattr(
        inbound_transfer_module,
        "TelephonyConcurrencyLimiter",
        Limiter,
    )

    await inbound_transfer_module.complete_inbound_transfer(
        object(),
        attempt=attempt,
        succeeded=False,
        result={
            "status": "cleanup_pending",
            # A non-compliant provider response must never rewrite the exact
            # child identity persisted before ARI creation.
            "target_call_id": "provider-returned-different-id",
            "provider_leg_id": "provider-returned-other-id",
            "error": "target_termination_unconfirmed",
        },
        redis_client=object(),
    )

    assert "SET provider_leg_id" in conn.update_query
    assert "status IN ('initiated','ringing','answered')" in conn.update_query
    assert "SET status=" not in conn.update_query
    assert conn.update_args[2] == attempt.provider_leg_id
    assert Limiter.releases == []
    assert not any(
        "INSERT INTO inbound_usage_transactions" in query for query, _args in conn.fetches
    )
    assert any("transfer_cleanup_pending" in str(args[0]) for args in conn.executions)


@pytest.mark.asyncio
async def test_cleanup_pending_converges_when_target_absence_is_proved_later(
    monkeypatch,
):
    class StatefulConn:
        def __init__(self):
            self.status = "initiated"
            self.billing_status = "reserved"
            self.executions = []
            self.terminal_usage = []

        def transaction(self):
            return _Context(self)

        async def execute(self, *args):
            self.executions.append(args)
            return None

        async def fetchrow(self, query, *args):
            if "FROM calls" in query and "JOIN calls" not in query:
                return {
                    "tenant_id": "22222222-2222-2222-2222-222222222222",
                    "talklee_call_id": "IN-late-proof",
                    "status": "in_call",
                    "processing_status": "active",
                    "ended_at": None,
                }
            if "FROM call_legs\n" in query:
                if self.status not in {"initiated", "ringing", "answered"}:
                    return None
                return {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "status": self.status,
                    "metadata": {"lease_id": "44444444-4444-4444-4444-444444444444"},
                    "provider_leg_id": "talky-xfer-0000000000000000000e",
                    "billing_status": self.billing_status,
                    "reserved_seconds": 120,
                    "started_at": object(),
                    "answered_at": None,
                    "duration_seconds": None,
                    "actual_duration_seconds": 0,
                }
            if "SET provider_leg_id" in query:
                return (
                    {
                        "id": args[0],
                        "status": self.status,
                    }
                    if self.status in {"initiated", "ringing", "answered"}
                    else None
                )
            if "SELECT l.id" in query:
                if self.status not in {"initiated", "ringing", "answered"}:
                    return None
                return {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "call_id": "11111111-1111-1111-1111-111111111111",
                    "status": self.status,
                    "metadata": {"lease_id": "44444444-4444-4444-4444-444444444444"},
                    "tenant_id": "22222222-2222-2222-2222-222222222222",
                    "talklee_call_id": "IN-late-proof",
                    "provider_leg_id": "talky-xfer-0000000000000000000e",
                    "billing_status": self.billing_status,
                    "reserved_seconds": 120,
                    "started_at": object(),
                    "answered_at": None,
                    "duration_seconds": None,
                    "actual_duration_seconds": 0,
                }
            if "transaction_type='reserve'" in query:
                return {"id": "usage-reserve", "quantity_seconds": 120}
            if "INSERT INTO inbound_usage_transactions" in query:
                self.terminal_usage.append(args)
                return {"id": "usage-release"}
            if "UPDATE call_legs" in query:
                if self.status not in {"initiated", "ringing", "answered"}:
                    return None
                self.status = "failed"
                self.billing_status = args[4]
                return {"id": args[0]}
            raise AssertionError(query)

    class Limiter:
        releases = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def release_lease(self, *_args, **kwargs):
            self.releases.append(kwargs)
            return True

    conn = StatefulConn()
    attempt = inbound_transfer_module.InboundTransferAttempt(
        inbound=True,
        call_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        talklee_call_id="IN-late-proof",
        leg_id="33333333-3333-3333-3333-333333333333",
        provider_leg_id="talky-xfer-0000000000000000000e",
        lease_id="44444444-4444-4444-4444-444444444444",
        destination="+14155550123",
        attempt_number=1,
        hop_number=1,
    )
    monkeypatch.setattr(
        inbound_transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )
    monkeypatch.setattr(
        inbound_transfer_module,
        "TelephonyConcurrencyLimiter",
        Limiter,
    )

    # API timeout: target absence has not been proved. Keep child + lease.
    await inbound_transfer_module.complete_inbound_transfer(
        object(),
        attempt=attempt,
        succeeded=False,
        result={
            "status": "cleanup_pending",
            "provider_leg_id": attempt.provider_leg_id,
        },
        redis_client=object(),
    )
    assert conn.status == "initiated"
    assert Limiter.releases == []

    # Adapter inventory later proves only the target absent while the parent
    # AI call continues. The domain callback closes the child and its lease.
    assert (
        await inbound_transfer_module.finalize_proven_inbound_transfer_cleanup(
            object(),
            parent_call_id="parent-provider-id",
            provider_leg_id=attempt.provider_leg_id,
            reason="target_absent_after_timeout",
            redis_client=object(),
        )
        == 1
    )
    assert conn.status == "failed"
    assert conn.billing_status == "released"
    assert len(conn.terminal_usage) == 1
    assert conn.terminal_usage[0][3] == "release"
    assert conn.terminal_usage[0][4] == -120
    assert Limiter.releases == [
        {
            "tenant_id": "22222222-2222-2222-2222-222222222222",
            "lease_id": "44444444-4444-4444-4444-444444444444",
            "reason": "transfer_target_absent",
            "request_id": "transfer-cleanup:33333333-3333-3333-3333-333333333333",
        }
    ]

    # Duplicate proof is idempotent and cannot release twice.
    assert (
        await inbound_transfer_module.finalize_proven_inbound_transfer_cleanup(
            object(),
            parent_call_id="parent-provider-id",
            provider_leg_id=attempt.provider_leg_id,
            reason="duplicate",
            redis_client=object(),
        )
        == 0
    )
    assert len(Limiter.releases) == 1
    assert len(conn.terminal_usage) == 1


@pytest.mark.asyncio
async def test_parent_finalization_completes_only_answered_transfer_legs(monkeypatch):
    answered_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    initiated_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    ringing_id = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

    class FinalizationConn:
        def __init__(self):
            self.rows = [
                {
                    "id": answered_id,
                    "status": "answered",
                    "metadata": {"lease_id": "lease-answered"},
                    "provider_leg_id": "target-answered",
                    "billing_status": "reserved",
                    "reserved_seconds": 120,
                    "started_at": object(),
                    "answered_at": object(),
                    "duration_seconds": None,
                    "actual_duration_seconds": 37,
                },
                {
                    "id": initiated_id,
                    "status": "initiated",
                    "metadata": {"lease_id": "lease-initiated"},
                    "provider_leg_id": "target-initiated",
                    "billing_status": "reserved",
                    "reserved_seconds": 120,
                    "started_at": object(),
                    "answered_at": None,
                    "duration_seconds": None,
                    "actual_duration_seconds": 0,
                },
                {
                    "id": ringing_id,
                    "status": "ringing",
                    "metadata": {"lease_id": "lease-ringing"},
                    "provider_leg_id": "target-ringing",
                    "billing_status": "reserved",
                    "reserved_seconds": 120,
                    "started_at": object(),
                    "answered_at": None,
                    "duration_seconds": None,
                    "actual_duration_seconds": 0,
                },
            ]
            self.executions = []
            self.terminal_usage = []

        def transaction(self):
            return _Context(self)

        async def fetchrow(self, query, *args):
            if "FROM calls" in query:
                return {
                    "tenant_id": "22222222-2222-2222-2222-222222222222",
                    "talklee_call_id": "IN-parent-finalization",
                }
            if "platform_runtime_controls" in query:
                return {"inbound_settlement_enabled": True}
            if "transaction_type='reserve'" in query:
                return {
                    "id": f"reserve-{args[1]}",
                    "quantity_seconds": 120,
                }
            if "INSERT INTO inbound_usage_transactions" in query:
                self.terminal_usage.append(args)
                return {"id": f"terminal-{args[2]}"}
            if "UPDATE call_legs" in query:
                leg_id = args[0]
                row = next(item for item in self.rows if item["id"] == leg_id)
                assert row["status"] == args[7]
                assert row["billing_status"] == args[8]
                row["status"] = args[2]
                row["duration_seconds"] = args[3]
                row["billing_status"] = args[4]
                return {"id": leg_id}
            raise AssertionError(query)

        async def fetch(self, query, *_args):
            if "FROM call_legs" not in query:
                raise AssertionError(query)
            return [
                dict(row)
                for row in self.rows
                if row["status"] in {"initiated", "ringing", "answered"}
            ]

        async def execute(self, query, *args):
            self.executions.append((query, args))
            return None

        async def fetchval(self, query, *_args):
            if "billing_status NOT IN" not in query:
                raise AssertionError(query)
            return sum(
                row["billing_status"] not in {"finalized", "released", "reversed"}
                for row in self.rows
            )

    class Limiter:
        releases = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def release_lease(self, *_args, **kwargs):
            self.releases.append(kwargs)
            return True

    Limiter.releases = []
    conn = FinalizationConn()
    monkeypatch.setattr(
        inbound_transfer_module,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )
    monkeypatch.setattr(
        inbound_transfer_module,
        "TelephonyConcurrencyLimiter",
        Limiter,
    )

    finalized = await inbound_transfer_module.finalize_connected_inbound_transfers(
        object(),
        call_id="11111111-1111-1111-1111-111111111111",
        terminal_reason="caller_hangup",
        redis_client=object(),
    )

    assert finalized == 3
    assert {row["id"]: row["status"] for row in conn.rows} == {
        answered_id: "completed",
        initiated_id: "failed",
        ringing_id: "failed",
    }
    assert {row["id"]: row["billing_status"] for row in conn.rows} == {
        answered_id: "finalized",
        initiated_id: "released",
        ringing_id: "released",
    }
    assert {row["id"]: row["duration_seconds"] for row in conn.rows} == {
        answered_id: 37,
        initiated_id: 0,
        ringing_id: 0,
    }
    assert {args[2]: (args[3], args[4]) for args in conn.terminal_usage} == {
        answered_id: ("finalize", -83),
        initiated_id: ("release", -120),
        ringing_id: ("release", -120),
    }
    events = [args for query, args in conn.executions if "INSERT INTO call_events" in query]
    assert {args[2]: args[3] for args in events} == {
        answered_id: "transfer_completed",
        initiated_id: "transfer_failed",
        ringing_id: "transfer_failed",
    }
    payloads = {args[2]: json.loads(args[4]) for args in events}
    assert payloads[answered_id]["terminal_before_answer"] is False
    assert payloads[initiated_id]["terminal_before_answer"] is True
    assert payloads[ringing_id]["terminal_before_answer"] is True
    assert {item["lease_id"]: item["reason"] for item in Limiter.releases} == {
        "lease-answered": "completed",
        "lease-initiated": "parent_terminated_before_transfer_answer",
        "lease-ringing": "parent_terminated_before_transfer_answer",
    }

    assert (
        await inbound_transfer_module.finalize_connected_inbound_transfers(
            object(),
            call_id="11111111-1111-1111-1111-111111111111",
            terminal_reason="duplicate",
            redis_client=object(),
        )
        == 0
    )
    assert len(Limiter.releases) == 3
    assert len(conn.terminal_usage) == 3


@pytest.mark.asyncio
async def test_missing_call_fails_closed():
    with pytest.raises(HTTPException) as exc:
        await _run(None)

    assert exc.value.status_code == 404

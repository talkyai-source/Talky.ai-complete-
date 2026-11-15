"""Outbound transfer policy boundary: trunk, brief check, no-answer handling.

``_execute_transfer`` runs against fakes (no database, no PBX). An inbound call
must never reach the new outbound code; every non-Asterisk adapter keeps its
old call shape.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.api.v1.endpoints.telephony_bridge as bridge


TENANT = "22222222-2222-2222-2222-222222222222"
CALL = "11111111-1111-1111-1111-111111111111"
CAMPAIGN = "44444444-4444-4444-4444-444444444444"
TARGET = "+442071234567"
OWN_NUMBER = "+442046132300"
TRUNK = "trunk-acme-1"

OPEN_HOURS = {
    "timezone": "UTC",
    "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "start": "00:00",
    "end": "23:59",
}
CLOSED_HOURS = {
    "timezone": "UTC",
    "days": [],
    "start": "09:00",
    "end": "17:00",
}


class _Conn:
    def __init__(self, *, call=True, campaign=True, brief=None, leg=None):
        self.call = call
        self.campaign = campaign
        self.brief = {} if brief is None else brief
        self.leg = leg
        self.reads = []
        self.events = []
        self.executes = []

    def transaction(self):
        @asynccontextmanager
        async def _tx():
            yield

        return _tx()

    async def execute(self, sql, *args):
        self.executes.append(sql)
        if "INSERT INTO call_events" in sql:
            assert "'outbound_transfer'" in sql
            self.events.append(args)
        return "OK"

    async def fetchrow(self, sql, *args):
        self.reads.append((sql, args))
        if sql.startswith("SELECT campaign_id FROM calls"):
            if not self.call:
                return None
            return {"campaign_id": CAMPAIGN}
        if "FROM campaigns" in sql:
            if not self.campaign:
                return None
            return {"script_config": {"campaign_brief": self.brief}}
        if "FROM call_legs" in sql:
            return self.leg
        raise AssertionError(f"unexpected SQL: {sql}")


class _Adapter:
    def __init__(self, name="asterisk", result=None):
        self.name = name
        self.connected = True
        self.result = result or {"status": "completed"}
        self.calls = []

    async def transfer(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return dict(self.result)


def _outbound_attempt(**over):
    values = {"inbound": False, "call_id": CALL, "tenant_id": TENANT, "talklee_call_id": "TK-1"}
    values.update(over)
    return SimpleNamespace(**values)


def _brief(**extra):
    return {"transfer_destinations": [{"name": "Sales", "number": TARGET, "when": ""}], **extra}


def _leg(endpoint=TRUNK, from_number=OWN_NUMBER):
    metadata = {"trunk_id": "t1", "route_reason": "x"}
    if endpoint is not None:
        metadata["endpoint"] = endpoint
    return {"metadata": metadata, "from_number": from_number}


async def _run(
    *,
    conn=None,
    adapter=None,
    destination=TARGET,
    mode="blind",
    attempt=None,
    **kwargs,
):
    conn = conn or _Conn(brief=_brief(), leg=_leg())
    adapter = adapter or _Adapter()
    tenants = []

    @asynccontextmanager
    async def _acquire(pool, tenant_id, **_kw):
        tenants.append(tenant_id)
        yield conn

    container = SimpleNamespace(is_initialized=True, db_pool=object(), redis=None)
    with (
        patch.object(bridge, "_adapter", adapter),
        patch("app.core.db_utils.acquire_with_tenant", _acquire),
        patch("app.core.container.get_container", return_value=container),
        patch.object(
            bridge,
            "_enforce_inbound_transfer_policy",
            return_value=attempt or _outbound_attempt(),
        ) as policy,
    ):
        async def _policy(*a, **k):
            return attempt or _outbound_attempt()

        policy.side_effect = _policy
        result = await bridge._execute_transfer(CALL, destination, mode, **kwargs)
    return result, conn, adapter, tenants


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["1001", "sales_queue"])
async def test_extension_or_named_destination_is_called_as_today(destination):
    result, conn, adapter, tenants = await _run(destination=destination)
    assert adapter.calls == [((CALL, destination, "blind"), {})]
    assert conn.reads == [] and tenants == []
    assert result == {"status": "completed"}


@pytest.mark.asyncio
async def test_e164_on_brief_passes_recorded_trunk_ring_seconds_and_own_number():
    result, conn, adapter, tenants = await _run(ring_seconds=25, if_no_answer="return_to_agent")
    assert adapter.calls == [
        (
            (CALL, TARGET, "blind"),
            {"trunk_endpoint": TRUNK, "ring_seconds": 25, "caller_id": OWN_NUMBER},
        )
    ]
    assert result == {"status": "completed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("leg", [None, _leg(endpoint=None), _leg(endpoint="  ")])
async def test_missing_trunk_passes_no_trunk_and_no_default(leg):
    conn = _Conn(brief=_brief(), leg=leg)
    _result, _conn, adapter, _t = await _run(conn=conn, ring_seconds=20)
    (_args, kwargs), = adapter.calls
    assert not kwargs.get("trunk_endpoint")
    assert kwargs["ring_seconds"] == 20


@pytest.mark.asyncio
async def test_caller_id_dropped_when_leg_number_is_not_e164():
    conn = _Conn(brief=_brief(), leg=_leg(from_number="1001"))
    _r, _c, adapter, _t = await _run(conn=conn)
    assert not adapter.calls[0][1].get("caller_id")


@pytest.mark.asyncio
async def test_destination_not_on_brief_is_refused_without_adapter():
    result, conn, adapter, _t = await _run(destination="+442079999999")
    assert result["status"] == "failed"
    assert result["error"] == "transfer_destination_not_allowed"
    assert result["caller_media_retained"] is True
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_legacy_single_destination_is_accepted():
    conn = _Conn(brief={"transfer_destination": TARGET}, leg=_leg())
    result, _c, adapter, _t = await _run(conn=conn)
    assert result == {"status": "completed"} and len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_outside_transfer_hours_is_refused_without_adapter():
    conn = _Conn(brief=_brief(transfer_hours=CLOSED_HOURS), leg=_leg())
    result, _c, adapter, _t = await _run(conn=conn)
    assert result["error"] == "transfer_closed" and result["status"] == "failed"
    assert result["caller_media_retained"] is True
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_inside_transfer_hours_is_allowed():
    conn = _Conn(brief=_brief(transfer_hours=OPEN_HOURS), leg=_leg())
    _r, _c, adapter, _t = await _run(conn=conn)
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conn",
    [
        _Conn(campaign=False, leg=_leg()),
        _Conn(call=False, brief=_brief(), leg=_leg()),
        _Conn(brief={}, leg=_leg()),
    ],
)
async def test_unreadable_campaign_or_call_is_refused_without_adapter(conn):
    result, _c, adapter, _t = await _run(conn=conn)
    assert result["status"] == "failed"
    assert result["error"] == "transfer_destination_not_allowed"
    assert result["caller_media_retained"] is True
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_reads_are_tenant_scoped():
    _r, conn, _a, tenants = await _run()
    assert tenants and tenants[-1] == TENANT
    assert len(conn.reads) == 3  # calls.campaign_id, campaign brief, leg
    for sql, args in conn.reads:
        assert "tenant_id = $2" in sql
        assert args[1] == TENANT
    assert conn.reads[0][0].startswith("SELECT campaign_id FROM calls")
    assert set(tenants) == {TENANT} and None not in tenants


@pytest.mark.asyncio
async def test_ids_come_from_attempt_and_no_bypass_lookup():
    other = "99999999-9999-9999-9999-999999999999"
    _r, conn, _a, tenants = await _run(
        attempt=_outbound_attempt(call_id=other, tenant_id=TENANT, talklee_call_id="TK-9")
    )
    assert not any("bypass_rls" in sql for sql in conn.executes)
    assert not any("external_call_uuid" in sql for sql, _ in conn.reads)
    assert conn.reads[0][1] == (other, TENANT)
    assert all(e[0] == other and e[1] == "TK-9" for e in conn.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["tenant_id", "call_id"])
async def test_attempt_without_ids_is_refused_without_adapter(missing):
    result, conn, adapter, tenants = await _run(attempt=_outbound_attempt(**{missing: None}))
    assert result["error"] == "transfer_destination_not_allowed"
    assert result["caller_media_retained"] is True
    assert adapter.calls == [] and conn.reads == [] and tenants == []


@pytest.mark.asyncio
async def test_failed_caller_retained_defaults_to_return_to_agent_applied():
    failed = {
        "status": "failed",
        "error": "no_answer",
        "caller_media_retained": True,
        "target_termination_confirmed": True,
    }
    for value in (None, "return_to_agent"):
        result, _c, adapter, _t = await _run(adapter=_Adapter(result=failed), if_no_answer=value)
        assert result["fallback_status"] == "applied"
        assert result["fallback_action"] == "return_to_agent"
        assert result["target_termination_confirmed"] is True


@pytest.mark.asyncio
async def test_failed_without_proof_is_not_reported_applied():
    failed = {"status": "failed", "caller_media_retained": True}
    result, *_ = await _run(adapter=_Adapter(result=failed))
    assert result["fallback_status"] == "unconfirmed"


def _forbid(name):
    async def _boom(*_a, **_k):
        raise AssertionError(f"{name} must not run for an outbound transfer")

    return _boom


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed,expected", [(True, "applied"), (False, "unconfirmed")])
async def test_failed_end_call_hangs_nothing_up(confirmed, expected):
    failed = {
        "status": "failed",
        "caller_media_retained": True,
        "target_termination_confirmed": confirmed,
        "call_id": CALL,
    }
    with (
        patch.object(bridge, "mark_termination_pending_and_load_context", _forbid("mark")),
        patch.object(bridge, "request_confirmed_hangup", _forbid("hangup")),
        patch.object(bridge, "finalize_proven_inbound_termination", _forbid("finalize")),
        patch.object(bridge, "_apply_inbound_transfer_failure_action", _forbid("inbound handler")),
    ):
        result, *_ = await _run(adapter=_Adapter(result=failed), if_no_answer="end_call")
    assert result["fallback_action"] == "end_call"
    assert result["fallback_status"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cleanup_pending", "unconfirmed", "termination_unconfirmed"])
async def test_cleanup_pending_is_returned_unchanged(status):
    pending = {"status": status, "caller_media_retained": True}
    result, *_ = await _run(adapter=_Adapter(result=pending), if_no_answer="end_call")
    assert result == pending


@pytest.mark.asyncio
async def test_completed_result_is_returned_unchanged():
    done = {"status": "completed", "target": "x"}
    result, *_ = await _run(adapter=_Adapter(result=done), if_no_answer="end_call")
    assert result == done


@pytest.mark.asyncio
async def test_audit_lines_written_for_attempt_and_outcome():
    _r, conn, *_ = await _run()
    assert [e[3] for e in conn.events] == ["transfer_requested", "transfer_connected"]
    assert all(e[0] == CALL and e[2] is None for e in conn.events)
    assert all(TARGET not in str(e[4]) for e in conn.events)
    assert '"destination_tail": "4567"' in conn.events[0][4]


@pytest.mark.asyncio
async def test_inbound_never_reaches_outbound_code():
    attempt = SimpleNamespace(
        inbound=True,
        is_replay=False,
        provider_leg_id="leg-1",
        destination=TARGET,
        leg_id="L1",
        call_id=CALL,
        tenant_id=TENANT,
        failure_action="return_to_agent",
    )

    async def _complete(*_a, **_k):
        return None

    with patch.object(bridge, "_complete_transfer_attempt", _complete):
        result, conn, adapter, tenants = await _run(
            attempt=attempt, ring_seconds=30, if_no_answer="end_call"
        )
    assert adapter.calls == [((CALL, TARGET, "blind"), {"provider_leg_id": "leg-1"})]
    assert conn.reads == [] and conn.events == [] and tenants == []


@pytest.mark.asyncio
async def test_real_authorize_returns_ids_for_outbound_row_and_stays_outbound():
    from tests.unit.test_inbound_transfer_controls import _run as authorize_run

    attempt, _conn = await authorize_run({"direction": "outbound", "route_snapshot": {}})
    assert attempt.inbound is False
    assert attempt.call_id == "11111111-1111-1111-1111-111111111111"
    assert attempt.tenant_id == "22222222-2222-2222-2222-222222222222"
    assert attempt.talklee_call_id == "IN-test-call"
    assert attempt.is_replay is False and attempt.leg_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["freeswitch", "twilio", "vonage", "sip"])
async def test_non_asterisk_adapter_is_called_as_before(name):
    result, conn, adapter, tenants = await _run(adapter=_Adapter(name=name), ring_seconds=20)
    assert adapter.calls == [((CALL, TARGET, "blind"), {})]
    assert conn.reads == [] and tenants == []

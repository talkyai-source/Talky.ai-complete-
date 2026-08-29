from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import calls, telephony_bridge
from app.core import container as container_module
from app.domain.services.telephony import inbound_admission
from app.domain.services.telephony.termination import TerminationContext
from app.domain.services.call_status import TERMINAL_CALL_STATUSES


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, **_kwargs):
        return _Acquire(self.conn)


def _stub_termination_context(monkeypatch, module, row, *, events=None):
    async def mark(_pool, *, call_reference, tenant_id=None, timeout_s=5.0):
        del tenant_id, timeout_s
        previous_status = str(row["status"] or "")
        if previous_status not in TERMINAL_CALL_STATUSES:
            row["status"] = "termination_pending"
            if events is not None:
                events.append("fence")
        return TerminationContext(
            call_id=str(call_reference),
            tenant_id=None,
            provider_call_id=(
                row.get("provider_call_id") or row.get("external_call_uuid")
            ),
            previous_status=previous_status,
            provider_leg_ids=(),
        )

    monkeypatch.setattr(module, "mark_termination_pending_and_load_context", mark)

    async def finalize(*_args, **_kwargs):
        if events is not None:
            events.append("settle")

    if hasattr(module, "finalize_proven_inbound_termination"):
        monkeypatch.setattr(module, "finalize_proven_inbound_termination", finalize)


class _CallConn:
    def __init__(self, row, events=None):
        self.row = row
        self.events = events if events is not None else []
        self.queries: list[str] = []

    async def fetchrow(self, query, *_args):
        self.queries.append(query)
        if "SELECT external_call_uuid" in query:
            return self.row
        if "UPDATE calls" in query:
            self.events.append("update")
            self.row["status"] = "ended"
            return {"status": "ended"}
        raise AssertionError(f"Unexpected query: {query}")

    async def fetchval(self, query, *_args):
        self.queries.append(query)
        if "SELECT status FROM calls" in query:
            return self.row["status"]
        raise AssertionError(f"Unexpected query: {query}")


def _user(tenant_id) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="tenant-operator@example.com",
        tenant_id=str(tenant_id),
        role="admin",
    )


@pytest.mark.asyncio
async def test_tenant_unconfirmed_hangup_performs_no_terminal_write(monkeypatch):
    tenant_id = uuid4()
    call_id = uuid4()
    row = {
        "external_call_uuid": "pbx-unconfirmed",
        "provider_call_id": None,
        "provider": "asterisk",
        "direction": "outbound",
        "status": "in_call",
        "started_at": datetime.now(timezone.utc),
        "answered_at": datetime.now(timezone.utc),
    }
    conn = _CallConn(row)

    class Adapter:
        async def hangup_confirmed(self, _call_id):
            return False

    monkeypatch.setattr(telephony_bridge, "_adapter", Adapter())
    _stub_termination_context(monkeypatch, calls, row)

    with pytest.raises(HTTPException) as exc_info:
        await calls.hangup_live_call(
            call_id=str(call_id),
            current_user=_user(tenant_id),
            db_client=SimpleNamespace(pool=_Pool(conn)),
        )

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail["error"] == "termination_unconfirmed"
    assert exc_info.value.detail["termination_status"] == "requested"
    assert exc_info.value.detail["call_status"] == "termination_pending"
    assert row["status"] == "termination_pending"
    assert not any("UPDATE calls" in query for query in conn.queries)


@pytest.mark.asyncio
async def test_tenant_confirmed_hangup_orders_proof_before_settlement_and_projection(
    monkeypatch,
):
    tenant_id = uuid4()
    call_id = uuid4()
    events: list[str] = []
    row = {
        "external_call_uuid": "pbx-confirmed",
        "provider_call_id": "pbx-confirmed",
        "provider": "asterisk",
        "direction": "inbound",
        "status": "in_call",
        "started_at": datetime.now(timezone.utc),
        "answered_at": datetime.now(timezone.utc),
    }
    conn = _CallConn(row, events)

    class Adapter:
        async def hangup_confirmed(self, _call_id):
            events.append("proof")
            return True

    class AdmissionService:
        def __init__(self, _pool):
            pass

        async def finalize(self, _request):
            events.append("settle")

        async def release(self, **_kwargs):
            raise AssertionError("answered calls must finalize")

    monkeypatch.setattr(telephony_bridge, "_adapter", Adapter())
    monkeypatch.setattr(inbound_admission, "InboundAdmissionService", AdmissionService)
    _stub_termination_context(monkeypatch, calls, row, events=events)

    result = await calls.hangup_live_call(
        call_id=str(call_id),
        current_user=_user(tenant_id),
        db_client=SimpleNamespace(pool=_Pool(conn)),
    )

    assert events == ["fence", "proof", "settle", "update"]
    assert result["status"] == "confirmed"
    assert result["provider_hangup_confirmed"] is True
    assert result["call_status"] == "ended"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["ended", "cancelled", "rejected"])
async def test_tenant_terminal_replay_is_idempotent_after_provider_absence_proof(
    monkeypatch,
    terminal_status,
):
    tenant_id = uuid4()
    call_id = uuid4()
    row = {
        "external_call_uuid": "pbx-already-ended",
        "provider_call_id": "pbx-already-ended",
        "provider": "asterisk",
        "direction": "inbound",
        "status": terminal_status,
        "started_at": datetime.now(timezone.utc),
        "answered_at": datetime.now(timezone.utc),
    }
    conn = _CallConn(row)

    requested: list[str] = []

    class Adapter:
        async def hangup_confirmed(self, provider_call_id):
            requested.append(provider_call_id)
            return True

    monkeypatch.setattr(telephony_bridge, "_adapter", Adapter())
    _stub_termination_context(monkeypatch, calls, row)

    result = await calls.hangup_live_call(
        call_id=str(call_id),
        current_user=_user(tenant_id),
        db_client=SimpleNamespace(pool=_Pool(conn)),
    )

    assert requested == ["pbx-already-ended"]
    assert result["status"] == "already_terminal"
    assert result["call_status"] == terminal_status
    assert result["provider_hangup_requested"] is True
    assert result["provider_hangup_confirmed"] is True


@pytest.mark.asyncio
async def test_tenant_terminal_replay_does_not_treat_database_state_as_pbx_proof(
    monkeypatch,
):
    tenant_id = uuid4()
    call_id = uuid4()
    row = {
        "external_call_uuid": "pbx-terminal-but-live",
        "provider_call_id": "pbx-terminal-but-live",
        "provider": "asterisk",
        "direction": "inbound",
        "status": "ended",
        "started_at": datetime.now(timezone.utc),
        "answered_at": datetime.now(timezone.utc),
    }
    conn = _CallConn(row)

    class Adapter:
        async def hangup_confirmed(self, _call_id):
            return False

    monkeypatch.setattr(telephony_bridge, "_adapter", Adapter())
    _stub_termination_context(monkeypatch, calls, row)

    with pytest.raises(HTTPException) as exc_info:
        await calls.hangup_live_call(
            call_id=str(call_id),
            current_user=_user(tenant_id),
            db_client=SimpleNamespace(pool=_Pool(conn)),
        )

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail["error"] == "termination_unconfirmed"
    assert exc_info.value.detail["call_status"] == "ended"
    assert not any("UPDATE calls" in query for query in conn.queries)


@pytest.mark.asyncio
async def test_raw_hangup_reports_only_confirmed_provider_absence(monkeypatch):
    async def authorize(_request):
        return SimpleNamespace(tenant_id="tenant-1", is_internal=False)

    async def verify(_ctx, _call_id):
        return None

    class Adapter:
        async def hangup_confirmed(self, call_id):
            assert call_id == "pbx-raw"
            return True

    monkeypatch.setattr(telephony_bridge, "_require_call_control", authorize)
    monkeypatch.setattr(telephony_bridge, "_verify_call_ownership", verify)
    monkeypatch.setattr(telephony_bridge, "_adapter", Adapter())
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object()),
    )
    _stub_termination_context(
        monkeypatch,
        telephony_bridge,
        {
            "status": "in_call",
            "provider_call_id": "pbx-raw",
            "external_call_uuid": "pbx-raw",
        },
    )

    response = await telephony_bridge.hangup_call("pbx-raw", SimpleNamespace())
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["status"] == "confirmed"
    assert body["provider_hangup_confirmed"] is True


@pytest.mark.asyncio
async def test_raw_hangup_returns_gateway_error_when_proof_is_missing(monkeypatch):
    async def authorize(_request):
        return SimpleNamespace(tenant_id="tenant-1", is_internal=False)

    async def verify(_ctx, _call_id):
        return None

    class Adapter:
        async def hangup_confirmed(self, _call_id):
            return False

    monkeypatch.setattr(telephony_bridge, "_require_call_control", authorize)
    monkeypatch.setattr(telephony_bridge, "_verify_call_ownership", verify)
    monkeypatch.setattr(telephony_bridge, "_adapter", Adapter())
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object()),
    )
    _stub_termination_context(
        monkeypatch,
        telephony_bridge,
        {
            "status": "in_call",
            "provider_call_id": "pbx-raw",
            "external_call_uuid": "pbx-raw",
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await telephony_bridge.hangup_call("pbx-raw", SimpleNamespace())

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail["error"] == "termination_unconfirmed"
    assert exc_info.value.detail["termination_status"] == "requested"


class _Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return None


class _CampaignConn:
    def __init__(self):
        self.fenced: list[str] = []

    def transaction(self):
        return _Transaction()

    async def execute(self, query, *args):
        if "app.bypass_rls" in query:
            return None
        assert "status='termination_pending'" in query
        self.fenced = list(args[0])

    async def fetch(self, query, *_args):
        if "FROM calls c" in query:
            assert "COALESCE(c.provider_call_id, c.external_call_uuid)" in query
            assert "FOR UPDATE OF c" in query
            return [
                {"durable_call_id": "call-good", "provider_call_id": "pbx-good"},
                {
                    "durable_call_id": "call-still-live",
                    "provider_call_id": "pbx-still-live",
                },
            ]
        assert "FROM call_legs" in query
        return [
            {
                "durable_call_id": "call-good",
                "provider_leg_id": "talky-xfer-good",
            }
        ]


@pytest.mark.asyncio
async def test_campaign_bulk_hangup_separates_attempts_from_confirmations(monkeypatch):
    class Adapter:
        async def hangup_confirmed(self, call_id):
            return call_id != "pbx-still-live"

        async def hangup_many_confirmed(self, call_ids):
            return tuple(call_ids) == ("pbx-good", "talky-xfer-good")

    conn = _CampaignConn()
    pool = _Pool(conn)
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=pool),
    )
    monkeypatch.setattr(telephony_bridge, "_adapter", Adapter())

    result = await telephony_bridge.hangup_calls_for_campaign(str(uuid4()))

    assert result == {
        "status": "partial",
        "total_selected": 2,
        "requested": 2,
        "attempted": 2,
        "confirmed": 1,
        "deferred": 1,
        "unconfirmed": 1,
        "missing_identity": 0,
        "reasons": {"hangup_unconfirmed": 1},
        "lookup_error": None,
    }
    assert conn.fenced == ["call-good", "call-still-live"]


@pytest.mark.asyncio
async def test_campaign_bulk_lookup_failure_is_not_zero_success(monkeypatch):
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=None),
    )

    result = await telephony_bridge.hangup_calls_for_campaign(str(uuid4()))

    assert result["status"] == "lookup_failed"
    assert result["lookup_error"] == "database_unavailable"
    assert result["total_selected"] == 0

"""Bridge origination: a disconnected adapter is retryable, and the line is recorded.

1. ``make_call`` answered a disconnected adapter with a bare
   ``HTTPException(400, "Telephony adapter not connected")``. The dialer's
   ``parse_bridge_error`` found no code in that body, classified it INTERNAL and
   failed the lead after one retry. It now returns 503 with the coded detail
   ``telephony_adapter_unavailable`` (same shape as ``telephony_not_active_on_node``).

2. ``calls.route_snapshot`` is ``'{}'`` on every outbound call and nothing stored
   the trunk. After a successful originate the bridge now merges
   ``{trunk_id, endpoint, route_reason}`` into the outbound ``pstn_outbound``
   leg's ``metadata`` (best effort, tenant-scoped).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints import telephony_bridge
from app.workers import retry_policy as rp

TENANT_ID = "11111111-1111-4111-8111-111111111111"
CAMPAIGN_ID = "33333333-3333-4333-8333-333333333333"
VOICE_CALL_ID = "66666666-6666-4666-8666-666666666666"
SERVICE_HEADER_VALUE = "bridge-check-1"


def _internal_request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "server": ("api.talky.test", 443),
            "path": "/api/v1/sip/telephony/call",
            "raw_path": b"/api/v1/sip/telephony/call",
            "query_string": b"",
            "headers": [(b"x-internal-service-token", SERVICE_HEADER_VALUE.encode())],
            "client": ("127.0.0.1", 1),
            "state": {},
        }
    )


def _body() -> telephony_bridge.MakeCallRequest:
    return telephony_bridge.MakeCallRequest(
        destination="+15551234567",
        caller_id="+15557654321",
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        durable_call_id=VOICE_CALL_ID,
        talklee_call_id="TKY-W4",
        dialer_job_id="88888888-8888-4888-8888-888888888888",
        dialer_attempt_number=1,
    )


def _install(monkeypatch, *, owner: bool, adapter):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SERVICE_HEADER_VALUE)
    monkeypatch.delenv("TELEPHONY_ENABLED", raising=False)
    monkeypatch.setattr(
        telephony_bridge, "_load_agent_lead_context", AsyncMock(return_value=None)
    )
    state = SimpleNamespace(
        is_telephony_owner=lambda: owner,
        telephony_owner_id=AsyncMock(return_value="other-pod"),
    )
    monkeypatch.setattr(telephony_bridge, "get_state_backend", lambda: state)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter",
    [None, SimpleNamespace(connected=False, name="asterisk")],
    ids=["no_adapter", "adapter_disconnected"],
)
async def test_disconnected_adapter_is_a_coded_retryable_503(monkeypatch, adapter):
    _install(monkeypatch, owner=True, adapter=adapter)

    with pytest.raises(HTTPException) as caught:
        await telephony_bridge.make_call(_internal_request(), _body())

    exc = caught.value
    assert exc.status_code == 503, exc.detail  # was a bare 400 (permanent for the dialer)
    assert exc.detail["error"] == "telephony_adapter_unavailable"
    assert exc.headers == {"Retry-After": "2"}
    # The dialer reads the code from the FastAPI body shape {"detail": {...}}.
    code, _message = rp.parse_bridge_error(json.dumps({"detail": exc.detail}))
    assert code == "telephony_adapter_unavailable"


@pytest.mark.asyncio
async def test_non_owner_still_reports_not_active_on_node_before_the_adapter_check(
    monkeypatch,
):
    _install(monkeypatch, owner=False, adapter=None)

    with pytest.raises(HTTPException) as caught:
        await telephony_bridge.make_call(_internal_request(), _body())

    assert caught.value.status_code == 503
    assert caught.value.detail["error"] == "telephony_not_active_on_node"


# ── the line used is recorded on the outbound leg ──────────────────────


class _LegConn:
    """Stands in for the tenant connection; records call_legs updates."""

    def __init__(self, *, fail_legs: bool = False):
        self.fail_legs = fail_legs
        self.leg_updates: list[tuple[str, tuple]] = []
        self.calls_bound = False

    def transaction(self):
        class _Tx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return _Tx()

    async def execute(self, query, *args):
        normalized = " ".join(query.split())
        lowered = normalized.lower()
        if lowered.startswith("set local") or lowered.startswith("select set_config"):
            return "SELECT 1"
        if lowered.startswith("update call_legs"):
            if self.fail_legs:
                raise RuntimeError("synthetic database hiccup")
            self.leg_updates.append((normalized, args))
            return "UPDATE 1"
        if lowered.startswith("update calls"):
            self.calls_bound = True
            return "UPDATE 1"
        raise AssertionError(f"Unexpected execute: {normalized}")


def _use_conn(monkeypatch, conn) -> None:
    from app.core import db_utils

    @asynccontextmanager
    async def acquire(_pool, _tenant, **_kwargs):
        yield conn

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)


ROUTE_META = {
    "trunk_id": "bb717019-0000-4000-8000-000000000000",
    "endpoint": "trunk-bb717019-0000-4000-8000-000000000000",
    "route_reason": "own_trunk",
}


@pytest.mark.asyncio
async def test_line_used_is_merged_into_the_outbound_leg_metadata(monkeypatch):
    conn = _LegConn()
    _use_conn(monkeypatch, conn)

    await telephony_bridge._bind_durable_outbound_call_provider(
        object(),
        tenant_id=TENANT_ID,
        durable_call_id=VOICE_CALL_ID,
        provider="asterisk",
        provider_call_id="talky-out-planned",
        from_number="+442046132300",
        route_meta=ROUTE_META,
    )

    assert conn.calls_bound is True
    route_updates = [
        (sql, args) for sql, args in conn.leg_updates if "metadata" in sql.lower()
    ]
    assert len(route_updates) == 1
    sql, args = route_updates[0]
    lowered = sql.lower()
    # jsonb merge, not overwrite; only the outbound PSTN leg; tenant-scoped.
    assert "|| $3::jsonb" in lowered
    assert "l.leg_type = 'pstn_outbound'" in lowered
    assert "l.direction = 'outbound'" in lowered
    assert "c.tenant_id = $2::uuid" in lowered
    assert args[0] == VOICE_CALL_ID
    assert args[1] == TENANT_ID
    assert json.loads(args[2]) == ROUTE_META


@pytest.mark.asyncio
async def test_recording_the_line_never_fails_a_ringing_call(monkeypatch):
    conn = _LegConn(fail_legs=True)
    _use_conn(monkeypatch, conn)

    await telephony_bridge._bind_durable_outbound_call_provider(
        object(),
        tenant_id=TENANT_ID,
        durable_call_id=VOICE_CALL_ID,
        provider="asterisk",
        provider_call_id="talky-out-planned",
        from_number="+442046132300",
        route_meta=ROUTE_META,
    )

    assert conn.calls_bound is True  # the bind itself still committed


@pytest.mark.asyncio
async def test_no_route_means_no_extra_leg_update(monkeypatch):
    """Non-Asterisk adapters resolve no route: only the from_number write runs."""
    conn = _LegConn()
    _use_conn(monkeypatch, conn)

    await telephony_bridge._bind_durable_outbound_call_provider(
        object(),
        tenant_id=TENANT_ID,
        durable_call_id=VOICE_CALL_ID,
        provider="twilio",
        provider_call_id="CA123",
        from_number="+15557654321",
    )

    assert [sql for sql, _ in conn.leg_updates if "metadata" in sql.lower()] == []
    assert len(conn.leg_updates) == 1  # the from_number write

"""Settings -> Telephony shows the number outbound calls really go out from.

Owner report 2026-10-14: the backend dialled from one number while the
Telephony page kept showing another. The page showed each trunk's stored
caller ID; the dialer picks the line and number by its own rules, falling back
to the tenant's calling-rules number when the line sets none. The page now asks
the same read-only preflight that campaign launch and redial use.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints.telephony_sip import trunks
from app.domain.services.telephony import caller_id_guard, outbound_readiness, trunk_resolver
from app.domain.services.telephony.trunk_resolver import OutboundTrunkRoute

TENANT_ID = "790ca2db-6696-4fe9-9a2c-cd690c414a1e"
USER = CurrentUser(id="ab2de8d4-86b2-425e-a9ed-68a83e5d5f9c", email="owner@example.test",
                   tenant_id=TENANT_ID, role="tenant_admin")


def _request():
    return Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
                    "server": ("api.talky.test", 443), "path": "/api/v1/telephony/sip/trunks/outbound-route",
                    "raw_path": b"/api/v1/telephony/sip/trunks/outbound-route", "query_string": b"",
                    "headers": [], "client": ("127.0.0.1", 1)})


class _Conn:
    def __init__(self, calling_rules, trunk_names):
        self.calling_rules, self.trunk_names, self.sql = calling_rules, trunk_names, []

    async def fetchval(self, sql, *args):
        self.sql.append((sql, args))
        if "FROM tenants" in sql:
            return self.calling_rules
        assert args[1] == TENANT_ID  # the trunk name is read inside the tenant
        return self.trunk_names.get(args[0])


def _install(monkeypatch, route, *, calling_rules, trunk_names=None, caller_id_allowed=True):
    conn = _Conn(calling_rules, trunk_names or {})

    @asynccontextmanager
    async def acquire(_pool, tenant_id, **_kwargs):
        assert str(tenant_id) == TENANT_ID
        yield conn

    async def selection(*_args, **_kwargs):
        return None

    async def requires_sip(*_args, **_kwargs):
        return True

    async def resolve(_pool, *, tenant_id, environment, campaign_id=None):
        assert tenant_id == TENANT_ID and campaign_id is None
        return route

    async def ownership(_pool, *, tenant_id, caller_id, environment):
        return type("Decision", (), {"allowed": caller_id_allowed, "require_attestation": False})()

    monkeypatch.setattr(trunks, "acquire_with_tenant", acquire)
    monkeypatch.setattr(outbound_readiness, "require_production_outbound_selection", selection)
    monkeypatch.setattr(trunk_resolver, "requires_sip_readiness", requires_sip)
    monkeypatch.setattr(trunk_resolver, "resolve_outbound_trunk", resolve)
    monkeypatch.setattr(caller_id_guard, "check_caller_id_ownership", ownership)
    return conn


@pytest.mark.asyncio
async def test_a_line_without_its_own_caller_id_shows_the_calling_rules_number(monkeypatch):
    # The trunk card had no caller ID; the dialer used the tenant's number.
    _install(monkeypatch, OutboundTrunkRoute("blazedigitel-endpoint", None, "t-primary", True, "platform_default"),
             calling_rules={"caller_id": "+17789249977"}, trunk_names={"t-primary": "blaze-primary"})
    result = await trunks.get_outbound_route(_request(), USER, object())
    assert result.ready is True and result.reason_code == "ready"
    assert result.caller_id == "+17789249977"
    assert result.trunk_id == "t-primary" and result.trunk_name == "blaze-primary"


@pytest.mark.asyncio
async def test_a_line_with_a_verified_caller_id_shows_that_number_not_the_calling_rules_one(monkeypatch):
    # The UK tenant's calling rules say +44 20 4613 2300, but its active line
    # presents +1 778 924 9977; the page must show what is dialled.
    _install(monkeypatch, OutboundTrunkRoute("trunk-9406fd7f", "+17789249977", "9406fd7f", False, "own_trunk"),
             calling_rules={"caller_id": "+442046132300"}, trunk_names={"9406fd7f": "17789249977"})
    result = await trunks.get_outbound_route(_request(), USER, object())
    assert result.ready is True
    assert result.caller_id == "+17789249977" and result.trunk_name == "17789249977"


@pytest.mark.asyncio
async def test_a_refused_route_says_calls_cannot_go_out_and_why(monkeypatch):
    _install(monkeypatch, OutboundTrunkRoute(None, None, "9406fd7f", False, "selected_trunk_not_ready", True),
             calling_rules={"caller_id": "+442046132300"}, trunk_names={"9406fd7f": "17789249977"})
    result = await trunks.get_outbound_route(_request(), USER, object())
    assert result.ready is False and result.reason_code == "selected_trunk_not_ready"
    assert "not ready" in (result.reason or "")


@pytest.mark.asyncio
async def test_an_unverified_number_is_shown_as_not_ready(monkeypatch):
    _install(monkeypatch, OutboundTrunkRoute("blazedigitel-endpoint", None, None, True, "platform_default"),
             calling_rules={"caller_id": "+15550000000"}, caller_id_allowed=False)
    result = await trunks.get_outbound_route(_request(), USER, object())
    assert result.ready is False and result.reason_code == "caller_id_not_verified"
    assert result.caller_id == "+15550000000" and result.trunk_name is None


@pytest.mark.asyncio
async def test_a_failed_check_is_a_retryable_problem_not_a_crash(monkeypatch):
    _install(monkeypatch, None, calling_rules={})

    async def broken(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(trunk_resolver, "resolve_outbound_trunk", broken)
    response = await trunks.get_outbound_route(_request(), USER, object())
    assert response.status_code == 503

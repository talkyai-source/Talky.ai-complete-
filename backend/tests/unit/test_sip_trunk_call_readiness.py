from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints.telephony_sip import trunks
from app.api.v1.endpoints.telephony_sip.schemas import SIPTrunkCreateRequest
from app.domain.services.telephony.trunk_resolver import DidRow, TrunkRow, choose_outbound_route
from app.domain.services.telephony.trunk_runtime import evaluate_trunk_runtime


@pytest.mark.parametrize("status,ready", [("loaded", False), ("reachable", True), ("unreachable", False), ("unknown", False)])
def test_ip_auth_outbound_requires_live_qualified_contact(status, ready):
    row = dict(is_active=True, direction="both", metadata={"register": False},
               live_registration_status=status, live_status_checked_at=datetime.now(timezone.utc))
    assert evaluate_trunk_runtime(row, require_inbound=False).ready is ready
    row["live_status_checked_at"] -= timedelta(minutes=5)
    assert evaluate_trunk_runtime(row, require_inbound=False).ready is False


def test_down_own_trunk_never_falls_back_to_the_shared_platform_default():
    # The tenant's own line is down: refuse, never silently dial the shared
    # platform line instead (an explicit campaign/pool assignment is likewise
    # never replaced -- see test_trunk_resolver).
    now = datetime.now(timezone.utc)
    route = choose_outbound_route(
        active_trunks=[TrunkRow("platform", "platform-default", True, now - timedelta(minutes=1)),
                       TrunkRow("selected", "selected", True, now, runtime_ready=False, created_at=now)],
        dialable_numbers=[DidRow("+14165550123", "verified")],
        env_default_endpoint="platform", platform_default_trunk_name="platform-default",
        is_production=True, shared_default_enabled=True,
    )
    assert route.refused and route.trunk_id == "selected" and route.endpoint is None
    assert route.reason == "selected_trunk_not_ready"


def test_ready_own_trunk_is_used_when_a_newer_own_trunk_is_down():
    # This test used to assert the opposite (the newer, down own trunk was
    # "selected" by updated_at and refused the call although an older own trunk
    # was registered). A not-ready or inbound-only line must not shadow a ready
    # one: readiness is part of the selection, created_at breaks ties.
    now = datetime.now(timezone.utc)
    route = choose_outbound_route(
        active_trunks=[TrunkRow("older", "other", True, now - timedelta(minutes=1),
                                created_at=now - timedelta(days=2)),
                       TrunkRow("selected", "selected", True, now, runtime_ready=False,
                                created_at=now - timedelta(days=1))],
        dialable_numbers=[DidRow("+14165550123", "verified")],
        env_default_endpoint="platform", platform_default_trunk_name="platform-default",
        is_production=True, shared_default_enabled=True,
    )
    assert route.refused is False
    assert route.trunk_id == "older" and route.endpoint == "trunk-older"


def test_ip_auth_needs_no_credentials_or_registration_configuration():
    request = SIPTrunkCreateRequest(trunk_name="IP carrier", sip_domain="sip.example.com")
    assert request.auth_username is None and not request.metadata.get("register")
    with pytest.raises(ValueError, match="REGISTER requires credentials"):
        SIPTrunkCreateRequest(trunk_name="Bad registration", sip_domain="sip.example.com", metadata={"register": True})


@pytest.mark.parametrize("direction,status,inbound", [("both", "loaded", True), ("inbound", "loaded", True), ("outbound", "loaded", False), ("both", "missing_config", False)])
def test_trunk_api_projects_inbound_and_outbound_readiness_separately(direction, status, inbound):
    now = datetime.now(timezone.utc)
    row = {"id": uuid4(), "tenant_id": uuid4(), "trunk_name": "IP carrier",
           "sip_domain": "sip.example.com", "port": 5060, "transport": "udp",
           "direction": direction, "is_active": True, "auth_username": None,
           "auth_password_encrypted": None, "metadata": {"register": False},
           "live_registration_status": status, "live_status_checked_at": now,
           "created_at": now, "updated_at": now}
    response = trunks._row_to_response(row)
    assert response.inbound_runtime_ready is inbound
    assert response.runtime_ready is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case,status", [
    ("unused", 200), ("active", 200), ("history", 200),
    ("foreign", 404), ("shared", 409), ("cleanup_failed", 503),
])
async def test_delete_is_tenant_scoped_and_never_changes_what_it_cannot_finish(monkeypatch, case, status):
    # Current contract (owner report 2026-10-14): an active line, pins and inbound
    # history no longer block deletion; a foreign line is a 404, a shared line another
    # account uses is a 409, and a failed file cleanup changes nothing (503).
    # The full matrix is in test_trunk_delete_flow.py and, on real PostgreSQL, in
    # tests/integration/test_op04_trunk_lifecycle.py.
    from app.infrastructure.telephony import pjsip_config_generator

    tenant, tid = str(uuid4()), uuid4()
    commands = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield self

        async def fetchrow(self, query, *args):
            assert "tenant_id=$1 AND id=$2" in query and "deleted_at IS NULL" in query and "FOR UPDATE" in query
            assert args == (tenant, tid)
            return None if case == "foreign" else {
                "id": tid, "is_active": case == "active", "trunk_name": "my-trunk", "auth_password_encrypted": None,
            }

        async def fetchval(self, query, *args):
            if "pool_trunk" in query:
                return case == "shared"
            return case == "history" if "inbound_did_assignments" in query else False

        async def fetch(self, query, *args):
            commands.append((query, args))
            return []

        async def execute(self, query, *args):
            commands.append((" ".join(query.split()), args))

    @asynccontextmanager
    async def acquire(*_args, **kwargs):
        yield Conn()

    cleanup = AsyncMock(side_effect=RuntimeError("reload unavailable") if case == "cleanup_failed" else None)
    monkeypatch.setattr(trunks, "acquire_with_tenant", acquire)
    monkeypatch.setattr(trunks, "_claim_idempotency", AsyncMock(return_value=("new", None, None)))
    monkeypatch.setattr(trunks, "_store_idempotency_result", AsyncMock())
    monkeypatch.setattr(trunks, "_enforce_ws_i_quota", AsyncMock(return_value=None))
    monkeypatch.setattr(trunks, "_store_error_idempotency_result", AsyncMock())
    monkeypatch.setattr(pjsip_config_generator, "remove_trunk_config", cleanup)
    request = Request({"type": "http", "method": "DELETE", "path": f"/trunks/{tid}", "headers": []})
    try:
        result = await trunks.delete_sip_trunk(tid, request, "delete-key", SimpleNamespace(tenant_id=tenant, id=str(uuid4())), object())
        actual = getattr(result, "status_code", 200)
    except HTTPException as exc:
        actual = exc.status_code
    assert actual == status
    deletes = [args for query, args in commands if query.startswith("DELETE FROM tenant_sip_trunks")]
    retired = [args for query, args in commands if query.startswith("UPDATE tenant_sip_trunks")]
    assert deletes == ([(tenant, tid)] if case in {"unused", "active"} else [])
    assert len(retired) == (1 if case == "history" else 0)
    if case in {"foreign", "shared", "cleanup_failed"}:
        assert not any(q.startswith(("UPDATE", "DELETE")) for q, _ in commands), "nothing may change"
    assert cleanup.await_count == (1 if case in {"unused", "active", "history", "cleanup_failed"} else 0)


@pytest.mark.asyncio
async def test_campaign_start_blocks_before_mutations_when_trunk_down(monkeypatch):
    from app.domain.services.campaign_service import CampaignError, CampaignService
    from app.domain.services.telephony import trunk_resolver

    monkeypatch.setenv("TELEPHONY_ADAPTER", "asterisk")
    service = CampaignService(SimpleNamespace(pool=None))
    service.get_campaign = AsyncMock(return_value={"direction": "outbound", "status": "draft", "tenant_id": "tenant"})
    service._get_pending_leads = AsyncMock()
    service._update_campaign_status = AsyncMock()
    monkeypatch.setattr(trunk_resolver, "resolve_outbound_trunk", AsyncMock(return_value=SimpleNamespace(refused=True, reason="selected_trunk_not_ready", trunk_id=None, caller_id=None)))
    # The machine code stays on the exception; the message is plain language
    # (it no longer echoes the raw code to the user).
    with pytest.raises(CampaignError, match="not registered with the carrier") as exc:
        await service.start_campaign("campaign", tenant_id="tenant")
    assert exc.value.reason_code == "selected_trunk_not_ready"
    assert exc.value.status_code == 409
    service._get_pending_leads.assert_not_awaited()
    service._update_campaign_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,has_trunks,assigned,expected", [
    ("sip", False, False, True),
    ("none", True, False, True),
    ("none", False, True, True),
    ("none", False, False, False),
    ("twilio", True, True, False),
    ("vonage", True, True, False),
])
async def test_auto_start_uses_selected_inventory_and_respects_cloud_provider(monkeypatch, provider, has_trunks, assigned, expected):
    from app.core import db_utils
    from app.domain.services.telephony.trunk_resolver import requires_sip_readiness

    monkeypatch.setenv("TELEPHONY_ADAPTER", "auto")
    monkeypatch.delenv("TELEPHONY_PROVIDER", raising=False)
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={
        "active_telephony_provider": provider, "has_trunks": has_trunks, "pool_trunk": None,
    }))

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    campaign = {"calling_config": {"trunk": {"id": "selected"}} if assigned else {}}
    assert await requires_sip_readiness(object(), tenant_id=str(uuid4()), campaign=campaign) is expected


@pytest.mark.asyncio
async def test_assignment_projection_reads_current_trunk_not_cached_number(monkeypatch):
    conn = SimpleNamespace(execute=AsyncMock(), fetchrow=AsyncMock(return_value={"trunk_name": "Current", "caller_id": "+14165550123"}))

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(trunks, "acquire_with_tenant", acquire)
    tenant, tid = str(uuid4()), str(uuid4())
    result = await trunks._current_assignment_projection(object(), tenant, tid)
    assert result["caller_id"] == "+14165550123"
    assert conn.fetchrow.await_args.args[1:] == (tid, tenant)


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant_provider,expected", [("sip", True), ("none", False)])
@pytest.mark.parametrize("cloud", ["twilio", "vonage"])
@pytest.mark.parametrize("source", ["environment", "config"])
async def test_tenant_sip_choice_precedes_cloud_platform_default(monkeypatch, tenant_provider, expected, cloud, source):
    from app.core import db_utils
    from app.domain.services.telephony.trunk_resolver import requires_sip_readiness
    from app.infrastructure.telephony.provider_factory import TelephonyProviderFactory

    monkeypatch.setenv("TELEPHONY_ADAPTER", "auto")
    monkeypatch.delenv("TELEPHONY_PROVIDER", raising=False)
    monkeypatch.setattr(TelephonyProviderFactory, "_read_config_active", lambda: cloud if source == "config" else "sip")
    if source == "environment":
        monkeypatch.setenv("TELEPHONY_PROVIDER", cloud)
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={
        "active_telephony_provider": tenant_provider,
        "has_trunks": True,
        "pool_trunk": None,
    }))

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    assert await requires_sip_readiness(object(), tenant_id=str(uuid4()), campaign={}) is expected
    conn.fetchrow.assert_awaited_once()

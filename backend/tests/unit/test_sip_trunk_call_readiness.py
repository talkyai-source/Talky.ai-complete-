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


def test_selected_down_trunk_never_falls_back_to_other_trunk():
    now = datetime.now(timezone.utc)
    route = choose_outbound_route(
        active_trunks=[TrunkRow("older", "other", True, now - timedelta(minutes=1)),
                       TrunkRow("selected", "selected", True, now, runtime_ready=False)],
        dialable_numbers=[DidRow("+14165550123", "verified")],
        env_default_endpoint="platform", platform_default_trunk_name="platform-default",
        is_production=True, shared_default_enabled=True,
    )
    assert route.refused and route.trunk_id == "selected" and route.endpoint is None


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
@pytest.mark.parametrize("case,status", [("unused", 200), ("active", 409), ("assigned", 409), ("foreign", 404), ("cleanup_failed", 503)])
async def test_delete_is_tenant_scoped_and_preserves_used_or_unclean_trunk(monkeypatch, case, status):
    from app.infrastructure.telephony import pjsip_config_generator

    tenant, tid = str(uuid4()), uuid4()
    commands = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield self

        async def fetchrow(self, query, *args):
            assert "tenant_id=$1 AND id=$2 FOR UPDATE" in query
            assert args == (tenant, tid)
            return None if case == "foreign" else {"id": tid, "is_active": case == "active", "trunk_name": "my-trunk"}

        async def fetchval(self, query, *args):
            assert "inbound_did_assignments" in query and "calling_config" in query
            return case == "assigned"

        async def execute(self, query, *args):
            commands.append((query, args))

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
    deletes = [args for query, args in commands if query.startswith("DELETE")]
    assert deletes == ([(tenant, tid)] if case == "unused" else [])
    assert cleanup.await_count == (1 if case in {"unused", "cleanup_failed"} else 0)


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
    with pytest.raises(CampaignError, match="selected_trunk_not_ready") as exc:
        await service.start_campaign("campaign", tenant_id="tenant")
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

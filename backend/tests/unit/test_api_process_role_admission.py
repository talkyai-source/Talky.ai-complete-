"""Actual entrypoint refusal on the dashboard process; synthetic ports only."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints import telephony_bridge as bridge
from app.core import container, inbound_startup


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", "0"])
async def test_disabled_manual_start_refuses_before_storage_or_adapter(monkeypatch, value):
    monkeypatch.setenv("TELEPHONY_ENABLED", value)
    storage = Mock(side_effect=AssertionError("storage must not be touched"))
    create = AsyncMock(side_effect=AssertionError("adapter must not be created"))
    monkeypatch.setattr(container, "get_container", storage)
    monkeypatch.setattr(bridge.CallControlAdapterFactory, "create", create)
    with pytest.raises(HTTPException) as caught:
        await bridge.start_telephony(adapter_type="auto", _authorized=None)
    assert caught.value.status_code == 503
    assert caught.value.detail["error"] == "telephony_disabled_on_node"
    storage.assert_not_called()
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", "0"])
async def test_disabled_origination_refuses_before_tenant_intent_or_adapter(monkeypatch, value):
    monkeypatch.setenv("TELEPHONY_ENABLED", value)
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "synthetic-role-test-token")
    tenant = Mock(side_effect=AssertionError("tenant/intent work must not start"))
    monkeypatch.setattr(bridge, "resolve_call_tenant", tenant)
    request = Request({"type": "http", "headers": [
        (b"x-internal-service-token", b"synthetic-role-test-token")
    ]})
    with pytest.raises(HTTPException) as caught:
        await bridge.make_call(request, bridge.MakeCallRequest(destination="+15555550123"))
    assert caught.value.status_code == 503
    assert caught.value.detail["error"] == "telephony_disabled_on_node"
    tenant.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_origination_still_requires_authentication(monkeypatch):
    monkeypatch.setenv("TELEPHONY_ENABLED", "false")
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    with pytest.raises(HTTPException) as caught:
        await bridge.make_call(Request({"type": "http", "headers": []}),
                               bridge.MakeCallRequest(destination="+15555550123"))
    assert caught.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "true"])
async def test_enabled_manual_start_keeps_existing_connected_owner(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TELEPHONY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("TELEPHONY_ENABLED", value)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(inbound_startup, "platform_inbound_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(inbound_startup, "validate_production_inbound_database_role", AsyncMock())
    monkeypatch.setattr(inbound_startup, "validate_live_production_inbound_adapter", Mock())
    monkeypatch.setattr(bridge, "get_state_backend", lambda: SimpleNamespace(is_telephony_owner=lambda: True))
    monkeypatch.setattr(bridge, "_adapter", SimpleNamespace(connected=True, name="asterisk"))
    create = AsyncMock(side_effect=AssertionError("existing adapter must be retained"))
    monkeypatch.setattr(bridge.CallControlAdapterFactory, "create", create)
    response = await bridge.start_telephony(adapter_type="asterisk", _authorized=None)
    assert json.loads(response.body) == {"status": "already_connected", "adapter": "asterisk"}
    create.assert_not_awaited()

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_production_inbound_nonowner_cannot_create_adapter(monkeypatch):
    import app.core.container as container_module
    import app.core.inbound_startup as startup
    import app.api.v1.endpoints.telephony_bridge as bridge

    create = AsyncMock(side_effect=AssertionError("factory must stay untouched"))
    state = SimpleNamespace(
        strict_ownership_active=True,
        is_telephony_owner=lambda: False,
    )
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", "redis")
    monkeypatch.setattr(container_module, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(startup, "platform_inbound_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        startup,
        "validate_production_inbound_database_role",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(startup, "validate_production_inbound_state_backend", lambda **_kwargs: None)
    monkeypatch.setattr(bridge, "get_state_backend", lambda: state)
    monkeypatch.setattr(bridge.CallControlAdapterFactory, "create", create)
    monkeypatch.setattr(bridge, "_adapter", None)

    with pytest.raises(HTTPException) as exc_info:
        await bridge.start_telephony(adapter_type="asterisk", _authorized=None)

    assert exc_info.value.status_code == 503
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_connected_nonowner_is_fenced_instead_of_reported_connected(monkeypatch):
    import app.core.container as container_module
    import app.core.inbound_startup as startup
    import app.api.v1.endpoints.telephony_bridge as bridge

    class StaleAdapter:
        name = "asterisk"

        def __init__(self):
            self.connected = True
            self.fenced = False
            self.force_handoff = False

        def fence_ownership_loss(self):
            self.fenced = True
            self.connected = False

        async def disconnect(self, *, force_handoff=False):
            self.force_handoff = force_handoff

    adapter = StaleAdapter()
    state = SimpleNamespace(
        strict_ownership_active=True,
        is_telephony_owner=lambda: False,
    )
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", "redis")
    monkeypatch.setattr(container_module, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(startup, "platform_inbound_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        startup,
        "validate_production_inbound_database_role",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(startup, "validate_production_inbound_state_backend", lambda **_kwargs: None)
    monkeypatch.setattr(bridge, "get_state_backend", lambda: state)
    monkeypatch.setattr(bridge, "_adapter", adapter)

    with pytest.raises(HTTPException) as exc_info:
        await bridge.start_telephony(adapter_type="asterisk", _authorized=None)

    assert exc_info.value.status_code == 503
    assert adapter.fenced is True
    assert adapter.force_handoff is True


@pytest.mark.asyncio
async def test_nonowner_cannot_start_when_inbound_switch_is_off(monkeypatch):
    import app.core.container as container_module
    import app.core.inbound_startup as startup
    import app.api.v1.endpoints.telephony_bridge as bridge

    create = AsyncMock(side_effect=AssertionError("factory must stay untouched"))
    state = SimpleNamespace(
        strict_ownership_active=True,
        is_telephony_owner=lambda: False,
    )
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", "redis")
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(db_pool=object()),
    )
    monkeypatch.setattr(
        startup,
        "platform_inbound_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(bridge, "get_state_backend", lambda: state)
    monkeypatch.setattr(bridge.CallControlAdapterFactory, "create", create)
    monkeypatch.setattr(bridge, "_adapter", None)

    with pytest.raises(HTTPException) as exc_info:
        await bridge.start_telephony(adapter_type="asterisk", _authorized=None)

    assert exc_info.value.status_code == 503
    create.assert_not_awaited()

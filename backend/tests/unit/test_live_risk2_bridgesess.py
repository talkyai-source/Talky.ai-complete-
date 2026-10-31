"""auth-6 follow-up: live-call control/read routes must honour session revocation.

``_require_call_control`` / ``_require_call_read`` trusted the access JWT only, so a
token whose login session was revoked (logout, password reset) could still hang up or
transfer a live call until it expired.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import telephony_bridge as tb
from app.core.security import internal_auth
from app.core.security.rbac import Permission, reset_rbac_seeding_probe_cache


def _request(*, bearer="Bearer abc"):
    req = MagicMock()
    req.headers = {"Authorization": bearer} if bearer else {}
    req.state.tenant_id = "t1"
    req.state.user_id = "u1"
    req.state.user_role = "tenant_admin"
    return req


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    reset_rbac_seeding_probe_cache()
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    # Permissions are granted, so only the session check can deny.
    granted = {Permission.CALLS_DELETE.value, Permission.CALLS_READ.value}
    monkeypatch.setattr(tb, "get_effective_permissions", AsyncMock(return_value=granted))
    monkeypatch.setattr(tb, "rbac_data_is_seeded", AsyncMock(return_value=True))
    yield
    reset_rbac_seeding_probe_cache()


def _session(monkeypatch, session):
    monkeypatch.setattr(
        internal_auth,
        "decode_and_validate_token",
        lambda t: {"sub": "u1", "sid": "s1", "tenant_id": "t1"},
    )

    @asynccontextmanager
    async def acq(pool, tenant):
        yield object()

    monkeypatch.setattr(internal_auth, "acquire_with_tenant", acq)
    monkeypatch.setattr(internal_auth, "get_session_by_id", AsyncMock(return_value=session))
    monkeypatch.setattr(internal_auth, "load_current_principal", AsyncMock(return_value={}))


@pytest.mark.asyncio
async def test_revoked_session_cannot_control_calls(monkeypatch):
    _session(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        await tb._require_call_control(_request(), db_pool=object())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_revoked_session_cannot_read_transfer_attempts(monkeypatch):
    _session(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        await tb._require_call_read(_request(), db_pool=object())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_hangup_route_rejects_revoked_session_before_touching_adapter(monkeypatch):
    _session(monkeypatch, None)
    adapter = MagicMock()
    adapter.hangup = AsyncMock()
    monkeypatch.setattr(tb, "_adapter", adapter)
    monkeypatch.setattr(
        "app.core.container.get_db_pool_from_container", lambda: object()
    )
    with pytest.raises(HTTPException) as exc:
        await tb.hangup_call("call-1", _request())
    assert exc.value.status_code == 401
    adapter.hangup.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_session_still_passes_both_gates(monkeypatch):
    _session(monkeypatch, {"id": "s1"})
    ctx = await tb._require_call_control(_request(), db_pool=object())
    assert not ctx.is_internal and ctx.tenant_id == "t1"
    ctx = await tb._require_call_read(_request(), db_pool=object())
    assert not ctx.is_internal and ctx.tenant_id == "t1"


@pytest.mark.asyncio
async def test_internal_service_caller_skips_session_check(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "service-secret-123")
    session_lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(internal_auth, "get_session_by_id", session_lookup)
    req = MagicMock()
    req.headers = {"x-internal-service-token": "service-secret-123"}
    req.state.tenant_id = None
    req.state.user_role = None
    assert (await tb._require_call_control(req)).is_internal
    assert (await tb._require_call_read(req)).is_internal
    session_lookup.assert_not_awaited()

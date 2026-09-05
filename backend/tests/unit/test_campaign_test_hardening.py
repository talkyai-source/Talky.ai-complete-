"""Failure-path contracts for campaign testing; no live provider traffic."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.api.v1.endpoints import campaign_test_ws as ep
from app.domain.models.ai_config import AIProviderConfig
from tests.unit.test_campaign_test_ws import _Harness, _CAMPAIGN, FakeWebSocket, _end_call_frame, _FakeAcquire


@pytest.mark.asyncio
@pytest.mark.parametrize("sid", [None, "revoked", "expired", "wrong-user"])
async def test_invalid_login_session_never_allocates_provider(sid):
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with (
            patch("app.core.jwt_security.decode_and_validate_token", return_value={"sub": "user-1", "sid": sid}),
            patch.object(ep, "_session_is_active", AsyncMock(return_value=False), create=True),
        ):
            ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
            await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    assert ws.closed_code == 1008
    assert any(f.get("code") == "auth_required" for f in ws.sent)
    h.orchestrator.create_voice_session.assert_not_awaited()
    h.fetch_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_session_database_failure_is_retryable_and_closed():
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with patch.object(ep, "_session_is_active", AsyncMock(side_effect=RuntimeError("private database error")), create=True):
            ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
            await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    assert ws.closed_code == 1011
    assert any(f.get("code") == "authorization_unavailable" for f in ws.sent)
    assert "private database error" not in str(ws.sent)
    h.orchestrator.create_voice_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_session_query_uses_revocable_user_bound_lookup():
    uid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={"id": sid, "user_id": uid}))
    with patch("app.core.db_utils.acquire_with_tenant", return_value=_FakeAcquire(conn)):
        assert await ep._session_is_active(object(), uid, sid)
    sql, *params = conn.fetchrow.await_args.args
    assert "revoked = FALSE" in sql and "expires_at > $2" in sql
    assert "user_id = $3" in sql
    assert params[0] == sid and params[2] == uid


@pytest.mark.asyncio
async def test_open_test_stops_when_login_is_revoked(monkeypatch):
    monkeypatch.setattr(ep, "_AUTH_RECHECK_SECONDS", 0, raising=False)
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with patch.object(ep, "_session_is_active", AsyncMock(side_effect=[True, False]), create=True):
            ws = FakeWebSocket(cookies={"talky_at": "signed"})
            ws.receive = AsyncMock(side_effect=lambda: asyncio.sleep(1, result={"type": "websocket.disconnect"}))
            # AsyncMock does not await a coroutine returned by a regular side effect.
            async def receive():
                await asyncio.sleep(1)
                return {"type": "websocket.disconnect"}
            ws.receive = receive
            await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    assert ws.closed_code == 1008
    assert any(f.get("code") == "auth_required" for f in ws.sent)
    h.orchestrator.end_session.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_state", ["failure", "unwired"])
async def test_unreadable_tenant_config_refuses_default_agent(lookup_state):
    from app.domain.services.tenant_ai_config_resolver import TenantAIConfigResolver
    resolver = TenantAIConfigResolver()
    if lookup_state == "failure":
        resolver.set_db_lookup(AsyncMock(side_effect=RuntimeError("private config outage")))
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with patch("app.domain.services.tenant_ai_config_resolver.get_tenant_ai_config_resolver", return_value=resolver):
            ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
            await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    assert ws.closed_code == 1011
    assert any(f.get("code") == "ai_config_unavailable" for f in ws.sent)
    assert "private config outage" not in str(ws.sent)
    h.orchestrator.create_voice_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_tenant_without_config_uses_documented_defaults():
    from app.domain.services.tenant_ai_config_resolver import TenantAIConfigResolver
    resolver = TenantAIConfigResolver()
    resolver.set_db_lookup(AsyncMock(return_value=None))
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with patch("app.domain.services.tenant_ai_config_resolver.get_tenant_ai_config_resolver", return_value=resolver):
            ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
            await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    h.orchestrator.create_voice_session.assert_awaited_once()
    assert any(f.get("type") == "ready" for f in ws.sent)


@pytest.mark.asyncio
async def test_voice_tuning_outage_refuses_to_substitute_defaults():
    from app.domain.services.voice_tuning import VoiceTuningResolver
    resolver = VoiceTuningResolver()
    resolver.set_db_lookup(AsyncMock(side_effect=RuntimeError("tuning unavailable")))
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with patch("app.domain.services.voice_tuning.get_voice_tuning_resolver", return_value=resolver):
            ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
            await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    assert ws.closed_code == 1011
    assert any(f.get("code") == "ai_config_unavailable" for f in ws.sent)
    h.orchestrator.create_voice_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("membership", ["suspended", "removed"])
async def test_direct_grant_cannot_override_inactive_membership(membership):
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with patch.object(ep, "_has_test_membership", AsyncMock(return_value=False), create=True):
            ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
            await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    assert ws.closed_code == 1008
    assert any(f.get("code") == "permission_denied" for f in ws.sent)
    h.orchestrator.create_voice_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_membership_lookup_checks_active_tenant_or_explicit_platform_role():
    conn = SimpleNamespace(fetchval=AsyncMock(return_value=True))
    with patch("app.core.db_utils.acquire_with_tenant", return_value=_FakeAcquire(conn)):
        assert await ep._has_test_membership(object(), "user-1", "tenant-A")
    sql, uid, tenant = conn.fetchval.await_args.args
    assert "tenant_id = $2" in sql and "status = 'active'" in sql
    assert "r.name = 'platform_admin'" in sql and "r.tenant_scoped = FALSE" in sql
    assert uid == "user-1" and tenant == "tenant-A"


@pytest.mark.asyncio
async def test_open_test_stops_when_membership_is_removed(monkeypatch):
    monkeypatch.setattr(ep, "_AUTH_RECHECK_SECONDS", 0)
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with patch.object(ep, "_has_test_membership", AsyncMock(side_effect=[True, False]), create=True):
            ws = FakeWebSocket(cookies={"talky_at": "signed"})
            async def receive():
                await asyncio.sleep(1)
                return {"type": "websocket.disconnect"}
            ws.receive = receive
            await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    assert ws.closed_code == 1008
    assert any(f.get("code") == "permission_denied" for f in ws.sent)
    h.orchestrator.end_session.assert_awaited_once()

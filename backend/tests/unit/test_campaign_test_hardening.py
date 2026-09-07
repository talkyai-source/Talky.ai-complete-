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
async def test_oversized_test_prompt_is_rejected_before_provider_creation(monkeypatch):
    monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "600")
    campaign = {**_CAMPAIGN, "script_config": {**_CAMPAIGN["script_config"], "additional_instructions": "context " * 100}}
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=campaign) as h:
        ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
        await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    assert ws.closed_code == 1008
    assert any(f.get("code") == "campaign_prompt_invalid" for f in ws.sent)
    h.orchestrator.create_voice_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transcript", "teardown", "finalize"])
async def test_cleanup_failure_does_not_skip_other_obligations(failure):
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        target = {"transcript": h.persist_test_transcript, "teardown": h.orchestrator.end_session,
                  "finalize": h.finalise_test_call}[failure]
        target.side_effect = RuntimeError("cleanup failed")
        ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
        await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    h.persist_test_transcript.assert_awaited_once()
    h.orchestrator.end_session.assert_awaited_once()
    h.finalise_test_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_exception_during_cancel_still_releases_gateway_and_session():
    from app.domain.services.voice_orchestrator import VoiceOrchestrator
    async def pipeline():
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("pipeline finally failed")
    task = asyncio.create_task(pipeline())
    await asyncio.sleep(0)
    gateway = SimpleNamespace(on_call_ended=AsyncMock(), cleanup=AsyncMock())
    session = SimpleNamespace(call_id="call-1", pipeline_task=task, realtime_bridge=None,
                              realtime_session=None, pipeline=None, event_repo=None, config=None,
                              media_gateway=gateway, tts_provider=None)
    orchestrator = object.__new__(VoiceOrchestrator)
    orchestrator._active_sessions = {session.call_id: session}
    await orchestrator.end_session(session)
    gateway.cleanup.assert_awaited_once()
    assert not orchestrator._active_sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "cancelled"])
async def test_transcript_timeout_or_cancellation_still_finalizes_test(monkeypatch, mode):
    monkeypatch.setattr(ep, "_CLEANUP_TIMEOUT_SECONDS", 0.01)
    async def persist(*args):
        if mode == "cancelled":
            raise asyncio.CancelledError()
        await asyncio.Event().wait()
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        h.persist_test_transcript.side_effect = persist
        ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
        if mode == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
        else:
            await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    h.orchestrator.end_session.assert_awaited_once()
    h.finalise_test_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_campaign_read_outage_is_retryable_not_missing():
    real_fetch = ep._fetch_campaign_row
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with (
            patch.object(ep, "_fetch_campaign_row", real_fetch),
            patch("app.core.db_utils.acquire_with_tenant", side_effect=RuntimeError("private database outage")),
        ):
            ws = FakeWebSocket(cookies={"talky_at": "signed"}, recv_frames=[_end_call_frame()])
            await ep.campaign_test_websocket(ws, str(uuid.uuid4()), first_speaker="user")
    assert ws.closed_code == 1011
    assert any(f.get("code") == "campaign_lookup_failed" for f in ws.sent)
    assert "private database outage" not in str(ws.sent)
    h.orchestrator.create_voice_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_campaign_miss_keeps_ownership_predicate_and_policy_close():
    real_fetch = ep._fetch_campaign_row
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value=None))
    cid = str(uuid.uuid4())
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with (
            patch.object(ep, "_fetch_campaign_row", real_fetch),
            patch("app.core.db_utils.acquire_with_tenant", return_value=_FakeAcquire(conn)),
        ):
            ws = FakeWebSocket(cookies={"talky_at": "signed"})
            await ep.campaign_test_websocket(ws, cid, first_speaker="user")
    sql, call_campaign, tenant = conn.fetchrow.await_args.args
    assert "id = $1 AND tenant_id = $2" in sql
    assert call_campaign == cid and tenant == "tenant-A"
    assert ws.closed_code == 1008
    h.orchestrator.create_voice_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_auth_watchdog_cannot_skip_session_cleanup():
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with patch.object(ep, "_watch_login_session", AsyncMock(side_effect=OSError("transport closed"))):
            ws = FakeWebSocket(cookies={"talky_at": "signed"})
            async def receive():
                await asyncio.sleep(1)
                return {"type": "websocket.disconnect"}
            ws.receive = receive
            try:
                await ep.campaign_test_websocket(ws, "camp-1", first_speaker="user")
            except OSError:
                pass
    h.orchestrator.end_session.assert_awaited_once()
    h.finalise_test_call.assert_awaited_once()


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

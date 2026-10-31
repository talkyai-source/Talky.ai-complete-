"""Review-only probes: failures document unmet behavior, not applied fixes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import asyncio

import pytest

from tests.unit.test_campaign_test_ws import (
    _Harness, _CAMPAIGN, _end_call_frame, FakeWebSocket, campaign_test_ws,
)
from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.tenant_ai_config_resolver import TenantAIConfigResolver


@pytest.mark.asyncio
@pytest.mark.parametrize("sid", [None, "revoked-session"])
async def test_session_invalidity_must_prevent_provider_creation(sid):
    payload = {"sub": "user-1"}
    if sid:
        payload["sid"] = sid
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with (
            patch("app.core.jwt_security.decode_and_validate_token", return_value=payload),
            patch("app.core.security.sessions.get_session_by_id", AsyncMock(return_value=None)) as session_lookup,
        ):
            ws = FakeWebSocket(cookies={"talky_at": "signed-token"}, recv_frames=[_end_call_frame()])
            await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    print(f"sid={sid!r} session_lookups={session_lookup.await_count} provider_creations={h.orchestrator.create_voice_session.await_count}")
    h.orchestrator.create_voice_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_tenant_config_outage_must_not_report_normal_ready():
    resolver = TenantAIConfigResolver()
    resolver.set_db_lookup(AsyncMock(side_effect=RuntimeError("audit database outage")))
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        with patch("app.domain.services.tenant_ai_config_resolver.get_tenant_ai_config_resolver", return_value=resolver):
            ws = FakeWebSocket(cookies={"talky_at": "tok"}, recv_frames=[_end_call_frame()])
            await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    print(f"frames={ws.sent} resolved_llm={h.captured['config'].llm_model}")
    assert not any(frame.get("type") == "ready" for frame in ws.sent)


@pytest.mark.asyncio
async def test_failed_teardown_must_still_finalize_durable_test_row():
    from app.domain.services.voice_orchestrator import VoiceOrchestrator
    async def pipeline():
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("audit pipeline cancellation failure")
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN) as h:
        original_create = h.orchestrator.create_voice_session.side_effect
        async def create(config):
            session = original_create(config)
            session.pipeline_task = asyncio.create_task(pipeline())
            await asyncio.sleep(0)
            return session
        h.orchestrator.create_voice_session.side_effect = create
        h.orchestrator.end_session.side_effect = lambda session: None
        async def real_end(session):
            await VoiceOrchestrator.end_session(h.orchestrator, session)
        h.orchestrator.end_session.side_effect = real_end
        ws = FakeWebSocket(cookies={"talky_at": "tok"}, recv_frames=[_end_call_frame()])
        try:
            await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="user")
        except RuntimeError:
            pass
    print(f"persisted={h.persist_test_transcript.await_count} finalized={h.finalise_test_call.await_count}")
    h.finalise_test_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_campaign_database_outage_must_not_be_reported_as_missing():
    real_fetch = campaign_test_ws._fetch_campaign_row
    with _Harness(tenant_cfg=AIProviderConfig(), campaign_row=_CAMPAIGN):
        with (
            patch.object(campaign_test_ws, "_fetch_campaign_row", real_fetch),
            patch("app.core.db_utils.acquire_with_tenant", side_effect=RuntimeError("audit database outage")),
        ):
            ws = FakeWebSocket(cookies={"talky_at": "tok"})
            await campaign_test_ws.campaign_test_websocket(ws, "camp-1", first_speaker="user")
    print(f"close={ws.closed_code} frames={ws.sent}")
    assert ws.closed_code == 1011


def test_realtime_name_selection_must_consult_the_actual_realtime_voice():
    from app.domain.services import telephony_session_config as tsc
    consulted = []
    def gender(voice_id):
        consulted.append(voice_id)
        return None
    with patch.object(tsc, "_resolve_voice_gender_safe", side_effect=gender):
        config = tsc.build_telephony_session_config(
            gateway_type="browser", campaign=_CAMPAIGN,
            ai_config_override=AIProviderConfig(pipeline_mode="realtime", realtime_voice="marin"),
        )
    print(f"actual_realtime_voice={config.realtime_voice} consulted_for_name={consulted}")
    assert config.realtime_voice in consulted


@pytest.mark.asyncio
async def test_suspended_membership_must_disable_direct_test_permission():
    from app.core.security.rbac import get_user_permissions, Permission
    conn = SimpleNamespace(fetch=AsyncMock(side_effect=[[], [{"name": "campaigns:update"}]]))
    permissions = await get_user_permissions(conn, "user-1", "tenant-A")
    print(f"role_grants=[] direct_grants=campaigns:update permissions={permissions}")
    assert Permission.CAMPAIGNS_UPDATE not in permissions


def test_over_budget_guidance_must_be_preserved_or_rejected():
    from app.domain.services import telephony_session_config as tsc
    budget = tsc.campaign_guidance_char_budget()
    marker = "AUDIT_MIDDLE_RULE_NEVER_PROMISE_FREE_WORK"
    guidance = ("context " * budget) + marker + (" closing" * budget)
    campaign = {**_CAMPAIGN, "script_config": {
        **_CAMPAIGN.get("script_config", {}), "additional_instructions": guidance,
    }}
    try:
        config = tsc.build_telephony_session_config(
            gateway_type="browser", campaign=campaign, ai_config_override=AIProviderConfig(),
        )
    except ValueError:
        return
    print(f"input_chars={len(guidance)} marker_preserved={marker in config.system_prompt} elision_present={'omitted for length' in config.system_prompt}")
    assert marker in config.system_prompt

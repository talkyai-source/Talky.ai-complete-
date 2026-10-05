"""Paid tenant configuration failures must not silently select another profile."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import socket

import pytest

from app.api.v1.endpoints import twilio_bridge, vonage_bridge
from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.telephony import prewarm
from app.domain.services.tenant_ai_config_resolver import (
    TenantAIConfigResolver, TenantAIConfigUnavailable,
)
from app.domain.services.voice_tuning import VoiceTuningResolver

TENANT = "c4442497-fbc8-4675-8424-995456831b39"
CAMPAIGN = "b16e4330-63a8-4ef9-854f-0056837f3f00"


@pytest.fixture(autouse=True)
async def offline_only(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Profile admission regression attempted network access")

    # Install after Windows asyncio has constructed its local socketpair.
    with monkeypatch.context() as network:
        network.setattr(socket.socket, "connect", blocked)
        network.setattr(socket.socket, "connect_ex", blocked)
        network.setattr(socket, "getaddrinfo", blocked)
        yield


@pytest.fixture
def admission(monkeypatch):
    ai, tuning = TenantAIConfigResolver(), VoiceTuningResolver()
    ai_lookup = AsyncMock(return_value=AIProviderConfig(pipeline_mode="realtime", realtime_voice="ash"))
    tuning_lookup = AsyncMock(return_value={"stt_eot_timeout_ms": 1800})
    ai.set_db_lookup(ai_lookup)
    tuning.set_db_lookup(tuning_lookup)
    monkeypatch.setattr("app.domain.services.tenant_ai_config_resolver.get_tenant_ai_config_resolver", lambda: ai)
    monkeypatch.setattr("app.domain.services.voice_tuning.get_voice_tuning_resolver", lambda: tuning)
    configs = []

    async def create(config):
        configs.append(config)
        return NS(call_id="synthetic-config-call", call_session=NS(call_id="synthetic-config-call"),
                  realtime_bridge=object() if config.pipeline_mode == "realtime" else None,
                  stt_provider=NS(), tts_provider=NS(), llm_provider=NS())

    factory = AsyncMock(side_effect=create)
    orchestrator = NS(create_voice_session=factory, end_session=AsyncMock())
    monkeypatch.setattr(prewarm, "_get_orchestrator", lambda: orchestrator)
    external = []
    for name in ("warm_tts_inference_path", "warm_llm_stream", "prepare_pre_originate_greeting",
                 "_resolve_session_accent", "_attach_llm_opener"):
        port = AsyncMock()
        monkeypatch.setattr(prewarm, name, port)
        external.append(port)
    monkeypatch.setattr(prewarm, "_start_opening_ladder_generation", lambda *args: None)
    monkeypatch.setattr("app.services.scripts.knowledge.session_inject.apply_campaign_knowledge", AsyncMock())
    monkeypatch.setattr("app.core.container.get_container", lambda: NS(db_pool=object()))
    route = AsyncMock(return_value=NS(resolved=True, tenant_id=TENANT))
    monkeypatch.setattr("app.domain.services.telephony.inbound_router.resolve_inbound_route", route)
    return NS(ai=ai, tuning=tuning, ai_lookup=ai_lookup, tuning_lookup=tuning_lookup,
              factory=factory, external=external, configs=configs, route=route)


async def prepare(*, tenant=TENANT):
    campaign = {"id": CAMPAIGN, "script_config": {"company_name": "Synthetic Company"}}
    if tenant:
        campaign["tenant_id"] = tenant
    return await prewarm.prepare_prewarmed_session(
        first_speaker="agent", campaign_id=CAMPAIGN, agent_name="Ava",
        container=NS(db_client=None), campaign_row=campaign,
    )


def make_unavailable(admission, lookup, mode):
    if mode == "unwired":
        getattr(admission, lookup).set_db_lookup(None)
    else:
        getattr(admission, lookup + "_lookup").side_effect = RuntimeError("synthetic lookup unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["ai", "tuning"])
@pytest.mark.parametrize("mode", ["exception", "unwired"])
async def test_known_tenant_prewarm_rejects_unavailable_profile_before_any_provider_creation(admission, lookup, mode):
    make_unavailable(admission, lookup, mode)
    result = await prepare()
    assert result.session is None
    assert "TenantAIConfigUnavailable" in result.failure_reason
    admission.factory.assert_not_awaited()
    for port in admission.external:
        port.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["realtime", "cascaded"])
async def test_prewarm_preserves_successfully_resolved_saved_selection(admission, pipeline):
    admission.ai_lookup.return_value = AIProviderConfig(pipeline_mode=pipeline, realtime_voice="ash")
    result = await prepare()
    assert result.session is not None and result.failure_reason is None
    config = admission.configs[0]
    assert config.tenant_id == TENANT and config.pipeline_mode == pipeline
    assert config.realtime_voice == "ash"
    if pipeline == "cascaded":
        assert config.stt_eot_timeout_ms == 1800
    admission.ai_lookup.assert_awaited_once_with(TENANT)
    admission.tuning_lookup.assert_awaited_once_with(TENANT)


@pytest.mark.asyncio
async def test_prewarm_successful_absent_rows_remain_explicit_default_compatible(admission):
    admission.ai_lookup.return_value = None
    admission.tuning_lookup.return_value = None
    result = await prepare()
    assert result.session is not None and result.failure_reason is None
    assert admission.configs[0].pipeline_mode == admission.ai.default().pipeline_mode
    admission.ai_lookup.assert_awaited_once_with(TENANT)
    admission.tuning_lookup.assert_awaited_once_with(TENANT)


@pytest.mark.asyncio
async def test_genuinely_tenantless_prewarm_does_not_call_tenant_lookups(admission):
    admission.ai_lookup.side_effect = RuntimeError("must not read another tenant")
    admission.tuning_lookup.side_effect = RuntimeError("must not read another tenant")
    result = await prepare(tenant=None)
    assert result.session is not None and result.failure_reason is None
    assert admission.configs[0].tenant_id is None
    admission.ai_lookup.assert_not_awaited()
    admission.tuning_lookup.assert_not_awaited()


BUILDERS = [twilio_bridge._build_twilio_session_config, vonage_bridge._build_vonage_session_config]


@pytest.mark.asyncio
@pytest.mark.parametrize("builder", BUILDERS)
@pytest.mark.parametrize("lookup", ["ai", "tuning"])
@pytest.mark.parametrize("mode", ["exception", "unwired"])
async def test_resolved_did_rejects_unavailable_profile(admission, builder, lookup, mode):
    make_unavailable(admission, lookup, mode)
    with pytest.raises(TenantAIConfigUnavailable):
        await builder("+12025550123")
    admission.factory.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("builder", BUILDERS)
async def test_resolved_did_keeps_saved_profile_and_tuning(admission, builder):
    config = await builder("+12025550123")
    assert config.tenant_id == TENANT and config.pipeline_mode == "realtime"
    assert config.realtime_voice == "ash" and config.stt_eot_timeout_ms == 1800


@pytest.mark.asyncio
@pytest.mark.parametrize("builder", BUILDERS)
async def test_resolved_did_successfully_absent_rows_keep_defaults(admission, builder):
    admission.ai_lookup.return_value = None
    admission.tuning_lookup.return_value = None
    config = await builder("+12025550123")
    assert config.tenant_id == TENANT
    assert config.pipeline_mode == admission.ai.default().pipeline_mode


@pytest.mark.asyncio
@pytest.mark.parametrize("builder", BUILDERS)
async def test_no_did_remains_genuinely_tenantless_default_path(admission, builder):
    config = await builder(None)
    assert config.tenant_id is None
    assert config.pipeline_mode == admission.ai.default().pipeline_mode
    admission.ai_lookup.assert_not_awaited()
    admission.tuning_lookup.assert_not_awaited()
    admission.route.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["ai", "tuning"])
@pytest.mark.parametrize("mode", ["exception", "unwired"])
async def test_actual_origination_boundary_returns_503_without_opening_provider(admission, monkeypatch, lookup, mode):
    from app.api.v1.endpoints import telephony_bridge
    from tests.unit.test_outbound_campaign_boundaries import (
        _install_call_path, _campaign, _assert_call_error,
    )

    path = _install_call_path(monkeypatch, campaign_rows=[_campaign(), _campaign()])
    # Keep actual prewarm/resolver/session-config logic. Existing endpoint
    # fixture supplies only route, guard, DB and adapter boundary ports.
    monkeypatch.setattr(telephony_bridge, "prepare_prewarmed_session", prewarm.prepare_prewarmed_session)
    monkeypatch.setattr(telephony_bridge, "emit_event_via_pool", AsyncMock())
    make_unavailable(admission, lookup, mode)
    error = await _assert_call_error(path, 503)
    assert "TenantAIConfigUnavailable" in error.detail
    admission.factory.assert_not_awaited()
    assert "originate" not in path.events
    assert "bind_call" not in path.events

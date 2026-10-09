"""Selected AssemblyAI configuration reaches call sessions and its own credential."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.telephony_session_config import (
    build_telephony_session_config,
    resolve_stt_selection,
)
from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig


@pytest.mark.parametrize("mode", ["balanced", "min_latency", "max_accuracy"])
def test_selected_mode_reaches_campaign_session_without_flux_overrides(mode):
    selected = AIProviderConfig(stt_engine="assemblyai", assemblyai_settings={"mode": mode})
    session = build_telephony_session_config(ai_config_override=selected)
    assert session.stt_provider_type == "assemblyai"
    assert session.stt_model == "universal-3-6-pro"
    assert session.stt_language == "en"
    assert session.assemblyai_settings["mode"] == mode
    assert session.assemblyai_settings["min_turn_silence"] is None
    assert session.assemblyai_settings["max_turn_silence"] is None


def test_runtime_rejects_non_english_even_if_api_validation_was_bypassed():
    with pytest.raises(ValueError, match="English"):
        resolve_stt_selection(SimpleNamespace(stt_engine="assemblyai", stt_language="es"))


@pytest.mark.asyncio
@pytest.mark.parametrize("bridge,rate", [("twilio", 8000), ("vonage", 16000)])
async def test_cloud_session_preserves_settings_and_transport_rate(monkeypatch, bridge, rate):
    from importlib import import_module

    from app.domain.services import tenant_ai_config_resolver, voice_tuning

    selected = AIProviderConfig(
        stt_engine="assemblyai",
        assemblyai_settings={
            "mode": "max_accuracy",
            "keyterms_prompt": ["Talky"],
            "min_turn_silence": 700,
        },
    )
    monkeypatch.setattr(
        tenant_ai_config_resolver,
        "resolve_ai_config_for_did",
        AsyncMock(return_value=("tenant-a", selected)),
    )
    resolver = SimpleNamespace(for_tenant_async=AsyncMock(return_value=voice_tuning.VoiceTuning()))
    monkeypatch.setattr(voice_tuning, "get_voice_tuning_resolver", lambda: resolver)
    module = import_module(f"app.api.v1.endpoints.{bridge}_bridge")
    session = await getattr(module, f"_build_{bridge}_session_config")("synthetic-did")
    assert session.stt_provider_type == "assemblyai"
    assert session.stt_sample_rate == rate
    assert session.assemblyai_settings["keyterms_prompt"] == ["Talky"]
    assert session.assemblyai_settings["min_turn_silence"] == 700


@pytest.mark.asyncio
@pytest.mark.parametrize("failover", [False, True])
async def test_assembly_only_account_needs_no_deepgram_key(monkeypatch, failover):
    from app.domain.services import credential_resolver
    from app.infrastructure.stt import assemblyai

    monkeypatch.setenv("STT_FAILOVER_ENABLED", str(failover).lower())
    fake = SimpleNamespace(initialize=AsyncMock())
    resolver = SimpleNamespace(
        resolve=AsyncMock(
            side_effect=lambda provider, **kw: "assembly-secret"
            if provider == "assemblyai"
            else None
        )
    )
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: resolver)
    monkeypatch.setattr(assemblyai, "AssemblyAISTTProvider", lambda: fake)
    config = VoiceSessionConfig(
        stt_provider_type="assemblyai",
        tenant_id="tenant-a",
        assemblyai_settings={"mode": "min_latency"},
    )
    actual = await VoiceOrchestrator()._create_stt_provider(config)
    assert actual is fake
    sent = fake.initialize.await_args.args[0]
    assert sent["api_key"] == "assembly-secret"
    assert sent["model"] == "universal-3-6-pro"
    assert sent["assemblyai_settings"] == {"mode": "min_latency"}
    assert not {"eot_threshold", "eager_eot_threshold", "eot_timeout_ms"}.intersection(sent)
    resolver.resolve.assert_any_await("assemblyai", tenant_id="tenant-a")
    if not failover:
        resolver.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_opt_in_failover_keeps_provider_credentials_separate(monkeypatch):
    from app.domain.services import credential_resolver
    from app.domain.services.resilient_stt import ResilientSTTProvider
    from app.infrastructure.stt import assemblyai, deepgram_nova

    monkeypatch.setenv("STT_FAILOVER_ENABLED", "true")
    monkeypatch.delenv("STT_SECONDARY_MODEL", raising=False)
    primary = SimpleNamespace(initialize=AsyncMock(), name="assemblyai")
    secondary = SimpleNamespace(initialize=AsyncMock(), name="nova")
    resolver = SimpleNamespace(
        resolve=AsyncMock(side_effect=lambda provider, **kw: provider + "-secret")
    )
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: resolver)
    monkeypatch.setattr(assemblyai, "AssemblyAISTTProvider", lambda: primary)
    monkeypatch.setattr(deepgram_nova, "DeepgramNovaSTTProvider", lambda: secondary)
    actual = await VoiceOrchestrator()._create_stt_provider(
        VoiceSessionConfig(stt_provider_type="assemblyai")
    )
    assert isinstance(actual, ResilientSTTProvider)
    assert primary.initialize.await_args.args[0]["api_key"] == "assemblyai-secret"
    assert secondary.initialize.await_args.args[0]["api_key"] == "deepgram-secret"


def test_pinned_inbound_snapshot_rehydrates_assembly_settings():
    from app.domain.services.telephony.lifecycle import _pinned_inbound_ai_config

    selected = AIProviderConfig(
        stt_engine="assemblyai",
        assemblyai_settings={"mode": "max_accuracy", "prompt": "English support calls."},
    )
    raw = selected.model_dump(mode="json")
    raw.update(id="config-1", voice_tuning={})
    result = _pinned_inbound_ai_config({"config_snapshot": {"tenant_ai_config": raw}})
    config = result[0]
    assert resolve_stt_selection(config)["assemblyai_settings"]["mode"] == "max_accuracy"
    assert (
        resolve_stt_selection(config)["assemblyai_settings"]["prompt"] == "English support calls."
    )


@pytest.mark.asyncio
async def test_context_errors_are_nonfatal_and_private(caplog):
    from app.domain.services.stt_context import update_stt_agent_context

    provider = SimpleNamespace(
        update_agent_context=AsyncMock(
            side_effect=RuntimeError("secret-in-url caller@example.test")
        )
    )
    await update_stt_agent_context(provider, "call-a", "What is your email?")
    provider.update_agent_context.assert_awaited_once_with("call-a", "What is your email?")
    assert "secret-in-url" not in caplog.text
    assert "caller@example.test" not in caplog.text


@pytest.mark.asyncio
async def test_resilience_context_follows_active_provider():
    from app.domain.services.resilient_stt import ResilientSTTProvider

    primary = SimpleNamespace(name="assemblyai", update_agent_context=AsyncMock())
    secondary = SimpleNamespace(name="nova")
    wrapper = ResilientSTTProvider(primary=primary, secondary=secondary)
    await wrapper.update_agent_context("call-a", "Your email address?")
    primary.update_agent_context.assert_awaited_once()
    wrapper._active = secondary
    await wrapper.update_agent_context("call-a", "Thank you.")
    assert primary.update_agent_context.await_count == 1

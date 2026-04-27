"""T1.3 integration — orchestrator wraps providers in resilient
wrappers when the env flag is set.

Coverage:
  - STT_FAILOVER_ENABLED unset / falsy → primary STT only.
  - STT_FAILOVER_ENABLED=true → ResilientSTTProvider wrapping primary
    + a secondary Deepgram instance.
  - STT secondary init failure → fall back to primary alone.
  - TTS_FAILOVER_ENABLED unset → bare primary TTS.
  - TTS_FAILOVER_ENABLED=true (Cartesia primary) → Resilient wrapping
    Cartesia + ElevenLabs secondary.
  - TTS_SECONDARY_PROVIDER override is honoured.
  - TTS_SECONDARY_VOICE_MAP parses into the policy.
  - Voice-map parser handles bad input.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.voice_orchestrator import (
    VoiceOrchestrator,
    VoiceSessionConfig,
    _failover_enabled,
    _parse_voice_map,
)
from app.domain.services.resilient_stt import ResilientSTTProvider
from app.domain.services.resilient_tts import ResilientTTSProvider


def _resolver(value: str = "k") -> MagicMock:
    fake = MagicMock()
    fake.resolve = AsyncMock(return_value=value)
    return fake


def _provider_class_factory(*, raise_on_init: bool = False):
    """Build a MagicMock that pretends to be a provider class.
    Calling the class returns an instance with `initialize`,
    `name`, and other minimal surface."""
    instance = MagicMock(initialize=AsyncMock(side_effect=RuntimeError("init failed") if raise_on_init else None))
    instance.name = "fake"
    cls = MagicMock(return_value=instance)
    return cls, instance


# ──────────────────────────────────────────────────────────────────────────
# Helper parsing
# ──────────────────────────────────────────────────────────────────────────

def test_failover_enabled_truthy_values(monkeypatch: pytest.MonkeyPatch):
    for v in ("1", "true", "TRUE", "Yes", "on"):
        monkeypatch.setenv("FOO", v)
        assert _failover_enabled("FOO") is True


def test_failover_enabled_falsy_values(monkeypatch: pytest.MonkeyPatch):
    for v in ("", "0", "false", "no", "off", "garbage"):
        if v == "":
            monkeypatch.delenv("FOO", raising=False)
        else:
            monkeypatch.setenv("FOO", v)
        assert _failover_enabled("FOO") is False


def test_parse_voice_map_simple():
    out = _parse_voice_map("a=b,c=d")
    assert out == {"a": "b", "c": "d"}


def test_parse_voice_map_handles_whitespace_and_bad_entries():
    out = _parse_voice_map("  primary1 = secondary1 , noequalshere , =empty , real=value")
    assert out == {"primary1": "secondary1", "real": "value"}


def test_parse_voice_map_empty():
    assert _parse_voice_map("") == {}


# ──────────────────────────────────────────────────────────────────────────
# STT failover
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stt_no_failover_returns_primary(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("STT_FAILOVER_ENABLED", raising=False)
    cfg = VoiceSessionConfig(tenant_id="t1")
    fake_cls, instance = _provider_class_factory()

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=_resolver("dg-key"),
    ), patch(
        "app.infrastructure.stt.deepgram_flux.DeepgramFluxSTTProvider", fake_cls,
    ):
        result = await VoiceOrchestrator()._create_stt_provider(cfg)

    # Bare primary — not wrapped.
    assert not isinstance(result, ResilientSTTProvider)


@pytest.mark.asyncio
async def test_stt_failover_enabled_wraps_in_resilient(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STT_FAILOVER_ENABLED", "true")
    cfg = VoiceSessionConfig(tenant_id="t1")

    # Two distinct provider instances so the wrapper can hold both.
    primary = MagicMock(initialize=AsyncMock(), name="primary")
    primary.name = "deepgram_flux_primary"
    secondary = MagicMock(initialize=AsyncMock(), name="secondary")
    secondary.name = "deepgram_flux_secondary"
    instances = iter([primary, secondary])
    fake_cls = MagicMock(side_effect=lambda *a, **k: next(instances))

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=_resolver("dg-key"),
    ), patch(
        "app.infrastructure.stt.deepgram_flux.DeepgramFluxSTTProvider", fake_cls,
    ):
        result = await VoiceOrchestrator()._create_stt_provider(cfg)

    assert isinstance(result, ResilientSTTProvider)
    # Both instances initialised.
    primary.initialize.assert_awaited_once()
    secondary.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_stt_secondary_init_failure_falls_back_to_primary(monkeypatch):
    monkeypatch.setenv("STT_FAILOVER_ENABLED", "true")
    cfg = VoiceSessionConfig(tenant_id="t1")

    primary = MagicMock(initialize=AsyncMock())
    primary.name = "deepgram_flux"
    secondary = MagicMock(initialize=AsyncMock(side_effect=RuntimeError("auth fail")))
    secondary.name = "deepgram_flux_alt"
    instances = iter([primary, secondary])
    fake_cls = MagicMock(side_effect=lambda *a, **k: next(instances))

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=_resolver("dg-key"),
    ), patch(
        "app.infrastructure.stt.deepgram_flux.DeepgramFluxSTTProvider", fake_cls,
    ):
        result = await VoiceOrchestrator()._create_stt_provider(cfg)

    # Wrapper bypassed — primary returned bare.
    assert not isinstance(result, ResilientSTTProvider)
    assert result is primary


# ──────────────────────────────────────────────────────────────────────────
# TTS failover
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tts_no_failover_returns_primary(monkeypatch):
    monkeypatch.delenv("TTS_FAILOVER_ENABLED", raising=False)
    cfg = VoiceSessionConfig(tts_provider_type="cartesia", voice_id="v")
    fake_cls, instance = _provider_class_factory()

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=_resolver("c-key"),
    ), patch(
        "app.infrastructure.tts.cartesia.CartesiaTTSProvider", fake_cls,
    ):
        result = await VoiceOrchestrator()._create_tts_provider(cfg)

    assert not isinstance(result, ResilientTTSProvider)


@pytest.mark.asyncio
async def test_tts_failover_enabled_cartesia_wraps_with_elevenlabs_default(monkeypatch):
    monkeypatch.setenv("TTS_FAILOVER_ENABLED", "1")
    monkeypatch.delenv("TTS_SECONDARY_PROVIDER", raising=False)
    cfg = VoiceSessionConfig(tts_provider_type="cartesia", voice_id="v")

    cart_instance = MagicMock(initialize=AsyncMock())
    cart_instance.name = "cartesia"
    eleven_instance = MagicMock(initialize=AsyncMock())
    eleven_instance.name = "elevenlabs"
    cart_cls = MagicMock(return_value=cart_instance)
    eleven_cls = MagicMock(return_value=eleven_instance)

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=_resolver("k"),
    ), patch(
        "app.infrastructure.tts.cartesia.CartesiaTTSProvider", cart_cls,
    ), patch(
        "app.infrastructure.tts.elevenlabs_tts.ElevenLabsTTSProvider", eleven_cls,
    ):
        result = await VoiceOrchestrator()._create_tts_provider(cfg)

    assert isinstance(result, ResilientTTSProvider)
    cart_instance.initialize.assert_awaited_once()
    eleven_instance.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_tts_secondary_provider_override_honoured(monkeypatch):
    """`TTS_SECONDARY_PROVIDER=deepgram` overrides the default
    Cartesia→ElevenLabs pairing."""
    monkeypatch.setenv("TTS_FAILOVER_ENABLED", "yes")
    monkeypatch.setenv("TTS_SECONDARY_PROVIDER", "deepgram")
    cfg = VoiceSessionConfig(tts_provider_type="cartesia", voice_id="v")

    cart_instance = MagicMock(initialize=AsyncMock())
    cart_instance.name = "cartesia"
    dg_instance = MagicMock(initialize=AsyncMock())
    dg_instance.name = "deepgram"
    cart_cls = MagicMock(return_value=cart_instance)
    dg_cls = MagicMock(return_value=dg_instance)

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=_resolver("k"),
    ), patch(
        "app.infrastructure.tts.cartesia.CartesiaTTSProvider", cart_cls,
    ), patch(
        "app.infrastructure.tts.deepgram_tts.DeepgramTTSProvider", dg_cls,
    ):
        result = await VoiceOrchestrator()._create_tts_provider(cfg)

    assert isinstance(result, ResilientTTSProvider)
    dg_instance.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_tts_voice_map_flows_into_policy(monkeypatch):
    monkeypatch.setenv("TTS_FAILOVER_ENABLED", "true")
    monkeypatch.setenv(
        "TTS_SECONDARY_VOICE_MAP",
        "cartesia-tessa=eleven-bella, cartesia-bob=eleven-charlie",
    )
    cfg = VoiceSessionConfig(tts_provider_type="cartesia", voice_id="cartesia-tessa")

    cart = MagicMock(initialize=AsyncMock())
    cart.name = "cartesia"
    eleven = MagicMock(initialize=AsyncMock())
    eleven.name = "elevenlabs"

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=_resolver("k"),
    ), patch(
        "app.infrastructure.tts.cartesia.CartesiaTTSProvider",
        MagicMock(return_value=cart),
    ), patch(
        "app.infrastructure.tts.elevenlabs_tts.ElevenLabsTTSProvider",
        MagicMock(return_value=eleven),
    ):
        result = await VoiceOrchestrator()._create_tts_provider(cfg)

    assert isinstance(result, ResilientTTSProvider)
    # The wrapper holds the policy with the parsed map.
    assert result._policy.voice_id_map == {
        "cartesia-tessa": "eleven-bella",
        "cartesia-bob": "eleven-charlie",
    }


@pytest.mark.asyncio
async def test_tts_secondary_unknown_provider_returns_primary(monkeypatch):
    monkeypatch.setenv("TTS_FAILOVER_ENABLED", "true")
    monkeypatch.setenv("TTS_SECONDARY_PROVIDER", "unknown-xyz")
    cfg = VoiceSessionConfig(tts_provider_type="cartesia", voice_id="v")
    cart = MagicMock(initialize=AsyncMock())
    cart.name = "cartesia"

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=_resolver("k"),
    ), patch(
        "app.infrastructure.tts.cartesia.CartesiaTTSProvider",
        MagicMock(return_value=cart),
    ):
        result = await VoiceOrchestrator()._create_tts_provider(cfg)

    # Wrapper bypassed because secondary couldn't be built.
    assert not isinstance(result, ResilientTTSProvider)

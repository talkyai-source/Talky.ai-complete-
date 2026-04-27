"""T1.1 follow-up — orchestrator passes tenant context to providers.

Confirms that VoiceOrchestrator constructs each provider with a
tenant-resolved API key when `VoiceSessionConfig.tenant_id` is set,
and falls back to the env var otherwise.

Tests are surgical — we patch CredentialResolver + the concrete
provider class so we never touch real Deepgram / Cartesia / Groq
clients. The point is to prove the wiring, not the providers.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.voice_orchestrator import (
    VoiceOrchestrator,
    VoiceSessionConfig,
)


@pytest.fixture
def orch():
    return VoiceOrchestrator()


def _resolver_returning(value: str | None):
    """Build a CredentialResolver-shaped fake whose .resolve() yields
    a known string. Records the kwargs each call site used so we can
    assert tenant_id and provider were passed."""
    fake = MagicMock()
    fake.resolve = AsyncMock(return_value=value)
    return fake


# ──────────────────────────────────────────────────────────────────────────
# LLM provider construction
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_provider_uses_resolver_with_tenant(orch):
    cfg = VoiceSessionConfig(
        llm_provider_type="groq",
        llm_model="llama-3.1-8b-instant",
        tenant_id="tenant-A",
    )
    fake_resolver = _resolver_returning("tenant-A-key")
    fake_provider = MagicMock(initialize=AsyncMock())
    fake_factory = MagicMock(create=MagicMock(return_value=fake_provider))

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=fake_resolver,
    ), patch(
        "app.infrastructure.llm.factory.LLMFactory", fake_factory,
    ):
        await orch._create_llm_provider(cfg)

    fake_resolver.resolve.assert_awaited_once()
    call_kwargs = fake_resolver.resolve.await_args.kwargs
    pos_args = fake_resolver.resolve.await_args.args
    # First positional is the provider type
    assert pos_args[0] == "groq"
    assert call_kwargs["tenant_id"] == "tenant-A"
    assert call_kwargs["env_var"] == "GROQ_API_KEY"

    init_kwargs = fake_provider.initialize.await_args.args[0]
    assert init_kwargs["api_key"] == "tenant-A-key"


@pytest.mark.asyncio
async def test_llm_provider_falls_back_to_env_when_no_tenant(orch, monkeypatch):
    """Tenant-less session (Ask AI demo) — resolver still runs but
    with tenant_id=None, so it returns whatever env says."""
    cfg = VoiceSessionConfig(llm_provider_type="gemini", llm_model="gemini-2.5-flash")
    fake_resolver = _resolver_returning("env-fallback-key")
    fake_provider = MagicMock(initialize=AsyncMock())
    fake_factory = MagicMock(create=MagicMock(return_value=fake_provider))

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=fake_resolver,
    ), patch(
        "app.infrastructure.llm.factory.LLMFactory", fake_factory,
    ):
        await orch._create_llm_provider(cfg)

    call_kwargs = fake_resolver.resolve.await_args.kwargs
    assert call_kwargs["tenant_id"] is None
    assert call_kwargs["env_var"] == "GEMINI_API_KEY"


# ──────────────────────────────────────────────────────────────────────────
# TTS provider construction
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tts_cartesia_uses_resolver_with_tenant(orch):
    cfg = VoiceSessionConfig(
        tts_provider_type="cartesia",
        tts_model="sonic-3",
        voice_id="voice-1",
        tenant_id="tenant-B",
    )
    fake_resolver = _resolver_returning("cartesia-tenant-key")
    fake_provider_cls = MagicMock()
    fake_instance = MagicMock(initialize=AsyncMock())
    fake_provider_cls.return_value = fake_instance

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=fake_resolver,
    ), patch(
        "app.infrastructure.tts.cartesia.CartesiaTTSProvider", fake_provider_cls,
    ):
        await orch._create_tts_provider(cfg)

    call_kwargs = fake_resolver.resolve.await_args.kwargs
    assert call_kwargs["tenant_id"] == "tenant-B"
    init_kwargs = fake_instance.initialize.await_args.args[0]
    assert init_kwargs["api_key"] == "cartesia-tenant-key"


@pytest.mark.asyncio
async def test_tts_elevenlabs_uses_resolver(orch):
    cfg = VoiceSessionConfig(
        tts_provider_type="elevenlabs",
        voice_id="voice-1",
        tenant_id="tenant-C",
    )
    fake_resolver = _resolver_returning("eleven-tenant-key")
    fake_instance = MagicMock(initialize=AsyncMock())
    fake_provider_cls = MagicMock(return_value=fake_instance)

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=fake_resolver,
    ), patch(
        "app.infrastructure.tts.elevenlabs_tts.ElevenLabsTTSProvider",
        fake_provider_cls,
    ):
        await orch._create_tts_provider(cfg)

    call_args = fake_resolver.resolve.await_args
    assert call_args.args[0] == "elevenlabs"
    assert call_args.kwargs["tenant_id"] == "tenant-C"


# ──────────────────────────────────────────────────────────────────────────
# STT provider construction
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stt_uses_resolver_with_tenant(orch):
    cfg = VoiceSessionConfig(tenant_id="tenant-D")
    fake_resolver = _resolver_returning("dg-tenant-key")
    fake_instance = MagicMock(initialize=AsyncMock())
    fake_provider_cls = MagicMock(return_value=fake_instance)

    with patch(
        "app.domain.services.credential_resolver.get_credential_resolver",
        return_value=fake_resolver,
    ), patch(
        "app.infrastructure.stt.deepgram_flux.DeepgramFluxSTTProvider",
        fake_provider_cls,
    ):
        await orch._create_stt_provider(cfg)

    call_args = fake_resolver.resolve.await_args
    assert call_args.args[0] == "deepgram"
    assert call_args.kwargs["tenant_id"] == "tenant-D"


# ──────────────────────────────────────────────────────────────────────────
# VoiceSessionConfig field surface
# ──────────────────────────────────────────────────────────────────────────

def test_voice_session_config_tenant_id_defaults_to_none():
    cfg = VoiceSessionConfig()
    assert cfg.tenant_id is None


def test_voice_session_config_accepts_tenant_id():
    cfg = VoiceSessionConfig(tenant_id="t-123")
    assert cfg.tenant_id == "t-123"


# ──────────────────────────────────────────────────────────────────────────
# Telephony session config plumbs tenant through
# ──────────────────────────────────────────────────────────────────────────

def test_telephony_session_config_propagates_tenant_id():
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )
    cfg = build_telephony_session_config(
        campaign={
            "id": "camp-1",
            "tenant_id": "tenant-XYZ",
            "script_config": {
                "persona_type": "lead_gen",
                "company_name": "Acme",
                "agent_names": ["Alex"],
                "campaign_slots": {
                    "industry": "roofing",
                    "services_description": "roofs",
                    "pricing_info": "free",
                    "coverage_area": "Austin",
                    "company_differentiator": "10yr",
                    "value_proposition": "no upfront cost",
                    "call_reason": "local homes",
                    "qualification_questions": ["Own?"],
                    "disqualifying_answers": ["renting"],
                    "calendar_booking_type": "free estimate",
                },
            },
        },
        agent_name_override="Alex",
    )
    assert cfg.tenant_id == "tenant-XYZ"
    assert cfg.campaign_id == "camp-1"


def test_telephony_session_config_no_campaign_yields_no_tenant():
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )
    cfg = build_telephony_session_config()
    assert cfg.tenant_id is None
    assert cfg.campaign_id == "telephony"

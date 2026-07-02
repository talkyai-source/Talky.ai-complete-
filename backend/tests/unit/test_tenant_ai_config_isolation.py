"""Per-tenant AI-config isolation tests (cross-tenant model-bleed fix).

The defect: per-call provider SELECTION (LLM model/provider/temperature/
max-tokens, STT engine, TTS, pipeline mode, realtime) was sourced from a
process-wide singleton. Tenant B saving/viewing AI Options overwrote what
tenant A's LIVE call read → cross-tenant model bleed.

The fix: each call sources its selection from the tenant's OWN persisted row
(tenant_ai_configs) via ``TenantAIConfigResolver``, threaded into the sync
``build_telephony_session_config`` as ``ai_config_override``. The process-global
is now only an immutable default for genuinely tenant-less paths.

These tests lock:
  (a) cross-tenant isolation — tenant A's config wins even when a DIFFERENT
      tenant's config is the process-global "last set";
  (b) the per-campaign script_config override still wins over the tenant config;
  (c) fallback to the process default when a tenant has no saved config;
  (d) the twilio/vonage bridges DERIVE the LLM provider from the resolved config
      instead of hardcoding "groq".
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.tenant_ai_config_resolver import (
    TenantAIConfigResolver,
    resolve_ai_config_for_did,
)


def _cfg(**overrides) -> AIProviderConfig:
    """A REAL AIProviderConfig (not a MagicMock) so getattr defaults resolve."""
    return AIProviderConfig(**overrides)


# ---------------------------------------------------------------------
# (a) cross-tenant isolation
# ---------------------------------------------------------------------


class TestCrossTenantIsolation:
    def test_session_uses_As_config_even_when_B_is_the_process_global(self):
        """The whole bug in one test: tenant A's live call must use A's saved
        model even though tenant B's config is what's sitting in the mutable
        process-global (get_global_config)."""
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )

        tenant_a = _cfg(
            llm_provider="gemini",
            llm_model="gemini-2.5-flash",
            llm_temperature=0.9,
            stt_engine="deepgram_nova",
        )
        # Tenant B "was the last one to save/view AI Options" — this is what the
        # process-global returns. It MUST NOT leak into A's call.
        tenant_b = _cfg(
            llm_provider="groq",
            llm_model="llama-3.3-70b-versatile",
            llm_temperature=0.2,
            stt_engine="deepgram_flux",
        )
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=tenant_b,
        ):
            config = build_telephony_session_config(
                gateway_type="telephony",
                ai_config_override=tenant_a,
            )
        # A's selection wins across every provider-selection field.
        assert config.llm_provider_type == "gemini"
        assert config.llm_model == "gemini-2.5-flash"
        assert config.llm_temperature == 0.9
        # Nova engine, not Flux → nova-3 primary.
        assert config.stt_provider_type == "deepgram_nova"
        assert config.stt_model == "nova-3"

    @pytest.mark.asyncio
    async def test_resolver_returns_tenant_row_regardless_of_default(self):
        """The resolver keys strictly on tenant_id — the process default is
        irrelevant when a per-tenant row exists."""
        resolver = TenantAIConfigResolver()
        tenant_a = _cfg(llm_provider="gemini", llm_model="gemini-2.5-flash")
        resolver.set_db_lookup(AsyncMock(return_value=tenant_a))

        # Even if the process default is something else entirely.
        with patch(
            "app.domain.services.global_ai_config.get_global_config",
            return_value=_cfg(llm_provider="groq", llm_model="llama-3.1-8b-instant"),
        ):
            resolved = await resolver.for_tenant_async("tenant-A")
        assert resolved.llm_provider == "gemini"
        assert resolved.llm_model == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_two_tenants_resolve_independently(self):
        """Interleaved resolution of two tenants never crosses wires."""
        resolver = TenantAIConfigResolver()
        rows = {
            "A": _cfg(llm_provider="gemini", llm_model="gemini-2.5-flash"),
            "B": _cfg(llm_provider="groq", llm_model="llama-3.1-8b-instant"),
        }

        async def _lookup(tid):
            return rows[tid]

        resolver.set_db_lookup(_lookup)
        a = await resolver.for_tenant_async("A")
        b = await resolver.for_tenant_async("B")
        a2 = await resolver.for_tenant_async("A")
        assert a.llm_model == "gemini-2.5-flash"
        assert b.llm_model == "llama-3.1-8b-instant"
        assert a2.llm_model == "gemini-2.5-flash"


# ---------------------------------------------------------------------
# (b) per-campaign script_config override still wins
# ---------------------------------------------------------------------


class TestPerCampaignOverrideStillWins:
    def test_campaign_pipeline_mode_beats_tenant_config(self):
        """A cascaded tenant can still run ONE campaign on realtime via the
        campaign's script_config — the per-campaign override outranks the
        tenant's AI-config default."""
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        tenant = _cfg(pipeline_mode="cascaded")  # tenant default: cascaded
        campaign = {
            "id": "realtime-campaign",
            "script_config": {
                "company_name": "Acme",
                "agent_names": ["Alex"],
                "pipeline_mode": "realtime",
                "realtime_voice": "sage",
            },
        }
        config = build_telephony_session_config(
            campaign=campaign, ai_config_override=tenant,
        )
        assert config.pipeline_mode == "realtime"
        assert config.realtime_voice == "sage"

    def test_campaign_voice_still_overrides_tenant_tts(self):
        """Per-campaign TTS voice/provider override remains intact on top of the
        threaded tenant config."""
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        tenant = _cfg(tts_provider="deepgram", tts_voice_id="aura-zeus-en")
        campaign = {
            "id": "c1",
            "script_config": {"company_name": "Acme", "agent_names": ["Alex"]},
            "tts_provider": "cartesia",
            "voice_id": "some-cartesia-voice",
        }
        config = build_telephony_session_config(
            campaign=campaign, ai_config_override=tenant,
        )
        assert config.tts_provider_type == "cartesia"
        assert config.voice_id == "some-cartesia-voice"
        # Different engine than tenant default → tts_model blanked for adapter default.
        assert config.tts_model == ""


# ---------------------------------------------------------------------
# (c) fallback to default when a tenant has no saved config
# ---------------------------------------------------------------------


class TestFallbackToDefault:
    @pytest.mark.asyncio
    async def test_no_row_falls_back_to_process_default(self):
        resolver = TenantAIConfigResolver()
        resolver.set_db_lookup(AsyncMock(return_value=None))  # no row for tenant
        default = _cfg(llm_provider="groq", llm_model="llama-3.1-8b-instant")
        with patch(
            "app.domain.services.global_ai_config.get_global_config",
            return_value=default,
        ):
            resolved = await resolver.for_tenant_async("tenant-with-no-row")
        assert resolved.llm_model == "llama-3.1-8b-instant"

    @pytest.mark.asyncio
    async def test_none_tenant_id_uses_default_without_db_hit(self):
        resolver = TenantAIConfigResolver()
        lookup = AsyncMock(return_value=_cfg(llm_model="should-not-be-used"))
        resolver.set_db_lookup(lookup)
        resolved = await resolver.for_tenant_async(None)
        # Tenant-less path never queries the DB.
        lookup.assert_not_called()
        assert isinstance(resolved, AIProviderConfig)

    @pytest.mark.asyncio
    async def test_lookup_error_falls_back_to_default_never_raises(self):
        resolver = TenantAIConfigResolver()
        resolver.set_db_lookup(AsyncMock(side_effect=RuntimeError("db down")))
        default = _cfg(llm_model="llama-3.1-8b-instant")
        with patch(
            "app.domain.services.global_ai_config.get_global_config",
            return_value=default,
        ):
            resolved = await resolver.for_tenant_async("tenant-A")
        assert resolved.llm_model == "llama-3.1-8b-instant"

    def test_build_config_without_override_uses_process_default(self):
        """Backward-compat: a sync caller passing no override (tests, Ask AI,
        browser) still gets today's behaviour off get_global_config."""
        from app.domain.services.telephony_session_config import (
            build_telephony_session_config,
        )
        default = _cfg(llm_provider="groq", llm_model="llama-3.3-70b-versatile")
        with patch(
            "app.domain.services.telephony_session_config.get_global_config",
            return_value=default,
        ):
            config = build_telephony_session_config(gateway_type="telephony")
        assert config.llm_model == "llama-3.3-70b-versatile"
        assert config.llm_provider_type == "groq"


# ---------------------------------------------------------------------
# (d) twilio / vonage bridges derive provider from config
# ---------------------------------------------------------------------


class TestBridgeProviderDerivation:
    @pytest.mark.asyncio
    async def test_twilio_derives_gemini_provider_not_hardcoded_groq(self):
        from app.api.v1.endpoints import twilio_bridge
        gemini = _cfg(llm_provider="gemini", llm_model="gemini-2.5-flash")
        with patch(
            "app.domain.services.tenant_ai_config_resolver.resolve_ai_config_for_did",
            AsyncMock(return_value=("tenant-A", gemini)),
        ):
            config = await twilio_bridge._build_twilio_session_config("+15551230000")
        # The old bug: llm_provider_type hardcoded "groq" while llm_model is a
        # gemini id → 404 every turn. Now derived from the resolved config.
        assert config.llm_provider_type == "gemini"
        assert config.llm_model == "gemini-2.5-flash"
        assert config.tenant_id == "tenant-A"

    @pytest.mark.asyncio
    async def test_vonage_derives_gemini_provider_not_hardcoded_groq(self):
        from app.api.v1.endpoints import vonage_bridge
        gemini = _cfg(llm_provider="gemini", llm_model="gemini-2.5-flash")
        with patch(
            "app.domain.services.tenant_ai_config_resolver.resolve_ai_config_for_did",
            AsyncMock(return_value=("tenant-B", gemini)),
        ):
            config = await vonage_bridge._build_vonage_session_config("+15559990000")
        assert config.llm_provider_type == "gemini"
        assert config.llm_model == "gemini-2.5-flash"
        assert config.tenant_id == "tenant-B"

    @pytest.mark.asyncio
    async def test_bridge_groq_config_still_yields_groq(self):
        """Sanity: a groq tenant still routes to groq (no regression)."""
        from app.api.v1.endpoints import twilio_bridge
        groq = _cfg(llm_provider="groq", llm_model="llama-3.1-8b-instant")
        with patch(
            "app.domain.services.tenant_ai_config_resolver.resolve_ai_config_for_did",
            AsyncMock(return_value=(None, groq)),
        ):
            config = await twilio_bridge._build_twilio_session_config(None)
        assert config.llm_provider_type == "groq"
        assert config.llm_model == "llama-3.1-8b-instant"
        assert config.tenant_id is None

    @pytest.mark.asyncio
    async def test_resolve_ai_config_for_did_none_did_uses_default(self):
        """No DID → no tenant → process default, no DB/route call."""
        with patch(
            "app.domain.services.tenant_ai_config_resolver."
            "get_tenant_ai_config_resolver"
        ) as _get_resolver:
            resolver = _get_resolver.return_value
            resolver.for_tenant_async = AsyncMock(return_value=_cfg())
            tenant_id, config = await resolve_ai_config_for_did(None)
        assert tenant_id is None
        resolver.for_tenant_async.assert_awaited_once_with(None)

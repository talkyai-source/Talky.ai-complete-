"""Offline endpoint regressions: viewing must not replace a saved profile."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints.ai_options import config as endpoint
from app.domain.models.ai_config import AIProviderConfig


class Storage:
    """SQL argument capture, not a database/RLS substitute."""
    async def execute(self, sql, *args):
        fields = [part.strip() for part in sql.split("INSERT INTO tenant_ai_configs (")[1].split(")")[0].split(",")]
        self.row = dict(zip(fields, args, strict=True))

    async def fetchrow(self, sql, tenant_id):
        assert tenant_id == self.row["tenant_id"]
        return self.row


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model,catalog", [
    ("deepgram", "aura-2", "_get_deepgram_voices_for_current_key"),
    ("cartesia", "sonic-3", "_get_live_cartesia_voices"),
    ("elevenlabs", "eleven_flash_v2_5", "get_elevenlabs_voices_for_current_key"),
])
async def test_viewing_saved_profile_does_not_replace_missing_catalog_voice(monkeypatch, provider, model, catalog):
    saved = AIProviderConfig(tts_provider=provider, tts_model=model,
                             tts_voice_id="saved-voice", tts_sample_rate=16000,
                             voice_tuning={"stt_eager_eot_threshold": None})
    expected = saved.model_dump()

    @asynccontextmanager
    async def acquire(*args):
        yield object()

    live_catalog = AsyncMock(return_value=[SimpleNamespace(id="different-voice")])
    persist = AsyncMock()
    monkeypatch.setattr(endpoint, "acquire_with_tenant", acquire)
    monkeypatch.setattr(endpoint, "_fetch_tenant_config", AsyncMock(return_value=saved))
    monkeypatch.setattr(endpoint, "_upsert_tenant_config", persist)
    monkeypatch.setattr(endpoint, catalog, live_catalog)
    monkeypatch.setattr(endpoint, "get_elevenlabs_tts_models_for_current_key", AsyncMock(return_value=[]))

    actual = await endpoint.get_config(SimpleNamespace(tenant_id="tenant-a"), SimpleNamespace(pool=object()))
    assert actual.model_dump() == expected
    persist.assert_not_awaited()
    live_catalog.assert_not_awaited()


@pytest.mark.asyncio
async def test_persisted_tuning_matches_returned_and_reloaded_profile():
    from app.api.v1.endpoints.ai_options._shared import _fetch_tenant_config, _upsert_tenant_config

    storage = Storage()
    saved = AIProviderConfig(voice_tuning={"stt_eot_threshold": "0.83",
        "stt_eager_eot_threshold": None, "stt_eot_timeout_ms": "900", "unknown": 7})
    await _upsert_tenant_config(storage, "tenant-a", saved)
    reloaded = await _fetch_tenant_config(storage, "tenant-a")
    assert saved.model_dump() == reloaded.model_dump()
    assert saved.voice_tuning["stt_eager_eot_threshold"] is None
    assert saved.voice_tuning["stt_eot_threshold"] == 0.83
    assert saved.voice_tuning["stt_eot_timeout_ms"] == 900
    assert "unknown" not in saved.voice_tuning


@pytest.mark.asyncio
@pytest.mark.parametrize("llm_provider,llm_model", [
    ("cerebras", "gpt-oss-120b"), ("groq", "openai/gpt-oss-20b"),
    ("openai", "gpt-6-luna"), ("gemini", "gemini-3.8-flash"),
])
@pytest.mark.parametrize("tts_provider,tts_model", [
    ("deepgram", "aura-2"), ("cartesia", "sonic-3"),
    ("elevenlabs", "eleven_flash_v2_5"), ("google", "Chirp3-HD"),
])
async def test_offered_profiles_save_reload_and_assign_to_campaign(monkeypatch, llm_provider, llm_model, tts_provider, tts_model):
    from app.api.v1.endpoints import ai_options
    from app.api.v1.endpoints.ai_options import _catalog
    from app.api.v1.endpoints.campaign_voice_config import build_campaign_voice_config
    from app.api.v1.schemas.campaigns import CampaignCreateRequest
    from app.domain.services import credential_resolver, voice_clone_service
    from app.domain.services.telephony_session_config import build_telephony_session_config
    from app.domain.services.voice_tuning import VoiceTuning

    # Fake only external catalogs, credentials and SQL transport. Exercise actual
    # save validation, serialization, reload, campaign assignment and builder.
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic")
    monkeypatch.setenv("CARTESIA_API_KEY", "synthetic")
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=AsyncMock(return_value="synthetic")))
    monkeypatch.setattr(voice_clone_service, "owned_voice_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(voice_clone_service, "all_platform_voice_ids", AsyncMock(return_value=set()))
    voice_id = "aura-2-thalia-en" if tts_provider == "deepgram" else "synthetic-voice"
    voices = [SimpleNamespace(id=voice_id, provider=tts_provider, provider_is_public=True)]
    for module in (endpoint, ai_options, _catalog):
        for name in ("_get_deepgram_voices_for_current_key", "_get_live_cartesia_voices", "get_elevenlabs_voices_for_current_key"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, AsyncMock(return_value=voices))
        if hasattr(module, "_english_google_voices"):
            monkeypatch.setattr(module, "_english_google_voices", lambda: voices)
    monkeypatch.setattr(endpoint, "get_elevenlabs_tts_models_for_current_key", AsyncMock(return_value=[SimpleNamespace(id=tts_model)]))
    storage = Storage()
    @asynccontextmanager
    async def acquire(*args):
        yield storage
    monkeypatch.setattr(endpoint, "acquire_with_tenant", acquire)
    user = SimpleNamespace(tenant_id="synthetic-tenant")
    db = SimpleNamespace(pool=object())
    selected = AIProviderConfig(llm_provider=llm_provider, llm_model=llm_model,
        tts_provider=tts_provider, tts_model=tts_model, tts_voice_id=voice_id,
        tts_sample_rate=24000 if tts_provider == "google" else 16000,
        llm_temperature=.4, llm_max_tokens=120,
        voice_tuning={"stt_eot_timeout_ms": 900, "stt_eager_eot_threshold": None})
    expected = selected.model_dump()
    saved = await endpoint.save_config(selected, user, db)
    reloaded = await endpoint.get_config(user, db)
    assert saved.config.model_dump() == reloaded.model_dump() == expected
    request = CampaignCreateRequest(name="Synthetic", company_name="Acme", agent_names=["Alex"],
        persona_type="lead_gen", knowledge_driven=True, tts_provider=tts_provider, voice_id=voice_id)
    script, assigned_voice = await build_campaign_voice_config(request, reloaded, pool=db.pool, tenant_id=user.tenant_id)
    campaign = {"id": "synthetic-campaign", "tenant_id": user.tenant_id,
        "script_config": script, "tts_provider": tts_provider, "voice_id": assigned_voice}
    runtime = build_telephony_session_config(campaign=campaign, ai_config_override=reloaded,
        voice_tuning_override=VoiceTuning(**reloaded.voice_tuning))
    assert (runtime.llm_provider_type, runtime.llm_model) == (llm_provider, llm_model)
    assert (runtime.tts_provider_type, runtime.tts_model, runtime.voice_id) == (tts_provider, tts_model, voice_id)
    assert runtime.llm_temperature == .4
    assert runtime.llm_max_tokens == 120
    assert runtime.stt_eot_timeout_ms == 900
    assert runtime.stt_eager_eot_threshold is None
    assert runtime.tts_sample_rate == 16000  # documented SIP transport conversion

"""AssemblyAI controls survive tenant save/read and never resolve Deepgram keys."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints.ai_options import config as endpoint
from app.api.v1.endpoints.ai_options import providers
from app.api.v1.endpoints.ai_options._shared import _fetch_tenant_config, _upsert_tenant_config
from app.domain.models.ai_config import AIProviderConfig
from app.domain.services import credential_resolver


class Storage:
    """Exercise real serializers with a tenant-indexed SQL transport double."""
    def __init__(self):
        self.rows = {}

    async def execute(self, sql, *args):
        fields = [part.strip() for part in sql.split("INSERT INTO tenant_ai_configs (")[1].split(")")[0].split(",")]
        self.rows[args[0]] = dict(zip(fields, args, strict=True))

    async def fetchrow(self, sql, tenant_id):
        assert "assemblyai_settings" in sql
        return self.rows.get(tenant_id)


def selected(**kwargs):
    return AIProviderConfig(stt_engine="assemblyai", **kwargs)


@pytest.mark.parametrize("engine", ["assemblyai", "AssemblyAI", " ASSEMBLYAI "])
def test_engine_canonicalizes_model_and_provider_without_affecting_flux(engine):
    config = AIProviderConfig(stt_engine=engine)
    assert config.stt_engine == "assemblyai"
    assert (config.stt_provider, config.stt_model, config.stt_language) == (
        "assemblyai", "universal-3-6-pro", "en",
    )
    assert config.assemblyai_settings.mode == "balanced"
    assert config.assemblyai_settings.min_turn_silence is None
    assert AIProviderConfig().stt_provider == "deepgram"
    assert AIProviderConfig().assemblyai_settings is None


@pytest.mark.parametrize("engine", ["deepgram_flux", "deepgram_nova", "deepgram-nova", "nova", "nova-3"])
def test_engine_normalization_preserves_established_deepgram_aliases(engine):
    from app.domain.services.telephony_session_config import resolve_stt_selection

    normalized = AIProviderConfig(stt_engine=f" {engine.upper()} ")
    assert normalized.stt_engine == engine
    assert normalized.stt_provider == "deepgram"
    assert normalized.assemblyai_settings is None
    assert resolve_stt_selection(normalized) == resolve_stt_selection(AIProviderConfig(stt_engine=engine))


@pytest.mark.parametrize("language", ["es", "multi", "", "en-US"])
@pytest.mark.parametrize("engine", ["assemblyai", " AssemblyAI "])
def test_non_english_selection_is_rejected(language, engine):
    with pytest.raises(ValidationError, match="English only"):
        AIProviderConfig(stt_engine=engine, stt_language=language)


@pytest.mark.parametrize("mode", ["balanced", "min_latency", "max_accuracy"])
@pytest.mark.parametrize("json_codec", [False, True])
async def test_all_modes_and_advanced_controls_roundtrip_per_tenant(mode, json_codec):
    storage = Storage()
    config = selected(assemblyai_settings={
        "region": "eu", "mode": mode, "min_turn_silence": 700, "max_turn_silence": 1900,
        "interruption_delay": 200, "vad_threshold": .4, "include_partial_turns": None,
        "prompt": "English booking call.", "keyterms_prompt": ["Talky", "Alyssa"],
        "auto_agent_context": False, "previous_context_n_turns": 7,
        "language_detection": True, "voice_focus": "near-field", "voice_focus_threshold": .6,
        "domain": "medical-v1", "speaker_labels": True, "max_speakers": 2,
        "speaker_labels_revision_interval_ms": 300000, "redact_pii": True,
        "redact_pii_policies": ["credit_card_number"], "redact_pii_sub": "entity_name",
        "filter_profanity": True, "session_heartbeat": True, "inactivity_timeout": 30,
    })
    await _upsert_tenant_config(storage, "A", config)
    await _upsert_tenant_config(storage, "B", AIProviderConfig())
    if json_codec:
        storage.rows["A"]["assemblyai_settings"] = json.loads(storage.rows["A"]["assemblyai_settings"])
    assert (await _fetch_tenant_config(storage, "A")).model_dump() == config.model_dump()
    assert (await _fetch_tenant_config(storage, "B")).assemblyai_settings is None
    # Switching engines keeps AssemblyAI preferences for the next switch back.
    flux = AIProviderConfig(assemblyai_settings=config.assemblyai_settings)
    await _upsert_tenant_config(storage, "A", flux)
    assert (await _fetch_tenant_config(storage, "A")).model_dump() == flux.model_dump()


async def test_corrupt_saved_settings_are_not_silently_replaced():
    storage = Storage()
    await _upsert_tenant_config(storage, "A", selected())
    storage.rows["A"]["assemblyai_settings"] = '{"mode":"fake"}'
    with pytest.raises(ValidationError):
        await _fetch_tenant_config(storage, "A")


@pytest.mark.parametrize("engine", ["assemblyai", "AssemblyAI", " assemblyai "])
async def test_save_requires_assemblyai_key_for_authenticated_tenant(monkeypatch, engine):
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=resolve))
    persisted = AsyncMock()
    monkeypatch.setattr(endpoint, "_upsert_tenant_config", persisted)
    with pytest.raises(HTTPException) as caught:
        await endpoint.save_config(AIProviderConfig(stt_engine=engine), SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=None))
    assert caught.value.status_code == 503
    assert "AssemblyAI API key" in caught.value.detail
    resolve.assert_awaited_once_with("assemblyai", tenant_id="A")
    persisted.assert_not_awaited()


@pytest.mark.parametrize("engine", ["assemblyai", " AssemblyAI "])
async def test_saved_endpoint_result_matches_reload_with_assemblyai_credentials(monkeypatch, engine):
    resolve = AsyncMock(return_value="synthetic-key")
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=resolve))
    monkeypatch.setattr(endpoint, "_get_deepgram_voices_for_current_key", AsyncMock(return_value=[SimpleNamespace(id="aura-2-zeus-en")]))
    storage = Storage()

    @asynccontextmanager
    async def acquire(*args):
        yield storage

    monkeypatch.setattr(endpoint, "acquire_with_tenant", acquire)
    user, db = SimpleNamespace(tenant_id="A"), SimpleNamespace(pool=object())
    config = AIProviderConfig(stt_engine=engine, tts_voice_id="aura-2-zeus-en", assemblyai_settings={"mode": "max_accuracy", "keyterms_prompt": ["Talky"]})
    saved = await endpoint.save_config(config, user, db)
    reloaded = await endpoint.get_config(user, db)
    assert saved.config.model_dump() == reloaded.model_dump() == config.model_dump()
    assert (reloaded.stt_engine, reloaded.stt_provider, reloaded.stt_model) == (
        "assemblyai", "assemblyai", "universal-3-6-pro",
    )
    resolve.assert_awaited_once_with("assemblyai", tenant_id="A")


@pytest.mark.parametrize("has_key", [False, True])
async def test_catalog_keeps_flux_nova_and_reports_assemblyai_availability(monkeypatch, has_key):
    from app.realtime import credentials
    monkeypatch.setattr(credentials, "resolve_openai_key", AsyncMock(return_value=None))
    monkeypatch.setattr(providers, "elevenlabs_enabled", lambda: False)
    resolve = AsyncMock(return_value="synthetic-key" if has_key else None)
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=resolve))
    catalog = await providers.list_providers(SimpleNamespace(tenant_id="A"))
    engines = {item["id"]: item for item in catalog.stt["engines"]}
    assert set(engines) == {"deepgram_flux", "assemblyai", "deepgram_nova"}
    assert engines["assemblyai"]["available"] is has_key
    assert bool(engines["assemblyai"]["unavailable_reason"]) is not has_key
    resolve.assert_awaited_once_with("assemblyai", tenant_id="A")


def test_environment_key_uses_assemblyai_name(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", " synthetic-assemblyai ")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "different-key")
    assert credential_resolver.resolve_sync_env_only("assemblyai") == "synthetic-assemblyai"

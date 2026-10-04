"""Tenant clone authorization must precede caches and provider initialization."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.domain.models.ai_config import VoiceInfo
from app.domain.services import voice_clone_service as ownership


TENANT = "00000000-0000-4000-8000-00000000000a"
USER = SimpleNamespace(tenant_id=TENANT)
DB = SimpleNamespace(pool=object())


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(ownership, "owned_voice_ids", AsyncMock(return_value={"my-clone"}))
    monkeypatch.setattr(ownership, "all_platform_voice_ids", AsyncMock(return_value={"my-clone", "foreign-clone"}))
    voices = [VoiceInfo(id=value, name=value, provider="elevenlabs", language="en", gender="female", provider_is_public=value == "stock-voice")
              for value in ("stock-voice", "my-clone", "foreign-clone", "orphan-private")]
    from app.api.v1.endpoints.ai_options import preview, testing, providers
    from app.infrastructure.tts import elevenlabs_catalog
    for module in (preview, testing):
        monkeypatch.setattr(module, "_get_live_cartesia_voices", AsyncMock(return_value=[]))
        monkeypatch.setattr(module, "_get_deepgram_voices_for_current_key", AsyncMock(return_value=[]))
        monkeypatch.setattr(module, "_find_elevenlabs_voice", AsyncMock(side_effect=lambda voice_id: next((v for v in voices if v.id == voice_id), None)))
    monkeypatch.setattr(providers, "_find_elevenlabs_voice", AsyncMock(side_effect=lambda voice_id: next((v for v in voices if v.id == voice_id), None)))
    monkeypatch.setattr(elevenlabs_catalog, "get_elevenlabs_voices_for_current_key", AsyncMock(return_value=voices))
    return voices



@pytest.mark.asyncio
async def test_catalog_filters_foreign_clone_but_preserves_owned_and_stock(monkeypatch, catalog):
    from app.api.v1.endpoints.ai_options import providers
    monkeypatch.setattr(providers, "_get_all_tts_voices", AsyncMock(return_value=catalog))
    result = await providers.list_voices(USER, DB)
    assert [voice["id"] for voice in result["voices"]] == ["stock-voice", "my-clone"]


@pytest.mark.asyncio
async def test_catalog_ownership_failure_never_returns_private_catalog(monkeypatch, catalog):
    from app.api.v1.endpoints.ai_options import providers
    monkeypatch.setattr(providers, "_get_all_tts_voices", AsyncMock(return_value=catalog))
    monkeypatch.setattr(ownership, "all_platform_voice_ids", AsyncMock(side_effect=RuntimeError("unavailable")))
    with pytest.raises(HTTPException) as exc:
        await providers.list_voices(USER, DB)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_cached_preview_denies_foreign_voice_before_cache_read(monkeypatch, catalog):
    from app.api.v1.endpoints.ai_options import preview
    cached = Mock(return_value=b"\0" * 16)
    monkeypatch.setattr(preview, "_load_preview_cache", cached)
    with pytest.raises(HTTPException) as exc:
        await preview.preview_voice(preview.VoicePreviewRequest(voice_id="foreign-clone"), USER, DB)
    assert exc.value.status_code == 403
    cached.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("voice_id", ["stock-voice", "my-clone"])
async def test_cached_preview_keeps_allowed_voices(monkeypatch, catalog, voice_id):
    from app.api.v1.endpoints.ai_options import preview
    monkeypatch.setattr(preview, "_load_preview_cache", Mock(return_value=b"\0" * 16))
    assert (await preview.preview_voice(preview.VoicePreviewRequest(voice_id=voice_id), USER, DB)).voice_id == voice_id


@pytest.mark.asyncio
async def test_sample_cannot_bypass_ownership(monkeypatch, catalog):
    from app.api.v1.endpoints.ai_options import providers
    download = AsyncMock()
    monkeypatch.setattr(providers, "ensure_elevenlabs_preview_cached", download)
    with pytest.raises(HTTPException) as exc:
        await providers.get_voice_sample("foreign-clone", USER, DB)
    assert exc.value.status_code == 403
    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_campaign_validation_is_tenant_scoped(monkeypatch, catalog):
    from app.api.v1.endpoints import ai_options, campaigns
    monkeypatch.setattr(ai_options, "get_elevenlabs_voices_for_current_key", AsyncMock(return_value=catalog))
    assert await campaigns._valid_voice_ids_for_provider("elevenlabs", pool=DB.pool, tenant_id=TENANT) == {"stock-voice", "my-clone"}


@pytest.mark.asyncio
async def test_runtime_denies_saved_foreign_voice_before_initializing_provider(monkeypatch, catalog):
    from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig
    from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider
    init = AsyncMock()
    monkeypatch.setattr(ElevenLabsTTSProvider, "initialize", init)
    with pytest.raises(ValueError, match="not available"):
        await VoiceOrchestrator(DB)._create_tts_provider(VoiceSessionConfig(
            tenant_id=TENANT, tts_provider_type="elevenlabs", voice_id="foreign-clone",
        ))
    init.assert_not_awaited()


@pytest.mark.asyncio
async def test_tts_test_denies_foreign_voice_before_provider_initialize(monkeypatch, catalog):
    from app.api.v1.endpoints.ai_options import testing
    from app.domain.models.ai_config import TTSTestRequest
    init = AsyncMock()
    monkeypatch.setattr(testing.ElevenLabsTTSProvider, "initialize", init)
    with pytest.raises(HTTPException) as exc:
        await testing.test_tts(TTSTestRequest(model="eleven_flash_v2_5", voice_id="foreign-clone", text="Synthetic sample"), USER, DB)
    assert exc.value.status_code == 403
    init.assert_not_awaited()


@pytest.mark.parametrize("claimed_provider", [None, "google", "cartesia", "deepgram"])
@pytest.mark.asyncio
async def test_orphan_private_voice_and_forged_provider_cannot_read_cache(monkeypatch, catalog, claimed_provider):
    from app.api.v1.endpoints.ai_options import preview
    cached = Mock(return_value=b"\0" * 16)
    monkeypatch.setattr(preview, "_load_preview_cache", cached)
    with pytest.raises(HTTPException) as exc:
        await preview.preview_voice(preview.VoicePreviewRequest(voice_id="orphan-private", provider=claimed_provider), USER, DB)
    assert exc.value.status_code == 403
    cached.assert_not_called()


@pytest.mark.parametrize("provider,voice_id", [("google", "google-stock"), ("cartesia", "cartesia-stock"), ("deepgram", "aura-stock")])
@pytest.mark.asyncio
async def test_non_elevenlabs_cached_preview_keeps_catalog_validated_stock(monkeypatch, catalog, provider, voice_id):
    from app.api.v1.endpoints.ai_options import preview
    voice = VoiceInfo(id=voice_id, name=voice_id, provider=provider)
    monkeypatch.setattr(preview, "_load_preview_cache", Mock(return_value=b"\0" * 16))
    monkeypatch.setattr(preview, "_is_cartesia_voice", lambda value: provider == "cartesia" and value == voice_id)
    monkeypatch.setattr(preview, "_is_google_voice", lambda value: provider == "google" and value == voice_id)
    monkeypatch.setattr(preview, "_find_cartesia_voice", lambda value: voice if provider == "cartesia" else None)
    monkeypatch.setattr(preview, "_find_google_voice", lambda value: voice if provider == "google" else None)
    monkeypatch.setattr(preview, "_get_deepgram_voices_for_current_key", AsyncMock(return_value=[voice] if provider == "deepgram" else []))
    result = await preview.preview_voice(preview.VoicePreviewRequest(voice_id=voice_id), USER, DB)
    assert result.voice_id == voice_id


@pytest.mark.asyncio
async def test_native_preview_stays_separate(monkeypatch, catalog):
    from app.api.v1.endpoints.ai_options import preview
    from app.realtime import preview as native
    synth = AsyncMock(return_value={"voice_id": "marin", "voice_name": "marin", "audio_base64": "AAAA", "duration_seconds": .1, "latency_ms": 0})
    monkeypatch.setattr(native, "preview_voice", synth)
    result = await preview.preview_voice(preview.VoicePreviewRequest(voice_id="marin", provider="realtime"), USER, DB)
    assert result.voice_id == "marin"
    synth.assert_awaited_once()


@pytest.mark.parametrize("category,sharing,expected", [
    ("premade", None, True), ("professional", {"status": "enabled", "enabled_in_library": True}, True),
    ("cloned", None, False), ("professional", {"status": "enabled"}, False),
    (None, None, False),
])
def test_public_voice_authority_comes_from_provider_metadata_not_editable_labels(category, sharing, expected):
    from app.infrastructure.tts.elevenlabs_catalog import _normalize_elevenlabs_voice
    voice = _normalize_elevenlabs_voice({"voice_id": "synthetic", "category": category, "sharing": sharing, "labels": {"use_case": "premade"}})
    assert voice.provider_is_public is expected


@pytest.mark.asyncio
async def test_uncertain_provider_delete_retains_clone_ownership(monkeypatch):
    from app.api.v1.endpoints.ai_options import clone
    monkeypatch.setattr(ownership, "get_owned", AsyncMock(return_value={"voice_id": "my-clone"}))
    delete_row = AsyncMock()
    monkeypatch.setattr(ownership, "delete_owned", delete_row)
    monkeypatch.setattr(clone, "delete_elevenlabs_voice", AsyncMock(side_effect=clone.ElevenLabsCloneError("temporary failure")))
    with pytest.raises(HTTPException) as exc:
        await clone.delete_cloned_voice("synthetic-row", USER, DB)
    assert exc.value.status_code == 502
    delete_row.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefetch_excludes_foreign_and_unregistered_private_ids(monkeypatch, catalog):
    from app.api.v1.endpoints.ai_options import preview
    monkeypatch.setattr(preview, "_get_all_tts_voices", AsyncMock(return_value=catalog))
    cache = Mock(return_value=b"\0" * 16)
    monkeypatch.setattr(preview, "_load_preview_cache", cache)
    result = await preview.prefetch_all_voice_samples(USER, DB)
    assert result["skipped_already_cached"] == 2
    assert [call.args[0] for call in cache.call_args_list] == ["stock-voice", "my-clone"]

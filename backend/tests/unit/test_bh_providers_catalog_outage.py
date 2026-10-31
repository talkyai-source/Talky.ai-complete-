"""An ElevenLabs catalog outage must not be cached as an empty catalog, and
must not surface as a permission-style 403 'Voice is not available'."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services import voice_clone_service as ownership
from app.domain.services.voice_eligibility import VoiceEligibilityError
from app.infrastructure.tts import elevenlabs_catalog as cat

TENANT = "00000000-0000-4000-8000-00000000000a"
DB = SimpleNamespace(pool=object())

_PAYLOAD = {
    "voices": [
        {"voice_id": "stock-voice", "name": "Stock", "category": "premade",
         "labels": {"gender": "female"}},
    ]
}


@pytest.fixture
def fresh_catalog(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(cat, "_elevenlabs_voices_cache", None)
    monkeypatch.setattr(cat, "_elevenlabs_voices_cache_expires_at", 0.0)
    monkeypatch.setattr(cat, "_elevenlabs_voices_retry_after", 0.0, raising=False)
    monkeypatch.setattr(cat, "_elevenlabs_last_error", None)
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(cat.time, "time", lambda: clock["now"])
    return clock


@pytest.mark.asyncio
async def test_failed_fetch_is_not_cached_for_five_minutes(monkeypatch, fresh_catalog):
    fetch = AsyncMock(side_effect=RuntimeError("elevenlabs down"))
    monkeypatch.setattr(cat, "_fetch_json", fetch)
    assert await cat.get_elevenlabs_voices_for_current_key() == []

    # ElevenLabs recovers; the next lookup (well inside the old 300 s TTL)
    # must see the real catalog.
    fetch.side_effect = None
    fetch.return_value = _PAYLOAD
    fresh_catalog["now"] += 30
    voices = await cat.get_elevenlabs_voices_for_current_key()
    assert [v.id for v in voices] == ["stock-voice"]


@pytest.mark.asyncio
async def test_failed_refresh_keeps_last_good_catalog(monkeypatch, fresh_catalog):
    monkeypatch.setattr(cat, "_fetch_json", AsyncMock(return_value=_PAYLOAD))
    assert [v.id for v in await cat.get_elevenlabs_voices_for_current_key()] == ["stock-voice"]

    fresh_catalog["now"] += cat._ELEVENLABS_CACHE_TTL_SECONDS + 1
    monkeypatch.setattr(cat, "_fetch_json", AsyncMock(side_effect=RuntimeError("elevenlabs down")))
    assert [v.id for v in await cat.get_elevenlabs_voices_for_current_key()] == ["stock-voice"]


@pytest.mark.asyncio
async def test_unset_key_is_not_cached_as_healthy_empty(monkeypatch, fresh_catalog):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert await cat.get_elevenlabs_voices_for_current_key() == []
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(cat, "_fetch_json", AsyncMock(return_value=_PAYLOAD))
    assert [v.id for v in await cat.get_elevenlabs_voices_for_current_key()] == ["stock-voice"]


@pytest.mark.asyncio
async def test_orchestrator_maps_catalog_outage_to_503_not_403(monkeypatch, fresh_catalog):
    from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig
    from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider

    monkeypatch.setattr(ownership, "owned_voice_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(ownership, "all_platform_voice_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(cat, "_fetch_json", AsyncMock(side_effect=RuntimeError("elevenlabs down")))
    init = AsyncMock()
    monkeypatch.setattr(ElevenLabsTTSProvider, "initialize", init)

    with pytest.raises(VoiceEligibilityError) as exc:
        await VoiceOrchestrator(DB)._create_tts_provider(VoiceSessionConfig(
            tenant_id=TENANT, tts_provider_type="elevenlabs", voice_id="stock-voice",
        ))
    assert exc.value.code == "catalog_unavailable"
    assert exc.value.status_code == 503
    init.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestrator_still_denies_private_voice_when_catalog_is_healthy(monkeypatch, fresh_catalog):
    from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig

    monkeypatch.setattr(ownership, "owned_voice_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(ownership, "all_platform_voice_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(cat, "_fetch_json", AsyncMock(return_value=_PAYLOAD))
    with pytest.raises(VoiceEligibilityError) as exc:
        await VoiceOrchestrator(DB)._create_tts_provider(VoiceSessionConfig(
            tenant_id=TENANT, tts_provider_type="elevenlabs", voice_id="orphan-private",
        ))
    assert exc.value.code == "voice_not_available"
    assert exc.value.status_code == 403

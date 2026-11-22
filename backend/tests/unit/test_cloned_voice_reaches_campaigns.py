"""A tenant's own fresh clone is never "not available" to its owner, and each
call logs where its voice came from."""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.ai_config import AIProviderConfig, VoiceInfo
from app.domain.services import voice_clone_service as ownership
from app.infrastructure.tts import elevenlabs_catalog

TENANT = "00000000-0000-4000-8000-00000000000a"
POOL = object()


def _voice(voice_id, public=False):
    return VoiceInfo(id=voice_id, name=voice_id, provider="elevenlabs", language="en",
                     gender="female", provider_is_public=public)


@pytest.fixture
def stale_cache(monkeypatch):
    """This process's cached catalogue lacks the tenant's new clone; a forced
    refresh (the other process created it) would show it."""
    monkeypatch.setattr(ownership, "owned_voice_ids", AsyncMock(return_value={"new-clone"}))
    monkeypatch.setattr(ownership, "all_platform_voice_ids",
                        AsyncMock(return_value={"new-clone", "foreign-clone"}))
    stale = [_voice("stock", public=True)]
    fresh = [*stale, _voice("new-clone"), _voice("foreign-clone")]
    state = {"fresh": False}
    fetch = AsyncMock(side_effect=lambda: fresh if state["fresh"] else stale)
    monkeypatch.setattr(elevenlabs_catalog, "get_elevenlabs_voices_for_current_key", fetch)

    def _invalidate():
        state["fresh"] = True
    monkeypatch.setattr(elevenlabs_catalog, "invalidate_elevenlabs_voices_cache", _invalidate)
    monkeypatch.setattr(elevenlabs_catalog, "_missing_clone_last_refresh_at", -1e9)
    return SimpleNamespace(stale=stale, fetch=fetch)


@pytest.mark.asyncio
async def test_own_clone_missing_from_cache_is_listed_and_foreign_stays_hidden(stale_cache):
    from app.domain.services.voice_eligibility import filter_tenant_voices
    result = await filter_tenant_voices(POOL, TENANT, list(stale_cache.stale))
    assert [v.id for v in result] == ["stock", "new-clone"]


@pytest.mark.asyncio
async def test_own_clone_missing_from_cache_is_accepted_on_campaign_save(monkeypatch, stale_cache):
    from app.api.v1.endpoints import ai_options, campaigns
    monkeypatch.setattr(ai_options, "get_elevenlabs_voices_for_current_key", stale_cache.fetch)
    ids = await campaigns._valid_voice_ids_for_provider("elevenlabs", pool=POOL, tenant_id=TENANT)
    assert "new-clone" in ids
    assert "foreign-clone" not in ids


@pytest.mark.asyncio
async def test_refresh_is_bounded_so_a_deleted_clone_cannot_refresh_every_request(monkeypatch):
    monkeypatch.setattr(ownership, "owned_voice_ids", AsyncMock(return_value={"gone-at-provider"}))
    monkeypatch.setattr(ownership, "all_platform_voice_ids", AsyncMock(return_value={"gone-at-provider"}))
    fetch = AsyncMock(return_value=[_voice("stock", public=True)])
    monkeypatch.setattr(elevenlabs_catalog, "get_elevenlabs_voices_for_current_key", fetch)
    monkeypatch.setattr(elevenlabs_catalog, "invalidate_elevenlabs_voices_cache", lambda: None)
    monkeypatch.setattr(elevenlabs_catalog, "_missing_clone_last_refresh_at", -1e9)
    from app.domain.services.voice_eligibility import filter_tenant_voices
    for _ in range(3):
        await filter_tenant_voices(POOL, TENANT, [_voice("stock", public=True)])
    assert fetch.await_count == 1


def _campaign(voice_id=None, provider=None):
    return SimpleNamespace(id="camp-1", tenant_id=TENANT, voice_id=voice_id, tts_provider=provider,
                           script_config={})


@pytest.mark.parametrize("voice,source,shown", [
    ("campaignvoice-1234", "campaign", "campaign"),
    (None, "account", "accountv"),
])
def test_call_logs_where_the_voice_came_from(caplog, voice, source, shown):
    from app.domain.services.telephony_session_config import build_telephony_session_config
    ai = AIProviderConfig(tts_provider="elevenlabs", tts_model="eleven_flash_v2_5",
                          tts_voice_id="accountvoice-9999")
    with caplog.at_level(logging.INFO, logger="app.domain.services.telephony_session_config"):
        build_telephony_session_config(campaign=_campaign(voice, "elevenlabs" if voice else None),
                                       ai_config_override=ai)
    lines = [r.getMessage() for r in caplog.records if "tts_voice_resolved" in r.getMessage()]
    assert len(lines) == 1
    assert f"source={source} provider=elevenlabs model=eleven_flash_v2_5 voice={shown} " in lines[0]
    assert "camp-1" in lines[0]

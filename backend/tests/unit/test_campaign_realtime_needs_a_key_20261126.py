"""A campaign cannot be saved onto the speech-to-speech engine with no key for it.

The account-level save already refuses this; the campaign-level save did not, so
a campaign saved that way failed every call with "Realtime could not start".
Both callers (create and update in campaigns.py) re-raise HTTPException, so the
refusal reaches the client as a 400, not a 500.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.campaign_voice_config import build_campaign_voice_config
from app.api.v1.schemas.campaigns import CampaignCreateRequest, CampaignUpdateRequest
from app.domain.models.ai_config import AIProviderConfig

COMMON = {"name": "Synthetic", "company_name": "Acme", "agent_names": ["Alex"], "persona_type": "lead_gen"}


def _resolver(monkeypatch, key):
    resolve = AsyncMock(return_value=key)
    monkeypatch.setattr("app.domain.services.credential_resolver.get_credential_resolver", lambda: SimpleNamespace(resolve=resolve))
    return resolve


@pytest.mark.asyncio
async def test_realtime_with_no_key_is_refused_with_a_400(monkeypatch):
    resolve = _resolver(monkeypatch, None)
    data = CampaignCreateRequest(**COMMON, pipeline_mode="realtime")
    with pytest.raises(HTTPException) as info:
        await build_campaign_voice_config(data, AIProviderConfig(), tenant_id="t1")
    assert info.value.status_code == 400
    assert info.value.detail["code"] == "ai_option_unavailable"
    assert resolve.await_args.args == ("openai",) and resolve.await_args.kwargs == {"tenant_id": "t1"}


@pytest.mark.asyncio
async def test_realtime_with_a_key_is_saved_as_before(monkeypatch):
    _resolver(monkeypatch, "dummy")
    data = CampaignCreateRequest(**COMMON, pipeline_mode="realtime")
    script, _ = await build_campaign_voice_config(data, AIProviderConfig(), tenant_id="t1")
    assert script["pipeline_mode"] == "realtime"


@pytest.mark.asyncio
async def test_xai_provider_checks_the_xai_key(monkeypatch):
    resolve = _resolver(monkeypatch, None)
    from app.realtime.xai import XAI_DEFAULT_MODEL
    account = AIProviderConfig(
        pipeline_mode="realtime", realtime_model=XAI_DEFAULT_MODEL, realtime_voice="eve",
        realtime_settings={"provider": "xai"},
    )
    data = CampaignCreateRequest(**COMMON, pipeline_mode="realtime")
    with pytest.raises(HTTPException):
        await build_campaign_voice_config(data, account, tenant_id="t1")
    assert resolve.await_args.args == ("xai",)


@pytest.mark.asyncio
async def test_cascaded_needs_no_key_and_never_asks(monkeypatch):
    resolve = _resolver(monkeypatch, None)
    data = CampaignCreateRequest(**COMMON, pipeline_mode="cascaded", voice_id="v1")

    async def ok(*a, **k):
        return {"v1"}

    monkeypatch.setattr("app.api.v1.endpoints.campaigns._valid_voice_ids_for_provider", ok)
    monkeypatch.setattr("app.api.v1.endpoints.campaigns._build_validated_script_config", lambda **k: {})
    script, _ = await build_campaign_voice_config(data, AIProviderConfig(), tenant_id="t1")
    assert script["pipeline_mode"] == "cascaded"
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_editing_other_fields_of_a_realtime_campaign_is_not_blocked(monkeypatch):
    resolve = _resolver(monkeypatch, None)
    previous = {"pipeline_mode": "realtime", "realtime_model": "gpt-realtime-2", "realtime_voice": "marin"}
    data = CampaignUpdateRequest(**COMMON)  # engine fields not in the request
    script, _ = await build_campaign_voice_config(data, AIProviderConfig(pipeline_mode="realtime"), existing=previous, tenant_id="t1")
    assert script["pipeline_mode"] == "realtime"
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_website_resending_the_engine_for_a_realtime_campaign_is_not_blocked(monkeypatch):
    """The website sends the stored engine fields with EVERY save. A campaign already on
    the engine must stay editable (rename, reschedule) after its key was removed."""
    resolve = _resolver(monkeypatch, None)
    previous = {"pipeline_mode": "realtime", "realtime_model": "gpt-realtime-2", "realtime_voice": "marin"}
    data = CampaignUpdateRequest(**COMMON, pipeline_mode="realtime", realtime_model="gpt-realtime-2", realtime_voice="marin")
    script, _ = await build_campaign_voice_config(data, AIProviderConfig(), existing=previous, tenant_id="t1")
    assert script["pipeline_mode"] == "realtime"
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_switching_a_saved_campaign_onto_realtime_with_no_key_is_refused(monkeypatch):
    _resolver(monkeypatch, None)
    previous = {"pipeline_mode": "cascaded"}
    data = CampaignUpdateRequest(**COMMON, pipeline_mode="realtime")
    with pytest.raises(HTTPException) as info:
        await build_campaign_voice_config(data, AIProviderConfig(), existing=previous, tenant_id="t1")
    assert info.value.status_code == 400


@pytest.mark.asyncio
async def test_an_older_campaign_that_inherited_realtime_from_the_account_stays_editable(monkeypatch):
    resolve = _resolver(monkeypatch, None)
    previous = {"company_name": "Acme"}  # saved before the engine was stored per campaign
    data = CampaignUpdateRequest(**COMMON)
    script, _ = await build_campaign_voice_config(data, AIProviderConfig(pipeline_mode="realtime"), existing=previous, tenant_id="t1")
    assert script["pipeline_mode"] == "realtime"
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_campaign_switched_to_realtime_by_the_account_default_is_checked(monkeypatch):
    _resolver(monkeypatch, None)
    data = CampaignCreateRequest(**COMMON)  # no pipeline_mode: inherits the account's realtime
    with pytest.raises(HTTPException):
        await build_campaign_voice_config(data, AIProviderConfig(pipeline_mode="realtime"), tenant_id="t1")

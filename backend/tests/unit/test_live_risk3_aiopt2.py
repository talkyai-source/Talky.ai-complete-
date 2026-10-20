"""aiopt-2: a campaign's voice is frozen to the tenant's provider at save time."""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import SimpleNamespace

import pytest

from app.api.v1.schemas.campaigns import CampaignCreateRequest, CampaignUpdateRequest

_BODY = {
    "name": "Line", "system_prompt": "", "voice_id": "voice-1",
    "persona_type": "receptionist", "agent_names": ["Sarah"], "company_name": "Dojo",
}
_TENANT = "1845a165-08aa-4554-bcec-2d31ac523662"


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


def _wire(monkeypatch, ep, tenant_provider, writes):
    async def _cfg(conn, tenant_id):
        return SimpleNamespace(tts_provider=tenant_provider, pipeline_mode="cascaded")

    async def _voices(provider, *, pool=None, tenant_id=None):
        return {"voice-1"}

    monkeypatch.setattr("app.api.v1.endpoints.ai_options._fetch_tenant_config", _cfg)
    monkeypatch.setattr(ep, "_valid_voice_ids_for_provider", _voices)
    monkeypatch.setattr(ep, "acquire_with_tenant", lambda pool, tenant: _Acquire())
    monkeypatch.setattr(ep, "_build_validated_script_config", lambda **kw: {"persona_type": kw["persona_type"]})
    monkeypatch.setattr(ep, "_reject_inbound_campaign_mutation", lambda *a, **k: None)

    def _update(db, *, campaign_id, tenant_id, payload, operation):
        writes.append(payload)
        return {"id": campaign_id, **payload}

    monkeypatch.setattr(ep, "_update_owned_outbound_campaign", _update)

    class _Q:
        def select(self, *a):
            return self

        def eq(self, *a):
            return self

        def insert(self, payload):
            writes.append(payload)
            return SimpleNamespace(execute=lambda: SimpleNamespace(error=None, data=[{"id": "c-1", **payload}]))

        def execute(self):
            return SimpleNamespace(error=None, data=[{"script_config": {}, "calling_config": None}])

    return SimpleNamespace(pool=object(), table=lambda name: _Q())


async def _create(monkeypatch, tenant_provider, **extra):
    from app.api.v1.endpoints import campaigns as ep

    writes: list = []
    db = _wire(monkeypatch, ep, tenant_provider, writes)
    user = SimpleNamespace(tenant_id=_TENANT, id="u1")
    await ep.create_campaign(
        CampaignCreateRequest(**_BODY, **extra), request=SimpleNamespace(),
        current_user=user, idempotency_key=None, db_client=db,
    )
    return writes[0]["tts_provider"]


async def _update(monkeypatch, tenant_provider, **extra):
    from app.api.v1.endpoints import campaigns as ep

    writes: list = []
    db = _wire(monkeypatch, ep, tenant_provider, writes)
    user = SimpleNamespace(tenant_id=_TENANT, id="u1")
    await ep.update_campaign(
        "c-1", CampaignUpdateRequest(**_BODY, **extra), request=SimpleNamespace(),
        current_user=user, db_client=db,
    )
    return writes[0]["tts_provider"]


@pytest.mark.asyncio
async def test_create_without_provider_stores_tenant_current(monkeypatch):
    assert await _create(monkeypatch, "elevenlabs") == "elevenlabs"


@pytest.mark.asyncio
async def test_create_enum_provider_is_stored_as_its_value(monkeypatch):
    from app.domain.models.ai_config import TTSProvider

    assert await _create(monkeypatch, TTSProvider.DEEPGRAM) == "deepgram"


@pytest.mark.asyncio
async def test_update_without_provider_stores_tenant_current(monkeypatch):
    assert await _update(monkeypatch, "elevenlabs") == "elevenlabs"


@pytest.mark.asyncio
async def test_explicit_provider_is_kept(monkeypatch):
    assert await _create(monkeypatch, "elevenlabs", tts_provider="cartesia") == "cartesia"
    assert await _update(monkeypatch, "elevenlabs", tts_provider="cartesia") == "cartesia"


@pytest.mark.asyncio
async def test_unresolvable_tenant_provider_keeps_null(monkeypatch):
    assert await _create(monkeypatch, None) is None
    assert await _update(monkeypatch, "") is None


def _load_script():
    path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "backfill_campaign_tts_provider.py"
    spec = importlib.util.spec_from_file_location("backfill_campaign_tts_provider", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_backfill_inference():
    m = _load_script()
    assert m.infer_provider("2c1a4a5e-1111-4222-8333-444455556666", "elevenlabs") == ("cartesia", "voice_shape")
    assert m.infer_provider("aura-asteria-en", "cartesia") == ("deepgram", "voice_shape")
    assert m.infer_provider("en-US-Chirp3-HD-Aoede", "cartesia") == ("google", "voice_shape")
    assert m.infer_provider("21m00Tcm4TlvDq8ikWAM", "ElevenLabs") == ("elevenlabs", "tenant_current")
    assert m.infer_provider("21m00Tcm4TlvDq8ikWAM", None) == (None, "unresolved")


def test_backfill_plan_rows_uses_each_tenants_provider():
    m = _load_script()
    plans = m.plan_rows(
        [{"id": "a", "tenant_id": "t1", "voice_id": "xyz"}, {"id": "b", "tenant_id": "t2", "voice_id": "xyz"}],
        {"t1": "elevenlabs"},
    )
    assert [(p.campaign_id, p.provider) for p in plans] == [("a", "elevenlabs"), ("b", None)]

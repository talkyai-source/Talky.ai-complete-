"""A campaign created with an Idempotency-Key stores its response (QA F2-09, QA-BE-060).

The saved response was built with ``json.dumps`` on the inserted row, whose id
and timestamps are a UUID and datetimes. That raised after the insert had
committed, the catch-all released the key and answered 500, and the client's
retry with the same key created a second campaign.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.api.v1.schemas.campaigns import CampaignCreateRequest

_BODY = {
    "name": "Spring offer",
    "system_prompt": "",
    "voice_id": "voice-1",
    "persona_type": "receptionist",
    "agent_names": ["Sarah"],
    "company_name": "Dojo",
}
CAMPAIGN_ID = UUID("0c0ffee0-0000-4000-8000-000000000001")
CREATED_AT = datetime(2026, 11, 5, 1, 2, 3, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_the_created_campaign_is_stored_for_a_replay_of_the_same_key(monkeypatch):
    from app.api.v1.endpoints import campaigns as ep

    class _Insert:
        def __init__(self, payload):
            self.payload = payload

        def execute(self):
            return SimpleNamespace(error=None, data=[{**self.payload, "id": CAMPAIGN_ID,
                                                      "created_at": CREATED_AT}])

    class _Table:
        def insert(self, payload):
            return _Insert(payload)

    class _Acquire:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    async def _cfg(conn, tenant_id):
        return SimpleNamespace(tts_provider="cartesia", pipeline_mode="cascaded")

    async def _voices(provider, *, pool=None, tenant_id=None):
        return {"voice-1"}

    stored, released = [], []

    async def store(request, status_code, body):
        stored.append((status_code, body))

    async def release(request):
        released.append(True)

    monkeypatch.setattr("app.api.v1.endpoints.ai_options._fetch_tenant_config", _cfg)
    monkeypatch.setattr(ep, "_valid_voice_ids_for_provider", _voices)
    monkeypatch.setattr(ep, "acquire_with_tenant", lambda pool, tenant: _Acquire())
    monkeypatch.setattr(ep, "_build_validated_script_config", lambda **kw: {"persona_type": kw["persona_type"]})
    monkeypatch.setattr(ep, "store_idempotent_response", store)
    monkeypatch.setattr(ep, "release_idempotency_lock", release)

    db_client = SimpleNamespace(pool=object(), table=lambda name: _Table())
    user = SimpleNamespace(tenant_id="1845a165-08aa-4554-bcec-2d31ac523662", id="u1")

    result = await ep.create_campaign(CampaignCreateRequest(**_BODY), request=SimpleNamespace(),
                                      current_user=user, idempotency_key="key-1", db_client=db_client)

    assert result["campaign"]["id"] == CAMPAIGN_ID
    assert released == []
    status_code, body = stored[0]
    replay = json.loads(body)
    assert status_code == 200
    assert replay["campaign"]["id"] == str(CAMPAIGN_ID)
    assert replay["campaign"]["created_at"].startswith("2026-11-05T01:02:03")

"""POST /campaigns can create a campaign that is born inbound (2026-09-09).

Owner: inbound campaign options must be the outbound options, no difference.
So the inbound flow uses the same creator; the only extra is ``direction``.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.v1.schemas.campaigns import CampaignCreateRequest

_BODY = {
    "name": "Main support line",
    "system_prompt": "",
    "voice_id": "voice-1",
    "persona_type": "receptionist",
    "agent_names": ["Sarah"],
    "company_name": "Dojo",
}


def test_direction_defaults_to_outbound_so_existing_clients_are_unchanged():
    assert CampaignCreateRequest(**_BODY).direction == "outbound"


def test_direction_inbound_is_accepted():
    assert CampaignCreateRequest(**_BODY, direction="inbound").direction == "inbound"


def test_direction_is_a_closed_set():
    with pytest.raises(ValidationError):
        CampaignCreateRequest(**_BODY, direction="sideways")


@pytest.mark.asyncio
async def test_create_endpoint_persists_the_requested_direction(monkeypatch):
    """Run the endpoint with fakes for its collaborators and read the row it inserts."""
    from types import SimpleNamespace

    from app.api.v1.endpoints import campaigns as ep

    inserted: list[dict] = []

    class _Insert:
        def __init__(self, payload):
            self.payload = payload

        def execute(self):
            inserted.append(self.payload)
            return SimpleNamespace(error=None, data=[{"id": "c-1", **self.payload}])

    class _Table:
        def insert(self, payload):
            return _Insert(payload)

    class _Acquire:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    async def _cfg(conn, tenant_id):
        return SimpleNamespace(tts_provider="cartesia")

    async def _voices(provider):
        return {"voice-1"}

    monkeypatch.setattr("app.api.v1.endpoints.ai_options._fetch_tenant_config", _cfg)
    monkeypatch.setattr(ep, "_valid_voice_ids_for_provider", _voices)
    monkeypatch.setattr(ep, "acquire_with_tenant", lambda pool, tenant: _Acquire())
    monkeypatch.setattr(ep, "_build_validated_script_config", lambda **kw: {"persona_type": kw["persona_type"]})

    db_client = SimpleNamespace(pool=object(), table=lambda name: _Table())
    user = SimpleNamespace(tenant_id="1845a165-08aa-4554-bcec-2d31ac523662", id="u1")
    body = CampaignCreateRequest(**_BODY, direction="inbound")

    result = await ep.create_campaign(body, request=SimpleNamespace(), current_user=user, idempotency_key=None, db_client=db_client)

    assert inserted[0]["direction"] == "inbound"
    assert inserted[0]["tenant_id"] == user.tenant_id
    assert result["campaign"]["direction"] == "inbound"

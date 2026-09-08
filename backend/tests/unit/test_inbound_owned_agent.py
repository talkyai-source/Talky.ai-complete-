"""An inbound campaign owns its own AI agent (2026-09-09).

Owner: "it must be a separate entity, able to be created separately, and have
everything of its own — same behaviour and mechanism as outbound; keep the
knowledge of each thing its own, don't mix."

The create request therefore carries an ``agent`` block (the same fields the
outbound creator collects) and the service inserts the ``campaigns`` row it
owns — born inbound — inside its own transaction. The legacy ``campaign_id``
path is kept for the existing configs but the two are mutually exclusive.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints import inbound_campaigns as ep
from app.api.v1.schemas.inbound_campaigns import InboundCampaignCreateRequest
from app.domain.services.inbound_campaign_service import InboundCampaignService

TENANT = uuid.UUID("1845a165-08aa-4554-bcec-2d31ac523662")

_BASE = {
    "name": "Main support line",
    "did_number": "+442046132300",
    "sip_trunk_id": str(uuid.uuid4()),
}
_AGENT = {
    "company_name": "Dojo",
    "agent_names": ["Sarah"],
    "persona_type": "receptionist",
    "voice_id": "voice-1",
    "goal": "Answer questions about card terminals and book a callback.",
}


# ── schema: exactly one of agent / campaign_id ───────────────────────────────

def test_agent_alone_is_a_valid_create_request():
    req = InboundCampaignCreateRequest(**_BASE, agent=_AGENT)
    assert req.campaign_id is None
    assert req.agent is not None and req.agent.persona_type == "receptionist"


def test_campaign_id_alone_is_still_accepted_for_existing_configs():
    req = InboundCampaignCreateRequest(**_BASE, campaign_id=str(uuid.uuid4()))
    assert req.agent is None


@pytest.mark.parametrize("extra", [{}, {"campaign_id": str(uuid.uuid4()), "agent": _AGENT}])
def test_neither_or_both_is_rejected(extra):
    with pytest.raises(ValidationError, match="exactly one of agent"):
        InboundCampaignCreateRequest(**_BASE, **extra)


def test_agent_needs_at_least_one_real_name():
    with pytest.raises(ValidationError):
        InboundCampaignCreateRequest(**_BASE, agent={**_AGENT, "agent_names": ["  "]})


# ── service: the owned campaigns row ─────────────────────────────────────────

class _InsertConn:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((" ".join(query.split()), args))
        assert query.lstrip().startswith("INSERT INTO campaigns")
        return {"id": uuid.UUID("99999999-9999-9999-9999-999999999999")}


@pytest.mark.asyncio
async def test_owned_campaign_is_born_inbound_and_draft_with_the_validated_agent():
    conn = _InsertConn()
    agent_row = {
        "name": "Main support line",
        "system_prompt": "Answer questions.",
        "voice_id": "voice-1",
        "tts_provider": "cartesia",
        "goal": "Answer questions.",
        "script_config": {"persona_type": "receptionist", "agent_names": ["Sarah"]},
    }

    campaign_id = await InboundCampaignService(None)._create_owned_campaign(
        conn, tenant_id=TENANT, agent_row=agent_row
    )

    assert str(campaign_id) == "99999999-9999-9999-9999-999999999999"
    (sql, args), = conn.calls
    assert "'inbound'" in sql and "'draft'" in sql, "the owned row must be born inbound + draft"
    assert args[0] == TENANT
    assert args[1] == "Main support line"
    assert args[3] == "voice-1" and args[4] == "cartesia"
    assert json.loads(args[6]) == agent_row["script_config"]


@pytest.mark.asyncio
async def test_create_with_agent_never_reads_a_campaign_id(monkeypatch):
    """With an agent block the service must not try to parse campaign_id — the
    old first line did ``_uuid(str(payload.get("campaign_id")))`` and would
    have raised on ``"None"``. We stop the transaction at the first DB touch."""
    service = InboundCampaignService(None)

    class _Stop(Exception):
        pass

    def _boom(*_a, **_k):
        raise _Stop()

    monkeypatch.setattr(service, "_transaction", _boom, raising=False)
    monkeypatch.setattr("app.domain.services.inbound_campaign_service.acquire_with_tenant", _boom, raising=False)

    with pytest.raises(Exception) as excinfo:
        await service.create_campaign(
            tenant_id=str(TENANT),
            actor_id=str(uuid.uuid4()),
            actor_role="tenant_admin",
            payload={**_BASE, "campaign_id": None, "agent_row": {"name": "x", "voice_id": "v", "script_config": {}}},
            idempotency_key="key-1",
        )
    # Whatever stops the transaction first, it is NOT a campaign_id parse error.
    assert "campaign_id" not in str(excinfo.value)


# ── endpoint: the agent row is validated exactly like POST /campaigns ────────

class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_prepare_agent_row_validates_voice_and_builds_script_config(monkeypatch):
    async def _cfg(conn, tenant_id):
        class _C:
            tts_provider = "cartesia"
        return _C()

    async def _voices(provider):
        assert provider == "cartesia"
        return {"voice-1"}

    captured = {}

    def _build(**kwargs):
        captured.update(kwargs)
        return {"persona_type": kwargs["persona_type"], "company_name": kwargs["company_name"]}

    monkeypatch.setattr("app.api.v1.endpoints.ai_options._fetch_tenant_config", _cfg)
    monkeypatch.setattr("app.api.v1.endpoints.campaigns._valid_voice_ids_for_provider", _voices)
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", lambda pool, tenant: _Acquire(object()))
    monkeypatch.setattr("app.domain.services.campaign_prompt_service.build_validated_script_config", _build)

    row = await ep.prepare_inbound_agent_row(
        object(), tenant_id=str(TENANT), name="  Main support line ", agent={**_AGENT, "agent_name_genders": {"Sarah": "female"}}
    )

    assert row["name"] == "Main support line"
    assert row["voice_id"] == "voice-1" and row["tts_provider"] is None
    assert row["goal"] == _AGENT["goal"] and row["system_prompt"] == _AGENT["goal"]
    assert captured["persona_type"] == "receptionist" and captured["knowledge_driven"] is True
    assert captured["additional_instructions"] == _AGENT["goal"]
    assert row["script_config"]["agent_name_genders"] == {"Sarah": "female"}


@pytest.mark.asyncio
async def test_prepare_agent_row_rejects_a_voice_the_provider_does_not_have(monkeypatch):
    async def _cfg(conn, tenant_id):
        return None  # falls back to AIProviderConfig() defaults

    async def _voices(provider):
        return {"someone-else"}

    monkeypatch.setattr("app.api.v1.endpoints.ai_options._fetch_tenant_config", _cfg)
    monkeypatch.setattr("app.api.v1.endpoints.campaigns._valid_voice_ids_for_provider", _voices)
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", lambda pool, tenant: _Acquire(object()))

    with pytest.raises(HTTPException) as exc:
        await ep.prepare_inbound_agent_row(object(), tenant_id=str(TENANT), name="x", agent=_AGENT)
    assert exc.value.status_code == 400 and "not available for TTS provider" in exc.value.detail

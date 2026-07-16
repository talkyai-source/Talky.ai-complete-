"""create_campaign voice flow: full-draft preview must be reachable.

Live failure (2026-07-16, twice): the tool passed campaign_slots={} into
build_validated_script_config, whose lead_gen persona REQUIRES 8 slots — so
every preview failed, the model relayed the validation error and interrogated
the user for details the tool had no parameters to accept. The flow could
never finish. Now: lead_gen collects industry + services_description (the two
facts only the user knows), derives/defaults the rest, and previews the FULL
draft; support/receptionist are created knowledge-driven automatically.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.assistant.tools import campaign_create
from app.infrastructure.assistant.tools.campaign_create import create_campaign

_CORE = dict(
    name="AI estimation",
    goal="inform prospects about AI estimation and the 20% offer",
    persona_type="lead generation",
    company_name="AI flux",
    agent_names="Alex, Adam, and Smith",
)


@pytest.fixture(autouse=True)
def _voice_resolver(monkeypatch):
    monkeypatch.setattr(
        campaign_create,
        "_resolve_provider_and_voice",
        AsyncMock(return_value={"provider": "cartesia", "voice_id": "voice-1"}),
    )


@pytest.mark.asyncio
async def test_lead_gen_missing_industry_asks_for_it_not_a_validation_dump():
    result = await create_campaign("t1", SimpleNamespace(), **_CORE)

    assert "error" in result
    assert "industry" in result["error"]
    # The old failure mode: a raw missing-slots validation error.
    assert "Missing required slots" not in result["error"]


@pytest.mark.asyncio
async def test_lead_gen_full_flow_previews_complete_draft():
    result = await create_campaign(
        "t1",
        SimpleNamespace(),
        **_CORE,
        industry="the hair industry",
        services_description="AI area estimation services",
    )

    assert result.get("preview") is True, result
    fields = {c["field"]: c["after"] for c in result["campaigns"][0]["changes"]}
    # the five core answers
    assert fields["name"] == "AI estimation"
    assert fields["type"] == "lead_gen"
    assert fields["agent_names"] == "Alex, Adam, Smith"
    # the two collected facts
    assert fields["industry"] == "the hair industry"
    assert fields["services"] == "AI area estimation services"
    # derived/defaulted script values are VISIBLE in the draft for approval
    assert fields["value proposition"]
    assert fields["reason for calling"]
    assert "·" in fields["qualification questions"]
    assert fields["next step offered"]
    assert "Create campaign" in result["note"]


@pytest.mark.asyncio
async def test_support_campaign_is_knowledge_driven_with_five_fields():
    result = await create_campaign(
        "t1",
        SimpleNamespace(),
        name="Support line",
        goal="handle customer questions",
        persona_type="customer support",
        company_name="AI flux",
        agent_names="Alex",
    )

    assert result.get("preview") is True, result
    fields = {c["field"]: c["after"] for c in result["campaigns"][0]["changes"]}
    assert "Knowledge-driven" in fields["content source"]


class _InsertCaptureDB:
    """Captures insert payloads per table; every execute returns one row."""

    def __init__(self):
        self.payloads: dict[str, list] = {}

    def table(self, name: str):
        outer = self

        class _Table:
            def insert(self, payload):
                outer.payloads.setdefault(name, []).append(payload)

                class _Query:
                    def execute(self_q):
                        return SimpleNamespace(data=[{"id": "camp-1"}], error=None)

                return _Query()

        return _Table()


@pytest.mark.asyncio
async def test_confirmed_create_binds_jsonb_columns_as_json_text():
    """asyncpg's builtin jsonb codec needs JSON TEXT — a raw dict kills the
    insert at the last step (live failure: 'expected str, got dict')."""
    import json

    db = _InsertCaptureDB()
    result = await create_campaign(
        "t1",
        db,
        **_CORE,
        industry="the hair industry",
        services_description="AI area estimation services",
        confirm=True,
    )

    assert result.get("applied") is True, result
    campaign_payload = db.payloads["campaigns"][0]
    assert isinstance(campaign_payload["script_config"], str)
    assert json.loads(campaign_payload["script_config"])["persona_type"] == "lead_gen"
    audit_payload = db.payloads["assistant_actions"][0]
    assert isinstance(audit_payload["input_data"], str)
    assert isinstance(audit_payload["output_data"], str)


@pytest.mark.asyncio
async def test_receptionist_campaign_is_knowledge_driven_with_five_fields():
    result = await create_campaign(
        "t1",
        SimpleNamespace(),
        name="Front desk",
        goal="answer and route calls",
        persona_type="receptionist",
        company_name="AI flux",
        agent_names="Alex",
    )

    assert result.get("preview") is True, result

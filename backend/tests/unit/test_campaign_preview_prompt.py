"""Tests for ``POST /campaigns/preview-prompt`` (T4-B4).

The endpoint is a pure transform — pull persona + slots out of the
request body, run them through ``compose_prompt`` and
``build_persona_greeting``, return the assembled prompt + opener.
No DB, no tenant filter, no async work besides the FastAPI shell.

These tests call the endpoint function directly with a mocked
``CurrentUser`` rather than spinning up a TestClient — same prompt
composition is already exercised end-to-end in
``test_prompt_composer_direction.py``; here we lock the response
shape and the error mapping.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.campaigns import preview_prompt
from app.api.v1.schemas.campaigns import CampaignPromptPreviewRequest


LEAD_GEN_SLOTS = {
    "industry": "construction estimating",
    "services_description": "estimating and takeoff services",
    "pricing_info": "per project or monthly",
    "coverage_area": "the USA",
    "company_differentiator": "24-hour turnaround",
    "value_proposition": "save you time on bids",
    "call_reason": "your recent inquiry",
    "qualification_questions": ["Are you bidding right now?"],
    "disqualifying_answers": ["not a contractor"],
    "calendar_booking_type": "a 15-minute discovery call",
}


def _fake_user():
    """Tiny stand-in for the auth dependency. The endpoint reads no
    fields off it today (the response is independent of tenant), but
    we still pass one so the signature matches."""
    from types import SimpleNamespace
    return SimpleNamespace(id="u1", tenant_id="t1")


@pytest.mark.asyncio
class TestPreviewPromptEndpoint:
    async def test_outbound_lead_gen_returns_outbound_prompt_and_greeting(self):
        body = CampaignPromptPreviewRequest(
            persona_type="lead_gen",
            company_name="Acme",
            agent_name="Adam",
            campaign_slots=LEAD_GEN_SLOTS,
            direction="outbound",
        )
        resp = await preview_prompt(body=body, current_user=_fake_user())

        assert resp.direction == "outbound"
        assert resp.has_inbound_directive is False
        assert resp.prompt_chars == len(resp.system_prompt)
        # Outbound greeting is the per-persona lead_gen sales opener.
        assert "Adam" in resp.greeting and "Acme" in resp.greeting
        # System prompt carries persona-specific markers (stage-machine body).
        assert "WHO YOU ARE" in resp.system_prompt

    async def test_inbound_carries_directive_and_inbound_greeting(self):
        body = CampaignPromptPreviewRequest(
            persona_type="lead_gen",
            company_name="Acme",
            agent_name="Adam",
            campaign_slots=LEAD_GEN_SLOTS,
            direction="inbound",
        )
        # Greeting now picks among multiple inbound variants — pin to
        # the first variant for a deterministic phrase assertion.
        from unittest.mock import patch
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            resp = await preview_prompt(body=body, current_user=_fake_user())

        assert resp.direction == "inbound"
        assert resp.has_inbound_directive is True
        # The caller-first directive was reframed: a caller-speaks-first call
        # is still OUR outbound call (see the caller-first "hello dojo" fix).
        assert "OUTBOUND CALL — CALLEE SPEAKS FIRST" in resp.system_prompt
        # Inbound greeting variant 0 contains the canonical phrase.
        assert "thanks for reaching out" in resp.greeting.lower()

    async def test_missing_required_slot_returns_400(self):
        # Drop a required slot — composer raises PromptCompositionError,
        # which the endpoint maps to a 400 with the message intact.
        broken_slots = {**LEAD_GEN_SLOTS}
        broken_slots.pop("call_reason")
        body = CampaignPromptPreviewRequest(
            persona_type="lead_gen",
            company_name="Acme",
            agent_name="Adam",
            campaign_slots=broken_slots,
            direction="outbound",
        )
        with pytest.raises(HTTPException) as exc_info:
            await preview_prompt(body=body, current_user=_fake_user())
        assert exc_info.value.status_code == 400
        assert "call_reason" in str(exc_info.value.detail)

    async def test_response_prompt_chars_matches_actual_length(self):
        body = CampaignPromptPreviewRequest(
            persona_type="lead_gen",
            company_name="Acme",
            agent_name="Adam",
            campaign_slots=LEAD_GEN_SLOTS,
            direction="outbound",
        )
        resp = await preview_prompt(body=body, current_user=_fake_user())
        # The chars field is a sanity check operators eyeball — must
        # actually equal the prompt length, not a stale or estimated value.
        assert resp.prompt_chars == len(resp.system_prompt)
        # Sanity: a real persona produces a non-trivial prompt.
        assert resp.prompt_chars > 1000

    async def test_additional_instructions_appended(self):
        body = CampaignPromptPreviewRequest(
            persona_type="lead_gen",
            company_name="Acme",
            agent_name="Adam",
            campaign_slots=LEAD_GEN_SLOTS,
            additional_instructions="Mention the 50% discount only on Fridays.",
            direction="outbound",
        )
        resp = await preview_prompt(body=body, current_user=_fake_user())
        assert "ADDITIONAL CAMPAIGN INSTRUCTIONS" in resp.system_prompt
        assert "50% discount only on Fridays" in resp.system_prompt

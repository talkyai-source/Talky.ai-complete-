"""Campaign guidance (the operator's additional_instructions) has ONE budget,
enforced where the operator can see it.

History: 7ef3cecc (2026-06-30) removed the save-side length cap ("the Goal can
now be arbitrarily long"); 6648c566 (2026-07-14) added a runtime cap in
telephony_session_config for latency. The only call site of that cap is the
live-call builder, so a 31,464-char script saved fine, previewed in full, and
had 62% of itself replaced with "[... middle omitted ...]" on every real call
(prod journal, campaign 09b7ee9c, 2026-08). These tests make the save/start
paths refuse over-budget guidance, count the structured brief in that same
budget, and make preview reject rather than display an elided prompt.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.domain.services.campaign_prompt_service import (
    CampaignPromptValidationError,
    build_validated_script_config,
    guidance_budget_violation,
)
from app.domain.services.campaign_brief import (
    campaign_guidance_text,
    normalize_campaign_brief,
)
from app.domain.services.telephony_session_config import (
    campaign_guidance_char_budget,
)


def _kw(guidance: str) -> dict:
    return dict(
        persona_type="lead_gen",
        company_name="Acme",
        agent_names=["Alex"],
        campaign_slots={},
        additional_instructions=guidance,
        knowledge_driven=True,
    )


def test_guidance_at_budget_is_accepted():
    budget = campaign_guidance_char_budget()
    brief = normalize_campaign_brief(None, company_name="Acme", agent_names=["Alex"])
    brief_chars = len(campaign_guidance_text("", brief))
    # campaign_guidance_text joins the brief and non-empty freeform layer with
    # two newlines. Fill the exact remaining capacity, not the old freeform-only
    # capacity.
    guidance = "x" * (budget - brief_chars - 2)
    out = build_validated_script_config(**_kw(guidance))
    assert len(campaign_guidance_text(out["additional_instructions"], out["campaign_brief"])) == budget


def test_guidance_over_budget_is_rejected_with_the_numbers():
    budget = campaign_guidance_char_budget()
    text = "word " * (budget // 5 + 200)
    with pytest.raises(CampaignPromptValidationError) as exc:
        build_validated_script_config(**_kw(text))
    msg = str(exc.value)
    assert f"{budget:,}" in msg
    assert "omit" not in msg.lower() or "nothing is trimmed" in msg.lower()
    assert "Company knowledge" in msg


def test_guidance_budget_violation_helper():
    budget = campaign_guidance_char_budget()
    assert guidance_budget_violation("short") is None
    v = guidance_budget_violation("a" * (budget + 1))
    assert v == (budget + 1, budget)


def test_budget_is_env_overridable(monkeypatch):
    monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "500")
    assert campaign_guidance_char_budget() == 500
    with pytest.raises(CampaignPromptValidationError):
        build_validated_script_config(**_kw("a" * 501))


# ── preview composes the live prompt ────────────────────────────────────────

@pytest.mark.asyncio
async def test_preview_refuses_over_budget_instead_of_eliding(monkeypatch):
    """Preview/save/start share one gate; no path returns shortened content."""
    from app.api.v1.endpoints import campaigns as ep
    from app.api.v1.schemas.campaigns import CampaignPromptPreviewRequest

    monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "600")
    guidance = ("alpha " * 200).strip()  # 1199 chars, over a 600 budget
    body = CampaignPromptPreviewRequest(
        persona_type="lead_gen",
        company_name="Acme",
        agent_name="Alex",
        additional_instructions=guidance,
        knowledge_driven=True,
        direction="outbound",
        opening_mode="callee_first",
    )
    with pytest.raises(HTTPException) as exc:
        await ep.preview_prompt(body, current_user=SimpleNamespace(tenant_id="t"))
    assert getattr(exc.value, "status_code", None) == 400
    assert "Nothing is trimmed automatically" in str(getattr(exc.value, "detail", ""))
    assert "omitted for length" not in str(getattr(exc.value, "detail", ""))


# ── the composition log names every layer ───────────────────────────────────

def test_prompt_composition_log_carries_layer_sizes_and_framing(caplog):
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )
    from app.domain.services.voice_orchestrator import Direction

    campaign = {
        "id": "c-log",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "script_config": {
            "company_name": "Acme",
            "agent_names": ["Alex"],
            "additional_instructions": "Always mention the spring offer.",
        },
    }
    with caplog.at_level(logging.INFO, logger="app.domain.services.telephony_session_config"):
        cfg = build_telephony_session_config(
            gateway_type="telephony",
            campaign=campaign,
            direction=Direction.OUTBOUND,
            opening_mode="callee_first",
        )
    composed = [r for r in caplog.records if "telephony_prompt_composed" in r.getMessage()]
    assert composed, "expected a telephony_prompt_composed log line"
    line = composed[-1].getMessage()
    assert "direction=outbound" in line
    assert "opening_mode=callee_first" in line
    assert "guidance_chars=32" in line
    assert "base_chars=" in line
    assert f"prompt_chars={len(cfg.system_prompt)}" in line or "prompt_chars=" in line
    identity = [r for r in caplog.records if "telephony_prompt_identity" in r.getMessage()]
    assert identity and "hash=" in identity[-1].getMessage()

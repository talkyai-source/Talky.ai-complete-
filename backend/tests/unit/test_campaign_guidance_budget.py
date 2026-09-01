"""Campaign guidance (the operator's additional_instructions) has ONE budget,
enforced where the operator can see it.

History: 7ef3cecc (2026-06-30) removed the save-side length cap ("the Goal can
now be arbitrarily long"); 6648c566 (2026-07-14) added a runtime cap in
telephony_session_config for latency. The only call site of that cap is the
live-call builder, so a 31,464-char script saved fine, previewed in full, and
had 62% of itself replaced with "[... middle omitted ...]" on every real call
(prod journal, campaign 09b7ee9c, 2026-08). These tests make the save/start
paths refuse over-budget guidance and make the preview compose exactly what
the live call composes.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.domain.services.campaign_prompt_service import (
    CampaignPromptValidationError,
    build_validated_script_config,
    guidance_budget_violation,
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
    out = build_validated_script_config(**_kw("x " * (budget // 2)))
    assert len(out["additional_instructions"]) <= budget


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
async def test_preview_reports_budget_and_composes_like_a_live_call(monkeypatch):
    """Preview must never show more prompt than the call will run. It reports
    the guidance size against the budget so the UI can block the save."""
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
    resp = await ep.preview_prompt(body, current_user=SimpleNamespace(tenant_id="t"))
    assert resp.campaign_guidance_chars == len(guidance)
    assert resp.campaign_guidance_budget_chars == 600
    assert resp.over_budget is True
    # The live builder elides the middle of over-budget guidance; the preview
    # shows the same thing rather than the full text.
    assert "omitted for length" in resp.system_prompt
    assert resp.direction == "outbound"
    assert resp.opening_mode == "callee_first"
    assert resp.has_inbound_directive is True


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

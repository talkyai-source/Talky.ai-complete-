"""Realtime campaign context must never trim the tenant-wide account notes."""
import pytest

from app.realtime.campaign_config import _with_campaign_context
from app.realtime.config import RealtimePrompt

NOTES = "NEVER quote prices. Do not discuss competitors."


def _context(guidance, notes=NOTES):
    prompt = RealtimePrompt(instructions=notes)
    return _with_campaign_context(prompt, {"additional_instructions": guidance}, lambda name: None)


def test_long_campaign_guidance_is_cut_before_the_account_notes(monkeypatch):
    monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "1000")
    from app.domain.services.telephony_session_config import campaign_guidance_char_budget
    budget = campaign_guidance_char_budget()
    guidance = ("Explain the offer clearly. " * 100)[:budget - 10]
    text = _context(guidance).instructions
    assert text.endswith(f"Account-wide notes: {NOTES}")
    assert len(text) <= budget


def test_guidance_that_fits_is_unchanged(monkeypatch):
    monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "1000")
    text = _context("Short script.").instructions
    assert text == f"Short script.\n\nAccount-wide notes: {NOTES}"


def test_guidance_without_notes_is_still_capped_at_the_budget(monkeypatch):
    monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "1000")
    from app.domain.services.telephony_session_config import campaign_guidance_char_budget
    budget = campaign_guidance_char_budget()
    text = _context("word " * 1000, notes="").instructions
    assert 0 < len(text) <= budget


def test_notes_that_alone_exceed_the_budget_are_refused_not_trimmed(monkeypatch):
    from app.domain.services.campaign_prompt_service import CampaignPromptValidationError
    monkeypatch.setenv("TELEPHONY_TENANT_PROMPT_MAX_CHARS", "1000")
    with pytest.raises(CampaignPromptValidationError):
        _context("Short script.", notes="x " * 600)

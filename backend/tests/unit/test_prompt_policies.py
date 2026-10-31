"""Behaviour policies live in .md files and stay within their token budgets.

Standards ARC-1 and LAT-3 (docs/standards/voice-agent-standards.md): generic
agent behaviour is edited as Markdown, not code, and every always-on block has
a declared budget so guidance cannot quietly grow the prompt (latency, bills).
"""
from __future__ import annotations

import pytest

from app.services.scripts.prompts import policies
from app.services.scripts.prompts.policies import load_policy, placeholders, policy, policy_names

# Sent on every outbound turn (personas and audio tags are per-campaign/voice).
ALWAYS_ON = ("conversation_guide", "contact_and_privacy", "how_to_speak",
             "non_negotiables", "company_knowledge", "ending_the_call")
ALWAYS_ON_BUDGET_TOKENS = 1100


def _tokens(text: str) -> int:
    return -(-len(text) // 4)


@pytest.mark.parametrize("name", policy_names())
def test_every_policy_declares_purpose_budget_and_exact_placeholders(name):
    p = policy(name)
    assert p.meta.get("purpose"), name
    assert _tokens(p.body) <= int(p.meta["budget_tokens"]), (name, _tokens(p.body))
    declared = {f.strip() for f in p.meta.get("placeholders", "").split(",") if f.strip()}
    assert placeholders(p.body) == declared, name
    assert p.body.endswith("\n") and "\r" not in p.body


def test_always_on_guidance_fits_one_budget():
    assert sum(_tokens(load_policy(n)) for n in ALWAYS_ON) <= ALWAYS_ON_BUDGET_TOKENS


def test_prompt_constants_are_the_policy_files():
    from app.services.scripts.prompts import guardrails, composer
    from app.services.scripts.prompts.personas import lead_gen, receptionist, customer_support
    from app.domain.services.voice_pipeline import end_call
    assert guardrails.COMMUNICATION_PRINCIPLES == load_policy("how_to_speak")
    assert guardrails.GENERIC_GUARDRAILS_REST == load_policy("contact_and_privacy")
    assert guardrails.COMPLIANCE_FLOOR_TEMPLATE == load_policy("non_negotiables")
    assert composer.KNOWLEDGE_PRECEDENCE == load_policy("company_knowledge")
    assert lead_gen.LEAD_GEN_PLAYBOOK == load_policy("personas/lead_gen")
    assert receptionist.RECEPTIONIST_BODY == load_policy("personas/receptionist")
    assert customer_support.CUSTOMER_SUPPORT_BODY == load_policy("personas/customer_support")
    assert end_call.END_CALL_TOKEN in end_call.CALL_CONTROL_RULES
    assert "{end_call_token}" not in end_call.CALL_CONTROL_RULES


@pytest.fixture
def fresh_cache():
    policy.cache_clear()
    yield
    policy.cache_clear()


def test_an_override_directory_replaces_a_policy(tmp_path, monkeypatch, fresh_cache):
    (tmp_path / "how_to_speak.md").write_text("## HOW TO SPEAK\nKeep it short.\n", encoding="utf-8")
    monkeypatch.setenv("PROMPT_POLICY_DIR", str(tmp_path))
    assert load_policy("how_to_speak") == "## HOW TO SPEAK\nKeep it short.\n"
    assert policy("how_to_speak").source.startswith(str(tmp_path))


def test_an_override_that_changes_placeholders_falls_back(tmp_path, monkeypatch, fresh_cache, caplog):
    default = load_policy("non_negotiables")
    policy.cache_clear()
    (tmp_path / "non_negotiables.md").write_text("Speak for {brand}.\n", encoding="utf-8")
    monkeypatch.setenv("PROMPT_POLICY_DIR", str(tmp_path))
    with caplog.at_level("ERROR", logger=policies.__name__):
        assert load_policy("non_negotiables") == default
    assert "prompt_policy_override_rejected" in caplog.text


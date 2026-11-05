"""Ask AI (Tessa) prompt hardening: shared comms principles, sensitive-info
boundaries, and full product knowledge."""
from __future__ import annotations

from app.domain.services.ask_ai_session_config import ASK_AI_SYSTEM_PROMPT
from app.domain.services.ask_ai_constants import TALKY_PRODUCT_INFO
from app.services.scripts.prompts.guardrails import COMMUNICATION_PRINCIPLES


def test_ask_ai_uses_shared_communication_principles():
    # Single source: literally the same constant the campaign agents use.
    assert COMMUNICATION_PRINCIPLES in ASK_AI_SYSTEM_PROMPT



def test_ask_ai_has_sensitive_info_boundaries():
    p = ASK_AI_SYSTEM_PROMPT
    assert "BOUNDARIES" in p
    assert "models or vendors you run on" in p        # no internal/tech leak
    assert "other customers' data" in p               # no cross-customer leak
    assert "unpublished pricing" in p                 # no unpublished pricing
    assert "Never ask the caller for sensitive data" in p  # no sensitive collection


def test_ask_ai_knows_the_product():
    assert "AI voice-calling platform" in ASK_AI_SYSTEM_PROMPT  # always-known summary
    # The always-available fact sheet covers features and plans.
    for token in ("voice cloning", "knowledge base", "timezone-aware",
                  "Basic", "Professional", "Enterprise"):
        assert token in TALKY_PRODUCT_INFO


def test_ask_ai_still_lean_no_markdown_rule():
    # Keeps its voice-safety rules.
    assert "No markdown" in ASK_AI_SYSTEM_PROMPT
    assert "do NOT greet again" in ASK_AI_SYSTEM_PROMPT


def test_tessa_never_hides_that_she_is_an_ai():
    """QA F11-04: asked "Am I talking to a real person?", the public demo said
    "I'm a real person on the line" in 3 of 3 live sessions (2 Nov 2026),
    because its own prompt said "Never say you are an AI". Judged by the same
    detector that warns tenants who write such instructions."""
    from app.domain.services.ask_ai_session_config import create_ask_ai_agent_config
    from app.services.scripts.prompts.guardrails import scan_instruction_conflicts

    rules = " ".join(create_ask_ai_agent_config().rules.do_not_say_rules)
    assert scan_instruction_conflicts(ASK_AI_SYSTEM_PROMPT) == []
    assert scan_instruction_conflicts(rules) == []
    assert "Talk-Lee's AI assistant" in ASK_AI_SYSTEM_PROMPT

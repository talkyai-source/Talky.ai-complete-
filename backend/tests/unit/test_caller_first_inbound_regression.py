"""End-to-end-shape regression for caller-speaks-first persona framing.

The bug class this test guards against: on a caller-speaks-first OUTBOUND
call the AI plays receptionist ("Hello, this is X — how can I help you?")
instead of leading with its own introduction + purpose, OR the call-
direction directive gets pushed out of early-token attention / silently
skipped on persona-composed prompts. Caller-first is a turn-taking choice
(wait for the callee to speak), NOT a genuine inbound call — the agent
dialed them, so it owns the opening.

These tests do NOT spin up the LLM — they validate the contract at the
last application boundary before the LLM call: the active
``call_session.system_prompt`` after the bridge applies caller-first
shaping. If that prompt doesn't carry the directive in a position where
the LLM will weight it, the AI cannot reliably produce the outbound
introduce-yourself-and-purpose opener.

What's exercised:
    bridge fan-in  →  build_telephony_session_config
                  →  select_inbound_base_prompt
                  →  active system_prompt the LLM will see

What's mocked:
    `random.choice` is patched in tests that assert the canonical
    receiver pattern, since `build_telephony_inbound_greeting` now
    returns one of several variants — pinning to the first variant
    keeps these tests deterministic.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.domain.services.telephony.modes.caller_first import (
    INBOUND_DIRECTIVE_SENTINEL,
    select_inbound_base_prompt,
)
from app.domain.services.telephony_session_config import (
    build_telephony_inbound_greeting,
)


# Canonical receiver-style opener pattern. The LLM's first reply on a
# user-first call should match this, modulo punctuation. Tests assert the
# system prompt INSTRUCTS the LLM to follow this pattern; we don't drive
# the LLM here but the prompt-level assertion is a strong proxy.
_INBOUND_OPENER_PATTERN = re.compile(
    r"Hello,\s+(?P<company>[\w\s&'\.\-]+?),\s+this\s+is\s+(?P<agent>\w[\w\s'\.\-]*?)"
    r"\s+--\s+how\s+can\s+i\s+help\s+you\??",
    flags=re.IGNORECASE,
)


def _persona_session(agent: str = "Sarah", company: str = "Acme"):
    """Mirror compose_prompt's output shape for a persona-driven campaign:
    a freeform prompt without any of the legacy markers. The bridge must
    still inject the inbound directive on top."""
    persona_prompt = (
        f"You are {agent}, a friendly customer support specialist at "
        f"{company}.\n"
        "Listen carefully and answer questions about our products and "
        "services. Stay calm and concise."
    )
    call_session = SimpleNamespace(
        system_prompt=persona_prompt,
        agent_config=SimpleNamespace(agent_name=agent, company_name=company),
    )
    return SimpleNamespace(call_session=call_session, call_id="persona-test")


class TestActivePromptCarriesInboundContract:
    """The minimum contract the LLM relies on for caller-first calls."""

    def test_persona_path_directive_dominates_persona(self):
        """Persona-composed prompt → directive prepended at top. The
        persona body still trails below (the customer's voice survives),
        but the call-direction framing wins."""
        voice_session = _persona_session()

        select_inbound_base_prompt(voice_session)

        active = voice_session.call_session.system_prompt
        # Sentinel at position 0 → LLM weights this most heavily.
        assert active.startswith(INBOUND_DIRECTIVE_SENTINEL)
        # Persona body still present below directive.
        assert "friendly customer support specialist at Acme" in active
        # Outbound opener present and parameterized to this campaign — the
        # agent introduces itself as "<agent> from <company>", not a receiver.
        assert "Sarah from Acme" in active

    def test_canonical_opener_matches_receiver_pattern(self):
        """The canonical opener phrasing must still match the receiver
        regex. The greeting now picks among a few variants — pin to
        the canonical first variant so this regression check stays
        deterministic. (At least one variant must always match the
        canonical pattern; the others are stylistic alternates.)"""
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            canonical = build_telephony_inbound_greeting("Adam", "All States Estimation")
        m = _INBOUND_OPENER_PATTERN.search(canonical)
        assert m is not None, (
            f"Canonical inbound greeting {canonical!r} no longer matches "
            "the receiver pattern — update either the greeting or the "
            "pattern, but they must stay in sync."
        )
        assert m.group("company") == "All States Estimation"
        assert m.group("agent") == "Adam"


class TestCallerFirstFailureModes:
    """Locks behaviour for the failure paths that historically bit us:
    silent skip on persona prompts, double-application, and edge inputs."""

    def test_persona_path_idempotent(self):
        voice_session = _persona_session()
        select_inbound_base_prompt(voice_session)
        first_pass = voice_session.call_session.system_prompt
        select_inbound_base_prompt(voice_session)
        assert voice_session.call_session.system_prompt == first_pass
        # Sentinel appears exactly once even after multiple applications.
        assert voice_session.call_session.system_prompt.count(
            INBOUND_DIRECTIVE_SENTINEL
        ) == 1

    def test_missing_agent_config_uses_grammatical_defaults(self):
        """An undercooked campaign (no agent_config at all) must still
        produce a grammatical inbound prompt rather than a crash or a
        prompt referencing 'None' / 'NoneType'."""
        call_session = SimpleNamespace(
            system_prompt="Some persona text without legacy markers.",
            agent_config=None,
        )
        voice_session = SimpleNamespace(call_session=call_session, call_id="bare-test")
        select_inbound_base_prompt(voice_session)
        active = voice_session.call_session.system_prompt
        assert active.startswith(INBOUND_DIRECTIVE_SENTINEL)
        assert "None" not in active.split("\n")[1:6]  # no Python None leakage in directive
        # Defaults from caller_first._resolve_agent_context.
        assert "your assistant" in active
        assert "the company" in active

    @pytest.mark.parametrize("agent,company", [
        ("Sarah", "Acme Co"),
        ("Adam", "All States Estimation"),
        ("Maya", "Northwind & Sons"),
        ("José", "Café del Mar"),
    ])
    def test_canonical_opener_renders_for_realistic_inputs(self, agent, company):
        """The greeting builder must produce a grammatical, parseable
        opener for a representative sample of real-world names across
        every variant. Catches regressions from clever-but-fragile
        string formatting."""
        # Sample many — every variant must include both slots and offer help.
        for _ in range(30):
            rendered = build_telephony_inbound_greeting(agent, company)
            assert agent in rendered
            assert company in rendered
            lower = rendered.lower()
            assert (
                "how can i help" in lower or "what can i do" in lower
            ), f"variant must offer help: {rendered!r}"

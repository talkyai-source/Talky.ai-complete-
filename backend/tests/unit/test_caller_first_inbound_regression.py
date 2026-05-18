"""End-to-end-shape regression for caller-speaks-first persona framing.

The bug class this test guards against: the AI greets like an outbound
caller ("Hi, this is X from Y") even though the campaign is configured
for caller-speaks-first. That happened historically when the inbound
prompt patch was append-only and got pushed out of early-token attention,
or when the patch silently skipped persona-composed prompts.

These tests do NOT spin up the LLM — they validate the contract at the
last application boundary before the LLM call: the active
``call_session.system_prompt`` after the bridge applies caller-first
shaping. If that prompt doesn't carry the inbound directive in a position
where the LLM will weight it, the AI cannot reliably produce a receiver
opener.

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
    TELEPHONY_ESTIMATION_SYSTEM_PROMPT,
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


def _legacy_session(agent: str = "Adam", company: str = "All States Estimation"):
    """Build a session that mirrors what build_telephony_session_config
    produces for the legacy estimation campaign — the prompt with the
    triple sniff markers (HARD RULES + Business Development Specialist
    + GREETING RESPONSE)."""
    legacy_prompt = TELEPHONY_ESTIMATION_SYSTEM_PROMPT.format(
        agent_name=agent, company_name=company,
    )
    call_session = SimpleNamespace(
        system_prompt=legacy_prompt,
        agent_config=SimpleNamespace(agent_name=agent, company_name=company),
    )
    return SimpleNamespace(call_session=call_session, call_id="legacy-test")


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

    def test_legacy_path_active_prompt_has_inbound_directive(self):
        """Legacy outbound prompt → full swap to inbound base. The active
        prompt must announce 'INBOUND CALL' at position 0 (early-token
        weighting) and contain the canonical opener phrase verbatim."""
        voice_session = _legacy_session()

        select_inbound_base_prompt(voice_session)

        active = voice_session.call_session.system_prompt
        assert active.startswith(INBOUND_DIRECTIVE_SENTINEL), (
            "Inbound sentinel must lead the prompt so early-token "
            "attention dominates the persona below it."
        )
        assert "Hello, All States Estimation, this is Adam" in active, (
            "Canonical receiver opener missing — LLM lacks the explicit "
            "phrasing it should follow on turn 0."
        )

    def test_legacy_path_does_not_leak_outbound_persona(self):
        """The legacy swap removes the outbound persona block; otherwise
        the LLM sees both 'you answered the phone' and 'you call
        contractors' and tends to mash them together. We use the two
        structural markers that uniquely identify the outbound persona —
        the role title and the GREETING RESPONSE flow header. Both must
        be gone after the swap."""
        voice_session = _legacy_session()

        select_inbound_base_prompt(voice_session)

        active = voice_session.call_session.system_prompt
        assert "Business Development Specialist" not in active
        assert "GREETING RESPONSE" not in active
        # The legacy outbound flow tells the AI to "call contractors"; the
        # inbound prompt mentions providing services TO contractors. The
        # active-voice "You call contractors" phrase is a clean tell.
        assert "You call contractors" not in active

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
        # Canonical opener present and parameterized to this campaign.
        assert "Hello, Acme, this is Sarah" in active

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

    def test_legacy_path_idempotent(self):
        voice_session = _legacy_session()
        select_inbound_base_prompt(voice_session)
        first_pass = voice_session.call_session.system_prompt
        select_inbound_base_prompt(voice_session)
        assert voice_session.call_session.system_prompt == first_pass

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

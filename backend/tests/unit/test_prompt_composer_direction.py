"""Direction-aware prompt composition (T4-A1).

These tests cover the contract that landed in Sprint A:
* compose_prompt accepts a ``direction`` keyword (default "outbound").
* INBOUND prompts carry the canonical inbound directive at position 0.
* OUTBOUND prompts have no inbound directive.
* Each persona × direction produces an opener that matches the direction.
* Few-shot examples and filler-word permission survive composition.
* Backward-compat: positional 4-arg compose_prompt still works (existing
  tests in test_prompt_composer.py exercise this path).
"""
from __future__ import annotations

import re

import pytest

from app.services.scripts.prompts.composer import (
    PromptCompositionError,
    compose_prompt,
)
from app.services.scripts.prompts.direction import INBOUND_DIRECTIVE_SENTINEL


def _flat(text: str) -> str:
    """Collapse all whitespace runs to single spaces. The persona
    templates are wrapped at ~70 chars, so phrases like 'thanks for
    reaching out' cross newlines in the rendered output. Operators
    care about the spoken phrase, not the wrapping; tests should too."""
    return re.sub(r"\s+", " ", text)


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

SUPPORT_SLOTS = {
    "business_hours": "Mon-Fri 9-5",
    "website": "example.com",
    "support_email": "support@example.com",
    "refund_policy": "14-day refund",
    "cancellation_policy": "anytime",
    "complaint_policy": "escalate to manager",
    "support_topics": ["billing", "access"],
    "common_issues": [{"issue": "login", "solution": "reset password"}],
    "escalate_triggers": ["legal threat"],
    "escalate_to": "the manager",
    "escalation_wait_time": "one business day",
}

RECEPTIONIST_SLOTS = {
    "business_type": "dental practice",
    "business_address": "123 Main St",
    "business_phone": "555-0100",
    "business_email": "hi@example.com",
    "website": "example.com",
    "opening_hours": "Mon-Fri 9-5",
    "services": ["cleaning", "checkup"],
    "emergency_protocol": "Direct emergencies to 911.",
    "new_patient_info_needed": ["full name", "date of birth"],
}


# ---------------------------------------------------------------------
# Direction parameter — basic contract
# ---------------------------------------------------------------------


class TestDirectionContract:
    def test_default_direction_is_outbound(self):
        """Existing call sites that don't pass direction get the
        historical outbound behaviour. No inbound directive on the
        default path."""
        out = compose_prompt(
            "lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS,
        )
        assert INBOUND_DIRECTIVE_SENTINEL not in out

    def test_explicit_outbound(self):
        out = compose_prompt(
            "lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS,
            direction="outbound",
        )
        assert INBOUND_DIRECTIVE_SENTINEL not in out

    def test_explicit_inbound_carries_directive_at_top(self):
        out = compose_prompt(
            "lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS,
            direction="inbound",
        )
        # Sentinel must appear at position 0 to dominate early-token
        # attention. Allow leading whitespace tolerance.
        assert out.lstrip().startswith(INBOUND_DIRECTIVE_SENTINEL)

    def test_unknown_direction_raises(self):
        with pytest.raises(PromptCompositionError, match="direction"):
            compose_prompt(
                "lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS,
                direction="sideways",
            )

    def test_direction_value_is_case_insensitive(self):
        """Operators / Direction enum values may arrive in mixed case;
        composer normalises so callers don't have to."""
        out = compose_prompt(
            "lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS,
            direction="INBOUND",
        )
        assert INBOUND_DIRECTIVE_SENTINEL in out


# ---------------------------------------------------------------------
# Per-persona × per-direction openers
# ---------------------------------------------------------------------


class TestPerPersonaDirectionalOpeners:
    def test_lead_gen_outbound_opener(self):
        """Outbound lead_gen — permission-based opener (introduce + reason +
        an easy out), not a pitch."""
        flat = _flat(compose_prompt(
            "lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS,
            direction="outbound",
        ))
        assert "Alex from Acme" in flat
        assert "out of the blue" in flat
        assert "bad time" in flat

    def test_lead_gen_inbound_opener(self):
        """Caller-speaks-first lead_gen — still an OUTBOUND call: introduce +
        reason, explicitly NOT a receptionist 'how can I help'."""
        flat = _flat(compose_prompt(
            "lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS,
            direction="inbound",
        ))
        assert "this is Alex from Acme" in flat
        assert "reaching out" in flat.lower()
        # The opener must explicitly tell the agent not to play receptionist.
        assert "do not play receptionist" in flat.lower()

    def test_customer_support_inbound_opener(self):
        flat = _flat(compose_prompt(
            "customer_support", "Sam", "Acme", SUPPORT_SLOTS,
            direction="inbound",
        ))
        assert "Thanks for calling Acme" in flat
        assert "this is Sam" in flat

    def test_customer_support_outbound_opener(self):
        """Customer support callbacks need a callback-shaped opener,
        not the inbound 'thanks for calling'."""
        flat = _flat(compose_prompt(
            "customer_support", "Sam", "Acme", SUPPORT_SLOTS,
            direction="outbound",
        ))
        assert "calling about your recent inquiry" in flat
        assert "Thanks for calling Acme" not in flat

    def test_receptionist_inbound_opener(self):
        flat = _flat(compose_prompt(
            "receptionist", "Maya", "Acme", RECEPTIONIST_SLOTS,
            direction="inbound",
        ))
        assert "Thank you for calling Acme" in flat
        assert "this is Maya" in flat

    def test_receptionist_outbound_opener(self):
        flat = _flat(compose_prompt(
            "receptionist", "Maya", "Acme", RECEPTIONIST_SLOTS,
            direction="outbound",
        ))
        assert "following up on your inquiry" in flat
        assert "Thank you for calling Acme" not in flat


# ---------------------------------------------------------------------
# Few-shot examples + filler-word permission (A3)
# ---------------------------------------------------------------------


class TestFewShotAndFillers:
    @pytest.mark.parametrize("persona,slots", [
        ("lead_gen", LEAD_GEN_SLOTS),
        ("customer_support", SUPPORT_SLOTS),
        ("receptionist", RECEPTIONIST_SLOTS),
    ])
    def test_filler_permission_present(self, persona, slots):
        """Models suppress disfluencies by default; the persona prompt
        must explicitly permit natural fillers ('uh', 'let me see',
        etc.) so the agent does not sound like a service bot."""
        out = compose_prompt(persona, "Alex", "Acme", slots)
        # Permission line we authored — assert it survives composition.
        assert "Use occasional fillers" in out

    @pytest.mark.parametrize("persona,slots", [
        ("lead_gen", LEAD_GEN_SLOTS),
        ("customer_support", SUPPORT_SLOTS),
        ("receptionist", RECEPTIONIST_SLOTS),
    ])
    def test_few_shot_examples_present(self, persona, slots):
        """Each persona body carries an EXAMPLES block with USER/AGENT
        pairs — the OpenAI 2026 Realtime Prompting Guide flagged this
        as the single biggest miss for voice agents."""
        out = compose_prompt(persona, "Alex", "Acme", slots)
        assert "EXAMPLES" in out
        # At least one USER: / AGENT: pair must be intact after slot
        # substitution; without them the LLM has nothing to mimic.
        assert re.search(r"USER:\s+\S", out)
        assert re.search(r"AGENT:\s+\S", out)


# ---------------------------------------------------------------------
# No placeholder leakage in either direction
# ---------------------------------------------------------------------


class TestNoPlaceholderLeakage:
    @pytest.mark.parametrize("persona,slots,direction", [
        ("lead_gen", LEAD_GEN_SLOTS, "outbound"),
        ("lead_gen", LEAD_GEN_SLOTS, "inbound"),
        ("customer_support", SUPPORT_SLOTS, "outbound"),
        ("customer_support", SUPPORT_SLOTS, "inbound"),
        ("receptionist", RECEPTIONIST_SLOTS, "outbound"),
        ("receptionist", RECEPTIONIST_SLOTS, "inbound"),
    ])
    def test_no_unfilled_braces(self, persona, slots, direction):
        """Every {placeholder} in every persona × direction template
        must be filled. An unfilled {brace} reaching the LLM is a
        silent contract bug — fail loud here."""
        out = compose_prompt(
            persona, "Alex", "Acme", slots, direction=direction,
        )
        # Allow code-style braces that legitimately appear in prose
        # (e.g. example JSON in the future). We assert no
        # ALL-LOWERCASE-IDENT brace pattern, which is what str.format
        # placeholders look like.
        leftover = re.findall(r"\{[a-z][a-z0-9_]*\}", out)
        assert leftover == [], (
            f"Persona {persona!r} direction {direction!r} leaked "
            f"placeholders: {leftover}"
        )


# ---------------------------------------------------------------------
# Inbound directive is exactly one block, not N
# ---------------------------------------------------------------------


class TestPronunciationsHook:
    """Optional ``pronunciations`` campaign slot (T4-A4). Models
    mis-pronounce custom company names on first mention — the block
    teaches them how before the persona body kicks in."""

    def test_pronunciations_dict_renders_block(self):
        slots = {**LEAD_GEN_SLOTS, "pronunciations": {"Acme": "AK-mee"}}
        out = compose_prompt("lead_gen", "Alex", "Acme", slots)
        assert "PRONUNCIATIONS" in out
        assert '"Acme" → say it like "AK-mee"' in out

    def test_pronunciations_landing_position_before_persona(self):
        """The block must land before the persona body so the LLM
        reads it before encountering the company name."""
        slots = {**LEAD_GEN_SLOTS, "pronunciations": {"Acme": "AK-mee"}}
        out = compose_prompt("lead_gen", "Alex", "Acme", slots)
        pron_idx = out.index("PRONUNCIATIONS")
        # "WHO YOU ARE" is the first persona-body marker (the playbook header).
        role_idx = out.index("WHO YOU ARE")
        assert pron_idx < role_idx

    def test_pronunciations_list_of_pairs_renders(self):
        """List form is supported for form-builders that prefer ordered
        sequences over dicts."""
        slots = {
            **LEAD_GEN_SLOTS,
            "pronunciations": [
                {"name": "Acme", "say": "AK-mee"},
                {"name": "Adam", "say": "AH-dum"},
            ],
        }
        out = compose_prompt("lead_gen", "Alex", "Acme", slots)
        assert '"Acme" → say it like "AK-mee"' in out
        assert '"Adam" → say it like "AH-dum"' in out

    def test_missing_pronunciations_is_silent(self):
        """No PRONUNCIATIONS block when the slot is absent. The vast
        majority of campaigns won't supply this — silence is the
        feature, not the absence."""
        out = compose_prompt("lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS)
        assert "PRONUNCIATIONS" not in out

    def test_empty_dict_is_silent(self):
        slots = {**LEAD_GEN_SLOTS, "pronunciations": {}}
        out = compose_prompt("lead_gen", "Alex", "Acme", slots)
        assert "PRONUNCIATIONS" not in out

    def test_dict_with_blank_values_is_silent(self):
        """A half-populated dict (operator started typing then deleted)
        must not render an empty PRONUNCIATIONS block."""
        slots = {
            **LEAD_GEN_SLOTS,
            "pronunciations": {"Acme": "", "  ": "spoken"},
        }
        out = compose_prompt("lead_gen", "Alex", "Acme", slots)
        assert "PRONUNCIATIONS" not in out

    def test_unsupported_type_is_silent(self):
        """A misconfigured slot (string instead of dict/list) logs a
        warning and renders nothing — must never crash the call."""
        slots = {**LEAD_GEN_SLOTS, "pronunciations": "Acme=AK-mee"}
        out = compose_prompt("lead_gen", "Alex", "Acme", slots)
        assert "PRONUNCIATIONS" not in out

    def test_pronunciations_present_in_inbound_too(self):
        """The block applies to both directions equally."""
        slots = {**LEAD_GEN_SLOTS, "pronunciations": {"Acme": "AK-mee"}}
        out = compose_prompt(
            "lead_gen", "Alex", "Acme", slots, direction="inbound",
        )
        assert "PRONUNCIATIONS" in out
        # Inbound directive comes first, then guardrails, then
        # pronunciations, then persona.
        directive_idx = out.index(INBOUND_DIRECTIVE_SENTINEL)
        pron_idx = out.index("PRONUNCIATIONS")
        role_idx = out.index("WHO YOU ARE")
        assert directive_idx < pron_idx < role_idx


class TestInboundDirectiveIdempotency:
    def test_inbound_directive_appears_exactly_once(self):
        """The directive is prepended by compose_prompt; running the
        runtime select_inbound_base_prompt() afterwards must not
        prepend a second copy. This locks the contract."""
        from app.domain.services.telephony.modes.caller_first import (
            select_inbound_base_prompt,
        )
        from types import SimpleNamespace

        out = compose_prompt(
            "lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS,
            direction="inbound",
        )
        call_session = SimpleNamespace(
            system_prompt=out,
            agent_config=SimpleNamespace(
                agent_name="Alex", company_name="Acme",
            ),
        )
        voice_session = SimpleNamespace(
            call_session=call_session, call_id="t",
        )
        select_inbound_base_prompt(voice_session)
        # The runtime helper's idempotency check sees the sentinel and
        # returns without modification.
        assert call_session.system_prompt == out
        assert out.count(INBOUND_DIRECTIVE_SENTINEL) == 1

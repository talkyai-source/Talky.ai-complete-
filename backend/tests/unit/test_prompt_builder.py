from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompt_builder import compose_system_prompt


BASE = "You are Alex. Be brief."


def test_compose_without_slots_returns_base_unchanged():
    # has_callback_executor=True isolates the ORIGINAL invariant this test
    # checks (no captured/conduct block for an empty state) from the new
    # CALLBACK POLICY line below, which is independent of any slot.
    out = compose_system_prompt(BASE, CallState(), has_callback_executor=True)
    assert out == BASE


def test_compose_defaults_to_no_callback_executor_and_adds_the_policy_line():
    """Every campaign today: action_tools.py has no live schedule_callback
    executor (issue: 'inbound-callback-promises-no-record' / a5e033c7's
    guardrail-forced mid-call retraction). The default must reflect that."""
    out = compose_system_prompt(BASE, CallState())
    # Round-2 reword (2026-09-24): asserts the invariant (never claim a
    # callback/booking is done), not the pre-reword bytes -- see
    # test_callback_promise_prompt_policy.py for the full finding.
    assert "never say a callback or booking has been scheduled, booked, or confirmed" in out.lower()
    assert "pass the caller's details to the team" in out.lower()
    assert BASE in out


def test_has_callback_executor_true_omits_the_policy_line():
    """Once a real executor exists, the line becomes irrelevant and drops out
    -- no campaign-side change required."""
    out = compose_system_prompt(BASE, CallState(), has_callback_executor=True)
    assert "callback policy" not in out.lower()
    assert out == BASE


def test_compose_with_email_prepends_captured_block():
    # A CONFIRMED email is a settled CAPTURED fact (issue #1).
    state = CallState(email="bob@example.com", email_confirmed=True)
    out = compose_system_prompt(BASE, state)
    assert out.startswith("CAPTURED")
    assert "bob@example.com" in out
    assert BASE in out


def test_compose_email_includes_do_not_reask_rule():
    state = CallState(email="bob@example.com", email_confirmed=True)
    out = compose_system_prompt(BASE, state)
    assert "do not ask" in out.lower() or "do not re-ask" in out.lower()


def test_compose_with_all_slots_filled():
    state = CallState(
        email="bob@example.com",
        follow_up="sunday",
        bidding_active=True,
        declined_count=1,
    )
    out = compose_system_prompt(BASE, state)
    assert "bob@example.com" in out
    assert "sunday" in out.lower()
    assert "bidding" in out.lower()


def test_compose_two_declines_mentions_close_politely():
    state = CallState(declined_count=2)
    out = compose_system_prompt(BASE, state)
    assert "declined" in out.lower()
    assert "close" in out.lower() or "end" in out.lower()


def test_compose_email_pins_value_with_natural_readback():
    # Payload-first (2026-07-02 A/B): the model gets the EXACT sentence to say —
    # the natural spoken read-back + confirm question — and a stop instruction.
    # No robotic letter-by-letter form on the live path.
    state = CallState(email="allstateestimation@gmail.com")
    out = compose_system_prompt(BASE, state)
    assert "allstateestimation@gmail.com" in out
    assert 'Say EXACTLY: "So that\'s allstateestimation at gmail dot com' in out
    assert "did i get that right" in out.lower()
    assert "a-l-l-s-t-a-t-e" not in out          # robotic spell-out is gone

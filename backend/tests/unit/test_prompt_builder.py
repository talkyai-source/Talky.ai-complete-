from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompt_builder import compose_system_prompt


BASE = "You are Alex. Be brief."


def test_compose_without_slots_returns_base_unchanged():
    out = compose_system_prompt(BASE, CallState())
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
    # Hybrid (2026-06-24): the model gets the EXACT pinned value and is told to
    # read it back NATURALLY + confirm — no more robotic letter-by-letter form on
    # the live path (the caller-facing read-back must sound human).
    state = CallState(email="allstateestimation@gmail.com")
    out = compose_system_prompt(BASE, state)
    assert "allstateestimation@gmail.com" in out
    assert "never re-transcribe" in out.lower()
    assert "naturally" in out.lower()
    assert "a-l-l-s-t-a-t-e" not in out          # robotic spell-out is gone

from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompt_builder import compose_system_prompt


BASE = "You are Alex. Be brief."


def test_compose_without_slots_returns_base_unchanged():
    out = compose_system_prompt(BASE, CallState())
    assert out == BASE


def test_compose_with_email_prepends_captured_block():
    state = CallState(email="bob@example.com")
    out = compose_system_prompt(BASE, state)
    assert out.startswith("CAPTURED")
    assert "bob@example.com" in out
    assert BASE in out


def test_compose_email_includes_do_not_reask_rule():
    state = CallState(email="bob@example.com")
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

from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompt_builder import compose_system_prompt


BASE = "You are Alex. Be brief."


def test_empty_contact_context_preserves_base_and_capability():
    # has_callback_executor=True isolates the ORIGINAL invariant this test
    # checks (no captured/conduct block for an empty state) from the new
    # CALLBACK POLICY line below, which is independent of any slot.
    out = compose_system_prompt(BASE, CallState(), has_callback_executor=True)
    assert out.endswith(BASE)
    assert '"callback_scheduling_available": true' in out
    assert '"email"' not in out


def test_default_callback_capability_is_unavailable():
    out = compose_system_prompt(BASE, CallState())
    assert '"callback_scheduling_available": false' in out
    assert "proof of an external action" in out and BASE in out


def test_callback_capability_can_be_available():
    out = compose_system_prompt(BASE, CallState(), has_callback_executor=True)
    assert '"callback_scheduling_available": true' in out
    assert "CALLBACK POLICY" not in out and out.endswith(BASE)


def test_compose_with_email_prepends_captured_block():
    # A CONFIRMED email is a settled CAPTURED fact (issue #1).
    out = compose_system_prompt(BASE, CallState(email="bob@example.com", email_confirmed=True))
    assert out.startswith("CONTACT CONTEXT")
    assert '"status": "confirmed"' in out and "bob@example.com" in out
    assert out.endswith(BASE)


def test_confirmed_email_is_context_without_commands():
    out = compose_system_prompt(BASE, CallState(email="bob@example.com", email_confirmed=True))
    assert '"status": "confirmed"' in out
    assert "do not ask" not in out.lower() and "Say EXACTLY" not in out


def test_compose_with_all_slots_filled():
    state = CallState(email="bob@example.com", follow_up="sunday", bidding_active=True, declined_count=1)
    out = compose_system_prompt(BASE, state)
    assert "bob@example.com" in out
    assert '"requested_follow_up_not_scheduled": "sunday"' in out
    assert "ACTION THIS TURN" not in out


def test_decline_counter_cannot_command_closing():
    out = compose_system_prompt(BASE, CallState(declined_count=2))
    assert "close" not in out.lower() and "end the call" not in out.lower()
    assert out.endswith(BASE)


def test_pending_email_has_no_compulsory_readback():
    # Payload-first (2026-07-02 A/B): the model gets the EXACT sentence to say —
    # the natural spoken read-back + confirm question — and a stop instruction.
    # No robotic letter-by-letter form on the live path.
    out = compose_system_prompt(BASE, CallState(email="allstateestimation@gmail.com"))
    assert "allstateestimation@gmail.com" in out
    assert "awaiting_confirmation" in out
    assert "Say EXACTLY" not in out and "did i get that right" not in out.lower()

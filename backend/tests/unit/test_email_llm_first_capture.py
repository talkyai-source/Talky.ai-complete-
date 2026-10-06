"""Historical readback normalizer controls plus original-call correction regression.

The live model now interprets corrections via record_contact. Utility tests do
not claim a parser or a scripted readback is still on the live path."""
from __future__ import annotations

import pytest

from app.services.scripts.spoken_email_normalizer import extract_email_from_agent_readback


# ── reading the address out of the agent's read-back ──────────────────────

@pytest.mark.parametrize("turn,expected", [
    # The sentence after the address is not part of it (was "gmail.com.is").
    ("Let me confirm that — allstate dot estimation at gmail dot com. Is that correct?",
     "allstate.estimation@gmail.com"),
    ("Allstate dot state dot estimation at gmail dot com. Is that correct?",
     "allstate.state.estimation@gmail.com"),
    # Two addresses in one read-back: the one next to the question counts.
    ("Allstate estimation at gmail dot com — so allstateestimation at gmail dot com. Is that correct?",
     "allstateestimation@gmail.com"),
    # Letter-by-letter spelling and "all one word" are how people read it back.
    ("So that's a-l-l-s-t-a-t-e-estimation, all one word, at gmail dot com. Is that right?",
     "allstateestimation@gmail.com"),
    ("So that's allstateestimation at gmail dot com, no dots — is that right?",
     "allstateestimation@gmail.com"),
    ("Thanks, bob at gmail dot com, correct?", "bob@gmail.com"),
])
def test_the_read_back_address_is_read_correctly(turn, expected):
    assert extract_email_from_agent_readback(turn) == expected


@pytest.mark.parametrize("turn", [
    "Should I reach you at your gmail dot com address?",
    "You work at microsoft and it's bob at gmail dot com, right?",
    "I'll send it to all state estimation at gmail dot com.",  # no question
])
def test_sentences_that_are_not_a_read_back_give_nothing(turn):
    assert extract_email_from_agent_readback(turn) is None


# ── the value on the table follows the latest read-back ───────────────────


# ── the whole call, replayed ──────────────────────────────────────────────


# ── the agent is told to ASK, not guess ───────────────────────────────────


@pytest.mark.asyncio
async def test_original_call_dot_correction_is_pending_until_later_model_confirmation():
    from app.domain.services.voice_pipeline.contact_recording import record_contact
    from tests.unit.test_model_contact_recording import SQLPort, args, caller, session

    pool, state = SQLPort(), session()
    first = "Allstate estimation at Gmail dot com."
    caller(state, first, 1)
    await record_contact(state, args(first, value="allstate.estimation@gmail.com"), pool=pool)
    correction = "Remove the dot."
    caller(state, correction, 2)
    result = await record_contact(state, args(correction, value="allstateestimation@gmail.com",
        expected="allstate.estimation@gmail.com"), pool=pool)
    assert result["saved"] and result["validation_status"] == "awaiting_confirmation"
    assert not state.captured_slots.email_confirmed
    # The fixture models interpretation; no scripted assistant readback proves correctness.
    caller(state, "Yeah. Perfect.", 3)
    result = await record_contact(state, args("Yeah. Perfect.", operation="confirm",
        value="allstateestimation@gmail.com", expected="allstateestimation@gmail.com"), pool=pool)
    assert result["saved"] and state.captured_slots.email_confirmed
    capture = state.captured_slots.email_capture
    assert capture.normalized_value == "allstateestimation@gmail.com"
    assert capture.confirmation_evidence == "model_interpreted_caller_confirmation"
    assert capture.readback is None and capture.value_source.caller_turn_order == 2

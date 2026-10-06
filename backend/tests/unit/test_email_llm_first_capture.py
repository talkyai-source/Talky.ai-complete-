"""Historical readback normalizer controls plus original-call correction regression.

The live model now interprets corrections via record_contact. Utility tests do
not claim a parser or a scripted readback is still on the live path."""
from __future__ import annotations

import pytest


# ── reading the address out of the agent's read-back ──────────────────────


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

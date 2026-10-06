"""Call5dfa4416: keep pending email/phone visible without a scripted dialogue.

Current model-tool regression uses synthetic arguments/SQL; it does not prove
speech understanding. The pending-state check protects caller evidence ownership.
"""
from __future__ import annotations

import pytest
from app.services.scripts.prompt_builder import compose_system_prompt


from app.domain.services.voice_pipeline.contact_capture import CaptureStatus, ContactCaptureState
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots

from app.services.scripts.call_state_tracker import CallState

READBACK = "So that's allstateestimation at gmail dot com — did I get that right?"


def test_unowned_pending_state_cannot_be_saved_as_caller_evidence():
    rows = snapshot_slots(_open_state())
    assert "email" not in rows  # No caller source owns this synthetic display state.


# ── review findings: none of these may be hijacked ──────────────────────

def _open_state():
    return CallState(email="allstateestimation@gmail.com", email_confirmed=False,
        phone_capture=ContactCaptureState(kind="phone", status=CaptureStatus.NEEDS_CLARIFICATION,
            raw_value="And note down my mobile number as well?"))




@pytest.mark.asyncio
async def test_model_phone_request_preserves_pending_email_and_unrelated_turn_does_not_consume_attempt():
    # Model-selected arguments exercise persistence, not a speech-understanding claim.
    from app.domain.services.voice_pipeline.contact_recording import record_contact
    from tests.unit.test_model_contact_recording import SQLPort, args, caller, session
    pool, state = SQLPort(), session()
    email = "allstateestimation@gmail.com"
    caller(state, "Allstate estimation at Gmail dot com.", 1)
    result = await record_contact(state, args("Allstate estimation at Gmail dot com.", value=email), pool=pool)
    assert result["saved"] and not state.captured_slots.email_confirmed
    request = "And note down my mobile number as well?"
    caller(state, request, 2)
    result = await record_contact(state, args(request, kind="phone", value=request), pool=pool)
    assert result["saved"] and result["validation_status"] == "needs_clarification"
    rows = snapshot_slots(state.captured_slots)
    assert rows["email"]["value"] == email and not rows["email"]["confirmed"]
    assert rows["phone"]["value"] is None and rows["phone"]["raw_value"] == request
    # An unrelated accepted caller turn does not manufacture a failed number attempt.
    caller(state, "It's two PM Sunday.", 3)
    assert snapshot_slots(state.captured_slots) == rows
    assert state.captured_slots.phone_capture.attempts == 0
    prompt = compose_system_prompt("BASE", state.captured_slots)
    assert email in prompt and request in prompt
    caller(state, "Yes, that email is correct.", 4)
    result = await record_contact(state, args("Yes, that email is correct.", operation="confirm", value=email, expected=email), pool=pool)
    assert result["saved"] and state.captured_slots.email_confirmed
    assert state.captured_slots.phone_capture.status is CaptureStatus.NEEDS_CLARIFICATION
    assert {"email", "phone"} <= {parameters[4] for _, parameters in pool.writes}

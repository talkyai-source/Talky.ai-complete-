"""Call1436672a: preserve multiple contacts through the current model tool.

The historical standalone link/readback/callback detector controls remain utility
regressions, not evidence of live speech rewriting. Scripted parser replay and
source-string assertions of the deleted speech judge were retired."""
from __future__ import annotations

from app.domain.services.voice_pipeline.conversation_guards import (
    phone_readback_changed,
    promises_timed_callback,
)
from app.domain.services.voice_pipeline.grounded_links import ground_spoken_links
from app.domain.services.voice_pipeline.lead_slot_capture import (
    snapshot_slots,
)


# ── 1. an email's name part is not a web address ──────────────────────────

def test_an_email_read_back_is_not_rewritten_as_our_website():
    line = "Let me confirm that — john.cena at gmail dot com. Is that correct?"
    spoken, changed = ground_spoken_links(line, ["allstateestimation.co.uk"])
    assert spoken == line
    assert changed == []


def test_a_made_up_web_address_is_still_replaced():
    spoken, changed = ground_spoken_links(
        "See allstate-samples.com/reports for examples.", ["allstateestimation.co.uk"]
    )
    assert "our website" in spoken
    assert changed


# ── 2. a spoken number with no country code ───────────────────────────────


def test_legacy_readback_detector_recognizes_dropped_digit():
    caller = ["zero three one two, zero seven five, zero four nine six."]
    wrong = "So that’s 0 3 1 2 , 0 7 5 , 0 4 9 Is that correct?"
    right = "So that's plus 9 2, 3 1 2, 0 7 5, 0 4 9 6 — did I get that right?"
    assert phone_readback_changed(wrong, caller, "+923120750496") is True
    assert phone_readback_changed(right, caller, "+923120750496") is False


# ── 5. no call back at a time the agent cannot book ──────────────────────

def test_a_timed_call_back_promise_is_caught():
    assert promises_timed_callback("We’ll ring you at 2 pm on that number.") is True
    assert promises_timed_callback("Would you like a call back at 2 pm?") is False
    assert promises_timed_callback("I'll pass on 2 pm as your preferred time.") is False


# ── 3 + 4. the second email: asked properly, confirmed, and stored ───────


import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,first,second", [
    ("email", "allstateestimation@gmail.com", "johncena@gmail.com"),
    ("phone", "+923120750496", "+447911123456"),
])
async def test_original_call_second_contact_is_saved_beside_confirmed_first(kind, first, second):
    from app.domain.services.voice_pipeline.contact_recording import record_contact
    from tests.unit.test_model_contact_recording import SQLPort, args, caller, session

    pool, state = SQLPort(), session()
    caller(state, first, 1)
    await record_contact(state, args(first, kind=kind, value=first), pool=pool)
    caller(state, "Yes.", 2)
    await record_contact(state, args("Yes.", kind=kind, operation="confirm", value=first, expected=first), pool=pool)
    extra = "Record my other contact as well: " + second
    caller(state, extra, 3)
    result = await record_contact(state, args(extra, kind=kind, operation="add", value=second, expected=first), pool=pool)
    rows = snapshot_slots(state.captured_slots)
    assert result["saved"] and rows[kind]["value"] == first and rows[kind]["confirmed"]
    assert rows[kind + "_2"]["value"] == second and not rows[kind + "_2"]["confirmed"]
    caller(state, "Yes, that second one is correct.", 4)
    result = await record_contact(state, args("Yes, that second one is correct.", kind=kind,
        operation="confirm", value=second, expected=second), pool=pool)
    rows = snapshot_slots(state.captured_slots)
    assert result["saved"] and rows[kind + "_2"]["confirmed"]
    assert rows[kind]["value"] == first and rows[kind]["confirmed"]
    assert {kind, kind + "_2"} <= {parameters[4] for _, parameters in pool.writes}

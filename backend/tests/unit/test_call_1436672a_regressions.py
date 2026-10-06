"""Call1436672a: preserve multiple contacts through the current model tool.

The historical standalone link/readback/callback detector controls remain utility
regressions, not evidence of live speech rewriting. Scripted parser replay and
source-string assertions of the deleted speech judge were retired."""
from __future__ import annotations

from app.domain.services.voice_pipeline.lead_slot_capture import (
    snapshot_slots,
)


# ── 1. an email's name part is not a web address ──────────────────────────


# ── 2. a spoken number with no country code ───────────────────────────────


# ── 5. no call back at a time the agent cannot book ──────────────────────


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

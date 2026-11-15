"""A caller's address is a live, confirmable detail with the rules the name follows."""
import json

import pytest

from app.domain.services.call_summary.business_details import extractable_campaign_fields
from app.domain.services.voice_pipeline.contact_recording import CONTACT_TOOL_SPEC, record_contact
from app.domain.services.voice_pipeline.lead_slot_capture import (
    pending_contact_revocations, snapshot_slots,
)
from app.services.scripts.prompt_builder import compose_system_prompt
from tests.unit.test_model_contact_recording import SQLPort, args, caller, session

A = "12 Elm Road, Leeds"
B = "40 Oak Lane, York"


def _address(s):
    return snapshot_slots(s.captured_slots).get("address")


async def _set(s, pool, text, order, value, expected=None):
    caller(s, text, order)
    return await record_contact(s, args(text, kind="address", value=value, expected=expected), pool=pool)


async def _confirm(s, pool, text, order, value):
    caller(s, text, order)
    return await record_contact(s, args(text, kind="address", operation="confirm", value=value,
                                        expected=value), pool=pool)


def test_tool_schema_lists_address_kind():
    kinds = CONTACT_TOOL_SPEC["function"]["parameters"]["properties"]["kind"]["enum"]
    assert "address" in kinds and kinds[:4] == ["email", "phone", "full_name", "company_name"]


@pytest.mark.asyncio
async def test_address_pending_then_confirmed_on_a_later_turn():
    pool, s = SQLPort(), session()
    pending = await _set(s, pool, f"I live at {A}.", 1, A)
    assert pending["saved"] and pending["validation_status"] == "awaiting_confirmation"
    row = _address(s)
    assert row["value"] == A and row["field_type"] == "text" and not row["confirmed"]
    done = await _confirm(s, pool, "Yes, that's right.", 2, A)
    assert done["saved"] and done["validation_status"] == "confirmed"
    assert _address(s)["confirmed"] and _address(s)["value"] == A


@pytest.mark.asyncio
async def test_address_is_stored_trimmed_and_whitespace_collapsed():
    pool, s = SQLPort(), session()
    text = "It is 12   Elm Road,  Leeds"
    caller(s, text, 1)
    result = await record_contact(s, args(text, kind="address", value="  12   Elm Road,\n Leeds  "), pool=pool)
    assert result["saved"] and result["value"] == "12 Elm Road, Leeds"


@pytest.mark.asyncio
async def test_confirm_on_the_same_turn_is_refused():
    pool, s = SQLPort(), session()
    text = f"I live at {A}."
    await _set(s, pool, text, 1, A)
    result = await record_contact(s, args(text, kind="address", operation="confirm", value=A, expected=A), pool=pool)
    assert result["status"] == "confirmation_not_current" and not _address(s)["confirmed"]


@pytest.mark.asyncio
async def test_correction_revokes_old_confirmed_row_and_saves_new_pending_then_confirmed():
    pool, s = SQLPort(), session()
    await _set(s, pool, f"I live at {A}.", 1, A)
    await _confirm(s, pool, "Yes.", 2, A)
    corrected = await _set(s, pool, f"Sorry, it is {B}.", 3, B, expected=A)
    assert corrected["saved"] and corrected["validation_status"] == "awaiting_confirmation"
    assert s.captured_slots.address == B and not s.captured_slots.address_confirmed
    assert not _address(s)["confirmed"] and _address(s)["value"] == B
    assert any("UPDATE call_lead_details" in sql for sql, _ in pool.writes)
    assert pending_contact_revocations(s) == {}  # revocation already applied
    done = await _confirm(s, pool, "Yes, that one.", 4, B)
    assert done["saved"] and done["validation_status"] == "confirmed"
    final = _address(s)
    assert final["value"] == B and final["confirmed"] is True
    assert any(B in repr(a) for _, a in pool.writes)


@pytest.mark.asyncio
async def test_correction_of_confirmed_address_needs_the_current_value():
    pool, s = SQLPort(), session()
    await _set(s, pool, f"I live at {A}.", 1, A)
    await _confirm(s, pool, "Yes.", 2, A)
    result = await _set(s, pool, f"No, {B}.", 3, B, expected="Something else")
    assert result["status"] == "contact_changed"
    assert _address(s)["value"] == A and _address(s)["confirmed"]


@pytest.mark.asyncio
async def test_newer_caller_words_replace_a_still_pending_address_without_expected_value():
    pool, s = SQLPort(), session()
    await _set(s, pool, f"I live at {A}.", 1, A)
    result = await _set(s, pool, f"No wait, {B}.", 2, B)
    assert result["saved"] and result["value"] == B
    assert _address(s)["value"] == B and not _address(s)["confirmed"]


@pytest.mark.asyncio
async def test_add_is_refused_for_address_and_withdraw_clears_it():
    pool, s = SQLPort(), session()
    await _set(s, pool, f"I live at {A}.", 1, A)
    await _confirm(s, pool, "Yes.", 2, A)
    caller(s, "Another one is", 3)
    refused = await record_contact(s, args("Another one is", kind="address", operation="add", value=B,
                                           expected=A), pool=pool)
    assert refused["status"] == "additional_contact_not_ready" and s.captured_slots.address == A
    caller(s, "Forget the address please", 4)
    gone = await record_contact(s, args("Forget the address please", kind="address", operation="withdraw",
                                        value=None, expected=A), pool=pool)
    assert gone["saved"] and gone["validation_status"] == "cancelled"
    assert _address(s)["value"] is None


@pytest.mark.asyncio
async def test_revised_caller_transcript_withdraws_the_address():
    pool, s = SQLPort(), session()
    await _set(s, pool, f"I live at {A}.", 1, A)
    caller(s, "Something different", 1)  # same item id/order, revised words
    assert s.captured_slots.address is None and not s.captured_slots.address_confirmed


@pytest.mark.asyncio
async def test_address_appears_in_the_live_contact_state_and_other_kinds_are_unaffected():
    pool, s = SQLPort(), session()
    await _set(s, pool, f"I live at {A}.", 1, A)
    caller(s, "I'm Sam Doe", 2)
    await record_contact(s, args("I'm Sam Doe", kind="full_name", value="Sam Doe"), pool=pool)
    facts = json.loads(compose_system_prompt("GUIDE", s.captured_slots).split("\n")[1])
    assert facts["address"] == {"field_key": "address", "value": A, "status": "awaiting_confirmation"}
    assert facts["full_name"]["value"] == "Sam Doe"


def test_post_call_extraction_still_fills_a_campaign_address_field():
    fields = [{"field_key": "address", "label": "Address", "field_type": "text"},
              {"field_key": "Address", "label": "Address", "field_type": "notes"},
              {"field_key": "budget", "label": "Budget", "field_type": "text"}]
    assert [f["field_key"] for f in extractable_campaign_fields(fields)] == ["address", "budget"]

"""Selected contact ownership through the shared tool and real lead SQL builder.

Only the database transport and model/provider events are synthetic.
"""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.services.voice_pipeline.action_execution import _contact
from app.domain.services.voice_pipeline.contact_recording import record_contact
from app.domain.services.voice_pipeline.lead_slot_capture import (
    capture_turn_slots, contact_outcome, contact_save_acknowledged, snapshot_slots,
)
from app.services.scripts.prompt_builder import compose_system_prompt
from tests.unit.test_model_contact_recording import SQLPort, args, caller, native, session


VALUES = {
    "email": ("alex@example.com", "blair@example.com", "casey@example.com"),
    "phone": ("+14155552671", "+442079460123", "+14155552672"),
}


async def two_contacts(s, pool, kind="email"):
    first, second, _ = VALUES[kind]
    for order, operation, value, expected in [
        (1, "set", first, None), (2, "confirm", first, first),
        (3, "add", second, first), (4, "confirm", second, second),
    ]:
        text = f"I want {value} {operation}."
        caller(s, text, order)
        result = await record_contact(s, args(text, kind=kind, operation=operation,
            value=value, expected=expected), pool=pool)
        assert result["saved"]


def target_args(text, kind, operation, value, expected, key=None):
    return {**args(text, kind=kind, operation=operation, value=value, expected=expected),
            "field_key": key or kind}


def writes_for(pool, key):
    return [(sql, p) for sql, p in pool.writes
            if (p[4] if "INSERT INTO" in sql else p[2]) == key]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["email", "phone"])
@pytest.mark.parametrize("operation", ["set", "withdraw"])
async def test_earlier_target_changes_its_lead_row_without_changing_current_recipient(kind, operation):
    pool, s = SQLPort(), session()
    await two_contacts(s, pool, kind)
    first, second, replacement = VALUES[kind]
    current = getattr(s.captured_slots, f"{kind}_capture")
    recipient = _contact(s, kind)
    before_current_writes = len(writes_for(pool, f"{kind}_2"))
    text = "Correct my first contact." if operation == "set" else "Remove my first contact."
    caller(s, text, 5)
    value = replacement if operation == "set" else None
    result = await record_contact(s, target_args(text, kind, operation, value, first), pool=pool)
    assert result["saved"] and result["field_key"] == kind
    assert getattr(s.captured_slots, f"{kind}_capture") is current
    assert _contact(s, kind) == recipient == (second, str(current.confirmed_at))
    assert len(writes_for(pool, f"{kind}_2")) == before_current_writes
    row = snapshot_slots(s.captured_slots)[kind]
    assert row["value"] == value and not row["confirmed"] and row["confirmed_at"] is None
    assert row["validation_status"] == ("awaiting_confirmation" if value else "cancelled")
    assert row["evidence"]["status_source"]["caller_turn_order"] == 5
    selected_writes = writes_for(pool, kind)
    assert "UPDATE call_lead_details" in selected_writes[-2][0]
    payload = selected_writes[-1][1]
    assert payload[6] == value and payload[8] is False and payload[12] == row["validation_status"]
    assert json.loads(payload[15])["status_source"] == row["evidence"]["status_source"]
    facts = json.loads(compose_system_prompt("GUIDE", s.captured_slots).split("\n")[1])
    assert facts[f"earlier_{kind}"][0] == {
        "field_key": kind, "value": value, "status": row["validation_status"],
        **({"caller_quote": text} if value is None else {}),
    }
    assert facts[kind]["field_key"] == f"{kind}_2"
    assert "earlier_confirmed_" not in str(facts)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_native_actual_tool_can_discover_and_withdraw_first_contact(provider):
    replay, pool = native(provider), SQLPort()
    s = replay.session
    s._lead_capture_binding = None
    bound = session()
    for name in ("_dialer_call_id", "_dialer_tenant_id", "_dialer_campaign_id", "_dialer_lead_id"):
        setattr(s, name, getattr(bound, name))
    s._voice_action_pool = pool
    first, second, _ = VALUES["email"]
    for operation, value, expected in [("set", first, None), ("confirm", first, first),
            ("add", second, first), ("confirm", second, second), ("withdraw", None, first)]:
        text = f"Please {operation} {value or 'the first address'}."
        await replay.step({"kind": "caller", "text": text})
        arguments = args(text, operation=operation, value=value, expected=expected)
        if operation == "withdraw":
            arguments["field_key"] = "email"
        await replay.step({"kind": "tool", "name": "record_contact", "arguments": arguments})
    results = [json.loads(row["item"]["output"]) for row in replay.socket.sent
               if row.get("item", {}).get("type") == "function_call_output"]
    assert results[2]["contacts"]["email"]["value"] == first
    assert results[2]["contacts"]["email_2"]["value"] == second
    assert results[-1]["saved"] and results[-1]["validation_status"] == "cancelled"
    assert snapshot_slots(s.captured_slots)["email"]["value"] is None
    assert replay.bridge._live_state.confirmed_email == second


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", ["value", "confirmation"])
async def test_archived_source_revision_demotes_only_its_owned_slot(revision):
    pool, s = SQLPort(), session()
    await two_contacts(s, pool)
    current = s.captured_slots.email_capture
    caller(s, "Correction to earlier caller words.", 1 if revision == "value" else 2)
    await capture_turn_slots(s, pool=pool)
    earlier = s.captured_slots.earlier_email_captures[0]
    assert not snapshot_slots(s.captured_slots)["email"]["confirmed"]
    assert earlier.normalized_value == (None if revision == "value" else VALUES["email"][0])
    assert s.captured_slots.email_capture is current and s.captured_slots.email_confirmed


@pytest.mark.asyncio
async def test_unselected_old_value_does_not_implicitly_choose_an_earlier_contact():
    pool, s = SQLPort(), session()
    await two_contacts(s, pool)
    caller(s, "Remove the first address.", 5)
    result = await record_contact(s, args("Remove the first address.", operation="withdraw",
        value=None, expected=VALUES["email"][0]), pool=pool)
    assert result["status"] == "contact_changed" and not result["saved"]
    assert snapshot_slots(s.captured_slots)["email"]["confirmed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fail", "conflict"])
async def test_selected_slot_cannot_borrow_current_acknowledgement_and_retry_is_exact(mode):
    pool, s = SQLPort(), session()
    await two_contacts(s, pool)
    setattr(pool, mode, True)
    text = "Correct the first address to casey@example.com."
    caller(s, text, 5)
    arguments = target_args(text, "email", "set", VALUES["email"][2], VALUES["email"][0])
    result = await record_contact(s, arguments, pool=pool)
    assert result["status"] == ("save_failed" if mode == "fail" else "not_saved")
    assert not result["saved"] and not result["confirmation_allowed"]
    assert contact_save_acknowledged(s, "email")  # B's old acknowledgement is still valid.
    assert not contact_save_acknowledged(s, "email", field_key="email")
    setattr(pool, mode, False)
    arguments["expected_value"] = VALUES["email"][2]
    result = await record_contact(s, arguments, pool=pool)
    assert result["saved"] and result["field_key"] == "email"
    count = len(pool.writes)
    assert (await record_contact(s, arguments, pool=pool))["saved"]
    assert len(pool.writes) == count


@pytest.mark.asyncio
async def test_earlier_partial_correction_stays_visible_then_requires_later_confirmation():
    pool, s = SQLPort(), session()
    await two_contacts(s, pool)
    text = "The first one should be casey at."
    caller(s, text, 5)
    result = await record_contact(s, target_args(text, "email", "set", "casey at", VALUES["email"][0]), pool=pool)
    assert result["saved"] and result["validation_status"] == "needs_clarification"
    assert result["caller_quote"] == text and result["value"] is None
    assert snapshot_slots(s.captured_slots)["email"]["raw_value"] == text
    text = "casey@example.com is the full first address."
    caller(s, text, 6)
    assert (await record_contact(s, target_args(text, "email", "set", VALUES["email"][2], None), pool=pool))["saved"]
    confirmation = target_args(text, "email", "confirm", VALUES["email"][2], VALUES["email"][2])
    assert (await record_contact(s, confirmation, pool=pool))["status"] == "confirmation_not_current"
    caller(s, "Yes, that first address is right.", 7)
    confirmation["source_quote"] = "Yes, that first address is right."
    result = await record_contact(s, confirmation, pool=pool)
    assert result["saved"] and result["validation_status"] == "confirmed"
    earlier = s.captured_slots.earlier_email_captures[0]
    assert earlier.value_source.caller_turn_order == 6 and earlier.confirmation_source.caller_turn_order == 7
    assert _contact(s, "email")[0] == VALUES["email"][1]


@pytest.mark.asyncio
async def test_withdrawn_position_is_not_reused_and_all_withdrawn_is_not_confirmed():
    pool, s = SQLPort(), session()
    await two_contacts(s, pool)
    caller(s, "Remove the first address.", 5)
    assert (await record_contact(s, target_args("Remove the first address.", "email", "withdraw",
        None, VALUES["email"][0]), pool=pool))["saved"]
    caller(s, "Add casey@example.com as well.", 6)
    result = await record_contact(s, args("Add casey@example.com as well.", operation="add",
        value=VALUES["email"][2], expected=VALUES["email"][1]), pool=pool)
    assert result["field_key"] == "email_3" and result["saved"]
    assert list(snapshot_slots(s.captured_slots)) == ["email", "email_2", "email_3"]
    assert snapshot_slots(s.captured_slots)["email"]["value"] is None
    for order, key, value in [(7, "email_2", VALUES["email"][1]), (8, "email_3", VALUES["email"][2])]:
        text = f"Remove {value}."
        caller(s, text, order)
        assert (await record_contact(s, target_args(text, "email", "withdraw", None, value, key), pool=pool))["saved"]
    assert contact_outcome(s.captured_slots)["email"] == "none"


@pytest.mark.asyncio
@pytest.mark.parametrize("key,status", [("email_1", "contact_not_found"), ("email_99", "contact_not_found"),
    ("phone", "contact_not_found"), ("email_02", "contact_not_found"), ("", "invalid_arguments"),
    (True, "invalid_arguments"), ([], "invalid_arguments"), ({}, "invalid_arguments")])
async def test_invalid_selector_never_changes_another_contact(key, status):
    pool, s = SQLPort(), session()
    await two_contacts(s, pool)
    before, count = s.captured_slots, len(pool.writes)
    caller(s, "Remove the first contact.", 5)
    arguments = {**target_args("Remove the first contact.", "email", "withdraw", None, VALUES["email"][0]),
                 "field_key": key}
    result = await record_contact(s, arguments, pool=pool)
    assert result["status"] == status and not result["saved"]
    assert s.captured_slots is before and len(pool.writes) == count


@pytest.mark.asyncio
async def test_target_cas_source_quote_and_add_current_only_boundaries():
    pool, s = SQLPort(), session()
    await two_contacts(s, pool)
    text = "Please change my first address."
    caller(s, text, 5)
    before, count = s.captured_slots, len(pool.writes)
    cases = [
        ({"expected_value": VALUES["email"][1]}, "contact_changed"),
        # An invented quote is refused as quote_not_found (it is checked against
        # every caller turn of the call since 2026-10-08, not only the current one).
        ({"source_quote": "Words the caller did not say."}, "quote_not_found"),
        ({"operation": "add"}, "additional_contact_not_ready"),
    ]
    for change, status in cases:
        arguments = {**target_args(text, "email", "set", VALUES["email"][2], VALUES["email"][0]), **change}
        assert (await record_contact(s, arguments, pool=pool))["status"] == status
    assert s.captured_slots is before and len(pool.writes) == count


@pytest.mark.asyncio
async def test_current_null_selector_and_archived_conflict_do_not_depend_on_value_uniqueness():
    pool, s = SQLPort(), session()
    await two_contacts(s, pool)
    text = "Use the first address for the second contact too."
    caller(s, text, 5)
    current_arguments = {**args(text, value=VALUES["email"][0], expected=VALUES["email"][1]), "field_key": None}
    assert (await record_contact(s, current_arguments, pool=pool))["field_key"] == "email_2"
    text = "Remove the first entry only."
    caller(s, text, 6)
    arguments = target_args(text, "email", "withdraw", None, VALUES["email"][0])
    first, conflict = await asyncio.gather(record_contact(s, arguments, pool=pool), record_contact(s, arguments, pool=pool))
    assert first["saved"] and conflict["status"] == "contact_changed"
    rows = snapshot_slots(s.captured_slots)
    assert rows["email"]["value"] is None and rows["email_2"]["value"] == VALUES["email"][0]


@pytest.mark.asyncio
async def test_archived_revision_during_write_never_claims_current_save():
    pool, s = SQLPort(), session()
    await two_contacts(s, pool)
    current = s.captured_slots.email_capture
    pool.entered.clear()
    pool.release = asyncio.Event()
    text = "Change the first address to casey@example.com."
    turn = caller(s, text, 5, "correction")
    task = asyncio.create_task(record_contact(s, target_args(text, "email", "set", VALUES["email"][2],
        VALUES["email"][0]), turn=turn, pool=pool))
    await pool.entered.wait()
    caller(s, "That was somebody else's address.", 5, "correction")
    pool.release.set()
    result = await task
    assert result["status"] == "stale_caller_turn" and not result["saved"]
    assert result["field_key"] == "email" and result["value"] is None
    assert result["validation_status"] == "needs_clarification"
    assert s.captured_slots.email_capture is current


@pytest.mark.asyncio
async def test_traditional_runner_uses_fixed_canonical_turn_for_earlier_contact_target():
    from app.domain.services.transcript_service import TranscriptService
    from app.domain.services.voice_pipeline.turn_runner import TurnRunner
    pool, s, transcripts = SQLPort(), session(), TranscriptService()
    await two_contacts(s, pool)
    text = "Remove my first address."
    s.call_id, s.turn_id, s.talklee_call_id = str(uuid4()), 5, None
    s.conversation_history, s._line_phone_checked = [], True
    row = transcripts.accumulate_turn(s.call_id, "user", text, is_final=True, turn_index=4)
    assert transcripts.bind_caller_turn(s.call_id, row, caller_turn_order=5)
    async def stream(current, websocket):
        turn = current._contact_turn
        assert turn.source.provider_item_id == "traditional:5"
        result = await record_contact(current, target_args(text, "email", "withdraw", None, VALUES["email"][0]),
            turn=turn, pool=pool)
        assert result["saved"] and result["field_key"] == "email"
        return "The first address is removed.", 1, 1
    pipeline = SimpleNamespace(transcript_service=transcripts, _stream_llm_and_tts=stream,
        _supports_llm_end_session_action=lambda _: False)
    task = asyncio.create_task(TurnRunner(pipeline).run(s, text))
    task._caller_turn_order = 5
    try:
        await task
        assert snapshot_slots(s.captured_slots)["email"]["value"] is None
        assert _contact(s, "email")[0] == VALUES["email"][1]
    finally:
        transcripts.clear_buffer(s.call_id)

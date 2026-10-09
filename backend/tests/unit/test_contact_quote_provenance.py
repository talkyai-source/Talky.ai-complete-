"""A detail confirmed now is recorded quoting where the caller actually said it.

Test call f5dcac8e (Safar Coaches, DeepSeek, 2026-10-08), replayed offline with
scripts/replay_call.py: the caller gave their name, then the number, then said
"Yes." to the read-back. Only then did the model record the contacts, quoting
"It's Uzair Majeed." and "zero three one two zero seven five zero four nine six"
from the earlier turns. The tool accepted only quotes from the current turn
("Yes."), so all four calls returned invalid_arguments, nothing was recorded,
and the agent said "Let me try saving those again" and then "noted". The same
would happen on a real phone call: confirmed leads silently lost.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.services.voice_pipeline.contact_capture import ContactSource
from app.domain.services.voice_pipeline.contact_recording import bind_contact_turn, record_contact
from app.services.scripts.call_state_tracker import CallState


class Pool:
    """Synthetic SQL port (as in test_model_contact_recording): no engine."""

    def __init__(self, is_test=False):
        self.is_test, self.writes = is_test, []

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql, *args):
        return None

    async def fetchrow(self, sql, *args):
        if "SELECT is_test" in sql:
            return {"is_test": self.is_test}
        self.writes.append(sql)
        return {"id": str(uuid4())}


def _session(pool, *, region=None, line_phone=None):
    slots = CallState(line_phone=line_phone) if line_phone else CallState()
    return SimpleNamespace(call_id="call-f5dcac8e", captured_slots=slots, contact_phone_region=region,
                           _voice_action_pool=pool, _dialer_call_id=str(uuid4()),
                           _dialer_tenant_id=str(uuid4()), _dialer_campaign_id=str(uuid4()),
                           _dialer_lead_id=None)


def _caller(s, text, order):
    return bind_contact_turn(s, text, ContactSource(f"traditional:{order}", order,
                                                     hashlib.sha256(text.strip().encode()).hexdigest()))


def _args(kind, operation, value, quote, expected=None):
    return {"kind": kind, "operation": operation, "value": value,
            "expected_value": expected, "source_quote": quote}


async def _replayed_flow(pool, **session_kwargs):
    s = _session(pool, **session_kwargs)
    _caller(s, "It's Uzair Majeed.", 11)
    _caller(s, "Uh, zero three one two zero seven five zero four nine six.", 12)
    yes = _caller(s, "Yes.", 13)
    name = await record_contact(s, _args("full_name", "set", "Uzair Majeed", "It's Uzair Majeed."), turn=yes)
    phone = await record_contact(
        s, _args("phone", "set", "03120750496", "zero three one two zero seven five zero four nine six", ""), turn=yes)
    return s, yes, name, phone


async def test_the_replayed_confirmation_turn_now_records_both_details():
    pool = Pool()
    s, yes, name, phone = await _replayed_flow(pool, line_phone="+923001234567")
    assert name["status"] == "saved" and name["saved"]
    assert phone["status"] == "saved" and phone["value"] == "+923120750496"
    capture = s.captured_slots.phone_capture
    assert capture.value_source.caller_turn_order == 12  # where the number was said
    confirmed = await record_contact(s, _args("phone", "confirm", "+923120750496", "Yes.", "+923120750496"), turn=yes)
    assert confirmed["status"] == "saved" and confirmed["validation_status"] == "confirmed"
    assert s.captured_slots.phone_capture.confirmation_source.caller_turn_order == 13
    assert pool.writes  # persisted on a real call


async def test_a_detail_given_earlier_prompts_confirmation_from_the_latest_words():
    # Replay of f5dcac8e with the fix: DeepSeek set both details on "Yes." and
    # stopped, so the caller's agreement was never recorded as confirmation.
    s, yes, name, phone = await _replayed_flow(Pool(), line_phone="+923001234567")
    for result in (name, phone):
        assert result["validation_status"] == "awaiting_confirmation"
        assert "confirm" in result["next_step"] and "latest words" in result["next_step"]
    fresh = await record_contact(
        s, _args("email", "set", "uzair@example.com", "uzair at example dot com"),
        turn=_caller(s, "It's uzair at example dot com.", 14))
    assert fresh["validation_status"] == "awaiting_confirmation" and "next_step" not in fresh


async def test_naming_the_new_value_as_expected_with_no_candidate_is_not_a_conflict():
    # Replays of f5dcac8e and 43020665 (2026-10-09): DeepSeek passed the value
    # it was setting as expected_value on a first set. Nothing could be stale,
    # yet each save was refused and the agent said "I wasn't able to save that".
    s = _session(Pool(), region="PK")
    said = "Uh, zero three one two zero seven five zero four nine six."
    turn = _caller(s, said, 1)
    first = await record_contact(s, _args("phone", "set", "+92 312 0750496", said, "+92 312 0750496"), turn=turn)
    assert first["status"] == "saved" and first["value"] == "+923120750496"
    # A candidate exists now: naming the new value instead of it is a conflict.
    later = _caller(s, "No, make it zero three one two zero seven five zero four nine seven.", 2)
    changed = await record_contact(s, _args("phone", "set", "03120750497", later.text, "03120750497"), turn=later)
    assert changed["status"] == "contact_changed"
    assert s.captured_slots.phone_capture.normalized_value == "+923120750496"
    # With no candidate, a different expected value is still a stale view.
    other = _session(Pool())
    turn = _caller(other, "It's alex at example dot com.", 1)
    stale = await record_contact(other, _args("email", "set", "alex@example.com", "alex at example dot com",
                                              "blair@example.com"), turn=turn)
    assert stale["status"] == "contact_changed" and other.captured_slots.email_capture is None


@pytest.mark.parametrize("absent", ["", "null", "NULL", " None "])
async def test_a_spelled_out_null_expected_value_means_no_candidate(absent):
    # Replay of 08b2791d (2026-10-09): DeepSeek sent expected_value "null" as
    # a string; the save was refused and only a second call recorded it.
    s = _session(Pool())
    said = "That's, um, Alex James one twenty three at Gmail dot com."
    turn = _caller(s, said, 1)
    result = await record_contact(s, _args("email", "set", "alexjames123@gmail.com", said, absent), turn=turn)
    assert result["status"] == "saved"


async def test_a_confirmation_sent_before_its_set_applies_when_the_set_lands():
    # Replay of 43020665 (2026-10-09): after "Yes." DeepSeek sent confirm, then
    # set, in one round. The confirm found nothing pending, so the caller's
    # agreement was lost although the set succeeded a moment later.
    s = _session(Pool())
    said = "That's mike at... mike one two three at g mail dot com."
    _caller(s, said, 1)
    yes = _caller(s, "Yes.", 2)
    early = await record_contact(s, _args("email", "confirm", "mike123@gmail.com", "Yes.", "mike123@gmail.com"), turn=yes)
    assert early["status"] == "confirm_deferred" and not early["saved"] and "Set it" in early["next_step"]
    landed = await record_contact(s, _args("email", "set", "mike123@gmail.com", said), turn=yes)
    assert landed["saved"] and landed["validation_status"] == "confirmed"
    capture = s.captured_slots.email_capture
    assert capture.value_source.caller_turn_order == 1 and capture.confirmation_source.caller_turn_order == 2


async def test_a_held_confirmation_needs_the_same_value_in_the_same_reply():
    s = _session(Pool())
    said = "It's alex at example dot com."
    _caller(s, said, 1)
    yes = _caller(s, "Yes.", 2)
    await record_contact(s, _args("email", "confirm", "blair@example.com", "Yes.", "blair@example.com"), turn=yes)
    other = await record_contact(s, _args("email", "set", "alex@example.com", said), turn=yes)
    assert other["validation_status"] == "awaiting_confirmation"  # agreed to a different value
    later = _caller(s, "Hmm.", 3)
    stale = await record_contact(s, _args("email", "set", "alex@example.com", said, "alex@example.com"), turn=later)
    assert stale["validation_status"] == "awaiting_confirmation"  # held only for its own reply


async def test_expected_value_matches_the_candidate_in_any_formatting():
    s = _session(Pool(), region="PK")
    said = "Uh, zero three one two zero seven five zero four nine six."
    first = _caller(s, said, 1)
    await record_contact(s, _args("phone", "set", "03120750496", said), turn=first)
    yes = _caller(s, "Yes, that's right.", 2)
    confirmed = await record_contact(
        s, _args("phone", "confirm", "+92 312 0750496", "Yes, that's right.", "+92 312 0750496"), turn=yes)
    assert confirmed["status"] == "saved" and confirmed["validation_status"] == "confirmed"


async def test_a_national_number_needs_a_country_when_nothing_in_the_call_says_it():
    s, _, _, phone = await _replayed_flow(Pool())
    assert phone["validation_status"] == "needs_clarification" and phone["value"] is None


async def test_a_quote_must_be_words_the_caller_said_in_this_call():
    s = _session(Pool())
    turn = _caller(s, "My email is alex at example dot com.", 1)
    result = await record_contact(s, _args("email", "set", "blair@example.com", "blair at example dot com"), turn=turn)
    assert result["status"] == "quote_not_found" and not result["saved"]
    assert s.captured_slots.email_capture is None


async def test_confirmation_is_about_now_not_an_earlier_turn():
    s = _session(Pool())
    _caller(s, "Use alex at example dot com.", 1)
    first = _caller(s, "Use alex at example dot com.", 1)
    await record_contact(s, _args("email", "set", "alex@example.com", "alex at example dot com"), turn=first)
    later = _caller(s, "Hang on, let me think.", 2)
    stale = await record_contact(
        s, _args("email", "confirm", "alex@example.com", "Use alex at example dot com.", "alex@example.com"), turn=later)
    assert stale["status"] == "quote_not_found"
    assert s.captured_slots.email_capture.status.value == "awaiting_confirmation"


async def test_quotes_match_whatever_the_case_and_punctuation():
    s = _session(Pool(), region="PK")
    turn = _caller(s, "uh, zero three one two zero seven five zero four nine six", 4)
    result = await record_contact(
        s, _args("phone", "set", "03120750496", "Zero three one two, zero seven five, zero four nine six."), turn=turn)
    assert result["status"] == "saved" and result["value"] == "+923120750496"


async def test_a_test_call_behaves_like_a_real_call_without_writing_lead_data():
    pool = Pool(is_test=True)
    s = _session(pool, region="PK")
    s._lead_capture_is_test = True
    turn = _caller(s, "It's zero three one two zero seven five zero four nine six.", 1)
    result = await record_contact(s, _args("phone", "set", "03120750496", "zero three one two zero seven five zero four nine six"), turn=turn)
    assert result["status"] == "saved_test_call" and result["saved"] is True
    assert pool.writes == []
    assert s.captured_slots.phone_capture.normalized_value == "+923120750496"


async def test_every_outcome_is_logged_without_the_value(caplog):
    s = _session(Pool(), region="PK")
    turn = _caller(s, "It's zero three one two zero seven five zero four nine six.", 1)
    with caplog.at_level(logging.INFO, logger="app.domain.services.voice_pipeline.contact_recording"):
        await record_contact(s, _args("phone", "set", "03120750496", "zero three one two"), turn=turn)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("record_contact_result"))
    assert "kind=phone" in line and "status=saved" in line and "quote_turn=1" in line
    assert "0312" not in line and "+92" not in line


async def test_an_email_never_gains_a_dot_the_caller_did_not_say():
    # Test call 43020665: "mike one two three at g mail dot com" was read back
    # as "mike dot one two three". The dot in "gmail dot com" proves nothing
    # about the name part.
    s = _session(Pool())
    turn = _caller(s, "That's mike at... mike one two three at g mail dot com.", 1)
    wrong = await record_contact(s, _args("email", "set", "mike.123@gmail.com", "mike one two three at g mail dot com"), turn=turn)
    assert wrong["status"] == "separator_not_said" and s.captured_slots.email_capture is None
    right = await record_contact(s, _args("email", "set", "mike123@gmail.com", "mike one two three at g mail dot com"), turn=turn)
    assert right["status"] == "saved" and right["value"] == "mike123@gmail.com"


async def test_separators_the_caller_did_say_are_kept():
    for said, value in [("j dot smith at outlook dot com", "j.smith@outlook.com"),
                        ("anna underscore lee at gmail dot com", "anna_lee@gmail.com"),
                        ("its tom.hart@example.com", "tom.hart@example.com")]:
        s = _session(Pool())
        turn = _caller(s, said, 1)
        result = await record_contact(s, _args("email", "set", value, said), turn=turn)
        assert result["status"] == "saved" and result["value"] == value, said

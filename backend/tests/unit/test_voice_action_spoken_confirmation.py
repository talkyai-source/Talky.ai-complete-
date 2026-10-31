"""Spoken authorizations compare the existing proposal, with no side effects."""
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline.action_confirmation import explicit_action_matches
from app.domain.services.voice_pipeline.action_execution import _confirmed


PHONE = "+14155552671"
SPOKEN = "plus one four one five five five five two six seven one"
EMAIL = {"recipient": "caller@example.test", "subject": "Requested details"}
CALLBACK = {"phone": PHONE, "scheduled_at": "2026-10-02T22:00:00+00:00", "timezone": "America/Los_Angeles"}


@pytest.mark.parametrize("text", [
    "Yes send the email to caller@example.test.",
    "Yes please, send the requested details to caller at example dot test.",
    "Please email me at c a l l e r at example dot test.",
    "I confirm, send the email titled Requested details to caller@example.test.",
])
def test_explicit_email_authorization_allows_spoken_address_and_asr_punctuation(text):
    assert explicit_action_matches("send_email", EMAIL, text)


@pytest.mark.parametrize("text", [
    "yes", "caller@example.test", "Send the email to other@example.test",
    "No, send the email to caller@example.test", "Please do not send the email to caller@example.test",
    "Send the email to caller@example.test and other@example.test",
    "Send the email to caller@example.test tomorrow", "If possible send the email to caller@example.test",
    "Can you send the email to caller@example.test?", "Send the email titled Another subject to caller@example.test",
])
def test_missing_ambiguous_negative_or_changed_email_authorization_is_rejected(text):
    assert not explicit_action_matches("send_email", EMAIL, text)


def test_email_punctuation_is_part_of_the_value_not_asr_punctuation():
    payload = {**EMAIL, "recipient": "bob.smith@example.test"}
    assert explicit_action_matches("send_email", payload, "send the email to bob dot smith at example dot test")
    assert not explicit_action_matches("send_email", payload, "send the email to bob smith at example dot test")


@pytest.mark.parametrize("text", [
    f"Yes transfer this call to {PHONE}", f"Please transfer me to {SPOKEN}",
    f"Connect me to {PHONE}", f"Please transfer me to {SPOKEN.removeprefix('plus ')}",
])
def test_spoken_transfer_stays_bound_to_configured_destination(text):
    assert explicit_action_matches("transfer_call", {"destination": PHONE}, text)


@pytest.mark.parametrize("number", ["4155552671", "+14155552672", "+14155552671 or +14155552672"])
def test_transfer_does_not_guess_missing_country_or_choose_among_numbers(number):
    assert not explicit_action_matches("transfer_call", {"destination": PHONE}, f"Please transfer me to {number}")


@pytest.mark.parametrize("text", [
    "Yes, call +14155552671 on October 2 2026 at 3:00 PM in America/Los_Angeles",
    f"Yes, call {SPOKEN} on October second twenty twenty six at three PM Los Angeles time.",
    f"Please schedule a callback to {PHONE} on the second of October two thousand twenty six at three o'clock P.M. in Los Angeles time",
])
def test_spoken_callback_matches_complete_canonical_date_time_zone_and_phone(text):
    assert explicit_action_matches("schedule_callback", CALLBACK, text)


@pytest.mark.parametrize("tail", [
    "on October second twenty twenty six at three Los Angeles time",
    "on October second twenty twenty six at three AM Los Angeles time",
    "on October third twenty twenty six at three PM Los Angeles time",
    "on October second twenty twenty seven at three PM Los Angeles time",
    "on October second at three PM Los Angeles time",
    "tomorrow at three PM Los Angeles time",
    "on October second twenty twenty six at three PM CST",
    "on October second twenty twenty six at three PM London time",
    "on October second twenty twenty six at three PM Los Angeles time or four PM",
])
def test_callback_does_not_guess_missing_or_conflicting_parameters(tail):
    assert not explicit_action_matches("schedule_callback", CALLBACK, f"Yes call {PHONE} {tail}")


def test_form_repeats_caller_fields_without_reciting_internal_inbox():
    payload = {"name": "Interest", "recipient": "private-inbox@example.test",
               "values": {"email": "caller@example.test", "phone": PHONE, "project_type": "commercial"}}
    text = f"Please submit the Interest form with email caller at example dot test and phone {SPOKEN} and project type commercial"
    assert explicit_action_matches("submit_form", payload, text)
    assert not explicit_action_matches("submit_form", payload, text.replace("commercial", "residential"))
    assert not explicit_action_matches("submit_form", payload, text + " and email other@example.test")


def test_same_turn_stale_values_and_bare_yes_remain_blocked_without_receipt():
    proposal = {"turn": 1, "action": "send_email", "payload": EMAIL,
                "summary": "send the email titled Requested details to caller@example.test"}
    session = SimpleNamespace(turn_id=1, _voice_action_delivered_text="")
    text = "Please send the email to caller at example dot test"
    assert not _confirmed(session, proposal, text)
    session.turn_id = 2
    assert _confirmed(session, proposal, text)
    assert not _confirmed(session, proposal, "yes")
    assert not _confirmed(session, {**proposal, "payload": {**EMAIL, "recipient": "new@example.test"}}, text)
    assert not _confirmed(session, proposal, "No, " + text)

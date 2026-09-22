"""A two-word spoken email must be asked about, never guessed.

Production, call 2427af7e (2026-09-22). The caller said:

    "Yeah. It is, uh, john co at g mail dot com."

The deterministic normaliser correctly refused to choose between johnco@ and
john.co@ -- extract_email_from_speech returned None -- but nothing said so. The
model filled the gap and read back "j o h n dot c o at g m a i l dot c o m",
inserting a dot the caller never spoke. Asked to read it back plainly, it
spelled it again; then it asked the caller to spell it, which was the opposite
of what was asked:

    "I'm not asking for the spelling. I asked that read it together what I
     have shared."

The fix names both readings so the caller can settle it in one word, and tells
the model outright never to say an address the caller has not given.
"""
from __future__ import annotations

import pytest

from app.domain.services.voice_pipeline.contact_capture import (
    CaptureStatus,
    _spoken_local_candidates,
)
from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt


@pytest.mark.parametrize(
    "spoken,joined,dotted",
    [
        ("yeah it is john co at gmail dot com", "johnco@gmail.com", "john.co@gmail.com"),
        # The exact utterance from call 2427af7e: STT split "gmail" in two.
        (
            "Yeah. It is, uh, john co at g mail dot com.",
            "johnco@gmail.com",
            "john.co@gmail.com",
        ),
        ("john co at gmail dot com", "johnco@gmail.com", "john.co@gmail.com"),
        (
            "my email is sarah jones at acme dot co dot uk",
            "sarahjones@acme.co.uk",
            "sarah.jones@acme.co.uk",
        ),
    ],
)
def test_two_word_locals_yield_both_readings(spoken, joined, dotted):
    assert _spoken_local_candidates(spoken) == (joined, dotted)


@pytest.mark.parametrize(
    "spoken",
    [
        "all state estimation at gmail dot com",  # three words: spell instead
        "b o b at gmail dot com",                 # letter-by-letter spelling
        "johnco at gmail dot com",                # already unambiguous
        "john dot co at gmail dot com",           # separator was spoken
        "call me at nine",                         # not an email at all
    ],
)
def test_other_shapes_are_left_alone(spoken):
    assert _spoken_local_candidates(spoken) == (None, None)


def test_the_live_prompt_names_both_readings_and_forbids_guessing():
    state = update_state_from_user_turn(
        CallState(), "my email is john co at gmail dot com"
    )
    assert state.email_capture is not None
    assert state.email_capture.status in {
        CaptureStatus.NEEDS_CLARIFICATION,
        CaptureStatus.INVALID,
    }
    prompt = compose_system_prompt("BASE", state)
    assert "johnco@gmail.com" in prompt
    assert "john.co@gmail.com" in prompt
    assert "Never say an email address back" in prompt


def test_unresolvable_email_still_forbids_inventing_one():
    """Three words: no pair to offer, but the prohibition must still be there."""
    state = update_state_from_user_turn(
        CallState(), "my email is all state estimation at gmail dot com"
    )
    prompt = compose_system_prompt("BASE", state)
    assert "one letter at a time" in prompt.lower()
    assert "Never say an email address back" in prompt


# --- speech-to-text splits the provider name ---------------------------------

@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("johnco at g mail dot com", "johnco@gmail.com"),
        ("bob at hot mail dot co dot uk", "bob@hotmail.co.uk"),
        ("sam at out look dot com", "sam@outlook.com"),
        ("amy at i cloud dot com", "amy@icloud.com"),
        ("ted at ya hoo dot com", "ted@yahoo.com"),
        ("bob at gmail dot com", "bob@gmail.com"),  # unchanged when already joined
    ],
)
def test_a_split_provider_name_still_resolves(spoken, expected):
    """Before this, every one of these returned None and the model guessed."""
    from app.services.scripts.spoken_email_normalizer import extract_email_from_speech

    assert extract_email_from_speech(spoken) == expected


@pytest.mark.parametrize(
    "text",
    [
        "i will look out look at it later",   # 'out look' not in domain position
        "g mail me the file tomorrow",
        "check the hot mail thing",
    ],
)
def test_provider_words_outside_an_address_are_not_glued(text):
    from app.services.scripts.spoken_email_normalizer import join_split_providers

    assert join_split_providers(text) == text


# --- a clear address behind a lead-in must resolve --------------------------

@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("my email is bob at gmail dot com", "bob@gmail.com"),
        ("yeah it is bob at g mail dot com", "bob@gmail.com"),
        ("info at acme dot co dot uk", "info@acme.co.uk"),
        # "me" is a real local part and must never be stripped.
        ("send it to me at gmail dot com", "me@gmail.com"),
    ],
)
def test_a_lead_in_no_longer_makes_a_clear_address_invalid(spoken, expected):
    """Before: INVALID, and the agent asked the caller to spell 'bob'."""
    from app.domain.services.voice_pipeline.contact_capture import (
        _extract_lead_in_email,
    )

    assert _extract_lead_in_email(spoken) == expected
    state = update_state_from_user_turn(CallState(), spoken)
    assert state.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.email_capture.normalized_value == expected


@pytest.mark.parametrize(
    "spoken",
    [
        "my email is all state estimation at gmail dot com",  # several words
        "my email is john co at gmail dot com",               # two words
        "bob at gmail dot com and at work it is sam at acme dot com",
        "call me at nine",
    ],
)
def test_the_lead_in_fallback_never_guesses(spoken):
    from app.domain.services.voice_pipeline.contact_capture import (
        _extract_lead_in_email,
    )

    assert _extract_lead_in_email(spoken) is None

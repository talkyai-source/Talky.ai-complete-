"""Caller opt-out and goodbye evidence; ordinary identity interpretation belongs to the model."""
import pytest
from app.domain.services.voice_pipeline.identity_disposition import contains_dnc, contains_explicit_goodbye

@pytest.mark.parametrize(
    "utterance",
    [
        "She's not here, goodbye.",
        "Good bye then.",
        "Alright, bye now.",
        "Okay, bye bye.",
        "I'm hanging up now.",
        "Sorry, I have to go.",
        "I've got to go, bye.",
        "I gotta go, sorry.",
        "gotta go!",
    ],
)
def test_explicit_goodbye_detected(utterance):
    assert contains_explicit_goodbye(utterance) is True

@pytest.mark.parametrize(
    "utterance",
    [
        "",
        "Okay, thanks.",
        "Alright, sounds good.",
        "By the way, can you call back later?",  # "by" must not match "bye"
        "I'll buy it, thanks.",
        "She's not here right now.",
        "bye",  # bare "bye" deliberately excluded (STT-noise risk)
    ],
)
def test_explicit_goodbye_not_detected(utterance):
    assert contains_explicit_goodbye(utterance) is False

@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("no no no no no no stop calling me", True),   # F-13: survives repetition guard
        ("Please take me off your list.", True),
        ("I don't call it Acme anymore.", False),      # naming remark, not DNC
        ("What time do you call back?", False),
    ],
)
def test_contains_dnc(utterance, expected):
    assert contains_dnc(utterance) is expected

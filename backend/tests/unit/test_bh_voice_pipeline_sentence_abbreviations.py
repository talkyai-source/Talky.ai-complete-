"""voice-pipeline-7: abbreviation periods must not end a sentence mid-buffer.

TTS synthesises each returned segment on its own, so a split inside "Dr. Patel"
or "9 a.m. to 5 p.m." gives falling sentence prosody and a pause mid-name or
mid-time. Real sentence ends after an abbreviation must still split, and a
streamed prefix must agree with the complete buffer.
"""
import pytest

from app.domain.services.voice_pipeline.sentence_segmentation import find_sentence_end


def _first(text):
    end = find_sentence_end(text)
    return text[: end + 1] if end >= 0 else None


@pytest.mark.parametrize("text", [
    "Please speak to Dr. Patel about that.",
    "Our office is at 12 St. James Street.",
    "We are open 9 a.m. to 5 p.m. on weekdays.",
    "Mr. and Mrs. Jones will call you back.",
    "Ask for J. Smith at the front desk.",
    "That is policy no. 12345 on your account.",
    "Acme Inc. will confirm the booking.",
    "It is roughly approx. ten minutes away.",
])
def test_abbreviation_period_is_not_a_sentence_end(text):
    assert _first(text) == text


@pytest.mark.parametrize("text, first", [
    ("We close at 5 p.m. Thanks for calling.", "We close at 5 p.m."),
    ("We open at 9 a.m. Please come early.", "We open at 9 a.m."),
    ("We stock chairs, desks, etc. Then there are lamps.", "We stock chairs, desks, etc."),
    ("Yes, so do I. Thanks for asking.", "Yes, so do I."),
    ("No. I do not need anything else.", "No."),
    ("Call us at five. Then we can talk.", "Call us at five."),
])
def test_real_sentence_end_after_an_abbreviation_still_splits(text, first):
    assert _first(text) == first


@pytest.mark.parametrize("text", [
    "Please speak to Dr. Patel about that.",
    "We are open 9 a.m. to 5 p.m. on weekdays.",
    "Our office is at 12 St. James Street.",
])
def test_streamed_prefixes_agree_with_the_complete_buffer(text):
    # No prefix may report a boundary before the real end of the sentence.
    for n in range(1, len(text)):
        assert find_sentence_end(text[:n]) == -1, text[:n]
    assert find_sentence_end(text) == len(text) - 1


"""Sentence boundaries for compact prose, emails, initials and decimals.

Historical utterances below exercise segmentation only. They do not establish
that the runtime identifies invented dialogue or truncates model speech.
"""
import pytest

from app.domain.services.voice_pipeline.sentence_segmentation import (
    _is_missing_space_boundary,
    find_sentence_end,
)



def _first_boundary(text: str) -> int:
    for i, ch in enumerate(text):
        if ch in ".!?" and _is_missing_space_boundary(text, i):
            return i
    return -1


# Every occurrence found in 30 days of production transcripts, verbatim.
PRODUCTION_CASES = [
    pytest.param(
        "Sorry — how are you handling card payments right now?I’m not sure. "
        "You’re a bit vague. This is a sales call?Yeah — it’s a sales call "
        "from Dojo, but I’ll keep it brief.",
        "Sorry — how are you handling card payments right now?",
        id="c63cdaff-real-call-invents-the-objection",
    ),
    pytest.param(
        "May I send a link to the overview to your mobile number?Okay, I’ll "
        "send the link now. If you’d like to chat later, just let me know a "
        "good time.",
        "May I send a link to the overview to your mobile number?",
        id="c63cdaff-real-call-answers-its-own-offer",
    ),
    pytest.param(
        "Anything on your desk right now you're pricing up?Yes, I have a "
        "commercial bid that I need to quote in the next 48 hours.Right — we "
        "can have a senior estimator review that commercial bid, free of "
        "charge. Fair?Yes.Great. Best email for the specialist to reach you?"
        "mike@example.com",
        "Anything on your desk right now you're pricing up?",
        id="319c0802-invents-the-brief-and-the-email",
    ),
    pytest.param(
        "Elizabeth at ex dot example dot com — right?Got it. I'll send the "
        "info there. Anything else you’d like me to include?",
        "Elizabeth at ex dot example dot com — right?",
        id="5a57e2a2-real-call-self-confirms-the-email",
    ),
    pytest.param(
        "All state Estimation at gmail dot com — a at g m a i l dot com?Got "
        "it. We'll send the sample. Anything else I can help with?",
        "All state Estimation at gmail dot com — a at g m a i l dot com?",
        id="6743949c-self-confirms-the-readback",
    ),
    pytest.param(
        "Would you like Azian to review the multi‑site setup and suggest the "
        "best option?Yes.Could I confirm the best email to send the details "
        "to?state estimation at gmail dot com — right?Perfect, I'll pass that "
        "to Azian. They'll be in touch soon. Thanks for your time — take care.",
        "Would you like Azian to review the multi‑site setup and suggest the "
        "best option?",
        id="c01404ba-the-whole-close-invented",
    ),
    pytest.param(
        "Alex here from Dojo — got a minute? I'm checking in with Azian to see "
        "if your payment setup still fits and if there’s anything else we can "
        "help with.Got it — how's your Dojo payment setup been recently?",
        "Alex here from Dojo — got a minute? I'm checking in with Azian to see "
        "if your payment setup still fits and if there’s anything else we can "
        "help with.",
        id="bf6a092c-glues-its-opener-to-the-next-turn",
    ),
    pytest.param(
        "Great. Could I confirm the best phone number to reach you?Sure, just "
        "the number, one digit at a time, please.",
        "Great. Could I confirm the best phone number to reach you?",
        id="bf6a092c-invents-the-callers-consent",
    ),
]


@pytest.mark.parametrize("text,spoken_prefix", PRODUCTION_CASES)
def test_missing_separator_has_a_sentence_boundary(text, spoken_prefix):
    idx = _first_boundary(text)
    assert idx >= 0, "no boundary detected in a known production instance"
    assert text[: idx + 1] == spoken_prefix










# --- the other half of the bar: text that must NOT be split -----------------

@pytest.mark.parametrize(
    "text",
    [
        "Mr.Smith is expecting your call",          # abbreviation, no space
        "Ask for Dr.Patel when you arrive",
        "Send it to J.Smith at the head office",    # single-letter initial
        "That is 3.5 percent on every transaction",  # decimal
        "Delivery is 1.2 million units a year",
        "Use e.g.Dojo Go for a portable terminal",  # dotted abbreviation
        "The U.S.Are covered by a separate rate",   # dotted token
    ],
)
def test_legitimate_bare_periods_are_not_boundaries(text):
    assert _first_boundary(text) == -1, text


@pytest.mark.parametrize(
    "text",
    [
        "Hello there. How can I help you today?",
        "Thanks for confirming. I'll send that over now.",
        "Is now a good time? I can call back later.",
        "Great! That's everything I needed.",
    ],
)
def test_normal_spaced_prose_is_untouched(text):
    assert _first_boundary(text) == -1

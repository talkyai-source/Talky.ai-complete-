"""Unit tests for interim machine detection (voicemail + call screening).

Covers the 2026-07-08 audit patterns verbatim: continuous voicemail greetings
that never finalize a turn, carrier call-screening ("record your name and
reason"), the post-screening "not available / after the tone" endgame, and the
precision boundary (a live receptionist must never trip it).
"""
from app.domain.services.voice_pipeline.machine_detection import assess_machine_text


def _a(text, turn=0, screening=False):
    return assess_machine_text(text, turn_index=turn, screening_seen=screening)


# ── opening voicemail greetings (real interims from production) ─────────
def test_forwarded_to_voicemail_interim():
    assert _a("Call has been forwarded to voice mail. The person") == "voicemail"


def test_o2_messaging_service():
    assert _a("Welcome to the o two messaging service. The person you are calling") == "voicemail"


def test_vodafone_voicemail():
    assert _a("This is the Vodafone voice mail service for o seven") == "voicemail"


def test_voicemail_only_in_opening_window():
    # Same wording later in the call (no screening seen) must NOT trip the
    # opening check — a live human quoting their voicemail should be safe.
    assert _a("Call has been forwarded to voicemail", turn=3) == "none"


# ── call screening (Apple/carrier) ───────────────────────────────────────
def test_screening_detected():
    assert _a("If you record your name and reason for calling, I'll see if this person is available.") == "screening"


def test_screening_stay_on_line():
    assert _a("Thanks. Please stay on the line.", turn=2) == "screening"


# ── post-screening endgame → hang up ────────────────────────────────────
def test_machine_end_after_screening():
    assert _a(
        "I'm sorry. This person is not available. If you would like to leave an additional message, please reply after the tone.",
        turn=4, screening=True,
    ) == "machine_end"


def test_no_machine_end_without_screening():
    # A live receptionist: "he's not available" — screening never seen, so
    # this can NEVER hang up the call.
    assert _a("I'm sorry, he's not available right now", turn=4) == "none"


# ── normal humans never match ────────────────────────────────────────────
def test_human_hello():
    assert _a("Hello?") == "none"


def test_human_conversation():
    assert _a("Yes speaking, what's this about?", turn=2) == "none"


def test_empty():
    assert _a("") == "none"

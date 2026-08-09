"""The two gates that decided a caller was not worth interrupting for.

WHY THIS EXISTS (2026-08-08)
---------------------------
Callers reported the agent talking over them. The cancellation machinery was
not at fault — 14 days of production logged ZERO `interrupt_tts` failures. The
agent talked over people because two gates upstream never asked for a
cancellation in the first place.

Both gates were reasonable-looking code with the same underlying bug: they
judged a PARTIAL transcript as though it were a whole utterance.

GATE 1 — `deepgram_flux._min_interrupt_words` (default was 2)
    Flux's StartOfTurn carries a partial, normally the caller's first word. In
    14 days it deferred 173 of 415 barge-in attempts (29%). The deferred text,
    straight from the journal:
        21x 'Hi.'   15x "I'm"   13x 'Thanks.'   2x 'Listen'   2x "Let's"
         1x 'Speaking'  1x 'Please.'  1x "What's"  1x 'Excuse'  1x 'Bye'
    'Excuse' is a caller saying "excuse me", one word in — the hard-interrupt
    list could never match it, because the second word had not arrived.

GATE 2 — `instant_opener.is_opener_echo`
    Inside the opener playback window, ANY bare greeting was classified as the
    agent's own echo. 50 journal lines / 25 events in 7 days. The text is
    redacted, but the redaction is a plain sha256[:8] (log_redact.py:359), so
    the utterances were recovered exactly:
        sha 2d8bd7d9 = 'Hello.'   0da72197 = 'Hello?'   580684f8 = 'Hey.'
    19 of the 21 distinct calls had no voicemail verdict — these were people.

WHAT THE GATES ACTUALLY COST — measured, not assumed
----------------------------------------------------
Single-call traces corrected an early over-reading of the aggregate counts.
Neither gate LOSES the interruption: the EndOfTurn grow-case picks it up. What
they cost is DELAY, and the two differ by an order of magnitude:

    call A  echo gate   09.909 ignored -> 10.076 barge_in_detected    167ms
    call B  echo gate   39.090 ignored -> 39.178 barge_in_detected     88ms
    call C  min-words   46.417 'Hi.'   -> 47.547 barge_in_detected   1130ms

    (call ids pseudonymised — this repo is public. Recover them from the
     journal with: grep "instant_opener_echo_ignored\|barge-in deferred")

Against the 300-500ms "agent audibly stops" target, the echo gate is inside
budget and the word-count gate is 2-4x outside it. That is why the min-words
default moved first, and it is the opposite of the priority the aggregate
counts alone suggested.

These tests pin the fixes AND the echo protection that must survive them —
the fix is not "delete the guard", it is "stop guessing from length".
"""
from __future__ import annotations

import time
import types

import pytest

from app.domain.services.voice_pipeline.backchannel import (
    is_backchannel,
    is_disfluency,
    is_hard_interrupt,
)
from app.domain.services.voice_pipeline.instant_opener import (
    _ECHO_ONSET_WINDOW_S,
    _ECHO_SAME_EVENT_DEBOUNCE_S,
    is_opener_echo,
)


# ── Gate 1: the word-count guard ───────────────────────────────────────────

# Verbatim from the production journal (7-day window), with counts.
_DEFERRED_IN_PRODUCTION = [
    "Hi.", "I'm", "Thanks.", "Listen", "Let's", "It's", "What's", "Speaking",
    "Please.", "Bye", "Excuse", "Saturday", "Nothing.", "Great.", "wanted",
    "problem", "reason", "Probably", "Still", "Passport",
]


def _stt_barge_in_allowed(text: str, min_words: int) -> bool:
    """Mirror of the StartOfTurn gate in deepgram_flux.receive_transcripts."""
    if is_hard_interrupt(text):
        return True
    if is_backchannel(text):
        return False
    if is_disfluency(text):
        return False
    return len(text.split()) >= min_words


@pytest.mark.parametrize("text", _DEFERRED_IN_PRODUCTION)
def test_real_caller_speech_is_no_longer_deferred(text):
    """THE REGRESSION. Every one of these was a real caller mid-sentence whose
    interruption production threw away."""
    assert _stt_barge_in_allowed(text, min_words=1), (
        f"{text!r} was deferred in production and must now interrupt"
    )


@pytest.mark.parametrize("text", ["uh", "Um,", "erm", "Ah", "mmm", "Hmm"])
def test_hesitation_noise_still_does_not_interrupt(text):
    """The guard's LEGITIMATE job survives — by naming noise, not by length."""
    assert is_disfluency(text)
    assert not _stt_barge_in_allowed(text, min_words=1)


@pytest.mark.parametrize("text", ["yeah", "ok", "mhm", "right", "got it"])
def test_backchannels_still_do_not_interrupt(text):
    assert not _stt_barge_in_allowed(text, min_words=1)


def test_disfluency_does_not_swallow_real_words():
    """'oh' is hesitation; 'oh no' is a reaction. Never eat a real word."""
    assert is_disfluency("oh")
    assert not is_disfluency("oh no")
    assert not is_disfluency("um actually no")
    assert not is_disfluency("")


def test_min_words_two_remains_available_as_a_rollback_lever():
    """The old behaviour must stay reachable without a deploy."""
    assert not _stt_barge_in_allowed("Listen", min_words=2)
    assert _stt_barge_in_allowed("Listen", min_words=1)


def test_flux_default_is_one():
    """The shipped default is what actually changes live behaviour."""
    from tests.unit._source_scan import code

    src = code("app/infrastructure/stt/deepgram_flux.py")
    assert 'os.getenv("DEEPGRAM_MIN_INTERRUPT_WORDS", "1")' in src


# ── Gate 2: the opener echo classifier ─────────────────────────────────────

def _session(*, in_flight=True, started_ago=0.0, spoken=None):
    return types.SimpleNamespace(
        call_id="test-call-0001",
        _instant_opener_in_flight=in_flight,
        _instant_opener_grace_until=0.0,
        _instant_opener_started_at=time.monotonic() - started_ago,
        _instant_opener_spoken_text=spoken,
        _instant_opener_echo_at=None,
    )


@pytest.mark.parametrize("text", ["Hello.", "Hello?", "Hey."])
def test_repeated_greeting_overrides_echo_classification(text):
    """THE REGRESSION, in the exact words production swallowed.

    First one: plausibly echo, ignore. Second, as a distinct utterance: that is
    a caller repeating themselves because they are being talked over.
    """
    s = _session()
    assert is_opener_echo(s, text) is True, "first is still treated as echo"

    # A genuinely later utterance, past the same-event debounce.
    s._instant_opener_echo_at = time.monotonic() - (_ECHO_SAME_EVENT_DEBOUNCE_S + 0.2)
    assert is_opener_echo(s, text) is False, (
        "a repeated greeting must interrupt — this is the caller telling us "
        "we are talking over them"
    )


@pytest.mark.parametrize("text", ["Hello.", "Hello?", "Hey."])
def test_greeting_late_in_playback_is_a_person_not_an_echo(text):
    """Acoustic echo of the pickup is immediate. Five seconds into a long
    agent-first greeting (disclosure + opener), a "Hello?" is a human."""
    s = _session(started_ago=_ECHO_ONSET_WINDOW_S + 3.5)
    assert is_opener_echo(s, text) is False


@pytest.mark.parametrize("text", ["Hello.", "Hello?", "Hey."])
def test_prompt_first_greeting_is_still_treated_as_echo(text):
    """Echo protection is NOT removed. The agent must not react to itself."""
    s = _session(started_ago=0.2)
    assert is_opener_echo(s, text) is True


def test_our_own_words_are_echo_whenever_they_land():
    """The one positive self-echo signal: the greeting we are playing, coming
    back. Trusted even past the onset bound."""
    s = _session(
        started_ago=_ECHO_ONSET_WINDOW_S + 5.0,
        spoken="Hi there, this is Sarah calling from Talky",
    )
    assert is_opener_echo(s, "Hi there") is True


def test_unknown_opener_text_fails_toward_the_caller():
    """No greeting text recorded must not mean 'assume echo'."""
    s = _session(started_ago=_ECHO_ONSET_WINDOW_S + 2.0, spoken=None)
    assert is_opener_echo(s, "Hello?") is False


def test_both_arming_sites_agree_on_one_event():
    """audio_ingest AND voice_pipeline_service both gate the SAME barge-in
    (production logs two lines per occurrence). Two calls in quick succession
    must not read as the caller repeating themselves."""
    s = _session()
    first = is_opener_echo(s, "Hello?")
    second = is_opener_echo(s, "Hello?")
    assert first is True and second is True, (
        "the double-call must be idempotent, or every first echo would "
        "immediately 'repeat' itself and barge in"
    )


def test_real_content_still_interrupts_immediately():
    """Unchanged and non-negotiable, at any point in the window."""
    s = _session()
    for text in ("stop", "wait", "is this about my roof", "who is this"):
        assert is_opener_echo(s, text) is False


def test_outside_the_window_nothing_is_echo():
    s = _session(in_flight=False)
    s._instant_opener_grace_until = time.monotonic() - 1.0
    assert is_opener_echo(s, "Hello?") is False

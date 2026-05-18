"""Tests for the turn-0 confidence + length floor in the voice pipeline.

The floor only fires on the very first user utterance of a call. Its job
is to keep clearly-broken inputs (single-letter mishears, low-confidence
fragments) from anchoring the conversation. Real short greetings ("uh",
"hi", "hey") must always pass.
"""
from app.domain.services.voice_pipeline_service import (
    _alpha_char_count,
    _should_reject_turn_0,
)


class TestAlphaCharCount:
    def test_letters_counted(self):
        assert _alpha_char_count("hello") == 5

    def test_punctuation_and_digits_excluded(self):
        assert _alpha_char_count("L.") == 1
        assert _alpha_char_count("1234") == 0
        assert _alpha_char_count("hi!") == 2

    def test_whitespace_excluded(self):
        assert _alpha_char_count("  hi  ") == 2

    def test_empty_string(self):
        assert _alpha_char_count("") == 0

    def test_unicode_letters_count(self):
        # Real callers sometimes greet with non-ASCII characters; the
        # gate must not silently drop those by treating them as punct.
        assert _alpha_char_count("café") == 4


class TestShouldRejectTurn0:
    def test_real_short_greetings_pass(self):
        """The floor must never block legitimate brief greetings — these
        are exactly the inputs the existing 'allow turn-0 backchannel'
        path was added for."""
        for greeting in ("hi", "uh", "yo", "hey", "yeah", "hello", "hi.", "hey?"):
            assert _should_reject_turn_0(greeting, confidence=0.9) is None, (
                f"Real greeting {greeting!r} was rejected"
            )

    def test_single_letter_with_punct_rejected(self):
        """'L.' is a classic mishear of 'hello' or noise picked as a
        letter. One alpha char cannot carry a real intent."""
        assert _should_reject_turn_0("L.", confidence=0.9) == "too_short"

    def test_pure_punctuation_rejected(self):
        assert _should_reject_turn_0(".", confidence=0.9) == "too_short"
        assert _should_reject_turn_0("...", confidence=0.9) == "too_short"

    def test_low_confidence_rejected(self):
        # A long-enough utterance but Flux flagged it as low-confidence.
        # Reject so the next, cleaner transcript can anchor the call.
        assert _should_reject_turn_0("yellow", confidence=0.3) == "low_confidence"

    def test_high_confidence_short_word_passes(self):
        """A short word Flux is sure about ('uh', 2 chars) must pass —
        the floor is not a length filter for confident transcripts."""
        assert _should_reject_turn_0("uh", confidence=0.95) is None

    def test_missing_confidence_passes(self):
        """Some Flux paths don't surface a confidence score. We trust
        them in that case rather than rejecting unconditionally —
        falsely rejecting real greetings is the worse failure mode."""
        assert _should_reject_turn_0("hello", confidence=None) is None
        # Even very short transcripts pass the confidence gate when
        # confidence is None — but the length gate still applies.
        assert _should_reject_turn_0("uh", confidence=None) is None
        assert _should_reject_turn_0("L.", confidence=None) == "too_short"

    def test_length_check_runs_before_confidence(self):
        """Length is the strictest signal (cheaper to compute, harder
        to argue with). The order keeps the rejection reason
        informative — 'too_short' wins over 'low_confidence' when both
        apply."""
        assert _should_reject_turn_0("L.", confidence=0.1) == "too_short"

    def test_boundary_confidence_passes(self):
        """The floor is < 0.4, not <= 0.4 — operators tuning the
        threshold should be able to use 0.4 as 'allow'."""
        assert _should_reject_turn_0("hello", confidence=0.4) is None


class TestPerTenantFloorOverrides:
    """The floor predicate accepts kwargs so per-tenant tuning at T3.9
    reaches the rule without changing handle_turn_end's call shape."""

    def test_strict_tenant_rejects_what_default_accepts(self):
        """A tenant that wants strict turn-0 hygiene (high confidence,
        long minimum word) rejects 'hi' even though it would pass for
        a default tenant. Same predicate, different kwargs."""
        # Default policy: 'hi' passes.
        assert _should_reject_turn_0("hi", confidence=0.9) is None
        # Strict tenant: requires 4+ alpha chars on turn 0.
        assert (
            _should_reject_turn_0(
                "hi", confidence=0.9,
                min_confidence=0.4, min_alpha_chars=4,
            )
            == "too_short"
        )

    def test_loose_tenant_accepts_what_default_rejects(self):
        """A medical-intake tenant might want the opposite — accept
        even very low-confidence transcripts because callers are often
        elderly, soft-spoken, or off-mic. The same predicate handles
        both extremes."""
        # Default policy: confidence 0.3 is rejected as low_confidence.
        assert (
            _should_reject_turn_0("hello", confidence=0.3) == "low_confidence"
        )
        # Loose tenant: accept anything Flux had any opinion on.
        assert (
            _should_reject_turn_0(
                "hello", confidence=0.3,
                min_confidence=0.0, min_alpha_chars=2,
            )
            is None
        )

    def test_kwargs_default_to_module_constants(self):
        """Backward-compat: a caller that doesn't pass kwargs gets the
        same behaviour the predicate had before T3.9. Pre-existing
        tests that hit the predicate this way still work."""
        # All three call shapes must produce identical results.
        a = _should_reject_turn_0("L.", confidence=0.9)
        b = _should_reject_turn_0("L.", confidence=0.9, min_confidence=0.4)
        c = _should_reject_turn_0(
            "L.", confidence=0.9, min_confidence=0.4, min_alpha_chars=2,
        )
        assert a == b == c == "too_short"

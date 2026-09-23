"""Regression test for the unbounded "spell it again" loop.

Production evidence: call 6aaeb4dd (2026-09-23). The email capture loop
(NEEDS_CLARIFICATION -- caller's spelling never parsed) asked the caller to
spell/re-spell four times over 13:09:02-13:10:06 (~128s, 55% of the 229s
call) because `advance_capture` had no attempt counter on that path -- only
AWAITING_CONFIRMATION (the post-parse "is this readback correct?" loop) was
bounded by MAX_CONFIRMATION_ATTEMPTS. Every unparseable turn either returned
the identical state object (`new_state is state`) or replaced it without
ever touching `attempts`, so the loop could run forever. See
issues_all.txt: email-respell-unbounded.

The seven utterances below are the real final-transcript turns from
6aaeb4dd.transcript.txt (13:08:56.979518 - 13:10:18.220835).
"""
from __future__ import annotations

from app.domain.services.voice_pipeline.contact_capture import (
    CaptureStatus,
    MAX_CLARIFICATION_ATTEMPTS,
    advance_capture,
)

# Verbatim final USER turns from 6aaeb4dd.transcript.txt, in order.
_CALL_6AAEB4DD_EMAIL_TURNS = [
    "at gmail dot com.",
    "Okay. I will split it the whole. That could be h i s h a m k h a n six "
    "eight two n e m a i l dot c o n.",
    "H i s h a m k h a n six eight two eight Gmail dot com.",
    "Did you get",
    "I get it?",
    "I six a m k h a n six eight two eight gmail dot com.",
    "H i s h a m k h a n six two at gmail dot com.",
]


def test_email_clarification_loop_never_advances_attempts_before_the_fix():
    """Encodes the bug as observed, so a regression trips this first.

    Before the fix every one of these turns left ``attempts`` at 0 -- the
    counter that exists never moved, which is the root cause of the
    unbounded loop. This assertion is the "reproduce before reasoning" step:
    it must fail on the unmodified module (attempts stayed 0 forever) and
    pass once advance_capture actually counts failed clarification turns.
    """
    state = None
    for utterance in _CALL_6AAEB4DD_EMAIL_TURNS:
        state = advance_capture(state, kind="email", utterance=utterance, mode_active=True)
        assert state is not None
        assert state.status is CaptureStatus.NEEDS_CLARIFICATION

    # Seven failed turns were fed in; on the unmodified code `state.attempts`
    # is 0 here (never incremented). The fix must make real progress.
    assert state.attempts > 0


def test_email_clarification_stops_asking_to_spell_after_three_asks():
    """After MAX_CLARIFICATION_ATTEMPTS asks, stop asking to spell.

    Call 6aaeb4dd's agent asked to spell/re-spell a 4th time at 13:10:06.
    The backend directive for that turn must no longer say "spell"; it must
    tell the model to read back its best understanding for a yes/no, or say
    the team will confirm and move on.
    """
    state = None
    prompts: list[str] = []
    for utterance in _CALL_6AAEB4DD_EMAIL_TURNS:
        state = advance_capture(state, kind="email", utterance=utterance, mode_active=True)
        prompts.append(state.clarification_prompt or "")

    assert MAX_CLARIFICATION_ATTEMPTS == 3
    # Only the first MAX_CLARIFICATION_ATTEMPTS turns may still ask to spell.
    spell_asks = [p for p in prompts if "spell" in p.lower()]
    assert len(spell_asks) <= MAX_CLARIFICATION_ATTEMPTS

    # The would-be 4th ask (turn index 3, matching the live 13:10:06 turn)
    # must have escalated instead of repeating a spelling request.
    escalated_prompt = prompts[3]
    assert "spell" not in escalated_prompt.lower()
    assert "yes or no" in escalated_prompt.lower() or "team" in escalated_prompt.lower()

    # The escalation is sticky: it does not revert to asking to spell again.
    assert "spell" not in prompts[-1].lower()


def test_phone_clarification_stops_asking_after_three_asks():
    """The same bound applies to phone NEEDS_CLARIFICATION/INVALID.

    A bare number with no region context (missing_region -> NEEDS_
    CLARIFICATION) used to ask "repeat the complete phone number" forever,
    identically to the email defect, because the same unbounded branch fed
    it.
    """
    state = None
    prompts: list[str] = []
    for _ in range(5):
        state = advance_capture(
            state,
            kind="phone",
            utterance="923016253193",
            mode_active=True,
        )
        prompts.append(state.clarification_prompt or "")

    repeat_asks = [p for p in prompts if "repeat" in p.lower() and "team" not in p.lower()]
    assert len(repeat_asks) <= MAX_CLARIFICATION_ATTEMPTS
    assert "team" in prompts[-1].lower() or "yes or no" in prompts[-1].lower()

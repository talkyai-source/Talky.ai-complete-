"""The agent's own recording disclosure must never be heard as caller speech.

WHY THIS EXISTS (regression, 2026-07-31)
----------------------------------------
The self-echo guard compared the caller's turn against ONLY the most recent
assistant message. That silently assumed exactly one agent utterance precedes
each caller turn — true until the spoken recording disclosure was added, which
makes the agent say the notice AND THEN the greeting before the callee has
said anything. By the caller's first turn the disclosure is two messages back,
so it was never compared.

Straight from a production transcript (2026-07-29 21:32):

    User: This call may be recorded.                    <- the agent's own notice
    Assistant: happy to continue. What's the best way I can help you today?
    User: Happy to continue. What's the best topic for you today?

The caller had not spoken at all. The agent answered its own compliance notice,
and its reply then echoed back as another "caller" turn — the call was derailed
from turn 0, on every call, because the disclosure is now spoken on every call.

The fix compares against every agent utterance SINCE THE CALLER LAST SPOKE,
which is exactly the audio still capable of echoing, and is self-limiting.
"""
from __future__ import annotations

import pytest

from app.services.scripts.echo_guard import strip_self_echo, strip_self_echo_multi

DISCLOSURE = "This call may be recorded for quality and training purposes."
GREETING = (
    "Hi, it's Sarah from All-state-estimation. We help UK contractors "
    "protect margins with accurate cost estimating."
)


def test_the_old_single_utterance_guard_missed_the_disclosure():
    """Pins the exact defect, so the fix cannot be quietly reverted.

    Compared only against the greeting (what the guard actually saw), the
    echoed disclosure survives and becomes 'caller speech'.
    """
    assert strip_self_echo("This call may be recorded.", GREETING) == (
        "This call may be recorded."
    )


def test_disclosure_echo_is_stripped_when_both_utterances_are_considered():
    assert strip_self_echo_multi(
        "This call may be recorded.", [GREETING, DISCLOSURE]
    ) == ""


def test_greeting_echo_is_still_stripped():
    """The original single-utterance case must keep working."""
    echoed = "Hi it's Sarah from All-state-estimation we help UK contractors"
    assert strip_self_echo_multi(echoed, [GREETING, DISCLOSURE]) == ""


def test_a_turn_echoing_BOTH_utterances_is_fully_cleaned():
    """Applied most-recent-first and fed forward, so two echoes in one turn
    are both removed rather than only the first."""
    both = (
        "Hi it's Sarah from All-state-estimation we help UK contractors "
        "This call may be recorded for quality and training purposes"
    )
    assert strip_self_echo_multi(both, [GREETING, DISCLOSURE]) == ""


def test_real_caller_speech_is_never_touched():
    """The whole risk of widening the comparison window is false stripping."""
    for utterance in (
        "Yes I handle the estimating side myself",
        "We mainly do commercial work and some residential",
        "Yeah go on then, what is it you do exactly",
        "No thanks, not interested",
    ):
        assert strip_self_echo_multi(utterance, [GREETING, DISCLOSURE]) == utterance


def test_short_backchannels_pass_through():
    """min_run=5 means a short human reply can never be mistaken for echo."""
    for utterance in ("Yes", "Yeah ok", "Hello?", "Go on"):
        assert strip_self_echo_multi(utterance, [GREETING, DISCLOSURE]) == utterance


@pytest.mark.parametrize("agent_texts", [None, (), [""], [None, ""]])
def test_empty_agent_history_is_a_no_op(agent_texts):
    assert strip_self_echo_multi("some caller words here", agent_texts) == (
        "some caller words here"
    )


def test_empty_caller_text_short_circuits():
    assert strip_self_echo_multi("", [GREETING, DISCLOSURE]) == ""


def test_turn_ender_collects_utterances_since_the_caller_last_spoke():
    """Structural pin on the bound.

    A fixed 'last N messages' would eventually strip a caller who legitimately
    repeats the agent's wording from earlier in the call. The bound must be the
    conversation position of the caller's previous turn.
    """
    import inspect

    from app.domain.services.voice_pipeline import turn_ender

    src = inspect.getsource(turn_ender)
    assert "strip_self_echo_multi" in src, "turn_ender must use the multi form"
    assert "_agent_since_user" in src
    # The loop must STOP at the previous user message, not take a fixed slice.
    assert "if _m.role == MessageRole.USER:" in src and "break" in src

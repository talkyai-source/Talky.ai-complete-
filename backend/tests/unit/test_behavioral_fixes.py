"""Prompt-audit behavioral fixes: #8 (silence→history), #16 (decline close),
#23 (greeting barge-in loop bound)."""
from __future__ import annotations

import types


# ── #8: a spoken silence-check is recorded in conversation history ───────────





# ── #16: repeated declines need current caller evidence before closing ──

def test_end_session_after_two_declines_still_requires_a_current_refusal():
    from app.domain.services.end_session_action import should_honor_end_session
    action = {"reason": "conversation_complete", "do_not_call": False}
    # An ambiguous acknowledgment does not inherit old permission to hang up.
    assert should_honor_end_session(action, "we're good", 1) is False
    assert should_honor_end_session(action, "we're good", 1, declined_count=2) is False
    assert should_honor_end_session(action, "I don't want this", 1, declined_count=2) is True


def test_one_decline_does_not_force_close():
    from app.domain.services.end_session_action import should_honor_end_session
    action = {"reason": "conversation_complete"}
    assert should_honor_end_session(action, "maybe later", 1, declined_count=1) is False


# ── #23: greeting barge-in re-intro loop is bounded ──────────────────────────

def test_greeting_bargein_loop_is_bounded():
    from app.domain.services.voice_pipeline.turn_runner import _note_unheard_greeting_bargein

    s = types.SimpleNamespace()  # not yet introduced
    _note_unheard_greeting_bargein(s)            # 1st unheard opening barge-in
    assert getattr(s, "_has_introduced", False) is False
    _note_unheard_greeting_bargein(s)
    # Runtime prompt skips the opening using interruption evidence, without
    # falsely claiming that the caller received an introduction.
    assert s._greeting_bargein_count == 2
    assert getattr(s, "_has_introduced", False) is False


def test_note_bargein_noop_once_introduced():
    from app.domain.services.voice_pipeline.turn_runner import _note_unheard_greeting_bargein
    s = types.SimpleNamespace(_has_introduced=True, _greeting_bargein_count=9)
    _note_unheard_greeting_bargein(s)
    assert s._has_introduced is True
    assert s._greeting_bargein_count == 9        # untouched

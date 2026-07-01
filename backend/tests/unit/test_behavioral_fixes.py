"""Prompt-audit behavioral fixes: #8 (silence→history), #16 (decline close),
#23 (greeting barge-in loop bound)."""
from __future__ import annotations

import types


# ── #8: a spoken silence-check is recorded in conversation history ───────────

def test_silence_check_recorded_to_history_and_transcript():
    from app.domain.services.voice_pipeline.audio_ingest import _record_silence_check
    from app.domain.models.conversation import MessageRole

    ts_calls = []
    ts = types.SimpleNamespace(accumulate_turn=lambda **kw: ts_calls.append(kw))
    pipeline = types.SimpleNamespace(transcript_service=ts)
    sess = types.SimpleNamespace(
        conversation_history=[], call_id="c1", talklee_call_id="t1", turn_id=3
    )
    _record_silence_check(pipeline, sess, "Are you still there?")

    # live history
    assert len(sess.conversation_history) == 1
    assert sess.conversation_history[0].role == MessageRole.ASSISTANT
    assert sess.conversation_history[0].content == "Are you still there?"
    # persisted transcript (re-audit flow #3)
    assert len(ts_calls) == 1
    assert ts_calls[0]["role"] == "assistant"
    assert ts_calls[0]["content"] == "Are you still there?"


def test_silence_check_never_raises_on_bad_session():
    from app.domain.services.voice_pipeline.audio_ingest import _record_silence_check
    # no conversation_history / no transcript_service -> must swallow, not raise
    _record_silence_check(types.SimpleNamespace(), types.SimpleNamespace(), "Still there?")


# ── #16: end-session is honored after two declines (not treated as phantom) ──

def test_end_session_honored_after_two_declines():
    from app.domain.services.end_session_action import should_honor_end_session
    action = {"reason": "conversation_complete", "do_not_call": False}
    # soft decline wording the regex doesn't catch + few turns -> phantom today
    assert should_honor_end_session(action, "we're good", 1) is False
    # ...but after two declines the close is legitimate
    assert should_honor_end_session(action, "we're good", 1, declined_count=2) is True


def test_one_decline_does_not_force_close():
    from app.domain.services.end_session_action import should_honor_end_session
    action = {"reason": "conversation_complete"}
    assert should_honor_end_session(action, "maybe later", 1, declined_count=1) is False


# ── #23: greeting barge-in re-intro loop is bounded ──────────────────────────

def test_greeting_bargein_loop_is_bounded():
    from app.domain.services.voice_pipeline.turn_runner import _note_unheard_greeting_bargein

    s = types.SimpleNamespace()  # not yet introduced
    _note_unheard_greeting_bargein(s)            # 1st unheard opening barge-in
    assert getattr(s, "_has_introduced", False) is False   # one clean re-attempt allowed
    _note_unheard_greeting_bargein(s)            # 2nd -> stop looping the intro
    assert s._has_introduced is True


def test_note_bargein_noop_once_introduced():
    from app.domain.services.voice_pipeline.turn_runner import _note_unheard_greeting_bargein
    s = types.SimpleNamespace(_has_introduced=True, _greeting_bargein_count=9)
    _note_unheard_greeting_bargein(s)
    assert s._has_introduced is True
    assert s._greeting_bargein_count == 9        # untouched

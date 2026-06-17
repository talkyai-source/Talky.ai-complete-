"""Unit tests for the phantom-goodbye guard.

The LLM sometimes emits the end-session action when the caller never signalled
they were done. should_honor_end_session validates real end-intent before we
hang up; opt-out is always honored (compliance).
"""
from app.domain.services.end_session_action import (
    caller_signaled_end,
    should_honor_end_session,
)


# --- caller_signaled_end --------------------------------------------------
def test_caller_end_cues_detected():
    for t in ["ok bye", "goodbye", "I gotta go", "that's all thanks",
              "not interested", "stop calling me", "take me off your list",
              "no thank you", "talk to you later"]:
        assert caller_signaled_end(t), t


def test_caller_non_end_phrases_not_detected():
    for t in ["tell me more about pricing", "can you call me back tomorrow",
              "I'm interested", "send that to my email", "yes that works",
              "", None]:
        assert not caller_signaled_end(t), t


# --- should_honor_end_session --------------------------------------------
def _action(reason="user_goodbye", do_not_call=False):
    return {"reason": reason, "farewell": "bye", "do_not_call": do_not_call}


def test_opt_out_always_honored_even_without_cue_or_turns():
    a = _action(reason="user_done", do_not_call=True)
    assert should_honor_end_session(a, "uh I dunno", user_turn_count=1) is True


def test_honored_when_caller_said_goodbye():
    assert should_honor_end_session(_action(), "okay, goodbye!", user_turn_count=1) is True


def test_phantom_suppressed_when_no_cue_and_few_turns():
    # Model claims user_goodbye but the transcript shows no end-intent.
    assert should_honor_end_session(_action(), "tell me more", user_turn_count=1) is False


def test_conversation_complete_honored_after_enough_turns():
    a = _action(reason="conversation_complete")
    assert should_honor_end_session(a, "okay sure", user_turn_count=3) is True


def test_conversation_complete_suppressed_when_too_early():
    a = _action(reason="conversation_complete")
    assert should_honor_end_session(a, "okay sure", user_turn_count=1) is False


def test_none_action_not_honored():
    assert should_honor_end_session(None, "bye", user_turn_count=5) is False

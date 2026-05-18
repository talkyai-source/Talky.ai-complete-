"""Tests for app.domain.services.telephony.outcome_resolver.

Each test isolates one branch of the decision table from the resolver's
docstring. The resolver consumes only `voice_session` state plus an
optional PBX cause string — no database, no network — so every case is
a tight in-process assertion."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domain.services.telephony.outcome_resolver import (
    CallOutcome,
    resolve_call_outcome,
)


def _voice_session(
    *,
    started_offset_s: float = 0.0,
    user_turns: int = 0,
    goal_achieved: bool = False,
    goal_failed: bool = False,
    pipeline_failed: bool = False,
    amd_voicemail: bool = False,
    transcripts: list[str] | None = None,
    context_goal_achieved: bool = False,
):
    """Build a minimal voice_session stub matching what the resolver reads."""
    started_at = datetime.now(timezone.utc) - timedelta(seconds=started_offset_s)
    history = []
    for i in range(user_turns):
        history.append(SimpleNamespace(role="user", content=f"hi {i}"))
    if transcripts:
        for t in transcripts:
            history.append(SimpleNamespace(role="assistant", content=t))
    ctx = SimpleNamespace(goal_achieved=context_goal_achieved)
    cs = SimpleNamespace(
        started_at=started_at,
        conversation_history=history,
        conversation_context=ctx,
    )
    return SimpleNamespace(
        call_session=cs,
        _goal_achieved=goal_achieved,
        _goal_failed=goal_failed,
        _pipeline_failed=pipeline_failed,
        _amd_voicemail=amd_voicemail,
    )


# ─── highest-priority signals ────────────────────────────────────


def test_explicit_goal_achieved_flag_wins():
    vs = _voice_session(goal_achieved=True)
    assert resolve_call_outcome(vs) is CallOutcome.GOAL_ACHIEVED


def test_conversation_context_goal_achieved_also_wins():
    # No direct flag, but ConversationContext.goal_achieved is True.
    # Resolver should still classify as GOAL_ACHIEVED.
    vs = _voice_session(context_goal_achieved=True)
    assert resolve_call_outcome(vs) is CallOutcome.GOAL_ACHIEVED


def test_goal_failed_flag_maps_to_goal_not_achieved():
    vs = _voice_session(goal_failed=True)
    assert resolve_call_outcome(vs) is CallOutcome.GOAL_NOT_ACHIEVED


def test_pipeline_failed_flag_maps_to_failed():
    vs = _voice_session(pipeline_failed=True)
    assert resolve_call_outcome(vs) is CallOutcome.FAILED


# ─── PBX cause-code branches ────────────────────────────────────


def test_busy_cause_returns_busy():
    vs = _voice_session()
    assert resolve_call_outcome(vs, hangup_reason="USER_BUSY") is CallOutcome.BUSY


def test_no_answer_cause_returns_no_answer():
    vs = _voice_session()
    assert resolve_call_outcome(vs, hangup_reason="no-answer") is CallOutcome.NO_ANSWER


def test_reject_cause_returns_rejected():
    vs = _voice_session()
    assert (
        resolve_call_outcome(vs, hangup_reason="call_rejected") is CallOutcome.REJECTED
    )


def test_unknown_cause_falls_through_to_session_state():
    # Cause string not in any bucket — resolver should keep going and
    # use the answered/no-answer heuristic. With no user turns and 0s
    # duration, that lands on NO_ANSWER.
    vs = _voice_session()
    assert resolve_call_outcome(vs, hangup_reason="unrecognised_cause") is CallOutcome.NO_ANSWER


# ─── voicemail heuristic ────────────────────────────────────────


def test_amd_voicemail_flag_maps_to_voicemail():
    vs = _voice_session(amd_voicemail=True, started_offset_s=20, user_turns=0)
    assert resolve_call_outcome(vs) is CallOutcome.VOICEMAIL


def test_voicemail_keyword_in_assistant_transcript():
    vs = _voice_session(
        started_offset_s=15,
        transcripts=["please leave a message after the beep"],
    )
    assert resolve_call_outcome(vs) is CallOutcome.VOICEMAIL


def test_explicit_pbx_busy_beats_voicemail_keyword():
    # If the PBX gave us a definite cause, trust it over the heuristic.
    vs = _voice_session(transcripts=["voicemail"])
    assert resolve_call_outcome(vs, hangup_reason="user_busy") is CallOutcome.BUSY


# ─── default ANSWERED / NO_ANSWER fallback ──────────────────────


def test_short_call_no_user_turns_is_no_answer():
    vs = _voice_session(started_offset_s=2, user_turns=0)
    assert resolve_call_outcome(vs) is CallOutcome.NO_ANSWER


def test_long_call_with_user_turn_is_answered():
    vs = _voice_session(started_offset_s=30, user_turns=1)
    assert resolve_call_outcome(vs) is CallOutcome.ANSWERED


def test_long_call_no_user_turns_still_answered():
    # Caller never spoke but stayed on the line — keep ANSWERED rather
    # than NO_ANSWER so the campaign progress still ticks.
    vs = _voice_session(started_offset_s=30, user_turns=0)
    assert resolve_call_outcome(vs) is CallOutcome.ANSWERED

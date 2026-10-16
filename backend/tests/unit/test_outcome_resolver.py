"""Tests for app.domain.services.telephony.outcome_resolver.

Each test isolates one branch of the decision table from the resolver's
docstring. The resolver consumes only `voice_session` state plus an
optional PBX cause string — no database, no network — so every case is
a tight in-process assertion."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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
    messages: list[tuple[str, str]] | None = None,
    tts_terminal_failure: bool = False,
):
    """Build a minimal voice_session stub matching what the resolver reads.

    ``messages`` is an explicit ordered ``(role, content)`` history and
    replaces the ``user_turns``/``transcripts`` shortcuts when given."""
    started_at = datetime.now(timezone.utc) - timedelta(seconds=started_offset_s)
    history = []
    if messages is not None:
        for role, content in messages:
            history.append(SimpleNamespace(role=role, content=content))
    else:
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
        _tts_terminal_failure_hangup=tts_terminal_failure,
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


def test_voicemail_keyword_in_callee_transcript():
    # The greeting is spoken by the machine, i.e. it is the CALLEE side of the
    # transcript (role "user"). This test used to model it as role
    # "assistant", which encoded the bug that the agent's own words could
    # classify a live call as voicemail.
    vs = _voice_session(
        started_offset_s=15,
        messages=[("user", "please leave a message after the beep")],
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


# ─── W2 item 1: voicemail heuristic must not mislabel live people ──────────


def test_name_containing_amd_is_not_voicemail():
    # 'Hi, is this Mr Hamdan?' contains the substring "amd"; the bare "amd"
    # hint matched it and a live person became VOICEMAIL (24 h redial).
    vs = _voice_session(
        started_offset_s=30,
        messages=[
            ("assistant", "Hi, is this Mr Hamdan?"),
            ("user", "Yes, speaking. Hamdan here."),
        ],
    )
    assert resolve_call_outcome(vs) is CallOutcome.ANSWERED


def test_callee_saying_a_name_with_amd_is_not_voicemail():
    vs = _voice_session(
        started_offset_s=30,
        messages=[("user", "This is Hamdan")],
    )
    assert resolve_call_outcome(vs) is CallOutcome.ANSWERED


def test_agent_opener_with_leave_a_message_is_not_voicemail():
    # The AGENT's own opener must never be scanned for machine phrases.
    vs = _voice_session(
        started_offset_s=30,
        messages=[
            ("assistant", "Hello, if now is not a good time I can leave a message."),
            ("user", "Go ahead, I am listening."),
        ],
    )
    assert resolve_call_outcome(vs) is CallOutcome.ANSWERED


def test_voicemail_phrase_must_be_a_whole_phrase_not_a_substring():
    vs = _voice_session(
        started_offset_s=30,
        messages=[("user", "I enjoy a mixed voicemailing hobby, anyway")],
    )
    assert resolve_call_outcome(vs) is CallOutcome.ANSWERED


@pytest.mark.parametrize(
    "greeting",
    [
        "You have reached the voicemail of John Smith",
        "Please leave a message after the tone.",
        "Leave your message after the beep",
    ],
)
def test_real_machine_greeting_from_callee_side_is_still_voicemail(greeting):
    vs = _voice_session(started_offset_s=15, messages=[("user", greeting)])
    assert resolve_call_outcome(vs) is CallOutcome.VOICEMAIL


def test_amd_flag_still_flags_voicemail_without_any_text():
    vs = _voice_session(started_offset_s=15, amd_voicemail=True)
    assert resolve_call_outcome(vs) is CallOutcome.VOICEMAIL


# ─── W2 item 2: a TTS terminal failure is not a successful call ────────────


def test_tts_terminal_failure_hangup_maps_to_failed():
    vs = _voice_session(
        started_offset_s=30, user_turns=2, tts_terminal_failure=True
    )
    assert resolve_call_outcome(vs) is CallOutcome.FAILED


def test_tts_terminal_failure_does_not_override_goal_achieved():
    vs = _voice_session(
        started_offset_s=30, goal_achieved=True, tts_terminal_failure=True
    )
    assert resolve_call_outcome(vs) is CallOutcome.GOAL_ACHIEVED


def test_tts_flag_absent_or_false_is_not_failed():
    vs = _voice_session(started_offset_s=30, user_turns=2)
    assert resolve_call_outcome(vs) is CallOutcome.ANSWERED


# ─── W2 item 4: carrier / network causes ────────────────────────────────────

# Cause text exactly as Asterisk emits it in ChannelDestroyed.cause_txt.
_ASTERISK_TEXT_BY_CODE = {
    1: "Unallocated (unassigned) number",
    3: "No route to destination",
    17: "User busy",
    18: "No user responding",
    19: "User alerting, no answer",
    21: "Call Rejected",
    22: "Number changed",
    27: "Destination out of order",
    28: "Invalid number format",
    34: "Circuit/channel congestion",
    38: "Network out of order",
    41: "Temporary failure",
    42: "Switching equipment congestion",
    44: "Requested channel not available",
    127: "Interworking, unspecified",
}

_EXPECTED_BY_CODE = {
    1: CallOutcome.INVALID,
    3: CallOutcome.FAILED,
    17: CallOutcome.BUSY,
    18: CallOutcome.NO_ANSWER,
    19: CallOutcome.NO_ANSWER,
    21: CallOutcome.REJECTED,
    22: CallOutcome.INVALID,
    27: CallOutcome.FAILED,
    28: CallOutcome.INVALID,
    34: CallOutcome.FAILED,
    38: CallOutcome.FAILED,
    41: CallOutcome.FAILED,
    42: CallOutcome.FAILED,
    44: CallOutcome.FAILED,
    127: CallOutcome.FAILED,
}


@pytest.mark.parametrize("code", sorted(_EXPECTED_BY_CODE))
def test_asterisk_cause_text_maps_to_expected_outcome(code):
    vs = _voice_session()
    got = resolve_call_outcome(vs, hangup_reason=_ASTERISK_TEXT_BY_CODE[code])
    assert got is _EXPECTED_BY_CODE[code], (code, _ASTERISK_TEXT_BY_CODE[code])


@pytest.mark.parametrize("code", sorted(_EXPECTED_BY_CODE))
def test_numeric_cause_maps_to_expected_outcome(code):
    vs = _voice_session()
    assert resolve_call_outcome(vs, hangup_reason=str(code)) is _EXPECTED_BY_CODE[code]


@pytest.mark.parametrize("code", sorted(_EXPECTED_BY_CODE))
def test_text_path_and_int_path_agree_for_every_cause(code):
    # Asterisk's own wording (text path) and the adapter's int fallback table
    # must classify identically, or the outcome depends on which field the
    # PBX happened to populate.
    from app.infrastructure.telephony.asterisk_adapter import _Q850_CAUSE_TEXT

    assert code in _Q850_CAUSE_TEXT, f"adapter table lacks Q.850 {code}"
    vs = _voice_session()
    via_text = resolve_call_outcome(vs, hangup_reason=_ASTERISK_TEXT_BY_CODE[code])
    via_table = resolve_call_outcome(vs, hangup_reason=_Q850_CAUSE_TEXT[code])
    via_int = resolve_call_outcome(vs, hangup_reason=str(code))
    assert via_text is via_table is via_int is _EXPECTED_BY_CODE[code]


def test_every_adapter_table_entry_resolves_to_a_definite_outcome():
    from app.infrastructure.telephony.asterisk_adapter import _Q850_CAUSE_TEXT

    vs = _voice_session(started_offset_s=30, user_turns=0)
    for code, text in _Q850_CAUSE_TEXT.items():
        by_text = resolve_call_outcome(vs, hangup_reason=text)
        by_int = resolve_call_outcome(vs, hangup_reason=str(code))
        assert by_text is by_int, (code, text, by_text, by_int)
        # a mapped cause must not fall through to the session-state default
        assert by_text is not CallOutcome.ANSWERED, (code, text)


def test_congestion_is_a_network_failure_not_busy():
    # The int fallback used to give BUSY (5 min redial) while the text gave
    # NO_ANSWER (24 h); both are wrong for a carrier/network fault.
    vs = _voice_session()
    for reason in ("switch_congestion", "Switching equipment congestion", "42"):
        assert resolve_call_outcome(vs, hangup_reason=reason) is CallOutcome.FAILED


def test_invalid_number_outcome_is_terminal_without_dnc_or_retry():
    from app.workers.disposition_policy import decide

    vs = _voice_session()
    outcome = resolve_call_outcome(vs, hangup_reason="Unallocated (unassigned) number")
    assert outcome is CallOutcome.INVALID
    decision = decide(outcome, 1)
    assert decision.should_retry is False and decision.is_success is False


def test_pre_answer_recovery_path_without_a_voice_session_uses_the_cause():
    # lifecycle's orphan / pre-answer settlement calls the resolver with no
    # voice session at all.
    assert (
        resolve_call_outcome(None, hangup_reason="Network out of order")
        is CallOutcome.FAILED
    )
    assert resolve_call_outcome(None, hangup_reason="User busy") is CallOutcome.BUSY
    assert resolve_call_outcome(None, hangup_reason=None) is CallOutcome.NO_ANSWER


@pytest.mark.parametrize("reason", [None, "", "  ", "Unknown", "unknown", "Normal Clearing"])
def test_empty_or_unknown_ring_out_stays_no_answer(reason):
    vs = _voice_session(started_offset_s=1, user_turns=0)
    assert resolve_call_outcome(vs, hangup_reason=reason) is CallOutcome.NO_ANSWER


@pytest.mark.parametrize(
    "reason",
    ["Network out of order", "Temporary failure", "Interworking, unspecified",
     "Requested channel not available", "38", "Unallocated (unassigned) number"],
)
def test_network_or_invalid_cause_after_a_real_conversation_stays_answered(reason):
    # A callee who spoke for 90 s and was then cut off by the network is a
    # conversation, not a failed dial: FAILED would redial them within minutes.
    vs = _voice_session(started_offset_s=90, user_turns=2)
    assert resolve_call_outcome(vs, hangup_reason=reason) is CallOutcome.ANSWERED


def test_network_cause_with_a_silent_session_is_still_failed():
    vs = _voice_session(started_offset_s=2, user_turns=0)
    assert resolve_call_outcome(vs, hangup_reason="Network out of order") is CallOutcome.FAILED

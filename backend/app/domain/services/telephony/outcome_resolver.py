"""Resolve a CallOutcome at hangup time from live voice_session state.

Background
----------
Until now `lifecycle._on_call_ended` wrote a hardcoded
`outcome="completed"` string to the calls row, which is neither a valid
`CallOutcome` enum value nor diagnostically useful — every call looked
identical regardless of whether it was answered, ringed-out, hit
voicemail, was rejected at SIP level, or completed the agent's goal.

This resolver replaces that placeholder with a small set of rules that
classify the outcome from signals already on the live session:

  - ``voice_session._goal_achieved``         -> ``GOAL_ACHIEVED``
  - ``voice_session._goal_failed``           -> ``GOAL_NOT_ACHIEVED``
  - ``voice_session._pipeline_failed``       -> ``FAILED``
  - ``call_session._tts_terminal_failure_hangup`` (TTS provider died and we
    hung up on the caller)                   -> ``FAILED``
  - Conversation had any user turn AND duration ≥ ``_MIN_ANSWERED_S`` -> ``ANSWERED``
  - PBX cause code in BUSY family (Q.850 17)                          -> ``BUSY``
  - PBX cause code in NO_ANSWER family (Q.850 18/19/20)               -> ``NO_ANSWER``
  - PBX cause code in REJECT family (Q.850 21)                        -> ``REJECTED``
  - Network / infrastructure causes (Q.850 3/27/34/38/41/42/44/127)   -> ``FAILED``
  - Number not routable (Q.850 1/22/28)                               -> ``INVALID``
  - Heuristic voicemail markers in the CALLEE's transcript or call_session metadata -> ``VOICEMAIL``
  - Default                                                            -> ``ANSWERED``
    (legacy fallback — preserves "did connect" semantics without the
    misleading "completed" string)

Why this lives in its own module
--------------------------------
``call_service.handle_call_status`` already runs the atomic
``update_call_status`` RPC + ``_update_campaign_counters`` chain — but
it needs an enum value, not the placeholder string. Putting the
mapping here keeps ``lifecycle._on_call_ended`` readable and makes the
rule table easy to unit-test in isolation.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.domain.models.dialer_job import CallOutcome

logger = logging.getLogger(__name__)


# A call must reach this duration AND at least one user turn to count
# as ANSWERED. Below this we trust the cause-code path; if no cause
# code is available we fall back to ANSWERED so we never accidentally
# strand a real conversation as NO_ANSWER.
_MIN_ANSWERED_S = 5

# Q.850 cause number -> outcome. THE single table: a textual cause is first
# resolved to its Q.850 number (``_CAUSE_TEXT_Q850``) and then looked up here,
# so Asterisk's own ``cause_txt`` wording, the adapter's integer-fallback text
# (``asterisk_adapter._Q850_CAUSE_TEXT``) and a bare number can never classify
# differently. (They used to: "Switching equipment congestion" fell through to
# NO_ANSWER while the int fallback "switch_congestion" gave BUSY.)
_Q850_OUTCOME: dict[int, CallOutcome] = {
    # Callee side.
    17: CallOutcome.BUSY,
    18: CallOutcome.NO_ANSWER,  # no user responding
    19: CallOutcome.NO_ANSWER,  # alerting, no answer
    20: CallOutcome.NO_ANSWER,  # subscriber absent / phone off
    21: CallOutcome.REJECTED,  # declined by the callee OR refused by the carrier
    # The number cannot be routed: terminal (no redial) but NOT do-not-call.
    1: CallOutcome.INVALID,  # unallocated (unassigned) number
    22: CallOutcome.INVALID,  # number changed
    28: CallOutcome.INVALID,  # invalid number format
    # Network / infrastructure fault: nothing to do with the prospect, so it is
    # a FAILED attempt (short retry per disposition_policy), never a 24 h
    # "no answer" and never a "busy".
    3: CallOutcome.FAILED,  # no route to destination
    27: CallOutcome.FAILED,  # destination out of order
    34: CallOutcome.FAILED,  # no circuit / channel available
    38: CallOutcome.FAILED,  # network out of order
    41: CallOutcome.FAILED,  # temporary failure
    42: CallOutcome.FAILED,  # switching equipment congestion
    44: CallOutcome.FAILED,  # requested channel not available
    127: CallOutcome.FAILED,  # interworking, unspecified
}

# Normalised cause wording -> Q.850 number. Keys come out of ``_normalise_cause``
# (lowercase, every run of non-alphanumerics collapsed to one underscore).
_CAUSE_TEXT_Q850: dict[str, int] = {
    "busy": 17,
    "user_busy": 17,
    "no_user_response": 18,
    "no_user_responding": 18,
    "no_answer": 19,
    "noanswer": 19,
    "user_alerting_no_answer": 19,
    "subscriber_absent": 20,
    "call_rejected": 21,
    "rejected": 21,
    "user_rejected": 21,
    "unallocated_number": 1,
    "unallocated_unassigned_number": 1,
    "unallocated": 1,
    "number_changed": 22,
    "invalid_number_format": 28,
    "invalid_number": 28,
    "no_route_to_destination": 3,
    "destination_out_of_order": 27,
    "circuit_congestion": 34,
    "circuit_channel_congestion": 34,
    "no_circuit_channel_available": 34,
    "no_circuit_available": 34,
    "network_out_of_order": 38,
    "temporary_failure": 41,
    "normal_temporary_failure": 41,
    "switch_congestion": 42,
    "switching_equipment_congestion": 42,
    "requested_channel_not_available": 44,
    "requested_chan_unavail": 44,
    "interworking": 127,
    "interworking_unspecified": 127,
}

# PBX-specific spellings (FreeSWITCH-style) that have no Q.850 number of their
# own; classified exactly as they were before.
_PBX_CAUSE_OUTCOME: dict[str, CallOutcome] = {
    "originator_cancel": CallOutcome.NO_ANSWER,
    "recovery_on_timer_expire": CallOutcome.NO_ANSWER,
    "destination_rejected": CallOutcome.REJECTED,
    "incompatible_destination": CallOutcome.REJECTED,
}

# Phrases an answering machine says. Matched as WHOLE phrases (word
# boundaries) against what the CALLEE said only. The old bare "amd" substring
# ("answering-machine-detection metadata flag") matched inside ordinary names
# -- "Hamdan" -- and real AMD already arrives through the ``_amd_voicemail``
# flag, so it is not a text hint at all.
_VOICEMAIL_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bvoicemail\b",
        r"\bleave a message\b",
        r"\bafter the tone\b",
        r"\bafter the beep\b",
    )
)

# Transcript roles that carry the other party's speech.
_CALLEE_ROLES = frozenset({"user", "callee"})


def _conversation_user_turns(voice_session: Any) -> int:
    """Count user-role turns on the live conversation history.

    Used as the "did the caller actually speak" signal — if it's zero
    the call is treated as no-conversation regardless of duration."""
    try:
        cs = getattr(voice_session, "call_session", None)
        history = getattr(cs, "conversation_history", []) if cs else []
        count = 0
        for msg in history:
            role = getattr(msg, "role", None)
            role_value = getattr(role, "value", None) if role is not None else None
            if role_value == "user" or role == "user":
                count += 1
        return count
    except Exception:
        return 0


def _wall_clock_seconds(voice_session: Any) -> float:
    """Wall-clock duration since CallSession.started_at, floored at 0.

    Reused from the (now-deprecated) metrics persister — kept here as
    a small helper so the resolver doesn't import a soon-to-be-deleted
    file."""
    try:
        from datetime import datetime, timezone
        cs = getattr(voice_session, "call_session", None)
        started_at = getattr(cs, "started_at", None) if cs else None
        if not isinstance(started_at, datetime):
            return 0.0
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())
    except Exception:
        return 0.0


def _normalise_cause(cause: Optional[str]) -> str:
    """Lowercase and collapse every run of non-alphanumerics to one ``_`` so
    cause comparisons are stable across adapter wording (FreeSWITCH vs
    Asterisk vs Vonage): ``"Unallocated (unassigned) number"`` ->
    ``"unallocated_unassigned_number"``, ``"no-answer"`` -> ``"no_answer"``."""
    if not cause:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", str(cause).strip().lower()).strip("_")


def _classify_cause(hangup_reason: Optional[str]) -> Optional[CallOutcome]:
    """Outcome implied by a PBX hangup cause, or None to fall through to the
    session-state heuristics (empty, ``Unknown``, normal clearing, ...)."""
    key = _normalise_cause(hangup_reason)
    if not key:
        return None
    if key.isdigit():
        return _Q850_OUTCOME.get(int(key))
    code = _CAUSE_TEXT_Q850.get(key)
    if code is not None:
        return _Q850_OUTCOME[code]
    return _PBX_CAUSE_OUTCOME.get(key)


def _message_role(msg: Any) -> str:
    role = getattr(msg, "role", None)
    return str(getattr(role, "value", role) or "").strip().lower()


def _looks_like_voicemail(voice_session: Any) -> bool:
    """Heuristic: an AMD flag on the session, or a machine phrase in what the
    CALLEE said early in the call. Cheap; runs once at hangup."""
    try:
        # AMD flag — set by answering-machine detection (final-transcript AMD
        # sets it on the voice session; the interim machine detector sets it
        # on the pipeline CallSession it holds — check both).
        if getattr(voice_session, "_amd_voicemail", False):
            return True
        cs = getattr(voice_session, "call_session", None)
        if cs is not None and getattr(cs, "_amd_voicemail", False):
            return True
        history = getattr(cs, "conversation_history", []) if cs else []
        # Look at the first few turns — voicemail greetings show up there.
        # Anything later is likely a real conversation. Only the callee's
        # words count: the agent's own opener ("...I can leave a message")
        # says nothing about who picked up.
        for msg in (history or [])[:3]:
            if _message_role(msg) not in _CALLEE_ROLES:
                continue
            blob = (getattr(msg, "content", "") or "").lower()
            if any(pattern.search(blob) for pattern in _VOICEMAIL_PATTERNS):
                return True
    except Exception:
        pass
    return False


def resolve_call_outcome(
    voice_session: Any,
    hangup_reason: Optional[str] = None,
) -> CallOutcome:
    """Classify the outcome of a hung-up call.

    Args:
        voice_session: the in-process VoiceSession the lifecycle hook holds.
            Reads ``_goal_achieved``, ``_goal_failed``, ``_pipeline_failed``,
            and ``call_session.conversation_history`` / ``started_at``.
        hangup_reason: optional PBX cause string. Adapters don't all
            forward it today; when missing the resolver works from
            session state alone.

    Returns:
        A CallOutcome enum value. Always returns *something* — the
        legacy fallback is ``ANSWERED`` so we never strand a real
        conversation.
    """
    # Highest-priority signals — explicit agent verdicts win.
    # Two ways the agent can flag a successful goal:
    #   1. Direct flag on voice_session set by the goal-achieved tool
    #      (`/webhooks/call/goal-achieved` mark + the in-pipeline tool
    #      handler — see `_set_goal_achieved` below).
    #   2. ConversationContext.goal_achieved on the call session — set
    #      by ConversationContext.set_outcome(SUCCESS) from the
    #      conversation engine when the user confirms the goal.
    if getattr(voice_session, "_goal_achieved", False):
        return CallOutcome.GOAL_ACHIEVED
    cs = getattr(voice_session, "call_session", None)
    ctx = getattr(cs, "conversation_context", None) if cs is not None else None
    if ctx is not None and getattr(ctx, "goal_achieved", False):
        return CallOutcome.GOAL_ACHIEVED
    if getattr(voice_session, "_goal_failed", False):
        return CallOutcome.GOAL_NOT_ACHIEVED
    if getattr(voice_session, "_pipeline_failed", False):
        return CallOutcome.FAILED
    # The TTS provider failed terminally and the call was hung up on the
    # caller (voice_pipeline/tts_playback.py). The inbound resolver already
    # reads this flag; without it here an outbound call whose agent went mute
    # fell through to ANSWERED -- a terminal success that is never redialled.
    # Only when the callee never spoke: FAILED is redialled 30 s and 2 min
    # later, and someone who just talked with the agent must not be rung back
    # twice within minutes (same rule as the network-cause branch below).
    if (
        getattr(cs, "_tts_terminal_failure_hangup", False) is True
        or getattr(voice_session, "_tts_terminal_failure_hangup", False) is True
    ) and _conversation_user_turns(voice_session) == 0:
        return CallOutcome.FAILED

    cause_outcome = _classify_cause(hangup_reason)
    if cause_outcome in (CallOutcome.FAILED, CallOutcome.INVALID) and voice_session is not None:
        # Network / invalid-number causes describe a call that never became a
        # conversation. If the callee actually spoke, a network drop at the
        # END must not turn it into FAILED (redialled 30 s later) or INVALID.
        if _conversation_user_turns(voice_session) > 0:
            cause_outcome = None
    if cause_outcome is not None:
        return cause_outcome

    # Voicemail check happens AFTER cause-code classification — if the
    # PBX gave us a definite BUSY/NO_ANSWER we trust it over heuristic
    # text matching.
    if _looks_like_voicemail(voice_session):
        return CallOutcome.VOICEMAIL

    # No explicit cause code. Use session state to pick between
    # ANSWERED (real conversation happened) and NO_ANSWER (line opened
    # but caller never spoke).
    duration = _wall_clock_seconds(voice_session)
    user_turns = _conversation_user_turns(voice_session)
    if user_turns == 0 and duration < _MIN_ANSWERED_S:
        return CallOutcome.NO_ANSWER

    return CallOutcome.ANSWERED

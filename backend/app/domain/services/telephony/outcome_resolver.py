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
  - Conversation had any user turn AND duration ≥ ``_MIN_ANSWERED_S`` -> ``ANSWERED``
  - PBX cause code in BUSY family                                     -> ``BUSY``
  - PBX cause code in NO_ANSWER family                                -> ``NO_ANSWER``
  - PBX cause code in REJECT family                                   -> ``REJECTED``
  - Heuristic voicemail markers in transcript or call_session metadata -> ``VOICEMAIL``
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
from typing import Any, Optional

from app.domain.models.dialer_job import CallOutcome

logger = logging.getLogger(__name__)


# A call must reach this duration AND at least one user turn to count
# as ANSWERED. Below this we trust the cause-code path; if no cause
# code is available we fall back to ANSWERED so we never accidentally
# strand a real conversation as NO_ANSWER.
_MIN_ANSWERED_S = 5

# PBX cause codes (lowercase, normalised) that indicate the line was
# busy. Different B2BUAs spell these slightly differently.
_BUSY_CAUSES = {
    "busy",
    "user_busy",
    "user-busy",
    "circuit_congestion",
    "switch_congestion",
    "destination_out_of_order",
}

_NO_ANSWER_CAUSES = {
    "no_answer",
    "no-answer",
    "no_user_response",
    "noanswer",
    "originator_cancel",
    "recovery_on_timer_expire",
}

_REJECT_CAUSES = {
    "call_rejected",
    "rejected",
    "user_rejected",
    "destination_rejected",
    "incompatible_destination",
}

# Anything resembling answering-machine detection or VM beep markers.
_VOICEMAIL_HINTS = (
    "voicemail",
    "leave a message",
    "after the tone",
    "after the beep",
    "amd",  # answering-machine-detection metadata flag
)


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
    """Lowercase + replace whitespace so cause-code comparisons are
    stable across adapter wording (FreeSWITCH vs Asterisk vs Vonage)."""
    if not cause:
        return ""
    return cause.strip().lower().replace(" ", "_").replace("-", "_")


def _looks_like_voicemail(voice_session: Any) -> bool:
    """Heuristic: scan the first assistant transcript and any AMD hint
    on the session for voicemail markers. Cheap; runs once at hangup."""
    try:
        # AMD flag — set by the telephony adapter if answering-machine
        # detection ran during call setup.
        if getattr(voice_session, "_amd_voicemail", False):
            return True
        cs = getattr(voice_session, "call_session", None)
        history = getattr(cs, "conversation_history", []) if cs else []
        # Look at the first 1-2 user turns — voicemail greetings show
        # up there. Anything later is likely a real conversation.
        for msg in (history or [])[:3]:
            content = getattr(msg, "content", "") or ""
            blob = content.lower()
            if any(hint in blob for hint in _VOICEMAIL_HINTS):
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

    cause = _normalise_cause(hangup_reason)
    if cause in _BUSY_CAUSES:
        return CallOutcome.BUSY
    if cause in _NO_ANSWER_CAUSES:
        return CallOutcome.NO_ANSWER
    if cause in _REJECT_CAUSES:
        return CallOutcome.REJECTED

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

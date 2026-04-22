"""Per-call slot tracker.

One `CallState` lives on the voice session; `update_state_from_user_turn`
is called for each finalised user turn before the LLM runs. The tracker
is *sticky* — once a slot is captured, it is not overwritten by garbage
from later turns. An explicit caller correction ("no it's bob@...") is
handled by prompt guidance (see telephony_session_config.py).

Pure function — no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from app.services.scripts.spoken_email_normalizer import extract_email_from_speech

_DAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|next week|later this week|end of week)\b",
    re.IGNORECASE,
)

_BIDDING_YES_RE = re.compile(
    r"\b(bidding|active\s+projects?|multiple\s+projects?|"
    r"have\s+(?:a\s+|multiple\s+)?projects?|working\s+on\s+(?:a\s+)?project|"
    r"multiple\s+type\s+of\s+projects?)\b",
    re.IGNORECASE,
)
_BIDDING_NO_RE = re.compile(
    r"\b(not\s+bidding|no\s+projects?|nothing\s+(?:right\s+)?now|"
    r"slow\s+period|between\s+jobs)\b",
    re.IGNORECASE,
)

_DECLINE_RE = re.compile(
    r"\b(not\s+interested|don'?t\s+want|no\s+thanks?|stop\s+calling|"
    r"remove\s+me|take\s+me\s+off)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CallState:
    """Sticky slot store. Frozen so every update is an explicit `replace()`."""
    email: Optional[str] = None
    follow_up: Optional[str] = None
    project_type: Optional[str] = None
    bidding_active: Optional[bool] = None
    declined_count: int = 0


def update_state_from_user_turn(state: CallState, utterance: str) -> CallState:
    """Return a new CallState with any new slots captured from `utterance`.

    Sticky semantics:
      - Non-None slots are only updated when we parse a new, non-None value.
      - declined_count always increments on decline match (not sticky).
    """
    if not utterance or not utterance.strip():
        return state

    email = state.email
    if email is None:
        parsed_email = extract_email_from_speech(utterance)
        if parsed_email:
            email = parsed_email

    follow_up = state.follow_up
    if follow_up is None:
        m = _DAY_RE.search(utterance)
        if m:
            follow_up = m.group(1).lower()

    bidding_active = state.bidding_active
    if bidding_active is None:
        if _BIDDING_NO_RE.search(utterance):
            bidding_active = False
        elif _BIDDING_YES_RE.search(utterance):
            bidding_active = True

    declined_count = state.declined_count
    if _DECLINE_RE.search(utterance):
        declined_count += 1

    return replace(
        state,
        email=email,
        follow_up=follow_up,
        bidding_active=bidding_active,
        declined_count=declined_count,
    )

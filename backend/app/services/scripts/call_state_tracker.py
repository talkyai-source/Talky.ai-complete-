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

# STRICT confirmation classifier for a CORE field (email/number). A wrong verdict
# here CORRUPTS data (wipes a good value or commits a mis-heard one), so — unlike
# the general classify_confirmation — only an UNAMBIGUOUS, focused yes/no counts;
# everything else is 'unclear' (no transition, value stays pending).
_CORE_AFFIRM_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|correct|exactly|perfect|spot\s+on|absolutely|"
    r"that'?s\s+(right|correct|it|the\s+one))\b",
    re.IGNORECASE,
)
_CORE_REJECT_LEAD_RE = re.compile(r"^\s*(no|nope|nah|wrong|incorrect)\b", re.IGNORECASE)
# A clear correction intent anywhere in a short reply.
_CORE_REJECT_ANY_RE = re.compile(
    r"\b(that'?s\s+(wrong|not\s+right|not\s+correct|incorrect|not\s+it)|"
    r"not\s+right|not\s+correct|incorrect|got\s+it\s+wrong|mis[\s-]?heard)\b",
    re.IGNORECASE,
)
_HAS_NEG_TOKEN_RE = re.compile(r"\b(no|not|nope|nah|wrong|incorrect)\b", re.IGNORECASE)


def _classify_core_confirmation(utterance: str) -> str:
    """'affirm' | 'reject' | 'unclear' for a caller reply to a CORE-field read-back.

    Deliberately conservative: a bare mid-sentence 'right'/'actually' or a 'no'
    that's about something else must NOT flip the value. If it isn't a clear,
    focused confirmation reply, we return 'unclear' and keep the value pending.
    """
    t = (utterance or "").strip()
    if not t:
        return "unclear"
    n = len(t.split())
    # Clear correction intent ("that's wrong / not right / you misheard").
    if _CORE_REJECT_ANY_RE.search(t):
        return "reject"
    # A short, LEADING bare negation ("no", "nope") — not a long unrelated sentence.
    if n <= 4 and _CORE_REJECT_LEAD_RE.match(t):
        return "reject"
    # A short, LEADING affirmation with no competing negation and no question.
    if n <= 6 and "?" not in t and _CORE_AFFIRM_RE.match(t) and not _HAS_NEG_TOKEN_RE.search(t):
        return "affirm"
    return "unclear"


@dataclass(frozen=True)
class CallState:
    """Sticky slot store. Frozen so every update is an explicit `replace()`."""
    email: Optional[str] = None
    # Confirm-before-commit (issue #1): a freshly captured email is NOT trusted
    # until the caller confirms the read-back. Only a confirmed email is shown to
    # the model as a settled "do not re-ask" fact; an unconfirmed one is flagged
    # for read-back. See prompt_builder.compose_system_prompt.
    email_confirmed: bool = False
    follow_up: Optional[str] = None
    project_type: Optional[str] = None
    bidding_active: Optional[bool] = None
    declined_count: int = 0


def update_state_from_user_turn(
    state: CallState, utterance: str, *, readback_issued: bool = False
) -> CallState:
    """Return a new CallState with any new slots captured from `utterance`.

    ``readback_issued`` — True only when the agent's MOST RECENT turn actually
    read the pending email back. A caller reply is interpreted as a confirmation
    (affirm/reject) ONLY then, so a stray "yes"/"no" on an unrelated turn can't
    falsely commit or wipe a captured email.

    Sticky semantics:
      - Non-None slots are only updated when we parse a new, non-None value.
      - declined_count always increments on decline match (not sticky).
    """
    if not utterance or not utterance.strip():
        return state

    # Email is sticky, but a CORRECTION must win: if the caller restates a
    # (different) email — e.g. after the agent read it back wrong — re-capture
    # the latest one. Turns with no email leave the stored value untouched.
    #
    # Confirm-before-commit (issue #1): a NEW/corrected email starts UNCONFIRMED
    # (the agent must read it back and the caller must confirm). When the email
    # is captured-but-unconfirmed and the caller doesn't restate it, this turn is
    # treated as their reply to the read-back: an affirmation CONFIRMS it, a
    # rejection RE-OPENS the field. Mirrors the CaptureConfirmation state machine.
    email = state.email
    email_confirmed = state.email_confirmed
    parsed_email = extract_email_from_speech(utterance)
    if parsed_email and parsed_email != email:
        email = parsed_email
        email_confirmed = False
    elif email and not email_confirmed and readback_issued:
        # Only when the agent actually read the email back this call do we treat
        # the caller's reply as a confirmation — and only an UNAMBIGUOUS yes/no
        # transitions (incidental words leave it pending).
        verdict = _classify_core_confirmation(utterance)
        if verdict == "affirm":
            email_confirmed = True
        elif verdict == "reject":
            email = None
            email_confirmed = False

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
        email_confirmed=email_confirmed,
        follow_up=follow_up,
        bidding_active=bidding_active,
        declined_count=declined_count,
    )

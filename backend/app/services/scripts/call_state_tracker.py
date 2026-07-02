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

from app.services.scripts.spoken_email_normalizer import (
    extract_email_from_speech,
    extract_phone_from_speech,
)

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
    r"that(?:'?s|\s+is)\s+(right|correct|it|the\s+one))\b",
    re.IGNORECASE,
)
_CORE_REJECT_LEAD_RE = re.compile(r"^\s*(no|nope|nah|wrong|incorrect)\b", re.IGNORECASE)
# A clear correction intent anywhere in a short reply. Handles both the
# contraction ("that's wrong") and the formal ("that is wrong").
_CORE_REJECT_ANY_RE = re.compile(
    r"\b(that(?:'?s|\s+is)\s+(wrong|not\s+right|not\s+correct|incorrect|not\s+it)|"
    r"not\s+right|not\s+correct|incorrect|got\s+it\s+wrong|mis[\s-]?heard)\b",
    re.IGNORECASE,
)
_HAS_NEG_TOKEN_RE = re.compile(r"\b(no|not|nope|nah|wrong|incorrect)\b", re.IGNORECASE)
# "no problem" / "no worries" LEAD with 'no' but mean YES.
_AFFIRM_DISCOURSE_RE = re.compile(r"^\s*no\s+(problem|worries)\b", re.IGNORECASE)
# A positive correctness word (so "no, that's right" reads as affirm, not reject).
# NB: excludes ambiguous words like "good" ("no good" = bad).
_CORRECTNESS_RE = re.compile(r"\b(correct|right|perfect|exactly|that'?s\s+it|spot\s+on)\b", re.IGNORECASE)
# Partial-correction / hedge signals — the value is NOT fully affirmed, so keep
# it pending rather than committing a wrong/old one.
_HEDGE_RE = re.compile(
    r"\b(except|but|apart\s+from|almost|nearly|not\s+quite|old|other|"
    r"one\s+letter|wrong\s+one|change|instead|actually\s+it'?s)\b",
    re.IGNORECASE,
)


def _classify_core_confirmation(utterance: str) -> str:
    """'affirm' | 'reject' | 'unclear' for a caller reply to a CORE-field read-back.

    Deliberately conservative — a wrong verdict corrupts data:
      * an explicit correction ("that's wrong") rejects;
      * a discourse-marker 'no' that AFFIRMS correctness ("no problem, that's
        correct", "no that's right") is a YES, not a reject;
      * a short bare 'no'/'nope' rejects;
      * a clean leading affirmation with no negation, hedge, or question affirms;
      * anything hedged/partial ("perfect except the number", "yes, my old email")
        or otherwise ambiguous stays 'unclear' (pending) so we neither wipe a good
        value nor commit a wrong one.
    """
    t = (utterance or "").strip()
    if not t:
        return "unclear"
    n = len(t.split())
    hedged = bool(_HEDGE_RE.search(t))
    leads_neg = bool(_CORE_REJECT_LEAD_RE.match(t))

    # Explicit correction intent -> reject.
    if _CORE_REJECT_ANY_RE.search(t):
        return "reject"

    # A leading discourse 'no' that affirms correctness ("no problem, that's
    # correct", "no that's right") is a YES.
    if leads_neg and _CORRECTNESS_RE.search(t) and not hedged:
        return "affirm"

    # A short, genuine leading bare negation -> reject (but not the affirmative
    # discourse markers "no problem" / "no worries").
    if n <= 4 and leads_neg and not _AFFIRM_DISCOURSE_RE.match(t):
        return "reject"

    # A short, clean LEADING affirmation: no negation token, no hedge, no question.
    if (
        n <= 6
        and "?" not in t
        and _CORE_AFFIRM_RE.match(t)
        and not _HAS_NEG_TOKEN_RE.search(t)
        and not hedged
    ):
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
    # How many read-back turns have passed without the caller confirming. Bounds
    # the confirm loop (issue: never-converging read-back) — after a few the
    # prompt offers a fallback (spell slowly / take it another way / move on).
    email_readback_attempts: int = 0
    # Confirm-before-commit for a phone / callback number (issue #1 gap): a number
    # is a CORE field too — one wrong digit makes it useless — so it gets the SAME
    # fail-closed gate as email. A freshly captured number is UNCONFIRMED until the
    # caller confirms the read-back; a correction re-opens it; ambiguous replies
    # after a read-back are counted so the prompt can fall back.
    phone: Optional[str] = None
    phone_confirmed: bool = False
    phone_readback_attempts: int = 0
    follow_up: Optional[str] = None
    project_type: Optional[str] = None
    bidding_active: Optional[bool] = None
    declined_count: int = 0


def update_state_from_user_turn(
    state: CallState,
    utterance: str,
    *,
    readback_issued: bool = False,
    confirmation_verdict: Optional[str] = None,
    phone_readback_issued: bool = False,
    phone_confirmation_verdict: Optional[str] = None,
) -> CallState:
    """Return a new CallState with any new slots captured from `utterance`.

    ``readback_issued`` — True only when the agent's MOST RECENT turn actually
    read the pending email back. A caller reply is interpreted as a confirmation
    (affirm/reject) ONLY then, so a stray "yes"/"no" on an unrelated turn can't
    falsely commit or wipe a captured email.

    ``confirmation_verdict`` — an optional pre-computed 'affirm'|'reject'|'unclear'
    (e.g. the hybrid regex+LLM classifier resolved in the async caller). When
    provided it is used verbatim; otherwise the deterministic regex classifier
    (_classify_core_confirmation) is used. Either way an unresolved verdict leaves
    the value PENDING — the gate is fail-closed.

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
    # rejection RE-OPENS the field. This IS the live confirm-before-commit gate.
    email = state.email
    email_confirmed = state.email_confirmed
    email_readback_attempts = state.email_readback_attempts
    parsed_email = extract_email_from_speech(utterance)
    if parsed_email and parsed_email != email:
        email = parsed_email
        email_confirmed = False
        email_readback_attempts = 0  # a new/corrected value starts a fresh loop
    elif email and not email_confirmed and readback_issued:
        # Only when the agent actually read the email back this call do we treat
        # the caller's reply as a confirmation. Use the caller-supplied verdict
        # (hybrid regex+LLM) if given, else the deterministic regex classifier —
        # and only an UNAMBIGUOUS yes/no transitions (unclear leaves it pending).
        verdict = (
            confirmation_verdict
            if confirmation_verdict is not None
            else _classify_core_confirmation(utterance)
        )
        if verdict == "affirm":
            email_confirmed = True
        elif verdict == "reject":
            email = None
            email_confirmed = False
            email_readback_attempts = 0
        else:
            # A read-back happened but the caller's reply was ambiguous — count it
            # so the prompt can fall back after too many unresolved attempts.
            email_readback_attempts = state.email_readback_attempts + 1

    # Phone / callback number — SAME confirm-before-commit gate as email, mirrored
    # exactly: a new/corrected number starts UNCONFIRMED; when a number is pending
    # and the agent read it back, the caller's reply is the confirmation (affirm ->
    # confirm, reject -> re-open, unclear -> hold + bounded attempt). Fail-closed.
    phone = state.phone
    phone_confirmed = state.phone_confirmed
    phone_readback_attempts = state.phone_readback_attempts
    parsed_phone = extract_phone_from_speech(utterance)
    if parsed_phone and parsed_phone != phone:
        phone = parsed_phone
        phone_confirmed = False
        phone_readback_attempts = 0
    elif phone and not phone_confirmed and phone_readback_issued:
        verdict = (
            phone_confirmation_verdict
            if phone_confirmation_verdict is not None
            else _classify_core_confirmation(utterance)
        )
        if verdict == "affirm":
            phone_confirmed = True
        elif verdict == "reject":
            phone = None
            phone_confirmed = False
            phone_readback_attempts = 0
        else:
            phone_readback_attempts = state.phone_readback_attempts + 1

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
        email_readback_attempts=email_readback_attempts,
        phone=phone,
        phone_confirmed=phone_confirmed,
        phone_readback_attempts=phone_readback_attempts,
        follow_up=follow_up,
        bidding_active=bidding_active,
        declined_count=declined_count,
    )

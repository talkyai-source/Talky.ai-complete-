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
from typing import Optional, Sequence

from app.domain.services.voice_pipeline.contact_capture import (
    CaptureKind,
    CaptureStatus,
    ContactCaptureState,
    advance_capture,
    has_capture_intent,
)

_DAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|next week|later this week|end of week)\b",
    re.IGNORECASE,
)
_AGENT_EMAIL_REQUEST_RE = re.compile(
    r"\b(?:"
    r"what(?:'s|\s+is)\s+(?:your|the\s+(?:best|preferred))\s+"
    r"e-?mail(?:\s+address)?|"
    r"(?:can|could|would|will)\s+you(?:\s+please)?\s+"
    r"(?:provide|share|give|tell|repeat|spell|say)\s+(?:me\s+)?"
    r"(?:your|the|that)?\s*e-?mail(?:\s+address)?|"
    r"please\s+(?:provide|share|give|tell|repeat|spell|say)\s+(?:me\s+)?"
    r"(?:your|the|that)?\s*e-?mail(?:\s+address)?|"
    r"(?:may|can|could)\s+i\s+(?:have|get|take)\s+(?:your|the)\s+"
    r"e-?mail(?:\s+address)?"
    r")\b",
    re.I,
)
_AGENT_PHONE_REQUEST_RE = re.compile(
    r"\b(?:"
    r"what(?:'s|\s+is)\s+(?:your|the\s+(?:best|preferred))\s+"
    r"(?:phone(?:\s+number)?|callback(?:\s+number)?|mobile(?:\s+number)?|"
    r"cell(?:\s+number)?|number)|"
    r"(?:can|could|would|will)\s+you(?:\s+please)?\s+"
    r"(?:provide|share|give|tell|repeat|say)\s+(?:me\s+)?"
    r"(?:your|the|that)?\s*(?:phone(?:\s+number)?|callback(?:\s+number)?|"
    r"mobile(?:\s+number)?|cell(?:\s+number)?|number)|"
    r"please\s+(?:provide|share|give|tell|repeat|say)\s+(?:me\s+)?"
    r"(?:your|the|that)?\s*(?:phone(?:\s+number)?|callback(?:\s+number)?|"
    r"mobile(?:\s+number)?|cell(?:\s+number)?|number)|"
    r"(?:may|can|could)\s+i\s+(?:have|get|take)\s+(?:your|the)\s+"
    r"(?:phone(?:\s+number)?|callback(?:\s+number)?|mobile(?:\s+number)?|"
    r"cell(?:\s+number)?|number)"
    r")\b",
    re.I,
)
_AGENT_REASK_RE = re.compile(
    r"\b(?:"
    r"(?:(?:can|could|would)\s+you(?:\s+please)?|please)\s+"
    r"(?:repeat|spell|say)(?:\s+(?:that|it|the\s+\w+))?|"
    r"didn'?t\s+catch[^.!?]{0,60}(?:repeat|spell|say)|"
    r"two\s+different[^.!?]{0,60}(?:repeat|spell|say)"
    r")\b",
    re.I,
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
    # Structured contact modes. The scalar fields above remain the stable
    # prompt/persistence interface; these records carry why a value is pending,
    # its raw/normalised audit pair, and the actual confirmation timestamp.
    email_capture: Optional[ContactCaptureState] = None
    phone_capture: Optional[ContactCaptureState] = None
    # Only one contact clarification/confirmation is active at a time. This
    # prevents "never mind" for a phone from cancelling an unrelated email.
    active_contact_kind: Optional[CaptureKind] = None
    follow_up: Optional[str] = None
    project_type: Optional[str] = None
    bidding_active: Optional[bool] = None
    declined_count: int = 0

    def __post_init__(self) -> None:
        # Calls/tests created before C3 may restore the scalar slots directly.
        # Lift those into the state machine once so every subsequent transition
        # still uses the canonical machine. This does not validate or persist a
        # legacy value; live capture always enters through ``advance_capture``.
        if self.email and (
            self.email_capture is None
            or self.email_capture.normalized_value != self.email
        ):
            status = (
                CaptureStatus.CONFIRMED
                if self.email_confirmed
                else CaptureStatus.AWAITING_CONFIRMATION
            )
            object.__setattr__(
                self,
                "email_capture",
                ContactCaptureState(
                    kind="email",
                    status=status,
                    raw_value=self.email,
                    normalized_value=self.email,
                    validation_status=status.value,
                    attempts=self.email_readback_attempts,
                    segments=tuple(self.email.split("@", 1)) if "@" in self.email else (),
                ),
            )
        if self.phone and (
            self.phone_capture is None
            or self.phone_capture.normalized_value != self.phone
        ):
            status = (
                CaptureStatus.CONFIRMED
                if self.phone_confirmed
                else CaptureStatus.AWAITING_CONFIRMATION
            )
            object.__setattr__(
                self,
                "phone_capture",
                ContactCaptureState(
                    kind="phone",
                    status=status,
                    raw_value=self.phone,
                    normalized_value=self.phone,
                    validation_status=status.value,
                    attempts=self.phone_readback_attempts,
                    segments=(self.phone,),
                ),
            )


def update_state_from_user_turn(
    state: CallState,
    utterance: str,
    *,
    readback_issued: bool = False,
    confirmation_verdict: Optional[str] = None,
    phone_readback_issued: bool = False,
    phone_confirmation_verdict: Optional[str] = None,
    phone_region: Optional[str] = None,
    transcript_confidence: Optional[float] = None,
    transcript_alternatives: Sequence[str] = (),
    explicit_contact_reask: bool = False,
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

    email_verdict = confirmation_verdict
    if readback_issued and email_verdict is None:
        email_verdict = _classify_core_confirmation(utterance)
    phone_verdict = phone_confirmation_verdict
    if phone_readback_issued and phone_verdict is None:
        phone_verdict = _classify_core_confirmation(utterance)

    active_kind = state.active_contact_kind
    email_intent = has_capture_intent("email", utterance)
    phone_intent = has_capture_intent("phone", utterance)
    dual_readback = readback_issued and phone_readback_issued
    if dual_readback:
        # The agent may read both pending values in one sentence and ask one
        # combined "did I get that right?". Both independently computed
        # verdicts belong to that same question; serialize later clarification,
        # not application of the answer that was already requested.
        active_kind = (
            active_kind if active_kind in {"email", "phone"} else "email"
        )
    elif readback_issued:
        active_kind = "email"
    elif phone_readback_issued:
        active_kind = "phone"
    elif email_intent and not phone_intent:
        active_kind = "email"
    elif phone_intent and not email_intent:
        active_kind = "phone"
    elif email_intent and phone_intent and active_kind not in {"email", "phone"}:
        # Retain both volunteered values, but serialize confirmation: email is
        # read back first and phone remains pending for the following turn.
        active_kind = "email"

    email_capture = state.email_capture
    phone_capture = state.phone_capture
    if active_kind == "email" or (email_intent and phone_intent) or dual_readback:
        email_capture = advance_capture(
            state.email_capture,
            kind="email",
            utterance=utterance,
            readback_issued=readback_issued,
            confirmation_verdict=email_verdict,
            transcript_confidence=transcript_confidence,
            transcript_alternatives=transcript_alternatives,
            explicit_reask=explicit_contact_reask,
            mode_active=True,
        )
    if active_kind == "phone" or (email_intent and phone_intent) or dual_readback:
        phone_capture = advance_capture(
            state.phone_capture,
            kind="phone",
            utterance=utterance,
            readback_issued=phone_readback_issued,
            confirmation_verdict=phone_verdict,
            phone_region=phone_region,
            transcript_confidence=transcript_confidence,
            transcript_alternatives=transcript_alternatives,
            explicit_reask=explicit_contact_reask,
            mode_active=True,
        )

    active_capture = (
        email_capture
        if active_kind == "email"
        else phone_capture if active_kind == "phone" else None
    )
    if active_capture is None or active_capture.status in {
        CaptureStatus.CONFIRMED,
        CaptureStatus.CANCELLED,
    }:
        active_kind = None
        for candidate_kind, candidate in (
            ("email", email_capture),
            ("phone", phone_capture),
        ):
            if candidate is not None and candidate.status in {
                CaptureStatus.NEEDS_CLARIFICATION,
                CaptureStatus.INVALID,
                CaptureStatus.AWAITING_CONFIRMATION,
            }:
                active_kind = candidate_kind
                break

    def public_value(capture: Optional[ContactCaptureState]) -> Optional[str]:
        if capture is None or capture.status in {
            CaptureStatus.INVALID,
            CaptureStatus.CANCELLED,
        }:
            return None
        return capture.normalized_value

    email = public_value(email_capture)
    email_confirmed = bool(
        email_capture and email_capture.status is CaptureStatus.CONFIRMED
    )
    email_readback_attempts = email_capture.attempts if email_capture else 0
    phone = public_value(phone_capture)
    phone_confirmed = bool(
        phone_capture and phone_capture.status is CaptureStatus.CONFIRMED
    )
    phone_readback_attempts = phone_capture.attempts if phone_capture else 0

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
        email_capture=email_capture,
        phone_capture=phone_capture,
        active_contact_kind=active_kind,
        follow_up=follow_up,
        bidding_active=bidding_active,
        declined_count=declined_count,
    )


def update_state_from_agent_turn(state: CallState, utterance: str) -> CallState:
    """Arm/clarify the explicit contact mode from an actual agent request.

    This is the varying production signal for digit-only replies and Flux,
    whose recognition confidence is deliberately unavailable. It never stores
    a value; it only controls what the next caller turn is allowed to parse.
    """
    text = str(utterance or "").strip()
    if not text:
        return state
    reask = bool(_AGENT_REASK_RE.search(text))
    kind: Optional[CaptureKind] = None
    if _AGENT_EMAIL_REQUEST_RE.search(text):
        kind = "email"
    elif _AGENT_PHONE_REQUEST_RE.search(text):
        kind = "phone"
    elif reask:
        kind = state.active_contact_kind
    if kind is None:
        return state

    capture = getattr(state, f"{kind}_capture")
    changes = {"active_contact_kind": kind}
    if (
        reask
        and capture is not None
        and capture.status is not CaptureStatus.CONFIRMED
    ):
        changes[f"{kind}_capture"] = replace(
            capture,
            status=CaptureStatus.NEEDS_CLARIFICATION,
            validation_status=CaptureStatus.NEEDS_CLARIFICATION.value,
            confirmed_at=None,
            attempts=min(capture.attempts + 1, 3),
            clarification_prompt=(
                "Please say 'the username is' and spell that segment, or repeat "
                "the complete email including at and the domain."
                if kind == "email"
                else "Please repeat the complete plus-prefixed phone number, or say "
                "'the first three digits are' or 'the last three digits are'."
            ),
        )
    return replace(state, **changes)

"""Two narrow, deterministic checks on each sentence before it is spoken.

Release review 2026-10-08: the conversational-model redesign removed the
deterministic speech guards, and its own offline contracts then failed:

* with no email action available, the agent said "I have sent the email.";
* after the caller said "I am not your customer.", the agent said "As our
  existing customer, your account is ready."

Both are claims a caller acts on. These checks enforce the rules the release
already states instead of reinterpreting the conversation:

1. A first-person claim that an action was just completed (sent, booked,
   transferred, passed on) is spoken only when an action result on this call
   has ``confirmation_allowed`` true (action_tools.py: confirmation is allowed
   only when that flag is set). Otherwise an honest line replaces it.
2. Once the caller explicitly denies being a customer, a sentence that claims
   an existing relationship is replaced, and the denial reaches the model in
   the live structured state (live_structured_state.py).

Everything else passes through unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from app.domain.models.conversation import MessageRole

# "I have sent the email", "We've just emailed it", "I sent you the link".
# Object words keep historical statements ("we sent you a quote last month")
# out of scope.
_SENT_CLAIM = re.compile(
    r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:just\s+|now\s+|already\s+)?"
    r"(?:sent|emailed|texted|messaged|forwarded)\s+(?:you\s+|it\s+|that\s+|this\s+)?"
    r"(?:the|it|that|this|over|a\s+(?:copy|link|confirmation)|an\s+email)\b",
    re.IGNORECASE,
)
# "I've booked you in", "We have scheduled the callback", "I've transferred you".
_DONE_CLAIM = re.compile(
    r"\b(?:i|we)(?:'ve|\s+have)\s+(?:just\s+|now\s+|already\s+)?"
    r"(?:booked|scheduled|arranged|transferred|set\s+up)\b",
    re.IGNORECASE,
)
# Passive or subject-less forms of the same claims: "The email has been sent",
# "Your callback has been scheduled", "You're all booked in for Tuesday".
_SENT_PASSIVE_CLAIM = re.compile(
    r"\b(?:email|e-mail|message|text|link|confirmation|details|information|copy|"
    r"brochure|quote|invoice|receipt|it|that|this)(?:'s|\s+has|\s+have)\s+"
    r"(?:just\s+|now\s+|already\s+)?been\s+(?:sent|emailed|texted|forwarded|delivered)\b",
    re.IGNORECASE,
)
_DONE_PASSIVE_CLAIM = re.compile(
    r"\b(?:callback|call\s*back|appointment|booking|reservation|slot|meeting|call|"
    r"demo|visit|consultation|it|that|this)(?:'s|\s+has|\s+have)\s+"
    r"(?:just\s+|now\s+|already\s+)?been\s+(?:booked|scheduled|arranged|confirmed|set\s+up)\b"
    r"|\byou(?:'re|\s+are|'ve\s+been|\s+have\s+been)\s+(?:all\s+)?"
    r"(?:booked|scheduled|set\s+up|signed\s+up)\b",
    re.IGNORECASE,
)
_TRANSFER_PASSIVE_CLAIM = re.compile(
    r"\byou(?:'re|\s+are)\s+(?:now\s+)?(?:being\s+)?(?:transferred|connected)\b"
    r"|\byou(?:'ve|\s+have)\s+been\s+(?:transferred|connected)\b",
    re.IGNORECASE,
)
_TRANSFERRED_CLAIM = re.compile(r"\btransferred\b", re.IGNORECASE)
# "I've passed your details on".
_PASSED_ON_CLAIM = re.compile(
    r"\b(?:i|we)(?:'ve|\s+have)\s+(?:just\s+|now\s+|already\s+)?passed\s+"
    r"(?:your|the|it|that|this)\b[^.?!]*\bon\b",
    re.IGNORECASE,
)

# The caller explicitly says they are not a customer.
_CALLER_DENIES_RELATIONSHIP = re.compile(
    r"\b(?:i'?m|i\s+am|we'?re|we\s+are)\s+not\s+(?:a|an|your|one\s+of\s+your)\s+"
    r"(?:existing\s+|current\s+)?(?:customer|client)s?\b"
    r"|\bnever\s+been\s+(?:a|your)\s+(?:customer|client)\b"
    r"|\b(?:i|we)\s+(?:don'?t|do\s+not)\s+have\s+an?\s+account\s+with\s+you\b",
    re.IGNORECASE,
)
# The agent claims an existing relationship.
_RELATIONSHIP_CLAIM = re.compile(
    r"\b(?:existing|current|valued|loyal)\s+(?:customer|client)s?\b"
    r"|\bas\s+(?:our|a)\s+(?:customer|client)\b"
    r"|\byour\s+account\s+(?:with\s+us|is)\b",
    re.IGNORECASE,
)

# "I've removed you from our list", "You've been taken off". Spoken only once
# the opt-out write landed on this call (session._opt_out_recorded).
_REMOVED_CLAIM = re.compile(
    r"\b(?:(?:i|we)(?:'ve|\s+have)|you(?:'ve|\s+have)\s+been|you'?re|you\s+are)\s+"
    r"(?:now\s+|just\s+|already\s+)?(?:removed|taken\s+(?:you\s+)?off|unsubscribed|opted\s+out)\b",
    re.IGNORECASE,
)
OPT_OUT_PENDING_LINE = "I've noted your request."

# Internal knowledge section ids (sections.py: f"k{digest[:8]}_{n}"). Test call
# c8df9107 (2026-10-08) spoke "k11a0797713" aloud when the agent ran out of
# lookup rounds mid-navigation.
_SECTION_ID = re.compile(r"\s*\bk[0-9a-f]{8}_\d+\b")

UNBACKED_ACTION_LINE = "I'm not able to do that from this call."
RELATIONSHIP_ACK_LINE = "Thanks for clarifying."


def _confirmed_actions(session: Any) -> set[str]:
    results = getattr(session, "_voice_action_results", None)
    if not isinstance(results, dict):
        return set()
    return {
        str(r.get("action") or name)
        for name, r in results.items()
        if isinstance(r, dict) and r.get("confirmation_allowed") is True
    }


def _needed_actions(sentence: str) -> Optional[frozenset[str]]:
    """Which confirmed action can back this claim; None when it claims nothing.

    A confirmed send_email must not unlock "I've booked you in": each kind of
    claim is checked against the action that does that thing.
    """
    needed: set[str] = set()
    if _SENT_CLAIM.search(sentence) or _SENT_PASSIVE_CLAIM.search(sentence):
        needed |= {"send_email", "submit_form"}
    if _TRANSFER_PASSIVE_CLAIM.search(sentence) or (
        _DONE_CLAIM.search(sentence) and _TRANSFERRED_CLAIM.search(sentence)
    ):
        needed |= {"transfer_call"}
    if _DONE_PASSIVE_CLAIM.search(sentence) or (
        _DONE_CLAIM.search(sentence) and not _TRANSFERRED_CLAIM.search(sentence)
    ):
        needed |= {"schedule_callback", "submit_form"}
    if _PASSED_ON_CLAIM.search(sentence):
        needed |= {"send_email", "submit_form", "schedule_callback", "transfer_call"}
    return frozenset(needed) if needed else None


def _confirmation_allowed(session: Any, sentence: Optional[str] = None) -> bool:
    confirmed = _confirmed_actions(session)
    if sentence is None:
        return bool(confirmed)
    needed = _needed_actions(sentence)
    return needed is None or bool(confirmed & needed)


def claims_completed_action(sentence: str) -> bool:
    return _needed_actions(sentence) is not None


def caller_denied_relationship(history: Iterable[Any]) -> bool:
    """True once any caller turn explicitly denies being a customer."""
    for message in history or ():
        if getattr(message, "role", None) != MessageRole.USER:
            continue
        if _CALLER_DENIES_RELATIONSHIP.search(str(getattr(message, "content", "") or "")):
            return True
    return False


def guard_spoken_sentence(session: Any, sentence: str) -> str:
    """Return what to actually speak for ``sentence``."""
    if not sentence:
        return sentence
    if _SECTION_ID.search(sentence):
        sentence = _SECTION_ID.sub("", sentence).strip()
        if not any(c.isalnum() for c in sentence):
            return ""
    if claims_completed_action(sentence) and not _confirmation_allowed(session, sentence):
        return UNBACKED_ACTION_LINE
    if _REMOVED_CLAIM.search(sentence) and getattr(session, "_opt_out_recorded", False) is not True:
        return OPT_OUT_PENDING_LINE
    if _RELATIONSHIP_CLAIM.search(sentence) and caller_denied_relationship(
        getattr(session, "conversation_history", ())
    ):
        return RELATIONSHIP_ACK_LINE
    return sentence

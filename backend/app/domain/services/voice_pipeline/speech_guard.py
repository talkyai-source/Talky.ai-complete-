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
   The claim must also match the action's real state (QA F10-01): delivery is
   never confirmed (a provider only accepts a message for sending), a transfer
   is "under way" only once it was accepted, and a request whose outcome is
   unresolved is neither sent, failed nor to be sent again.
2. Once the caller explicitly denies being a customer, a sentence that claims
   an existing relationship is replaced, and the denial reaches the model in
   the live structured state (live_structured_state.py).

Everything else passes through unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from app.domain.models.conversation import MessageRole
from app.domain.services.voice_pipeline.action_tools import safe_failure_speech

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
    r"(?:just\s+|now\s+|already\s+)?been\s+(?:sent|emailed|texted|forwarded)\b",
    re.IGNORECASE,
)
# Simple past of the same: "The email was sent to you." (_claims_now keeps "The
# quote was sent last week" out of scope).
_SENT_PAST_CLAIM = re.compile(
    r"\b(?:email|e-mail|message|text|link|confirmation|details|information|copy|"
    r"brochure|quote|invoice|receipt|it|that|this)\s+(?:was|were)\s+"
    r"(?:just\s+|now\s+|already\s+|successfully\s+)?(?:sent|emailed|texted|forwarded)\b",
    re.IGNORECASE,
)
# "The email was delivered to your inbox." No executor confirms delivery.
_DELIVERED_CLAIM = re.compile(
    r"\b(?:was|were|has\s+been|have\s+been|'s\s+been|got)\s+"
    r"(?:just\s+|now\s+|already\s+|successfully\s+)?delivered\b"
    r"|\bdelivered\s+(?:it\s+)?(?:to|into)\s+your\s+inbox\b"
    r"|\b(?:arrived|landed)\s+in\s+your\s+inbox\b"
    r"|\b(?:it'?s|it\s+is)\s+(?:now\s+|already\s+)?in\s+your\s+inbox\b",
    re.IGNORECASE,
)
# A transfer under way: "I'm transferring you now", "You're being transferred".
_TRANSFER_PROGRESS_CLAIM = re.compile(
    r"\b(?:i'?m|i\s+am|we'?re|we\s+are)\s+(?:now\s+|just\s+)?"
    r"(?:(?:transferring|connecting)\s+you|putting\s+you\s+through)\b"
    r"|^\W*(?:(?:transferring|connecting)\s+you|putting\s+you\s+through)\b"
    r"|\byou(?:'re|\s+are)\s+(?:now\s+)?being\s+(?:transferred|connected)\b",
    re.IGNORECASE,
)
# An unresolved outcome stated as a failure, or a blind resend.
_FAILED_CLAIM = re.compile(
    r"\b(?:could\s*n'?t|could\s+not|was\s*n'?t|was\s+not|were\s*n'?t|were\s+not|"
    r"did\s*n'?t|did\s+not)\s+(?:be\s+|get\s+)?(?:sent|send|delivered|go\s+through|"
    r"transferred|connected|booked|scheduled|submitted)\b"
    r"|\bfailed\s+to\s+(?:send|deliver|go\s+through|transfer|connect|book|schedule|submit)\b"
    r"|\b(?:sending|email|transfer|booking|request)\s+(?:has\s+)?failed\b",
    re.IGNORECASE,
)
_RESEND_REQUEST = re.compile(
    r"\bre-?send\b|\b(?:send|email|transfer|book)\s+(?:\w+\s+){0,3}?again\b",
    re.IGNORECASE,
)
# Which action a failure or resend sentence is about.
_ACTION_WORDS = (
    (re.compile(r"\b(?:e-?mail\w*|sen[dt]\w*|deliver\w*|message|link|form|submi\w*)\b",
                re.IGNORECASE), frozenset({"send_email", "submit_form"})),
    (re.compile(r"\b(?:transfer\w*|connect\w*|through)\b", re.IGNORECASE),
     frozenset({"transfer_call"})),
    (re.compile(r"\b(?:book\w*|schedul\w*|callback|call\s+back|appointment)\b", re.IGNORECASE),
     frozenset({"schedule_callback"})),
)
# A hedge covers the clause it governs: "I can't confirm that the email was
# sent" is not a claim, but "I'm not sure you got it, but I've sent it" is.
_CONJUNCTION = r"\b(?:but|though|although|however|and|so|yet)\b"
_HEDGED_CLAUSE = re.compile(
    r"\b(?:(?:can'?t|cannot|can\s+not|couldn'?t|could\s+not|unable\s+to|not\s+able\s+to)\s+"
    r"(?:confirm|verify)|(?:not\s+sure|don'?t\s+know|do\s+not\s+know)\s+(?:if|whether))\b"
    rf"(?:(?!{_CONJUNCTION})[^,;:.!?])*",
    re.IGNORECASE,
)
# An explicit past date on the claim itself: "The quote was sent last week".
# "Just now" or "a moment ago" is this call.
_PAST_TIME = re.compile(
    r"\b(?:last\s+(?:week|month|year|time)|yesterday|previously"
    r"|(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|a\s+few|few|several)\s+"
    r"(?:days?|weeks?|months?|years?)\s+ago)\b",
    re.IGNORECASE,
)
_CLAUSE_END = re.compile(rf"[,;:.!?]|{_CONJUNCTION}", re.IGNORECASE)
_UNRESOLVED_STATUSES = frozenset({"unknown", "in_progress"})
_SEND_ACTIONS = frozenset({"send_email", "submit_form"})
_TRANSFER_ACTIONS = frozenset({"transfer_call"})
_DONE_PASSIVE_CLAIM = re.compile(
    r"\b(?:callback|call\s*back|appointment|booking|reservation|slot|meeting|call|"
    r"demo|visit|consultation|it|that|this)(?:'s|\s+has|\s+have)\s+"
    r"(?:just\s+|now\s+|already\s+)?been\s+(?:booked|scheduled|arranged|confirmed|set\s+up)\b"
    r"|\byou(?:'re|\s+are|'ve\s+been|\s+have\s+been)\s+(?:all\s+)?"
    r"(?:booked|scheduled|set\s+up|signed\s+up)\b",
    re.IGNORECASE,
)
_TRANSFER_PASSIVE_CLAIM = re.compile(
    r"\byou(?:'re|\s+are)\s+(?:now\s+)?(?:transferred|connected)\b"
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
# The caller says they are a customer ("Actually, I am your customer.").
_CALLER_AFFIRMS_RELATIONSHIP = re.compile(
    r"\b(?:i'?m|i\s+am|we'?re|we\s+are)\s+(?:a|an|your|one\s+of\s+your)\s+"
    r"(?:existing\s+|current\s+)?(?:customer|client)s?\b",
    re.IGNORECASE,
)
# Words in quotation marks are someone else's ("The script says "I am your
# customer"."), never the caller's own affirmation.
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”')
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
UNRESOLVED_ACTION_LINE = "I couldn't confirm the outcome of that request."
RELATIONSHIP_ACK_LINE = "Thanks for clarifying."


def _action_results(session: Any) -> dict[str, dict]:
    results = getattr(session, "_voice_action_results", None)
    if not isinstance(results, dict):
        return {}
    return {
        str(r.get("action") or name): r
        for name, r in results.items()
        if isinstance(r, dict)
    }


def _claims_now(pattern: re.Pattern, text: str) -> bool:
    """A match of ``pattern`` that is not dated to an explicit past time in its own clause."""
    for match in pattern.finditer(text):
        rest = text[match.end():]
        end = _CLAUSE_END.search(rest)
        if not _PAST_TIME.search(rest[:end.start()] if end else rest):
            return True
    return False


def _needed_actions(sentence: str) -> Optional[frozenset[str]]:
    """Which confirmed action can back this claim; None when it claims nothing.

    A confirmed send_email must not unlock "I've booked you in": each kind of
    claim is checked against the action that does that thing.
    """
    needed: set[str] = set()
    if (_SENT_CLAIM.search(sentence) or _SENT_PASSIVE_CLAIM.search(sentence)
            or _claims_now(_SENT_PAST_CLAIM, sentence)):
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


def claims_completed_action(sentence: str) -> bool:
    return _needed_actions(sentence) is not None


def _action_claim_line(session: Any, sentence: str) -> Optional[str]:
    """The truthful line for an action claim this call's results do not back.

    None keeps the sentence. Each claim is checked against the state of the
    action that does that thing; only the hedged clause itself is set aside.
    """
    text = _HEDGED_CLAUSE.sub(" ", sentence)
    results = _action_results(session)

    def about(actions: Iterable[str]) -> list[dict]:
        return [r for name, r in results.items() if name in actions]

    def unresolved(actions: Iterable[str]) -> bool:
        return any(r.get("status") in _UNRESOLVED_STATUSES for r in about(actions))

    needed = _needed_actions(text)
    if needed is not None and not any(r.get("confirmation_allowed") is True for r in about(needed)):
        return UNRESOLVED_ACTION_LINE if unresolved(needed) else UNBACKED_ACTION_LINE
    if _claims_now(_DELIVERED_CLAIM, text):
        if unresolved(_SEND_ACTIONS):
            return UNRESOLVED_ACTION_LINE
        for r in about(_SEND_ACTIONS):
            if r.get("success") is True and r.get("status") in {"accepted", "provider_accepted"}:
                return safe_failure_speech(str(r.get("action")), r)
        return UNBACKED_ACTION_LINE
    if _TRANSFER_PROGRESS_CLAIM.search(text) and not any(
        r.get("success") is True for r in about(_TRANSFER_ACTIONS)
    ):
        return UNRESOLVED_ACTION_LINE if unresolved(_TRANSFER_ACTIONS) else UNBACKED_ACTION_LINE
    if _FAILED_CLAIM.search(text) or _RESEND_REQUEST.search(text):
        named = frozenset().union(*(actions for words, actions in _ACTION_WORDS if words.search(text)))
        if unresolved(named or results.keys()):
            return UNRESOLVED_ACTION_LINE
    return None


def caller_relationship(history: Iterable[Any]) -> Optional[str]:
    """The caller's latest own statement about being a customer: "denied",
    "affirmed", or None when they never said either (QA F10-02). A denial
    stands until the caller takes it back; quoted words don't take it back."""
    latest = None
    for message in history or ():
        if getattr(message, "role", None) != MessageRole.USER:
            continue
        text = str(getattr(message, "content", "") or "")
        own = _QUOTED.sub(lambda quoted: " " * len(quoted.group()), text)
        said = [(m.start(), "denied") for m in _CALLER_DENIES_RELATIONSHIP.finditer(text)]
        said += [(m.start(), "affirmed") for m in _CALLER_AFFIRMS_RELATIONSHIP.finditer(own)]
        if said:
            latest = max(said)[1]
    return latest


def caller_denied_relationship(history: Iterable[Any]) -> bool:
    """True while the caller's latest own statement denies being a customer."""
    return caller_relationship(history) == "denied"


def guard_spoken_sentence(session: Any, sentence: str) -> str:
    """Return what to actually speak for ``sentence``."""
    if not sentence:
        return sentence
    if _SECTION_ID.search(sentence):
        sentence = _SECTION_ID.sub("", sentence).strip()
        if not any(c.isalnum() for c in sentence):
            return ""
    action_line = _action_claim_line(session, sentence)
    if action_line is not None:
        return action_line
    if _REMOVED_CLAIM.search(sentence) and getattr(session, "_opt_out_recorded", False) is not True:
        return OPT_OUT_PENDING_LINE
    if _RELATIONSHIP_CLAIM.search(sentence) and caller_denied_relationship(
        getattr(session, "conversation_history", ())
    ):
        return RELATIONSHIP_ACK_LINE
    return sentence

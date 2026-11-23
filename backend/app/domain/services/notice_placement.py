"""Where the recording notice is said on an OUTGOING call.

Two ways exist. The platform's own wording is always said inside the opening
sentence ("..., on a recorded line. ..."). An account's own text is, by default
(`own_sentence_first`), said first as a separate sentence. An account may choose
`in_opening`: then the opening carries the recording words in place of the
account's own sentence. On a turn where no opening is said (the person's first
words were not a greeting, or the opening could not be prepared) the account's
own sentence is still spoken, alone, whatever it chose; on an outgoing call whose
first words were not a greeting the plain sentence "This is <agent>, an AI
assistant from <company>." follows it (``carrier="identity_line"``: only the
platform's own wording is said inside that sentence, never an account's own). The proof rules do not depend on the
placement: a notice is "confirmed" only when the phone system confirms the whole
spoken sentence was played, and any doubt is recorded as NOT confirmed.

This module only chooses the placement. It never decides WHETHER a notice is
owed (that is `RecordingPolicyService.decide`).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from app.domain.services.recording_policy_service import (
    is_platform_notice_text,
    spoken_disclosure_text,
)

logger = logging.getLogger(__name__)

OWN_SENTENCE_FIRST = "own_sentence_first"
IN_OPENING = "in_opening"
_PLACEMENTS = frozenset({OWN_SENTENCE_FIRST, IN_OPENING})


ENV_SWITCH = "OUTBOUND_NOTICE_PLACEMENT_CHOICE_ENABLED"
_ON_WORDS = ("1", "true", "yes", "on")


def placement_choice_enabled() -> bool:
    """The platform's switch for the account's choice. OFF unless set to 1/true/yes/on.

    Off (the default): no stored choice is used, so every call says the notice as it
    does without this feature; the settings screen is not offered the choice; the
    endpoint refuses. Whether "on a recorded line" may stand in for an account's own
    sentence is a decision for the platform's owner, and nothing here takes it.
    Read on every call, so a restart with the switch removed turns it off again.
    """
    return os.getenv("OUTBOUND_NOTICE_PLACEMENT_CHOICE_ENABLED", "").strip().lower() in _ON_WORDS


def normalize_placement(value: Any) -> str:
    """A stored value as a known placement; anything else is the default."""
    return value if isinstance(value, str) and value in _PLACEMENTS else OWN_SENTENCE_FIRST


@dataclass(frozen=True)
class NoticePlacement:
    in_opening: bool
    why: str
    said: str


def placement_for(
    decision: Any,
    text: Optional[str],
    *,
    opener_available: bool,
    pinned: bool,
    carrier: str = "opening",
) -> NoticePlacement:
    """Choose where the notice `text` is said. Pure: writes no log line.

    `carrier` is the sentence offered to carry the recording words: "opening"
    (the greeting) or "identity_line" (the one sentence said for first words that
    are not a greeting). On the identity line only the platform's own wording is
    said inside it; an account's own sentence is always said first, whatever it chose.

    `decide_notice_placement` is this plus the one per-call log line; the
    campaign preview calls this alone so it never writes evidence about a call.
    """
    stored = normalize_placement(getattr(decision, "outbound_notice_placement", None))
    choice_on = placement_choice_enabled()
    if not text:
        return NoticePlacement(False, "no_notice", "none")
    if not opener_available:
        return NoticePlacement(False, "opener_not_spoken", "notice_alone")
    if carrier == "identity_line":
        if is_platform_notice_text(text):
            return NoticePlacement(True, "platform_wording", "identity_line_with_recorded_line")
        why = "account_choice_is_for_the_opening" if stored == IN_OPENING else "account_sentence_first"
        return NoticePlacement(False, why, "notice_then_identity_line")
    if is_platform_notice_text(text):
        return NoticePlacement(True, "platform_wording", "opening_with_recorded_line")
    if pinned:
        return NoticePlacement(False, "incoming_line_text", "notice_then_opening")
    if stored == IN_OPENING and choice_on:
        return NoticePlacement(True, "account_choice", "opening_with_recorded_line")
    if stored == IN_OPENING:
        # The account chose it, but the platform's switch is off: said as by default.
        return NoticePlacement(False, "account_choice_switched_off", "notice_then_opening")
    return NoticePlacement(False, "account_sentence_first", "notice_then_opening")


def decide_notice_placement(
    decision: Any,
    text: Optional[str],
    *,
    opener_available: bool,
    pinned: bool,
    call_id: Any,
    path: str,
    carrier: str = "opening",
) -> NoticePlacement:
    """Choose where the notice `text` is said on this turn, and log it once."""
    stored = normalize_placement(getattr(decision, "outbound_notice_placement", None))
    choice_on = placement_choice_enabled()
    result = placement_for(
        decision, text, opener_available=opener_available, pinned=pinned, carrier=carrier,
    )
    logger.info(
        "recording_notice_placement call_id=%s path=%s stored=%s choice_switch=%s said=%s why=%s policy_reason=%s",
        str(call_id)[:12], path, stored, "on" if choice_on else "off",
        result.said, result.why, getattr(decision, "reason", "-"),
    )
    return result


def own_sentence_for_display(announcement_text: Optional[str]) -> Optional[str]:
    """The account's own sentence as it is spoken first, or None.

    None when the stored text is empty or is the platform's own wording (that is
    already said inside the opening sentence).
    """
    if not isinstance(announcement_text, str) or not announcement_text.strip():
        return None
    class _Stored:
        announcement_required = True
        announcement_text = ""
    stored = _Stored()
    stored.announcement_text = announcement_text
    spoken = spoken_disclosure_text(stored)
    if not spoken or is_platform_notice_text(spoken):
        return None
    return spoken

"""Two deterministic checks on what the agent says, for every campaign.

Both came from one live call, d644f0ea (2026-09-28, Dojo-PC → 940007), and
both are general: nothing in them knows about a campaign, a product or a
script.

1. The agent claimed a contact it did not have.
   The caller gave a WhatsApp number that never parsed, started correcting it,
   and the agent said "Thanks, I'll pass that on for Azian to contact you via
   WhatsApp" and hung up. There was no number to pass on. Saying so is a false
   statement to the caller and loses the lead. ``unbacked_contact_claim`` spots
   a claim to have / pass on contact details while no contact is confirmed and
   the caller was just trying to give one, and swaps in a re-ask.

2. The agent re-asked the same scripted question after every answer.
   "Are you still using Dojo for payments, or have you switched to another
   provider?" was tacked onto seven answers in a row while the caller was
   asking their own questions. ``is_repeated_question`` recognises a question
   the agent has already asked twice (worded slightly differently each time),
   so the reply can drop it once the rest of the answer has been spoken.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

CONTACT_REASK = (
    "Sorry, I don't think I caught the whole number. Could you say it again, "
    "digit by digit, starting with the country code?"
)
EMAIL_REASK = (
    "Sorry, I don't think I caught the whole email address. Could you spell it "
    "for me again?"
)

_CLAIM = re.compile(
    r"\b(?:"
    r"(?:i'?ll|i will|we'?ll|we will|i can|let me)\s+(?:pass|send|share|forward|hand)\s+"
    r"(?:that|it|this|those|them|your\s+\w+|the\s+(?:number|details|email|contact))\s+"
    r"(?:on|over|along|through|to)"
    r"|(?:i'?ve|i have|we'?ve|we have)\s+(?:got|noted|taken|saved|recorded|written)\s+"
    r"(?:down\s+)?(?:your|the|that)\s+(?:number|details|email|contact)"
    r"|noted\s+(?:that|it)\s+down"
    r")\b",
    re.IGNORECASE,
)
_DIGIT_WORDS = {
    "zero", "oh", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "double", "triple", "plus",
}
_EMAIL_CUES = re.compile(r"@|\bat\b.*\b(?:dot|com|co)\b|\bgmail\b|\bhotmail\b|\boutlook\b", re.IGNORECASE)


def _plain(text: str) -> str:
    """Model output uses typographic apostrophes ("I’ll"); match on plain ones."""
    return (text or "").replace("’", "'").replace("‘", "'")


def _digitish_count(text: str) -> int:
    words = re.findall(r"[a-z]+|\d", (text or "").lower())
    return sum(1 for w in words if w.isdigit() or w in _DIGIT_WORDS)


def _capture(call_state: Any, name: str):
    return getattr(call_state, name, None) if call_state is not None else None


def _confirmed(capture: Any) -> bool:
    status = getattr(capture, "status", None)
    return getattr(status, "value", status) == "confirmed"


def unbacked_contact_claim(
    text: str,
    call_state: Any,
    last_caller_text: Optional[str],
) -> Optional[str]:
    """The re-ask to speak instead of ``text``, or None when ``text`` is fine.

    Fires only when all three hold: the sentence claims to have or pass on the
    caller's details; no phone or email is confirmed; and the caller was just
    trying to give one (a capture is part-way, or their last turn was mostly
    digits / looked like an email). "I'll pass that on to Azian" about a
    complaint, with no contact in play, is left alone.
    """
    if not text or not _CLAIM.search(_plain(text)):
        return None
    phone = _capture(call_state, "phone_capture")
    email = _capture(call_state, "email_capture")
    if _confirmed(phone) or _confirmed(email):
        return None
    if bool(getattr(call_state, "phone_confirmed", False)) or bool(
        getattr(call_state, "email_confirmed", False)
    ):
        return None
    last = last_caller_text or ""
    if email is not None or _EMAIL_CUES.search(last):
        return EMAIL_REASK
    if phone is not None or _digitish_count(last) >= 6:
        return CONTACT_REASK
    return None


_QUESTION_STOP = frozenset(
    "a an the and or but to of for in on at by with from is are was were be do does "
    "did have has had you your i me my we our it its this that there so just now "
    "could would can will should may please let know tell if him his her he she "
    "they them their".split()
)


def _question_words(sentence: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9']+", (sentence or "").lower())
        if w not in _QUESTION_STOP and len(w) > 1
    }


def _questions_in(text: str) -> list[str]:
    return [q.strip() for q in re.findall(r"[^.!?]*\?", text or "") if q.strip()]


def is_repeated_question(
    sentence: str,
    earlier_agent_turns: Iterable[str],
    *,
    max_asks: int = 2,
    similarity: float = 0.6,
) -> bool:
    """True when ``sentence`` is a question already asked ``max_asks`` times.

    Similarity is the share of the shorter question's meaningful words that the
    other one also uses, so "...or have you switched to another provider?" and
    "...or have you moved to another provider?", or "call you or contact you by
    WhatsApp?" and "call you or reach you via WhatsApp?", count as the same.
    """
    if not sentence or "?" not in sentence:
        return False
    words = _question_words(_plain(sentence))
    if len(words) < 3:
        return False  # "Right?" / "Is that okay?" are not a scripted question
    asked = 0
    for turn in earlier_agent_turns:
        for q in _questions_in(turn):
            other = _question_words(_plain(q))
            if len(other) >= 3 and len(words & other) / min(len(words), len(other)) >= similarity:
                asked += 1
                break
    return asked >= max_asks

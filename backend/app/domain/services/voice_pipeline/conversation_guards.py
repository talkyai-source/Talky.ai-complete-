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
    r"(?:that|it|this|those|them|your\s+\w+|"
    r"(?:the|your)\s+(?:(?:project|contact|phone|mobile|email|callback|call-back|full|"
    r"these|those|all|your|the)\s+){0,2}(?:number|details|email|contact|info(?:rmation)?))\s+"
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
    if getattr(call_state, "earlier_email_captures", ()) or getattr(
        call_state, "earlier_phone_captures", ()
    ):
        return None
    ask = pending_contact_ask(call_state)
    if ask:
        # Say the open question itself (5dfa4416: the email read-back was
        # still waiting for a yes when the agent said "Perfect. I'll pass the
        # project details to the estimating team").
        return ask
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


# ── 2b. Never re-ask a question the caller already answered ────────────────
#
# Test call 5dfa4416: "Do you have any upcoming projects you might need an
# estimate for?" -- "Yes, I have plenty of it." -- and three turns later "Do
# you have any upcoming projects that need estimating?". The rule above only
# drops a question asked twice before; one clear answer is enough.

# Read-backs and contact asks legitimately come back after an unclear reply,
# and the goodbye guard relies on them; they are never "already answered".
_NOT_A_SCRIPT_QUESTION = re.compile(
    r"\b(?:number|email|e-mail|phone|mobile|address|spell|did\s+i\s+get|"
    r"is\s+that\s+(?:right|correct))\b|\bat\b.*\bdot\b|\d",
    re.IGNORECASE,
)


# A reply that is only a greeting or a "pardon?" did not answer anything.
_NOT_AN_ANSWER = frozenset(
    "hello hi hey hiya sorry what pardon huh hmm um uh er eh okay ok".split()
)


def answered_questions(history: Iterable[Any], *, limit: int = 8) -> list[tuple[str, str]]:
    """(question, answer) pairs: an agent question followed directly by a
    caller reply that is not itself a question or only a greeting. One word
    is an answer ("Yes.", and on call 2a75a4bb "zero" -- the caller's answer to
    the tendering question as heard, which was then asked again). Most recent
    last, at most ``limit``."""
    items = list(history or [])
    pairs: list[tuple[str, str]] = []
    for i, m in enumerate(items[:-1]):
        if str(getattr(getattr(m, "role", None), "value", getattr(m, "role", ""))) != "assistant":
            continue
        questions = _questions_in(str(getattr(m, "content", "") or ""))
        if not questions:
            continue
        q = questions[-1]
        if _NOT_A_SCRIPT_QUESTION.search(q) or len(_question_words(_plain(q))) < 3:
            continue
        nxt = items[i + 1]
        if str(getattr(getattr(nxt, "role", None), "value", getattr(nxt, "role", ""))) != "user":
            continue
        answer = str(getattr(nxt, "content", "") or "").strip()
        words = re.findall(r"[a-z0-9']+", answer.lower())
        if not words or answer.endswith("?") or all(w in _NOT_AN_ANSWER for w in words):
            continue
        pairs.append((q, answer))
    return pairs[-limit:]


def repeats_answered_question(
    sentence: str, answered: Iterable[tuple[str, str]], *, similarity: float = 0.6
) -> Optional[str]:
    """The caller's earlier answer when ``sentence`` asks an answered question
    again, else None."""
    if not sentence or "?" not in sentence or _NOT_A_SCRIPT_QUESTION.search(sentence):
        return None
    words = _question_words(_plain(sentence))
    if len(words) < 3:
        return None
    for q, answer in answered:
        other = _question_words(_plain(q))
        shared = words & other
        # Three shared content words as well as the ratio: "when do you need
        # the estimate by?" shares only "need"/"estimate" with the projects
        # question and is a new question.
        if (
            len(other) >= 3
            and len(shared) >= 3
            and len(shared) / min(len(words), len(other)) >= similarity
        ):
            return answer
    return None


def answered_note(answered: Iterable[tuple[str, str]]) -> Optional[str]:
    """One short line for the turn note: what is already answered."""
    items = [
        f'"{q[:80]}" (they said: "{a[:40]}")' for q, a in answered
    ]
    if not items:
        return None
    return "Already answered -- do not ask these again: " + "; ".join(items) + "."


# ── 3. A phone read-back must say the digits the caller said ───────────────
#
# Test call 1436672a (2026-09-29): the caller said "zero three one two, zero
# seven five, zero four nine six" and the agent read back "0 3 1 2, 0 7 5,
# 0 4 9 -- is that correct?" -- the last digit gone. The caller said yes to a
# wrong number. Any read-back whose digits are not the caller's (allowing only
# a country code added in front, or a leading 0 dropped for it) is replaced by
# a request to hear it again.

PHONE_REASK = (
    "Sorry, I want to get that exactly right. Could you say the number once "
    "more, slowly?"
)


def _digit_runs(text: str) -> list[str]:
    from app.services.scripts.spoken_email_normalizer import spoken_digits_to_numerals

    numerals = spoken_digits_to_numerals(text)
    runs = re.findall(r"\+?\d[\d\s().,\-]{5,}\d", numerals)
    return [re.sub(r"\D", "", r) for r in runs if len(re.sub(r"\D", "", r)) >= 7]


def _same_number(said: str, heard: str) -> bool:
    """True when two digit strings are the same number, up to a country code."""
    if said == heard:
        return True
    for a, b in ((said, heard), (heard, said)):
        core = b.lstrip("0")
        if core and a.endswith(core) and len(a) - len(core) <= 3:
            return True
    return False


def phone_readback_changed(
    sentence: str,
    caller_texts: Iterable[str],
    expected: Optional[str] = None,
) -> bool:
    """True when ``sentence`` reads back a number nobody said.

    The truth is the number the capture machine parsed (``expected``) or any
    number in the caller's recent turns. With neither there is nothing to
    compare against, and the sentence is left alone.
    """
    from app.domain.services.voice_pipeline.readback_guard import (
        is_unconfirmed_phone_readback,
    )

    if not sentence or not is_unconfirmed_phone_readback(sentence):
        return False
    spoken = _digit_runs(sentence)
    if not spoken:
        return False
    agent_digits = max(spoken, key=len)
    truths = [re.sub(r"\D", "", expected)] if expected else []
    for text in caller_texts:
        truths.extend(_digit_runs(text))
    truths = [t for t in truths if t]
    if not truths:
        return False
    return not any(_same_number(agent_digits, t) for t in truths)


# ── 4. No promise of a call back at a set time ─────────────────────────────
#
# Nothing can book a call back from a call (no executor exists; the prompt's
# CALLBACK POLICY says so). Test call 1436672a: "We'll ring you at 2 pm on
# that number." That is a commitment the business never made. The time the
# caller wants is still useful -- it is passed on as a preference.

CALLBACK_PREFERENCE = (
    "I'll pass that time on to the team as your preferred time for a call back."
)
_CALLBACK_PROMISE = re.compile(
    r"\b(?:we|i|someone|he|she|they|the\s+team|azian|[a-z]+)\s*(?:'ll|\s+will)\s+"
    r"(?:give\s+you\s+a\s+)?(?:ring|call|phone)\s+(?:you\s+)?(?:back\s+)?"
    r"[^.?!]*?\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?|tomorrow|today|tonight|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|morning|afternoon|"
    r"evening|o'?clock)\b",
    re.IGNORECASE,
)


def promises_timed_callback(sentence: str) -> bool:
    """True when the agent commits to calling back at a specific time or day."""
    return bool(sentence and _CALLBACK_PROMISE.search(_plain(sentence)))


# ── 5. No goodbye while a contact detail is still open ────────────────────
#
# Test call 5dfa4416 (2026-09-29): the email read-back had no yes and the
# caller had asked twice for their mobile number to be taken, and the agent
# said "Brilliant. Thanks for your time. Have a good day." The hang-up was
# held back (turn_ender sees the open capture) but the goodbye had already
# been spoken, so the caller left. The goodbye is replaced by the open
# question; a caller who is ending the call themselves is let go.

_CLOSING = re.compile(
    r"\b(?:thanks?(?:\s+you)?\s+(?:so\s+much\s+)?for\s+your\s+time"
    r"|have\s+a\s+(?:good|great|lovely|nice|brilliant)\s+"
    r"(?:day|one|afternoon|evening|weekend|rest\s+of\s+your\s+day)"
    r"|good\s*bye|bye(?:\s+(?:for\s+now|now|then))?|take\s+care|speak\s+(?:to\s+you\s+)?soon"
    r"|all\s+the\s+best)\b",
    re.IGNORECASE,
)

_OPEN_STATES = {"needs_clarification", "invalid", "awaiting_confirmation"}


def _status_value(capture: Any) -> str:
    status = getattr(capture, "status", None)
    return str(getattr(status, "value", status) or "")


def pending_contact_ask(call_state: Any) -> Optional[str]:
    """The question that settles the open contact detail, or None.

    The one being worked on comes first; a read-back is the exact sentence the
    confirm gate recognises, so the caller's yes to it counts.
    """
    if call_state is None:
        return None
    from app.services.scripts.spoken_email_normalizer import (
        natural_email_readback,
        natural_phone_readback,
    )

    active = getattr(call_state, "active_contact_kind", None)
    kinds = ["email", "phone"]
    if active == "phone":
        kinds.reverse()
    for kind in kinds:
        capture = _capture(call_state, f"{kind}_capture")
        status = _status_value(capture)
        if status not in _OPEN_STATES:
            continue
        value = getattr(capture, "normalized_value", None)
        if status == "awaiting_confirmation" and value:
            spoken = (
                natural_email_readback(value) if kind == "email"
                else natural_phone_readback(value)
            ) or value
            return f"So that's {spoken} — did I get that right?"
        raw = str(getattr(capture, "raw_value", "") or "").lower()
        if kind == "phone":
            if re.search(r"\d|\b(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)\b", raw):
                return "Could you say that number once more, slowly?"
            return "What's the best number to reach you on?"
        if re.search(r"@|\bat\b", raw):
            # They have given an address; asking for "the best email" again
            # is what made the caller on 68478c22 say "I have just shared the
            # email with you."
            return (
                "Just so I get your email exactly right -- could you spell the "
                "part before the at, one letter at a time?"
            )
        return "What's the best email address for you?"
    return None


_CLOSING_FILLER = frozenset(
    "thanks thank you so much very really again and brilliant great perfect "
    "lovely okay ok alright right cheers well then now too for your time it "
    "was nice good to speak talk with".split()
)


def _is_goodbye(sentence: str) -> bool:
    """The sentence is a goodbye and nothing else ("Thanks for your time.",
    "Have a good day."), not an ordinary sentence that contains one ("Thanks
    for your time explaining that", "take care of that paperwork")."""
    plain = _plain(sentence).lower()
    if not _CLOSING.search(plain):
        return False
    rest = _CLOSING.sub(" ", plain)
    words = [w for w in re.findall(r"[a-z']+", rest) if w not in _CLOSING_FILLER]
    return not words


def closing_while_contact_open(
    sentence: str, call_state: Any, last_caller_text: Optional[str]
) -> Optional[str]:
    """The open contact question to say instead of a goodbye, or None."""
    if not sentence or not _is_goodbye(sentence):
        return None
    from app.domain.services.end_session_action import caller_signaled_end

    if caller_signaled_end(last_caller_text):
        return None
    return pending_contact_ask(call_state)

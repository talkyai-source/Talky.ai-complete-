"""Deterministic backstop for an unconfirmed phone read-back.

Production, call 6aaeb4dd (2026-09-23). The capture state machine
(contact_capture.py) correctly classified the caller's 11-digit, region-less
number as NEEDS_CLARIFICATION and set a "please repeat ... + country code"
clarification_prompt -- the only enforcement was that prompt text injected
into the system prompt each turn (see prompt_builder.compose_system_prompt).
Cerebras gpt-oss-120b ignored it anyway:

    AGENT: "So that's 923 016 253 19, correct?"
    CALLER: "Yeah. Yeah."

Nothing was stored (state.phone stayed None, so no phone_confirm/
lead_slot_capture ever ran) but the unconfirmed, invalid number still leaked
into the AI call summary shown to staff (call.json summary_json.key_points).
See issues_all.txt#phone-capture-clarification-ignored.

This module does not replace the capture state machine; it only refuses to
let a NEEDS_CLARIFICATION/INVALID phone value reach TTS dressed up as a
confirmed read-back. A validated AWAITING_CONFIRMATION/CONFIRMED read-back
(the normal, working path -- call b97ce4c5 the same day) must pass through
untouched, so this only ever looks at the two "not yet valid" statuses.

A code review of the first cut of this guard caught a second defect: it
spoke `capture.clarification_prompt` verbatim as the re-ask. That field is
written to be injected into the MODEL's system prompt, not to be read aloud
to the caller -- see capture_mode_directive() and prompt_builder.py's
"BACKEND CONTACT MODE: ..." wrapping in the modules that own that state.
In the MAX_CONFIRMATION_ATTEMPTS branch it is literally a third-person
instruction to the agent ("Please ask for the complete plus-prefixed phone
number digit by digit, or ask them to say 'the first three digits are' or
'the last three digits are'."). Speaking that to a live caller just swaps
one fabrication (a confirmed-sounding read-back) for another (the backend's
own internal instruction). So this guard never speaks clarification_prompt;
it always substitutes its own fixed, caller-facing re-ask.
"""
from __future__ import annotations

import re

from app.domain.services.voice_pipeline.contact_capture import CaptureStatus

# The caller-facing re-ask this guard always substitutes. Deliberately NOT
# capture.clarification_prompt (see module docstring) -- that field is
# written for the model, and some of its branches are literal third-person
# instructions to the agent, not lines a human should ever hear spoken back.
_CALLER_REASK = (
    "Sorry, could you say the complete phone number again, one digit at a "
    "time, including the country code?"
)

# One spoken digit word. "Oh" is included -- callers routinely say "oh" for
# zero when reading a number aloud.
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"

# A phone-shaped run: 7+ digits (any of the usual spoken/written group
# separators -- space, dot, dash) or 7+ spelled-out digit words in a row.
# 7 is the shortest a real phone read-back segment gets; shorter runs are
# routinely dates, prices or reference numbers, not a number being read back.
#
# BUG (round-2 review, 2026-09-24): `{7,}` here counts REPEATS of "digit +
# optional separator", then requires one more trailing \d -- so it actually
# needed 8+ digits, not the 7+ this comment (and the original commit message)
# document. A genuine 7-digit read-back ("So that's 555 2671, correct?")
# fell through ungated. {6,} + the trailing \d is the correct floor for 7.
# The digit-word alternative already matches its own documented 7+ (6
# repeats + 1 trailing word), so only this branch needed the fix.
_DIGIT_RUN_RE = re.compile(
    r"(?:\d[\s.\-]*){6,}\d" rf"|(?:{_DIGIT_WORD}[\s,]+){{6,}}{_DIGIT_WORD}",
    re.IGNORECASE,
)

# A confirmation ask attached to that read-back. Deliberately narrow -- this
# only needs to catch the model asking the CALLER to confirm what it just
# said, not every question that happens to contain "right".
_CONFIRM_ASK_RE = re.compile(
    r"\b(?:correct|right|is that right|does that sound right)\b\s*\?",
    re.IGNORECASE,
)


def is_unconfirmed_phone_readback(sentence: str) -> bool:
    """True when `sentence` reads back a run of digits and asks to confirm it."""
    text = str(sentence or "")
    return bool(_DIGIT_RUN_RE.search(text) and _CONFIRM_ASK_RE.search(text))


def phone_readback_guard(session, sentence: str) -> tuple[str, bool]:
    """Guard one about-to-be-spoken sentence against a fabricated confirmation.

    Returns ``(sentence, False)`` unchanged unless this call's phone capture is
    NEEDS_CLARIFICATION or INVALID *and* `sentence` looks like a read-back
    confirmation ask -- in which case it returns this guard's own fixed,
    caller-facing re-ask (never capture.clarification_prompt -- see module
    docstring) and ``True``, so the caller hears a real re-ask instead of a
    confirmed-sounding fabrication.
    """
    capture = getattr(getattr(session, "captured_slots", None), "phone_capture", None)
    if capture is None or capture.status not in (
        CaptureStatus.NEEDS_CLARIFICATION,
        CaptureStatus.INVALID,
    ):
        return sentence, False
    if not is_unconfirmed_phone_readback(sentence):
        return sentence, False
    return _CALLER_REASK, True

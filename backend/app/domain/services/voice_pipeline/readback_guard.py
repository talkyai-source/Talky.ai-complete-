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
"""
from __future__ import annotations

import re
from typing import Optional

from app.domain.services.voice_pipeline.contact_capture import CaptureStatus

# One spoken digit word. "Oh" is included -- callers routinely say "oh" for
# zero when reading a number aloud.
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"

# A phone-shaped run: 7+ digits (any of the usual spoken/written group
# separators -- space, dot, dash) or 7+ spelled-out digit words in a row.
# 7 is the shortest a real phone read-back segment gets; shorter runs are
# routinely dates, prices or reference numbers, not a number being read back.
_DIGIT_RUN_RE = re.compile(
    r"(?:\d[\s.\-]*){7,}\d" rf"|(?:{_DIGIT_WORD}[\s,]+){{6,}}{_DIGIT_WORD}",
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
    confirmation ask -- in which case it returns the capture's own
    clarification prompt and ``True``, so the caller hears a real re-ask
    instead of a confirmed-sounding fabrication.
    """
    capture = getattr(getattr(session, "captured_slots", None), "phone_capture", None)
    if capture is None or capture.status not in (
        CaptureStatus.NEEDS_CLARIFICATION,
        CaptureStatus.INVALID,
    ):
        return sentence, False
    if not is_unconfirmed_phone_readback(sentence):
        return sentence, False
    prompt: Optional[str] = capture.clarification_prompt or (
        "Sorry, could you say the complete phone number again, one digit at a "
        "time, including the country code?"
    )
    return prompt, True

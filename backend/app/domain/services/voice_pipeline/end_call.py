"""Agent-initiated call ending + conversation-craft prompt rules.

The 2026-07-08 transcript audit showed the LLM ROLE-PLAYING hangups — it
literally output "[hangs up]" as text (spoken by TTS!) and the call kept
running for another minute until the silence timer fired. The agent had no
real way to end a call, so wrong numbers, "not interested", and detected
voicemails all dragged on, burning minutes and sounding unprofessional.

Mechanism: a text sentinel, not tool-calling. The prompt (see
:func:`call_control_rules`) tells the model to end its FINAL sentence with
the exact token ``[[END_CALL]]`` when the conversation is over. The single
TTS choke point (``synthesize_and_send_audio``) strips the token from every
outgoing sentence via :func:`extract_end_call` and flags the session; the
turn finisher then performs a real hangup after the goodbye audio has
played. A sentinel survives every model in the curated menu (no
function-calling support needed) and streams cleanly (the token rides the
last sentence).

Pure helpers — no I/O — so the stripping logic is unit-testable.
"""
from __future__ import annotations

import re

END_CALL_TOKEN = "[[END_CALL]]"

# Lenient on decoration ("[[ END CALL ]]", "END_CALL]]", "[[end_call]]"):
# models reproduce tokens imperfectly, and a half-spoken token is worse than
# a generously matched one.
_TOKEN_RE = re.compile(r"\[+\s*END[\s_-]?CALL\s*\]*|\bEND_CALL\b", re.IGNORECASE)


def extract_end_call(text: str) -> tuple[str, bool]:
    """Strip any END_CALL sentinel from ``text``.

    Returns ``(clean_text, requested)`` — ``requested`` is True when a
    sentinel was present. Safe on empty/None input.
    """
    if not text:
        return (text or "", False)
    cleaned, hits = _TOKEN_RE.subn("", text)
    if not hits:
        return (text, False)
    return (cleaned.strip(), True)


# Appended by the prompt composer for every campaign (before the compliance
# floor, which keeps the recency slot). Two jobs: give the model its ONE real
# call-ending capability, and anchor the lead-gen conversation craft the
# audit found missing (monologues, question-before-intro, no concrete CTA).
CALL_CONTROL_RULES = f"""\
## ENDING THE CALL (your one real control)
- When the conversation is genuinely over — the caller said goodbye or asked
  to stop, it's a wrong number, or a voicemail/answering machine answered —
  say at most ONE short, warm closing line and end that reply with the exact
  token {END_CALL_TOKEN} . The system hangs up for you.
- The token is invisible to the caller. Rely on it alone — stage directions
  written as words ("hangs up", "ends call") do nothing.
- Voicemail or answering machine: reply with {END_CALL_TOKEN} by itself —
  we call the person back another time instead of leaving recordings.

## HOW YOU SELL (conversation craft)
- Introduce yourself and the company FIRST, in one short line, then ask ONE
  clear question. One question per turn, always.
- Keep every turn under about 30 words. Short beats clever — earn the next
  sentence by letting them talk.
- Discover before you pitch: learn how they handle estimating today (who
  does it, how long it takes) before mentioning what we offer.
- Every call drives to ONE concrete next step — their email for a sample
  estimate, or a callback at a time THEY pick — and you confirm it back
  explicitly before closing.
"""


def call_control_rules() -> str:
    """The composed-prompt block granting END_CALL + conversation craft."""
    return CALL_CONTROL_RULES

"""Agent-initiated call ending + conversation-craft prompt rules.

The 2026-07-08 transcript audit showed the LLM ROLE-PLAYING hangups — it
literally output "[hangs up]" as text (spoken by TTS!) and the call kept
running for another minute until the silence timer fired. The agent had no
real way to end a call, so wrong numbers, "not interested", and detected
voicemails all dragged on, burning minutes and sounding unprofessional.

Mechanism: a text sentinel, not tool-calling. The prompt (see
:func:`call_control_rules`) tells the model to end its FINAL sentence with
the exact token ``[[END_CALL]]`` when the conversation is over.

2026-07-13 root-cause fix: the sentinel used to be read in
``synthesize_and_send_audio`` — AFTER ``turn_streamer`` had already run every
sentence through ``guardrails.clean_response`` (audio-tag stripping). That
stripper's bracket-tag regex treats ``[[END_CALL]]`` as a (double-bracketed)
audio tag and erases it on every non-``eleven_v3`` voice, so
``extract_end_call`` only ever saw an empty ``[]`` remnant and the flag was
never set — the agent said its goodbye and then just... didn't hang up.

The fix is ordering, not a regex patch: :func:`strip_and_flag` is now called
by ``turn_streamer`` on the RAW model text for a sentence/tail/aggregate
BEFORE that text is ever handed to ``clean_response`` (or anything else).
This module remains the one authoritative place that knows the sentinel's
shape; ``strip_and_flag`` is the one place a caller should reach for both the
extraction and the session flag-set, so the two never drift apart.

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


def strip_and_flag(session, text: str) -> str:
    """Extract the END_CALL sentinel from RAW model text and, if present,
    flag ``session`` so the turn finisher hangs up once this reply's audio
    has played. Returns the sentinel-free text.

    Callers MUST invoke this on text as soon as it leaves the model —
    before any TTS-directed cleaning (audio-tag stripping etc.) touches it.
    ``clean_response`` treats a bracketed sentinel as an audio tag and would
    silently erase it, which is exactly the bug this function's ordering
    fixes (see module docstring).

    The flag-set is synchronous (no ``await``), so calling this on a turn's
    synchronous streaming path guarantees it happens-before that same turn's
    finisher reads ``session._end_call_requested`` — no race window where the
    turn could be judged "over" before the flag lands. Idempotent: safe to
    call more than once across a turn's several text slices (per sentence,
    trailing tail, full aggregate) — a slice with no sentinel is returned
    unchanged and never clears a flag a prior slice already set.
    """
    clean, requested = extract_end_call(text)
    if requested:
        try:
            session._end_call_requested = True
        except Exception:
            pass
    return clean


# Appended by the prompt composer for every campaign (before the compliance
# floor, which keeps the recency slot). Two jobs: give the model its ONE real
# call-ending capability, and anchor the lead-gen conversation craft the
# audit found missing (monologues, question-before-intro, no concrete CTA).
CALL_CONTROL_RULES = f"""\
## ENDING THE CALL (your one real control)
- Call genuinely over — a clear goodbye, a WRONG BUSINESS (they've never heard
  of the company, it's a private residence, or plainly not a business line), or
  a voicemail/answering machine — say at most ONE short warm closing line, then
  end that reply with the exact token {END_CALL_TOKEN} . The system hangs up.
- WRONG PERSON is NOT this: if the business is right but your contact isn't
  here / isn't available / "no one by that name", do NOT end — that's a pivot,
  see WRONG PERSON / GATEKEEPER below. Only a wrong DESTINATION ends the call.
- The token is invisible to the caller; rely on it alone — words like
  "hangs up" do nothing.
- Voicemail/answering machine: reply with {END_CALL_TOKEN} alone — we call
  back another time instead of leaving a recording.

## HOW YOU SELL
- Introduce yourself and the company first, in one short line, then ask ONE
  question. Under ~30 words a turn — earn the next line by letting them talk.
- Discover before you pitch: learn how they handle it today before mentioning
  what we offer.
- Drive to ONE concrete next step — their email for a sample, or a callback
  at a time THEY pick — and confirm it back before closing.
"""


def call_control_rules() -> str:
    """The composed-prompt block granting END_CALL + conversation craft."""
    return CALL_CONTROL_RULES

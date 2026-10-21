"""Keep a repeated bare "Hello?" from cutting off the agent's first line.

Live 2026-10-20 ("Estimation new", 76 caller-first calls): the caller's
"Hello?" waited ~1.8 s for the agent's first words, so callers said "Hello?"
again. That second greeting counted as a barge-in and cut the first line off
within a second, 26 times in one day; callers then heard fragments and hung up.

On a caller-first call, during the first FIRST_LINE_GUARD_S seconds of the
agent's first line, a bare greeting neither interrupts nor gets its own reply:
the line already playing is the answer to it (owner decision 2026-10-21).
Real speech ("who is this?", "stop") still interrupts, and so does a
StartOfTurn whose text is unknown. No scripted opener is involved.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from app.domain.services.voice_pipeline.turn_helpers import _first_speaker_label

DEFAULT_GUARD_S = 2.5
# Greetings only. Unlike instant_opener's pickup set, no "yes"/"yeah"/"speaking":
# during "Hi, is this Mike?" those are the caller's ANSWER and must get through.
_GREETINGS = frozenset({"hello", "hullo", "hi", "hiya", "hey", "allo"})
_FILLER = frozenset({"there"})


def is_repeat_greeting(text: str) -> bool:
    words = [w.strip(".,!?'’\"-") for w in (text or "").lower().split()]
    words = [w for w in words if w]
    if not words or len(words) > 4:
        return False
    return any(w in _GREETINGS for w in words) and all(w in _GREETINGS or w in _FILLER for w in words)


def guard_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("VOICE_FIRST_LINE_GUARD_S", DEFAULT_GUARD_S)))
    except ValueError:
        return DEFAULT_GUARD_S


# tts_active drops between the sentences of one reply; only this much quiet
# ends the first line.
END_QUIET_S = 0.6


def note_playback(session, tts_active: bool, *, now: Optional[float] = None) -> None:
    """Called per caller-audio frame: stamps when the first line starts and ends.

    The recording notice (caller_first_disclosure) is not the first line: the
    agent's own opener after it is."""
    if getattr(session, "_first_line_done", False) or getattr(session, "_disclosure_in_flight", False):
        return
    now = time.monotonic() if now is None else now
    started = getattr(session, "_first_line_started_at", None)
    if tts_active:
        if started is None:
            session._first_line_started_at = now
        session._first_line_quiet_since = None
    elif started is not None:
        quiet_since = getattr(session, "_first_line_quiet_since", None)
        if quiet_since is None:
            session._first_line_quiet_since = now
        elif now - quiet_since >= END_QUIET_S:
            session._first_line_done = True
            session._first_line_barge_skipped = False


def first_line_in_progress(session, *, now: Optional[float] = None) -> bool:
    if getattr(session, "_disclosure_in_flight", False):
        # A cut-off notice discards the call's recording; a bare "Hello?" over
        # it is the caller still checking the line, not an interruption. Either
        # first speaker: an agent-first notice starts at answer, on the pickup
        # "Hello?" (2026-10-21).
        return True
    if _first_speaker_label(session) != "user" or getattr(session, "_first_line_done", False):
        return False
    now = time.monotonic() if now is None else now
    started = getattr(session, "_first_line_started_at", None)
    if started is None:
        if not getattr(session, "tts_active", False):
            return False
        # Speech started before the per-frame stamp caught up: it starts now.
        session._first_line_started_at = started = now
    return now - started <= guard_seconds()


def is_first_line_greeting_repeat(session, text: Optional[str], *, now: Optional[float] = None) -> bool:
    return is_repeat_greeting(text or "") and first_line_in_progress(session, now=now)

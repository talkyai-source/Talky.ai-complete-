"""Pure turn-taking decision helpers shared by the silence monitor.

Split out of ``audio_ingest.py`` on 2026-07-08 after two related production
bugs were found in the same call sample:

  1. An AGENT-FIRST call's very first line to the prospect was a mid-nudge
     ("I'm still here whenever you're ready.") — fired before the caller had
     ever spoken a word, because the old random phrase-pool nudge didn't
     distinguish "caller has never spoken" from "caller went quiet mid-call".
  2. "Sorry, did I lose you?" fired repeatedly (observed up to 6x on one
     call) — anxious/accusatory phrasing that SOTA voice-agent platforms
     (Vapi, Retell) explicitly avoid, and the old pool picked it at random on
     every tier instead of escalating.

SOTA guidance this module encodes (Vapi / Retell / LiveKit / AssemblyAI):
  - Nudge phrasing should be neutral ("Still there?"), never needy or
    accusatory.
  - Nudges should ESCALATE by tier (a short prompt first, a slightly
    warmer/longer one only if that goes unanswered too) rather than pick a
    random line each time.
  - Hangup and nudge are different timers at different scales; this module
    only owns the PHRASE/SUPPRESSION logic — the timers themselves stay in
    ``audio_ingest.py``.

Everything here is pure (no I/O, no clocks, no session access) so it is
directly unit-testable.

2026-08-11: ``choose_silence_phrase`` gained an optional ``ladder`` parameter
so the OPENING tier can use a per-call, LLM-authored ladder (varied wording,
still escalating) instead of always reciting the same 3 fixed lines — see
``telephony.opening_ladder`` for how that ladder is generated (during
pre-warm, never on this nudge path) and validated (same greeting-duplication
invariant this module already enforces on the static ladder). The parameter
defaults to ``None`` and every existing call site — including this module's
own tests — is unaffected: no ``ladder`` argument means byte-for-byte the old
behaviour.
"""
from __future__ import annotations

from typing import Optional

# Opening ladder — caller-first call, caller has not spoken yet. Kept short
# and low-key; this is a "hello, are you on the line" check — the way a human
# who just dialled re-checks a silent pickup — not a check-in and not a
# re-pitch.
#
# NO rung after the first may open a FRESH greeting — but escalating the SAME
# call-out is not only allowed, it is the point. These nudges fire BEFORE the
# agent's real opener ("Hi, it's James from Allstate — I'm calling
# because..."), which the persona delivers once the caller finally speaks. An
# earlier version had rung 2 as "Hi, can you hear me okay?", so a silent
# pickup heard "Hello?" → "Hi, can you hear me okay?" → "Hi, it's James
# from..." — three DIFFERENT greetings in a row (the "multiple hi and hello"
# report). The bug was the second greeting WORD, which reads as starting the
# conversation over; it was never the repetition itself.
#
# 2026-08-11 — the ladder now says what a person actually says. "Can you hear
# me okay?" was flagged by the owner as stilted and robotic, and it is: nobody
# on a silent line asks a formal audio-quality question. They call the same
# word again, louder and with more doubt:
#
#     "Hello?"  →  "Hello??"  →  "Helloooo — are you there?"
#
# The greeting-duplication invariant was refined to match (see
# test_greeting_duplication._fresh_greeting_rung_indexes): rung 0's word may
# repeat in any escalated spelling, a DIFFERENT greeting word may not appear.
# The old two-greeting ladder is still caught by that rule.
#
# Three rungs matches _OPENING_MAX_NUDGES=3 in audio_ingest.py (a human
# re-checks a silent pickup 2-3 times, not once). Rung 3 is also the LAST:
# choose_silence_phrase clamps at ``len(ladder) - 1``, so once the nudges are
# spent the monitor stops nudging rather than looping the ladder.
OPENING_PHRASES = ["Hello?", "Hello??", "Helloooo — are you there?"]

# Mid-conversation ladder — caller has spoken before and has now gone quiet.
# Neutral first ("Still there?" — Vapi's prescribed phrasing), then one warm,
# generic re-offer. Deliberately excludes "Sorry, did I lose you?" (reads as
# anxious/accusatory) and never repeats "I'm still here..." as a cold open.
MID_PHRASES = [
    "Still there?",
    "No rush — I'm still on the line whenever you're ready.",
]


def choose_silence_phrase(
    *, is_opening: bool, nudge_index: int, ladder: Optional[list] = None,
) -> str:
    """Return the escalation-ladder phrase for this nudge tier.

    ``nudge_index`` is 0-based (0 = first nudge, 1 = second, ...). Clamped to
    the last rung of the ladder so an out-of-range index (e.g. a future
    higher ``_MAX_NUDGES``) degrades to the warmest phrase instead of
    raising.

    ``ladder`` is an optional per-call OPENING ladder — see
    ``telephony.opening_ladder.generate_opening_ladder`` — read straight off
    the call session by audio_ingest.py's silence monitor
    (``getattr(session, "_opening_ladder", None)``). It is used verbatim when
    it is a non-empty, non-falsy sequence; anything else (None, [], a stray
    wrong type) falls through to the static ``OPENING_PHRASES`` below. This
    function never raises on a bad ``ladder`` argument — by the time a nudge
    fires the caller is already sitting in silence, so a generation-time
    problem (which ``opening_ladder.validate_ladder`` should already have
    caught before anything was ever stashed on the session) must never cost a
    nudge. ``ladder`` only ever applies to the OPENING tier: a "still there?"
    mid-call check-in was never in scope for this feature.
    """
    if is_opening and ladder:
        try:
            active_ladder = list(ladder)
            if not active_ladder:
                active_ladder = OPENING_PHRASES
        except Exception:
            active_ladder = OPENING_PHRASES
    else:
        active_ladder = OPENING_PHRASES if is_opening else MID_PHRASES
    if nudge_index < 0:
        nudge_index = 0
    idx = min(nudge_index, len(active_ladder) - 1)
    return active_ladder[idx]


def should_suppress_mid_nudge(*, is_caller_first: bool, caller_has_ever_spoken: bool) -> bool:
    """True when a MID nudge must be skipped entirely.

    On an AGENT-FIRST call (``is_caller_first`` is False) where the caller
    has never produced a real turn (and no fresh backchannel either — the
    caller passes ``caller_has_ever_spoken`` for that), the very first thing
    the prospect would hear from a mid-nudge is "I'm still here whenever
    you're ready" — before they've said a single word. That reads as needy
    and was the root cause of the 2026-07-08 production bug. In that
    situation only the 60s hangup should apply; no mid nudge is emitted.

    Caller-first calls are unaffected (they use the OPENING ladder, not
    this suppression) — this only ever prunes the MID path.
    """
    return (not is_caller_first) and (not caller_has_ever_spoken)

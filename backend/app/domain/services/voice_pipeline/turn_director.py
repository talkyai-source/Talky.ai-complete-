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
"""
from __future__ import annotations

# Opening ladder — caller-first call, caller has not spoken yet. Kept short
# and low-key; this is a "hello, are you on the line" check, not a check-in.
OPENING_PHRASES = ["Hello?", "Hi, can you hear me okay?"]

# Mid-conversation ladder — caller has spoken before and has now gone quiet.
# Neutral first ("Still there?" — Vapi's prescribed phrasing), then one warm,
# generic re-offer. Deliberately excludes "Sorry, did I lose you?" (reads as
# anxious/accusatory) and never repeats "I'm still here..." as a cold open.
MID_PHRASES = [
    "Still there?",
    "No rush — I'm still on the line whenever you're ready.",
]


def choose_silence_phrase(*, is_opening: bool, nudge_index: int) -> str:
    """Return the escalation-ladder phrase for this nudge tier.

    ``nudge_index`` is 0-based (0 = first nudge, 1 = second, ...). Clamped to
    the last rung of the ladder so an out-of-range index (e.g. a future
    higher ``_MAX_NUDGES``) degrades to the warmest phrase instead of
    raising.
    """
    ladder = OPENING_PHRASES if is_opening else MID_PHRASES
    if nudge_index < 0:
        nudge_index = 0
    idx = min(nudge_index, len(ladder) - 1)
    return ladder[idx]


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

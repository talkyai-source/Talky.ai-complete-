"""Backchannel detection for natural turn-taking.

Deepgram Flux deliberately leaves backchannel filtering to the application —
its docs state the app owns this, and guarantee that every ``StartOfTurn``
carries a non-empty transcript so we can inspect what was said. A *backchannel*
is a short acknowledgement a listener emits to show they're following ("yeah",
"mhm", "right", "got it") WITHOUT trying to take the floor.

Rule we enforce with this:
  * While the agent is speaking, a backchannel must NOT interrupt it and must
    NOT trigger a reply — the agent keeps going (that's what a human does).
  * When the agent is NOT speaking, the same word ("yeah") may be a real answer,
    so this is only applied as a guard *during* agent speech (callers check
    ``session.tts_active`` before suppressing).

Deliberately EXCLUDED from the set: "no", "nope", "stop", "wait" — mid-speech
those signal real disagreement / a genuine interrupt and MUST be allowed to
barge in. We only treat clear continuers/acknowledgements as backchannels.
"""
from __future__ import annotations

import re

# Clear continuers / acknowledgements only. Conservative on purpose.
_BACKCHANNELS = frozenset({
    "yeah", "yep", "yup", "yes", "ok", "okay", "kay", "mm", "hm", "mhm", "mmhm",
    "mmhmm", "mhmm", "uh huh", "uhuh", "uh-huh", "right", "sure", "got it",
    "gotcha", "i see", "oh", "ah", "ahh", "hmm", "cool", "nice", "alright",
    "makes sense", "exactly", "totally", "of course", "oh ok", "oh okay",
    "oh right", "oh yeah", "i understand", "understood", "for sure", "true",
})

# Strip everything except letters/spaces so "yeah," / "ok." / "mm-hm" normalise.
_NON_WORD = re.compile(r"[^a-z\s]")
_MAX_WORDS = 3


# Short utterances that MUST interrupt the agent immediately, even though
# they are only one or two words — disagreement, stop signals, and
# attention-getters. These bypass the min-words barge-in guard so the caller
# can always cut the agent off when they need to. Kept disjoint from the
# backchannel set above (which deliberately excludes no/nope/stop/wait).
_HARD_INTERRUPTS = frozenset({
    "no", "nope", "nah", "stop", "wait", "hold on", "hang on", "hold",
    "pause", "enough", "quiet", "shut up", "hey", "hello", "excuse me",
    "sorry", "actually no", "wait wait", "stop stop", "no no",
})


def is_hard_interrupt(text: str) -> bool:
    """True if ``text`` is a short utterance that must barge in regardless of
    the min-words guard (e.g. "stop", "wait", "no"). Empty → False."""
    if not text:
        return False
    cleaned = _NON_WORD.sub(" ", text.strip().lower())
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return False
    if cleaned in _HARD_INTERRUPTS:
        return True
    # A single critical token anywhere in a 1-2 word utterance ("no thanks",
    # "stop please") should still interrupt.
    words = cleaned.split()
    if len(words) <= 2 and any(w in {"no", "nope", "nah", "stop", "wait"} for w in words):
        return True
    return False


def is_backchannel(text: str) -> bool:
    """True if ``text`` is a short pure acknowledgement (a listener cue), not a
    real turn. Empty/garbage → False (let the normal path handle it)."""
    if not text:
        return False
    cleaned = _NON_WORD.sub(" ", text.strip().lower())
    cleaned = " ".join(cleaned.split())  # collapse whitespace
    if not cleaned:
        return False
    if cleaned in _BACKCHANNELS:
        return True
    words = cleaned.split()
    if len(words) > _MAX_WORDS:
        return False
    # Every token is itself a backchannel ("yeah yeah", "oh ok sure").
    return all(w in _BACKCHANNELS for w in words)

"""Backchannel filter for voice barge-in.

Problem: Deepgram Flux fires an EndOfTurn event on short caller sounds
like "hmm" or "yeah" mid-agent-speech. Without this filter, those turns
reach the LLM and trigger a full fresh response to a non-utterance —
the conversation loses its place.

Fix: in `handle_turn_end`, after the repetitive-transcript check, drop
turns whose entire transcript is a backchannel. The LLM never sees it,
so the agent keeps speaking.

Belt AND braces: the persona prompts also instruct the model to treat
short listening sounds as non-events — so even if an edge case slips
through here, the language-level rule catches it.
"""
from __future__ import annotations

import re

_BACKCHANNEL_EXACT: frozenset[str] = frozenset({
    # Listening affirmations
    "hmm", "hm", "mm", "mmm", "mhm", "m-hm", "uh huh", "uh-huh",
    "uhhuh", "um", "umm",
    # Agreement / acknowledgement
    "yeah", "yep", "yup", "yes", "okay", "ok", "right", "alright",
    "sure", "got it", "gotcha", "aha", "ah", "oh", "ooh", "i see",
    # Short negations
    "no", "nope", "nah",
    # Thinking sounds
    "uh", "er", "um hmm",
})

_BACKCHANNEL_RE = re.compile(
    r"^\s*(?:"
    r"(?:uh+\s*)?hm+|"
    r"m+h?m+|"
    r"uh[- ]?huh|"
    r"ya+h?|ye[sp]|yup|yep|"
    r"ok(?:ay)?|"
    r"right|alright|"
    r"sure|got\s*it|gotcha|"
    r"uh|er|ah+|oh+|"
    r"i\s*see|"
    r"no+p?e?|nah|"
    r"mm+|"
    r"(?:uh\s*)?hm+\s*(?:uh)?|"
    r"continue|go\s+on|go\s+ahead"
    r")\s*[.!,?]?\s*$",
    re.IGNORECASE,
)

# Backchannels are almost never more than 4 words. Beyond that, assume
# real content.
_BACKCHANNEL_MAX_WORDS = 4


def is_backchannel(transcript: str) -> bool:
    """True if `transcript` is a pure listening sound — "hmm", "yeah",
    "uh huh", "mm hmm" — with no substantive content.

    Returns False for empty strings (handled as a no-turn elsewhere) and
    for any transcript longer than 4 words.

    Examples:
        "hmm"                         → True
        "yeah okay"                   → True
        "uh huh"                      → True
        "yeah but what about price?"  → False (has real content)
        "no I already have solar"     → False (disqualifying info)
        ""                            → False
    """
    if not transcript:
        return False

    text = transcript.strip().lower()
    if not text:
        return False

    if len(text.split()) > _BACKCHANNEL_MAX_WORDS:
        return False

    stripped = text.rstrip(".,!?")
    if stripped in _BACKCHANNEL_EXACT:
        return True

    # All words backchannel sounds? ("yeah okay", "uh huh", etc.)
    words = stripped.split()
    if words and all(w in _BACKCHANNEL_EXACT for w in words):
        return True

    if _BACKCHANNEL_RE.match(text):
        return True

    return False

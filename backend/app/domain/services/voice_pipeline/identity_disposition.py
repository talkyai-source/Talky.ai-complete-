"""Caller evidence for opt-out persistence and explicit end-call authorization.

Ordinary identity interpretation and spoken responses belong to the model.
"""
import re

from app.domain.services.caller_assertions import (
    assertion_matches, continuation_after, last_asserted_position, phrase_pattern,
)

_DNC_PHRASES = (
    "stop calling",
    "dont call me", "dont call us", "dont call again", "dont call back",
    "dont call here", "dont call this", "dont call anymore", "dont ever call",
    "do not call me", "do not call us", "do not call again", "do not call back",
    "do not call here", "do not call this", "do not call anymore",
    "dont contact me", "dont contact us", "do not contact me", "do not contact us",
    "take me off", "remove me from", "take my number off",
    "lose my number", "delete my number",
    "opt me out", "unsubscribe",
    "never call me", "never call us", "never call again", "never contact",
    # UK wording; paraphrases beyond this floor are the model's verified quote.
    "stop ringing me", "stop ringing us", "stop ringing this number", "dont ring me", "dont ring us",
    "do not ring me", "do not ring us",
    "never ring me", "never ring us", "never ring again", "never ring this number again",
    "dont phone me", "do not phone me", "do not call list",
)

_EXPLICIT_GOODBYE_PHRASES = (
    "goodbye", "good bye",
    "bye now", "bye bye",
    "im hanging up", "i am hanging up",
    "i have to go", "ive got to go", "i gotta go", "gotta go",
    "hang up", "end the call", "end this call", "end call",
)

_DNC_PATTERN = phrase_pattern(_DNC_PHRASES)
_EXPLICIT_GOODBYE_PATTERN = phrase_pattern(_EXPLICIT_GOODBYE_PHRASES)


def contains_dnc(transcript: str) -> bool:
    """Directed opt-out, excluding quoted or negated uses."""
    return dnc_assertion_position(transcript) >= 0


# "Don't call me tomorrow" / "don't call me at work" is a scheduling
# preference, not an opt-out: a phrase followed at once by a time or place.
_SCHEDULING_AFTER = re.compile(
    r"^\W*(?:\w+\W+){0,2}?(?:tomorrow|today|tonight|later|right now|at the moment|at work|"
    r"this (?:week|morning|afternoon|evening|weekend)|until|before|after|in the (?:morning|evening)|"
    r"on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.I,
)


def dnc_assertion_position(transcript: str) -> int:
    for match in reversed(assertion_matches(transcript, _DNC_PATTERN)):
        if not _SCHEDULING_AFTER.match(str(transcript or "")[match.end():]):
            return match.start()
    return -1


def contains_explicit_goodbye(transcript: str) -> bool:
    """Explicit caller ending with no later request to continue."""
    position = last_asserted_position(transcript, _EXPLICIT_GOODBYE_PATTERN)
    return position >= 0 and not continuation_after(transcript, position)

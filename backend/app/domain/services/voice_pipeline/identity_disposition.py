"""Caller evidence for opt-out persistence and explicit end-call authorization.

Ordinary identity interpretation and spoken responses belong to the model.
"""
from app.domain.services.caller_assertions import (
    continuation_after, last_asserted_position, phrase_pattern,
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


def dnc_assertion_position(transcript: str) -> int:
    return last_asserted_position(transcript, _DNC_PATTERN)


def contains_explicit_goodbye(transcript: str) -> bool:
    """Explicit caller ending with no later request to continue."""
    position = last_asserted_position(transcript, _EXPLICIT_GOODBYE_PATTERN)
    return position >= 0 and not continuation_after(transcript, position)

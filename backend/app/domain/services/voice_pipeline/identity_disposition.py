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
    # Hostile dismissals count as a do-not-call request (owner decision 2026-10-21);
    # STT may mask profanity, so "f*** off" / "f off" are included.
    "fuck off", "f*** off", "f off", "fuck you", "piss off", "sod off",
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


# Only a "don't / do not call me ..." phrase can be a scheduling request. "Stop
# calling", "take me off", "remove me", "unsubscribe", "never call" and the
# do-not-call list stay opt-outs whatever follows ("stop calling me right now").
_SCHEDULABLE_PHRASE = re.compile(r"^(?:don'?t|do\s+not)\b(?!\s+call\s+list\b)", re.I)


def dnc_assertion_position(transcript: str) -> int:
    for match in reversed(assertion_matches(transcript, _DNC_PATTERN)):
        if (_SCHEDULABLE_PHRASE.match(match[0])
                and _SCHEDULING_AFTER.match(str(transcript or "")[match.end():])):
            continue
        return match.start()
    return -1


# STT writes the spaced goodbye "bye bye" as "Bye-bye." Same words: one character
# swapped for a space, so offsets in the transcript stay valid. Bare "bye" is
# still not a goodbye.
_HYPHENATED_BYE_BYE = re.compile(r"\b(bye)[-‐‑](?=bye\b)", re.I)


_BYE_BYE_ALLOWED_AFTER = frozenset(
    "then now for thanks thank you mate love dear darling cheers sir madam".split())
_BYE_BYE_SAID_BEFORE = re.compile(r"\b(?:said|say|saying|says)\b", re.I)
_PAUSE_BEFORE = re.compile(
    r"\b(?:hang\s+on|hold\s+on|wait|one\s+moment|just\s+a\s+(?:sec|second|minute))\b", re.I)
_RESUME_AFTER = re.compile(
    r"\b(?:go\s+on|carry\s+on|go\s+ahead|continue|keep\s+going|you\s+were\s+saying)\b", re.I)
_CLAUSE_END = re.compile(r"[.!?;\n]")


def _is_standalone_bye_bye(text: str, match: re.Match) -> bool:
    """A "bye bye" counts only as a farewell, not as words inside a sentence."""
    if " ".join(re.findall(r"[a-z]+", match[0].lower())) != "bye bye":
        return True
    ends = [m.end() for m in _CLAUSE_END.finditer(text, 0, match.start())]
    start = ends[-1] if ends else 0
    after = _CLAUSE_END.search(text, match.end())
    end = after.start() if after else len(text)
    before_clause, after_clause = text[start:match.start()], text[match.end():end]
    if _BYE_BYE_SAID_BEFORE.search(before_clause) or _PAUSE_BEFORE.search(text[:match.start()]):
        return False
    if _RESUME_AFTER.search(text[match.end():]):
        return False
    return all(w in _BYE_BYE_ALLOWED_AFTER for w in re.findall(r"[a-z']+", after_clause.lower()))


def contains_explicit_goodbye(transcript: str) -> bool:
    """Explicit caller ending with no later request to continue."""
    transcript = _HYPHENATED_BYE_BYE.sub(r"\1 ", str(transcript or ""))
    matches = [m for m in assertion_matches(transcript, _EXPLICIT_GOODBYE_PATTERN)
               if _is_standalone_bye_bye(transcript, m)]
    position = matches[-1].start() if matches else -1
    return position >= 0 and not continuation_after(transcript, position)

"""STT transcript heuristics extracted from VoicePipelineService (item 2).

Pure and stateless — guards against degenerate STT output before it
reaches the LLM.
"""
from __future__ import annotations

from collections import Counter


def is_repetitive_transcript(text: str) -> bool:
    """
    Detect Deepgram Flux hallucination: repetitive STT output (GitHub #1524).
    Returns True when a single word dominates >50% of a 6+ word transcript.
    Normal speech ("I'd like to know about your pricing") never hits this.
    """
    words = text.lower().split()
    if len(words) < 6:
        return False
    top_count = Counter(words).most_common(1)[0][1]
    return (top_count / len(words)) > 0.5


# Phrases that strongly indicate an answering machine / voicemail greeting.
# Matched case-insensitively as substrings against the FIRST caller-side
# transcript. Kept high-precision: these read as a recorded greeting, not
# something a live human says when they pick up an unexpected call.
# HIGH PRECISION by design: a false positive hangs up on a LIVE prospect, which
# is worse than staying on a voicemail a beat too long. So this list contains
# only wording a recorded greeting uses that a human answering an outbound call
# would not say about themselves in their first breath. Deliberately EXCLUDED as
# too ambiguous: bare "you've reached" / "you have reached" (a business answers
# "You've reached [Company]"), bare "please leave" ("please leave me alone"),
# and bare "is not available" (a receptionist: "he's not available").
_VOICEMAIL_PHRASES = (
    "leave a message",
    "leave your message",
    "leave your name and number",
    "after the tone",
    "after the beep",
    "at the tone",
    "at the beep",
    "record your message",
    "please record your message",
    "your call has been forwarded",
    "has been forwarded to",
    "the person you are trying to reach",
    "person you're trying to reach",
    "not available right now",
    "unable to take your call",
    "can't take your call right now",
    "cannot take your call right now",
    "you have reached the voicemail",
    "you've reached the voicemail",
    "reached the voicemail",
    "voicemail box",
    "automated voice messaging",
    "google voice",
    # UK carrier voicemail services (verbatim from 2026-07-08 production
    # transcripts: O2 "welcome to the o two messaging service", Vodafone
    # "this is the Vodafone voice mail service", EE "to the EE voice mail").
    # A live person answering an unexpected call never describes themselves
    # as a voicemail/messaging service in their first breath.
    "voicemail service",
    "messaging service",
    "the ee voicemail",
    "the e e voicemail",
    # Personal-greeting wording (2026-07-08 live miss: "Sorry I missed your
    # call... please leave me a message and your phone number"). A person who
    # just ANSWERED the phone cannot have "missed your call", and only a
    # recording asks you to "leave me/us a message".
    "sorry i missed your call",
    "leave me a message",
    "leave us a message",
    "the voicemail of",
)


def _normalise_voicemail_blob(text: str) -> str:
    """Whitespace-normalise AND unify the 'voice mail' spelling so every
    phrase is written once with 'voicemail' yet matches both STT spellings
    (Deepgram emits both, sometimes within one call)."""
    blob = " ".join(text.lower().split())
    return blob.replace("voice mail", "voicemail")


def is_voicemail_greeting(text: str) -> bool:
    """True when a transcript reads like an answering-machine / voicemail
    greeting rather than a live person answering.

    Used for real-time answering-machine detection: if the FIRST thing heard
    after the call connects matches, we hang up immediately (no point talking
    to a machine) and mark the call as voicemail. High precision by design —
    a false positive hangs up on a real person, so the phrase list only
    contains wording that is characteristic of a recorded greeting.
    """
    if not text:
        return False
    blob = _normalise_voicemail_blob(text)
    return any(phrase in blob for phrase in _VOICEMAIL_PHRASES)

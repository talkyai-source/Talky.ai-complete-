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
_VOICEMAIL_PHRASES = (
    "leave a message",
    "leave your message",
    "leave your name and number",
    "after the tone",
    "after the beep",
    "at the tone",
    "at the beep",
    "record your message",
    "your call has been forwarded",
    "has been forwarded to",
    "the person you are trying to reach",
    "person you're trying to reach",
    "is not available",
    "not available right now",
    "unable to take your call",
    "can't take your call",
    "cannot take your call",
    "you have reached the voicemail",
    "you've reached the voicemail",
    "you have reached",     # "you have reached 07… / the office of…"
    "you've reached",
    "please leave",
    "voicemail box",
    "automated voice messaging",
    "google voice",
    "please record",
)


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
    blob = " ".join(text.lower().split())  # normalise whitespace
    return any(phrase in blob for phrase in _VOICEMAIL_PHRASES)

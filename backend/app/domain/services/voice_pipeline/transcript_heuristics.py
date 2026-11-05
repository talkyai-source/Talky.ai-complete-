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
# and any "not available" / "can't come to the phone" / "unable to take your
# call" wording (2026-07-14 fix: "not available right now", "unable to take
# your call", "can't/cannot take your call right now" were removed — a live
# receptionist screening a transferred call routinely says exactly this in her
# FIRST breath, e.g. "John is not available right now" or "he's unable to take
# your call right now, can I take a message", which was hanging up on a real
# human. Genuine voicemail greetings that say this ALSO carry independent
# machine evidence below — the beep/tone/"leave a message" instruction, the
# carrier "forwarded to voicemail" wording, or "the person you are trying to
# reach" IVR phrasing — so recall on real voicemail is unaffected).
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
    # Corporate auto-attendant / PBX voicemail (2026-08-04 production miss).
    # Two outbound calls answered with EXACTLY this and nothing else — it was
    # the only final transcript on either call — yet AMD returned False, so the
    # agent played its recording notice and opener to a machine. The notice was
    # cut short by the machine's own speech, which suppressed the recording,
    # and both calls burned ~11s. A live person answering their own phone never
    # refers to themselves in the third person by extension number.
    "the person at extension",
    "person at extension",
    "the party at extension",
    "at the sound of the tone",
    "at the sound of the beep",
    # QA F10-06 (labelled set: 21 of 30 machines caught): mailbox states,
    # "text me or leave a voicemail", O2's "the person you are calling", and
    # recorded office-closed messages. A person who just picked up says none
    # of these. "Please hold while we try to connect you" is left out on
    # purpose: a live answer can follow it.
    "leave a voicemail",
    "mailbox is full",
    "voicemail is full",
    "cannot accept any more messages",
    "mailbox has not been set up",
    "voicemail has not been set up",
    "mailbox is not set up",
    "voicemail is not set up",
    "the person you are calling",
    "the person you have called",
    "person you're calling",
    "o two messaging",
    "o2 messaging",
    "office is currently closed",
    "offices are currently closed",
    "office is now closed",
    "offices are now closed",
)

# Network intercepts (QA F10-06): the network, not the callee, is speaking.
# They end the call like a voicemail, but are recorded as the carrier's own
# cause code would be: a number not in service like Q.850 1 (INVALID), a phone
# that is off or out of reach like Q.850 20 (NO_ANSWER).
_NOT_IN_SERVICE_PHRASES = (
    "not in service",
    "no longer in service",
    "has not been recognised",
    "has not been recognized",
    "is not a valid number",
)
_UNREACHABLE_PHRASES = (
    "subscriber you have called",
    "subscriber you are calling",
    "the subscriber is not available",
    "is switched off",
    "is not reachable",
    "cannot be reached at the moment",
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


def network_intercept(text: str) -> str | None:
    """"not_in_service" or "unreachable" when the network itself answered
    with an intercept message, else None. Same precision rule as
    ``is_voicemail_greeting``: no person answers a call this way."""
    if not text:
        return None
    blob = _normalise_voicemail_blob(text)
    if any(phrase in blob for phrase in _NOT_IN_SERVICE_PHRASES):
        return "not_in_service"
    if any(phrase in blob for phrase in _UNREACHABLE_PHRASES):
        return "unreachable"
    return None

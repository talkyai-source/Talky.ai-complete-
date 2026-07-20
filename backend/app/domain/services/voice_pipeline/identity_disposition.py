"""Deterministic wrong-number / identity disposition — pure, no I/O.

The 2026-07 audit (Case 1) found "wrong number" handling nondeterministic: two
contradictory prompt blocks (end_call vs gatekeeper) meant the LLM coin-flipped
between hanging up and pivoting on the SAME utterance. This module removes the
LLM's discretion for the unambiguous cases and encodes the CORRECT distinction
the prompts never did — wrong *destination* vs wrong *person*:

  * WRONG_BUSINESS — the caller reached the wrong company/a residence entirely.
    There is no one here to pivot to. → end the call politely.
  * DNC — "stop calling / take me off your list". → end the call (overrides all).
  * WRONG_PERSON — right business, the named contact isn't here/available.
    Whoever answered may be a route in. → NOT an end; the turn goes to the LLM,
    which now has a non-contradictory pivot rule.
  * AMBIGUOUS — bare "wrong number" with NO business/person scope. We can't tell
    which of the two it is, so we clarify ONCE ("wrong business entirely, or just
    the wrong person?"). A SECOND bare wrong-number after that clarify resolves to
    WRONG_BUSINESS (they didn't narrow it → treat as a wrong destination and exit).
  * NONE — nothing identity-related; ordinary turn.

Correctness guard (the review's HIGH finding): person-mismatch phrases like
"no one here by that name" must NEVER auto-hang-up — that is exactly a reachable
prospect. Destination evidence is required for WRONG_BUSINESS; everything else
that mentions the contact routes to the pivot.

All matching is high-precision substring/phrase matching on a normalized
transcript; when in doubt we DON'T end (fail toward keeping the call alive).
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class IdentityDisposition(str, Enum):
    NONE = "none"
    WRONG_PERSON = "wrong_person"       # pivot — hand to the LLM, do not end
    WRONG_BUSINESS = "wrong_business"   # deterministic polite end
    DNC = "dnc"                         # deterministic end (overrides all)
    AMBIGUOUS = "ambiguous"             # bare "wrong number" — clarify once


# Fixed lines spoken on the deterministic paths (no LLM involved).
WRONG_BUSINESS_CLOSE = "Sorry about that — looks like I've got the wrong number. Take care."
DNC_CLOSE = "Understood — I'll take you off the list. Sorry to have bothered you. Goodbye."
CLARIFY_SCOPE_LINE = (
    "Sorry — do you mean I've reached the wrong business entirely, "
    "or just the wrong person?"
)


def _norm(text: str) -> str:
    """Lowercase, collapse whitespace, strip most punctuation to spaces so
    phrase matching is robust to STT punctuation ("wrong number." / "wrong,
    number"). Apostrophes are REMOVED (not spaced) so contractions match their
    written form: "isn't here" → "isnt here", "doesn't work" → "doesnt work"."""
    t = (text or "").casefold()
    t = t.replace("'", "").replace("’", "")  # isn't -> isnt (contractions)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# "Do not call" — highest priority, always an exit (and now persists a durable
# opt-out, see turn_ender). DIRECTED forms only (F-14 fix 2026-07-20): bare
# "dont call"/"do not call" substring-matched "I don't call it Acme anymore"
# (a naming-convention remark) and hung up + promised removal. Every phrase
# here must carry its own object ("...me/us/again/back/this number") so a
# sentence that merely contains the words "don't call" can't trip it.
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

# Wrong DESTINATION — the company/line itself is wrong. Requires evidence that
# it's the wrong *place*, not just the wrong *person*. These end the call.
# HIGH-PRECISION ONLY (self-review 2026-07-17): phrases here hang up with no
# LLM, so anything a REAL prospect might say must stay out. Removed:
#   "never heard of ..."  — the common brush-off "never heard of you guys" is a
#                           live prospect, not a wrong number (prompt-side rule
#                           still lets the LLM end on true never-heard-of-the-
#                           company context).
#   bare "personal number/phone/line" — "call my personal number instead" is a
#                           CALLBACK REQUEST. Only "this is …" anchored forms
#                           are destination evidence.
_WRONG_BUSINESS_PHRASES = (
    "wrong company", "wrong business",
    "no such company", "no such business",
    "no company by that",
    # "this is my home" REMOVED (F-14 fix 2026-07-20): it substring-matched
    # "this is my home office for Acme" — a legitimate remote employee — and
    # hung up on them. "residence"/"residential" forms still catch a genuine
    # "you've reached a home" without swallowing a home office.
    "this is a residence", "private residence",
    "this is a personal number", "this is a personal phone",
    "this is a personal line", "this is my personal",
    "residential number", "not a business number", "not a business line",
    "there is no business", "theres no business",
    "you have the wrong company",
    "wrong organisation", "wrong organization",
)

# Wrong PERSON — right place, contact unavailable/unknown. These PIVOT (never
# end): whoever answered may redirect us. Deliberately includes "no one here by
# that name" — the review's canonical do-NOT-hang-up case.
_WRONG_PERSON_PHRASES = (
    "wrong person",
    "no one here by that name", "nobody here by that name",
    "no one by that name", "no one called", "nobody called",
    "not here", "isnt here", "is not here",
    "not available", "not in today", "not in right now",
    "not in at the moment", "away from", "on leave", "off today",
    "wrong department", "wrong extension", "not their department",
    # Redirect phrasings anchored on a TARGET ("you want the accounts team"),
    # never the bare "you want" — "you want to sell me something?" is a live
    # prospect and must not arm the wrong-person route (self-review).
    "you want the", "you need the", "youll want the",
    "you want to speak to", "you need to speak to", "you want to talk to",
    "doesnt work here", "does not work here", "no longer works here",
    "left the company", "left the business",
)

# Bare "wrong number" with no scope — ambiguous by itself.
_BARE_WRONG_NUMBER = ("wrong number", "got the wrong number", "you have the wrong")

# Positive confirmation that the BUSINESS is right (so any mismatch is the
# PERSON → pivot, never a wrong-destination end). F-14 fix 2026-07-20: the
# post-clarify branch used to read bare "business"/"no" as wrong-destination
# evidence, so "No, this is the right business, you have an old contact" —
# a caller explicitly CONFIRMING the business — hung up as WRONG_BUSINESS.
_RIGHT_BUSINESS_CONFIRM = (
    "right business", "right company", "right place", "right number",
    "correct business", "correct company",
    "you have the right", "this is the right", "reached the right",
    "is the right",
)

# Answering the clarify question with a PERSON/pivot signal.
_CLARIFY_PERSON_SIGNALS = ("person", "name", "someone", "somebody", "contact")

# Answering the clarify question with a DESTINATION/wrong-place signal.
_CLARIFY_BUSINESS_SIGNALS = (
    "wrong business", "wrong company", "wrong place", "wrong number",
    "the business", "the company", "not a business", "wrong organisation",
    "wrong organization",
)

# Explicit, unambiguous conversation-ending phrases (Defect 6). Deliberately
# NARROW: this only feeds the reverse gate's decision to let a model-issued
# END_CALL stand on a WRONG_PERSON turn ("she's not here — goodbye"), never
# the classify() precedence itself (person evidence still always pivots).
# Bare "bye" is intentionally EXCLUDED — it's one STT substitution away from
# "hi"/"by"/mid-word noise ("by the way", "buy") and is common as a soft
# filler ("bye now" IS included below because "now" anchors it as a genuine
# sign-off, not a fragment). Soft closers ("okay", "thanks", "alright") are
# excluded on purpose — they end a topic, not the call; treating them as a
# goodbye would let a model END_CALL slip through on turns where the caller
# was just acknowledging, not hanging up.
_EXPLICIT_GOODBYE_PHRASES = (
    "goodbye", "good bye",
    "bye now", "bye bye",
    "im hanging up", "i am hanging up",
    "i have to go", "ive got to go", "i gotta go", "gotta go",
)


def _contains_any(hay: str, needles) -> bool:
    return any(n in hay for n in needles)


def contains_dnc(transcript: str) -> bool:
    """True when the utterance carries a directed do-not-call request. Pure/
    stateless — used by turn_ender to EXEMPT a DNC from the repetitive-STT
    hallucination guard (F-13 fix 2026-07-20): "no no no no no no stop calling
    me" is >50% one word, so the guard rejected it BEFORE the DNC classifier
    ran — the caller's opt-out was silently dropped. Mirrors classify()'s DNC
    precedence check exactly."""
    return _contains_any(_norm(transcript), _DNC_PHRASES)


def contains_explicit_goodbye(transcript: str) -> bool:
    """True when the utterance carries an unambiguous, explicit conversation-
    ending phrase (see ``_EXPLICIT_GOODBYE_PHRASES``). Pure/stateless — used by
    the reverse enforcement gate to decide whether a model-issued END_CALL on a
    WRONG_PERSON turn should be honored ("she's not here — goodbye") rather
    than stripped. Does NOT affect ``classify_identity_disposition``'s
    precedence or return value."""
    return _contains_any(_norm(transcript), _EXPLICIT_GOODBYE_PHRASES)


def classify_identity_disposition(
    transcript: str, *, prior_clarify_asked: bool = False
) -> IdentityDisposition:
    """Map a caller utterance to a disposition.

    ``prior_clarify_asked`` — True when we already asked the scope-clarifying
    question earlier this call and the caller is answering it now; a second bare
    wrong-number then resolves to WRONG_BUSINESS rather than looping.

    Precedence: DNC → wrong-business evidence → person evidence (pivot) → bare
    wrong-number (ambiguous, or wrong-business on the 2nd pass) → none.
    """
    t = _norm(transcript)
    if not t:
        return IdentityDisposition.NONE

    if _contains_any(t, _DNC_PHRASES):
        return IdentityDisposition.DNC

    # A caller answering the clarify question. Polarity FIRST (F-14 fix): a
    # positive "right business" confirmation, or any person/pivot signal,
    # routes to WRONG_PERSON (pivot) and must win over the destination check —
    # otherwise "No, this is the RIGHT business, wrong contact" hangs up. Only
    # an explicit wrong-destination answer (with no right-business confirm)
    # ends. Anything else falls through (bare "wrong number" is still resolved
    # to WRONG_BUSINESS below via _BARE_WRONG_NUMBER on this 2nd pass).
    if prior_clarify_asked:
        if _contains_any(t, _RIGHT_BUSINESS_CONFIRM) or _contains_any(t, _CLARIFY_PERSON_SIGNALS):
            return IdentityDisposition.WRONG_PERSON
        if _contains_any(t, _CLARIFY_BUSINESS_SIGNALS):
            return IdentityDisposition.WRONG_BUSINESS

    if _contains_any(t, _WRONG_BUSINESS_PHRASES):
        return IdentityDisposition.WRONG_BUSINESS

    # Person evidence PIVOTS even when "wrong number" is also present
    # ("wrong number, no one here by that name" = wrong person at a real line).
    if _contains_any(t, _WRONG_PERSON_PHRASES):
        return IdentityDisposition.WRONG_PERSON

    if _contains_any(t, _BARE_WRONG_NUMBER):
        # Second bare wrong-number after a clarify → resolve to wrong business.
        return (
            IdentityDisposition.WRONG_BUSINESS
            if prior_clarify_asked
            else IdentityDisposition.AMBIGUOUS
        )

    return IdentityDisposition.NONE


def disposition_end_line(disposition: IdentityDisposition) -> Optional[str]:
    """The fixed closing line for a deterministic end disposition, else None."""
    if disposition == IdentityDisposition.WRONG_BUSINESS:
        return WRONG_BUSINESS_CLOSE
    if disposition == IdentityDisposition.DNC:
        return DNC_CLOSE
    return None

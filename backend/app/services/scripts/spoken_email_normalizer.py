"""Deterministic "spoken email -> canonical email" normalizer.

Voice transcripts say things like:
  "allstateestimation at the rate gmail dot com"
  "bob one two three at yahoo period co dot uk"

Without this helper, an 8B LLM has to (a) notice the caller said an
email, (b) stitch the tokens together. Small models miss (a) or (b)
often enough to loop. So we normalize *before* the model sees the turn
and inject the canonical form into the system prompt's CAPTURED block.

Pure function — no I/O. Safe to call per-turn.
"""
from __future__ import annotations

import re
from typing import Optional

# Spoken -> written substitutions, applied in order. Longer phrases first
# so "at the rate" wins over "at".
_SUBSTITUTIONS: list[tuple[str, str]] = [
    (r"\s+at\s+the\s+rate\s+", " @ "),
    (r"\s+at\s+sign\s+", " @ "),
    (r"\s+at\s+", " @ "),
    (r"\s+dot\s+", " . "),
    (r"\s+period\s+", " . "),
    (r"\s+underscore\s+", " _ "),
    (r"\s+(?:dash|hyphen|minus)\s+", " - "),
    # Spoken digits (limited to 0-9 single-token — multi-digit "twenty three"
    # is out of scope; real-world voice transcripts already convert to "23").
    (r"\bzero\b", "0"), (r"\bone\b", "1"), (r"\btwo\b", "2"),
    (r"\bthree\b", "3"), (r"\bfour\b", "4"), (r"\bfive\b", "5"),
    (r"\bsix\b", "6"), (r"\bseven\b", "7"), (r"\beight\b", "8"),
    (r"\bnine\b", "9"),
]

_EMAIL_RE = re.compile(
    r"[a-z0-9][a-z0-9._+\-]*@[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+",
    re.IGNORECASE,
)

# Carrier / lead-in words that precede a SPOKEN email ("you can send me on
# <email> at gmail dot com", "my email is …", "reach me at …"). Without
# stripping these, the spoken-path local-part becomes
# "youcansendmeonallstateestimation" — the #1 spoken-email bug. These are
# words that essentially never appear in a real local part, so dropping a
# leading run of them is safe.
_LEADIN_WORDS: frozenset[str] = frozenset({
    "you", "can", "could", "would", "please", "send", "sent", "me", "my",
    "mine", "it", "it's", "its", "is", "the", "a", "an", "on", "to", "at",
    "email", "e-mail", "mail", "address", "reach", "write", "down", "so",
    "well", "okay", "ok", "yeah", "yes", "here", "i", "i'll", "you'll",
    "and", "of", "for", "give", "get", "got", "have", "use", "using", "that",
    "this", "just", "sure", "alright", "right", "um", "uh", "er", "erm",
})


def _strip_leadin(local: str) -> str:
    """Drop a leading run of carrier words, then join the rest into a local
    part. 'you can send me on all state estimation' -> 'allstateestimation'."""
    tokens = local.split()
    while tokens and tokens[0].strip(".,;:!?").lower() in _LEADIN_WORDS:
        tokens.pop(0)
    return "".join(tokens)


def extract_email_from_speech(utterance: str) -> Optional[str]:
    """Return a canonical email if the utterance contains one; else None.

    Idempotent for utterances that already contain a written email.
    """
    if not utterance or not utterance.strip():
        return None

    original_had_at = "@" in utterance
    s = f" {utterance.lower().strip()} "
    for pattern, repl in _SUBSTITUTIONS:
        s = re.sub(pattern, repl, s)

    s = re.sub(r"\s*@\s*", "@", s)
    s = re.sub(r"\s*\.\s*", ".", s)
    s = re.sub(r"\s*_\s*", "_", s)
    s = re.sub(r"\s*-\s*", "-", s)

    if "@" in s:
        local, _, rest = s.partition("@")
        if original_had_at:
            # Written email — "my email is john@gmail.com". The last
            # whitespace-separated token before @ is the local part.
            tokens = local.split()
            local = tokens[-1] if tokens else ""
        else:
            # Spoken email — "you can send me on all state estimation at gmail
            # dot com". Drop the leading carrier phrase, then stitch the rest.
            local = _strip_leadin(local)
        rest = re.sub(r"\s+", "", rest)
        rest = rest.rstrip(".?!,;:")
        s = f"{local}@{rest}"

    match = _EMAIL_RE.search(s)
    if not match:
        return None
    return match.group(0).lower()


def spell_out_email(email: Optional[str]) -> str:
    """Render a canonical email as a letter-by-letter spoken read-back string.

    "bob@gmail.com" -> "b-o-b at gmail dot com".

    The local part is spelled character by character (hyphen-joined so TTS reads
    each letter distinctly); "@" becomes "at" and "." becomes "dot". This gives
    the LLM the EXACT words to say when confirming an email, so it never
    re-derives a messy version from the raw transcript (the #1 lead-capture bug:
    gluing carrier words in, or dropping/adding letters). Returns "" for a
    missing or malformed address.
    """
    if not email or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    if not local or not domain:
        return ""
    local_spelled = "-".join(local)
    domain_spoken = domain.replace(".", " dot ")
    return f"{local_spelled} at {domain_spoken}".strip()

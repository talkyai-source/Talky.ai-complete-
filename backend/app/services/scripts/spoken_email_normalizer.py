"""Deterministic spoken-email *syntax* normalizer (the small half of the hybrid).

Voice transcripts say things like:
  "bob at gmail dot com"                      -> bob@gmail.com         (pinned)
  "mary underscore smith at gmail dot com"    -> mary_smith@gmail.com  (pinned)
  "you can send me on all state estimation at gmail dot com" -> None   (LLM's job)

Design (2026-06-24): this layer owns ONLY the fixed email syntax — "at" / "at the
rate" -> @, "dot" -> ., "underscore" -> _, and gluing the domain. It pins an
address ONLY when the local part is a single, unambiguous token. The instant the
local part is several spoken words that must be *joined* ("all state estimation"),
or is preceded by carrier words ("you can send me on …"), it returns None and the
LLM assembles it and reads it back to confirm. We deliberately keep NO hand-written
carrier-word list — it mangled real local parts ("me@", "iphone@", "yes2024@").
Email is a core field; correctness lives in the read-back loop, not in a guess.

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

def extract_email_from_speech(utterance: str) -> Optional[str]:
    """Pin a canonical email ONLY when it is unambiguous; else return None and
    let the LLM assemble it.

    The hybrid split (see the module docstring): this layer converts the spoken
    email *syntax* and pins the address only when the local part is a single,
    unambiguous token —

      * a written address ("my email is john@gmail.com"), or
      * a clean spoken local ("bob at gmail dot com",
        "mary underscore smith at gmail dot com", "john dot smith at gmail dot com").

    When the local part is several spoken words that would have to be *joined*
    ("all state estimation"), or is preceded by carrier words ("you can send me
    on …"), we DO NOT guess a boundary — we return None and let the LLM
    understand it and read it back to confirm. Idempotent for written emails.
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

    if "@" not in s:
        return None

    local, _, rest = s.partition("@")
    local_tokens = local.split()
    if original_had_at:
        # Written email — the literal "@" is the anchor; the token immediately
        # before it is the local part. Unambiguous, so pin it.
        local = local_tokens[-1] if local_tokens else ""
    else:
        # Spoken email — the "@" came from "at"/"at the rate". Pin ONLY a single
        # unambiguous token. Multi-word locals and carrier words are the LLM's
        # job (it joins them and reads the result back to confirm).
        if len(local_tokens) != 1:
            return None
        local = local_tokens[0]

    rest = re.sub(r"\s+", "", rest).rstrip(".?!,;:")
    candidate = f"{local}@{rest}"

    match = _EMAIL_RE.search(candidate)
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


# Spoken forms for local-part separators (matches how people dictate emails).
# Unknown symbols render as "" — better a tiny gap than TTS reading a glyph name.
_SEPARATOR_WORDS = {".": "dot", "_": "underscore", "-": "dash", "+": "plus"}


def _is_pronounceable(word: str) -> bool:
    """A letter run is 'sayable as a word' if it's >=2 chars and has a vowel;
    otherwise it's a random run that should be spelled (e.g. 'xq', 'bcdf')."""
    return len(word) >= 2 and any(v in word.lower() for v in "aeiou")


def natural_email_readback(email: Optional[str]) -> str:
    """A HUMAN read-back of an email: pronounceable letter-runs are said as a
    WORD, digit-runs are read individually, and only a non-word run is spelled
    letter-by-letter. The domain is always said as words.

      "allstateestimation@gmail.com" -> "allstateestimation at gmail dot com"
      "john7890@gmail.com"            -> "john 7 8 9 0 at gmail dot com"
      "xq7@gmail.com"                 -> "x-q 7 at gmail dot com"
      "j.smith@gmail.com"             -> "j dot smith at gmail dot com"

    Local-part separators are SPOKEN as words like the domain's: a literal "."
    is just a silent TTS pause, so the caller would hear "j smith" and could
    yes-confirm jsmith@ when they meant j.smith@ — the read-back must make the
    separator audible for the confirmation to mean anything.

    Replaces the old letter-by-letter ``spell_out_email`` on the read-back path so
    the agent doesn't sound like a robot spelling every character. The LLM is
    separately instructed to tag-question ONLY genuinely-variable names.
    """
    if not email or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    if not local or not domain:
        return ""
    chunks: list[str] = []
    for run in re.findall(r"[A-Za-z]+|[0-9]+|[^A-Za-z0-9]+", local):
        if run.isdigit():
            chunks.append(" ".join(run))            # "7890" -> "7 8 9 0"
        elif run.isalpha():
            chunks.append(run if _is_pronounceable(run) else "-".join(run))
        else:
            chunks.append(" ".join(_SEPARATOR_WORDS.get(ch, "") for ch in run).strip())
    local_spoken = " ".join(c for c in chunks if c)
    domain_spoken = domain.replace(".", " dot ")
    return f"{local_spoken} at {domain_spoken}".strip()

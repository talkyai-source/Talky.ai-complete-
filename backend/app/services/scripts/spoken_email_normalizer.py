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


# ── multi-word email: parse the AGENT's own assembled read-back ──────────────
#
# The deterministic user-turn extractor above returns None for a multi-word /
# carrier-prefixed spoken local ("all state estimation at gmail dot com") — it
# refuses to guess a word boundary. That left the HARDEST emails OUTSIDE the
# confirm-before-commit gate entirely (they never entered CallState, so the
# read-back/verdict/commit loop never ran over them).
#
# This closes that gap from the other side: when the LLM has ASSEMBLED such an
# address and read it back in its OWN turn, we parse that assembled address out
# of the agent's read-back (deterministic text we control the shape of) and seed
# it into CallState as UNCONFIRMED — so the same gate runs. Conservative by
# design: we only parse when the turn is unmistakably a read-back (a confirm
# question) anchored on a recognizable read-back preamble, and we bail on any
# ambiguity (multiple "at"s, no preamble) rather than commit a mis-parsed local.

# The turn must actually ASK the caller to confirm — otherwise it's the agent
# merely mentioning an address, not reading it back for confirmation.
_READBACK_CONFIRM_RE = re.compile(
    r"\b(did\s+i\s+(get|say|hear)\s+(that|it|this)|is\s+(that|this|it)\s+(right|correct)|"
    r"got\s+(that|it)\s+right|that\s+right\?|is\s+that\s+ok(ay)?\?|"
    r"sounds?\s+right|correct\?|right\?)",
    re.IGNORECASE,
)

# Recognizable read-back preambles. The local part is exactly the words BETWEEN
# the preamble and the "@" — anchoring on a known preamble is far safer than a
# heuristic filler list, which broke on lead-ins like "the address you gave me".
_READBACK_PREAMBLE_RE = re.compile(
    r"\b(so\s+that'?s|so\s+that\s+is|that'?s|so\s+it'?s|it'?s|"
    r"so\s+i\s+have|i\s+have|i'?ve\s+got|i\s+got|"
    r"let\s+me\s+confirm|just\s+to\s+confirm|to\s+confirm|"
    r"reading\s+(that|it)\s+back|read\s+(that|it)\s+back|"
    r"your\s+email(\s+address)?\s+is|the\s+email\s+is|so\s+your\s+email\s+is)\b",
    re.IGNORECASE,
)


def extract_email_from_agent_readback(text: str) -> Optional[str]:
    """Parse an ASSEMBLED email out of the agent's own read-back turn.

    Returns the address ONLY when the turn is clearly a read-back-for-confirmation
    (contains a confirm question), has exactly one spoken "@" (so we don't cross
    two separate "at"s), and the local part sits after a recognizable preamble
    ("so that's …", "your email is …"). Everything between that preamble and the
    "@" is joined into the local part; the domain is read as words after it.

    Deliberately conservative — a mis-parsed local that the caller then confirms
    would commit a value they never actually approved. When the boundary is not
    unambiguous we return None (the agent simply re-reads it back next turn).
    """
    if not text or not text.strip():
        return None
    low = text.lower().strip()
    if not _READBACK_CONFIRM_RE.search(low):
        return None

    s = f" {low} "
    for pattern, repl in _SUBSTITUTIONS:
        s = re.sub(pattern, repl, s)
    s = re.sub(r"\s*@\s*", "@", s)
    s = re.sub(r"\s*\.\s*", ".", s)
    s = re.sub(r"\s*_\s*", "_", s)
    s = re.sub(r"\s*-\s*", "-", s)

    # Exactly one spoken "@": more than one means two "at"s in the sentence and we
    # can't tell which is the address — bail rather than guess.
    if s.count("@") != 1:
        return None

    at = s.index("@")
    before, after = s[:at], s[at + 1:]

    dm = re.match(r"[a-z0-9][a-z0-9.\-]*\.[a-z0-9\-]+", after)
    if not dm:
        return None
    domain = dm.group(0).rstrip(".?!,;:")

    pre = list(_READBACK_PREAMBLE_RE.finditer(before))
    if not pre:
        return None
    local_tokens = before[pre[-1].end():].split()
    if not local_tokens or "@" in "".join(local_tokens):
        return None
    local = "".join(local_tokens)

    candidate = f"{local}@{domain}"
    match = _EMAIL_RE.search(candidate)
    if not match:
        return None
    return match.group(0).lower()


# ── spoken phone / callback number (mirrors the email hybrid) ────────────────
#
# A callback / reference number is a CORE field just like email — a single mis-
# heard digit makes it useless. It gets the SAME deterministic confirm-before-
# commit gate. This pins a candidate number ONLY when the turn is unambiguously
# about a number (a phone-context cue, or a clearly-formatted number), and never
# when the turn is actually an email (its digits belong to the local part).

_PHONE_CONTEXT_RE = re.compile(
    r"\b(phone|number|numbers|call\s+me|callback|call\s+back|reach\s+me|"
    r"cell|mobile|text\s+me|contact\s+me|my\s+(cell|mobile|line)|dial)\b",
    re.IGNORECASE,
)
# If the turn is an email, its digits are NOT a phone — skip phone capture.
_EMAIL_CUE_RE = re.compile(
    r"@|\bdot\s+com\b|\b(g\s*mail|gmail|yahoo|hotmail|outlook|icloud|proton|aol)\b|"
    r"\bemail\b|\bat\s+the\s+rate\b|\bat\s+sign\b",
    re.IGNORECASE,
)
# A run of digits with optional phone formatting (spaces, dashes, dots, parens).
_PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\s().\-]{5,}\d")
_MIN_PHONE_DIGITS = 7
_MAX_PHONE_DIGITS = 15  # E.164 maximum


def extract_phone_from_speech(utterance: str) -> Optional[str]:
    """Pin a canonical phone/callback number ONLY when it is unambiguous.

    Returns a digit string (optionally "+"-prefixed) when the turn clearly names
    a number — a phone-context cue ("call me", "my number is …") or an already-
    formatted number ("555-123-4567") — and the digit count is a plausible phone
    length (7–15). Returns None for an email turn, an address, or a stray short
    number, so it never fires where it shouldn't. Correctness lives in the read-
    back loop (like email), not in a greedy guess.
    """
    if not utterance or not utterance.strip():
        return None
    if _EMAIL_CUE_RE.search(utterance):
        return None

    s = f" {utterance.lower().strip()} "
    # Reuse ONLY the spoken-digit substitutions (0–9). Slicing keeps this in sync
    # with the email normalizer's digit map without re-declaring it.
    for pattern, repl in _SUBSTITUTIONS:
        if repl.isdigit():
            s = re.sub(pattern, repl, s)
    # A leading spoken "plus" is the international "+" prefix; glue it to the digits.
    s = re.sub(r"\bplus\b\s*", "+", s)

    best: Optional[str] = None
    best_len = 0
    had_format = False
    for m in _PHONE_CANDIDATE_RE.finditer(s):
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        n = len(digits)
        if not (_MIN_PHONE_DIGITS <= n <= _MAX_PHONE_DIGITS):
            continue
        if re.search(r"[()\-]", raw) or raw.strip().startswith("+"):
            had_format = True
        if n > best_len:
            best_len = n
            best = ("+" + digits) if raw.strip().startswith("+") else digits

    if best is None:
        return None
    # Require an explicit signal that this really is a number: a context cue, or a
    # phone-formatted token. A bare digit run with neither is left alone.
    if not (had_format or _PHONE_CONTEXT_RE.search(utterance)):
        return None
    return best


def natural_phone_readback(phone: Optional[str]) -> str:
    """A spoken read-back of a phone number: each digit said individually so the
    caller can catch a single wrong one. "+" is spoken as "plus".

      "5551234567"  -> "5 5 5 1 2 3 4 5 6 7"
      "+441234567"  -> "plus 4 4 1 2 3 4 5 6 7"
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    spoken = " ".join(digits)
    return f"plus {spoken}" if phone.strip().startswith("+") else spoken


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

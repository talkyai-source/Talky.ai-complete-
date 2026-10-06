"""Shared number normalization and contact display helpers.

Used by action authorization and technical-disclosure exemptions, not to
extract caller contacts or prescribe dialogue.
"""
from __future__ import annotations
import re
from typing import Optional

_SUBSTITUTIONS = [
    (r"\bzero\b", "0"), (r"\bone\b", "1"), (r"\btwo\b", "2"),
    (r"\bthree\b", "3"), (r"\bfour\b", "4"), (r"\bfive\b", "5"),
    (r"\bsix\b", "6"), (r"\bseven\b", "7"), (r"\beight\b", "8"),
    (r"\bnine\b", "9"),
]
_PHONE_OH_RE = re.compile(r"\boh\b")
_PHONE_DOUBLE_RE = re.compile(r"\bdouble\s+(\d)\b")
_PHONE_TRIPLE_RE = re.compile(r"\btriple\s+(\d)\b")
_SEPARATOR_WORDS = {".": "dot", "_": "underscore", "-": "dash", "+": "plus"}


def _expand_phone_repeats(s: str) -> str:
    """Expand 'double <digit>' -> that digit twice, 'triple <digit>' -> three
    times. Only fires when a single digit immediately follows the word, so
    ordinary speech ("double check", "triple the size") is left untouched —
    there is no digit for the regex to anchor on, so it simply doesn't match.
    """
    s = _PHONE_DOUBLE_RE.sub(lambda m: m.group(1) * 2, s)
    s = _PHONE_TRIPLE_RE.sub(lambda m: m.group(1) * 3, s)
    return s


def spoken_digits_to_numerals(utterance: str) -> str:
    """Turn a spoken number into numerals: "zero three one two, zero seven
    five" -> "0312 075". Digit words, "oh", "double"/"triple" and a leading
    "plus" are converted; a comma BETWEEN digits is just a pause. Everything
    else is left as it was.
    """
    s = f" {str(utterance or '').lower()} "
    for pattern, repl in _SUBSTITUTIONS:
        if repl.isdigit():
            s = re.sub(pattern, repl, s)
    s = _PHONE_OH_RE.sub("0", s)
    s = _expand_phone_repeats(s)
    s = re.sub(r"\bplus\b\s*", "+", s)
    s = re.sub(r"(?<=\d)\s*,\s*(?=\d)", " ", s)
    return s.strip()


def _phone_groups(phone: str, digits: str) -> list[str]:
    """Split a number into the chunks people say it in, for its own country.

    Uses the international layout libphonenumber prints ("+92 312 0750496",
    "+44 7429 916656", "+1 647-347-6870"), then breaks any chunk longer than
    four digits into threes with the last chunk up to four -- how a person
    actually reads seven digits aloud ("075, 0496"). Falls back to the same
    3/4 rule from the end when the number can't be parsed.
    """
    groups: list[str] = []
    if phone.strip().startswith("+"):
        try:
            import phonenumbers

            parsed = phonenumbers.parse(phone, None)
            printed = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
            groups = [g for g in re.split(r"[\s\-()]+", printed.lstrip("+")) if g]
        except Exception:
            groups = []
    if not groups or "".join(groups) != digits:
        groups = [digits]

    def _split(chunk: str) -> list[str]:
        n = len(chunk)
        if n <= 5:
            return [chunk]
        if n == 6:
            return [chunk[:3], chunk[3:]]
        if n == 8:
            return [chunk[:4], chunk[4:]]
        head, tail = chunk[:-4], chunk[-4:]
        out = [head[i:i + 3] for i in range(0, len(head), 3)]
        if len(out) > 1 and len(out[-1]) == 1:
            out[-2:] = [out[-2] + out[-1]]
        return out + [tail]

    result: list[str] = []
    for g in groups:
        result.extend(_split(g))
    return result


def natural_phone_readback(phone: Optional[str]) -> str:
    """A spoken read-back of a phone number, in natural chunks.

    Every digit is still said on its own (so the caller can catch a single
    wrong one), but in the groups people use for their country, with a comma
    -- a short pause in TTS -- between groups, instead of one flat run of
    digits that sounds like a machine (2026-09-29, owner feedback on call
    b847f447). "+" is spoken as "plus".

      "+923120750496" -> "plus 9 2, 3 1 2, 0 7 5, 0 4 9 6"
      "+447429916656" -> "plus 4 4, 7 4 2 9, 9 1 6, 6 5 6"
      "+16473476870"  -> "plus 1, 6 4 7, 3 4 7, 6 8 7 0"
      "5551234567"    -> "5 5 5, 1 2 3, 4 5 6 7"
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    spoken = ", ".join(" ".join(group) for group in _phone_groups(phone, digits))
    return f"plus {spoken}" if phone.strip().startswith("+") else spoken


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

    This formatting is shared by action authorization and disclosure exemptions;
    it does not prescribe a live contact-confirmation script.
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

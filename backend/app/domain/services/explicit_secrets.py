"""Remove complete, explicitly labelled secret values from new voice text.

This is deliberately not a PII detector: ordinary names, emails, phone numbers
and business figures must survive. English labels plus numeric values, or a
labelled password token/quoted phrase, are the bounded contract. Unlabelled,
fragmented, unknown-language or oversized values and audio/provider retention
are not covered. Never retain secret-derived hashes, prefixes or last digits.
"""
from __future__ import annotations

import re

SECRET_REMOVED = "[secret removed]"
_SEPARATOR = r"\b[ \t]*(?:is\b|:|=)[ \t]*"
_NUMERIC = re.compile(
    r"\b(?:card[ \t]+number|(?:credit|debit|payment)[ -]?card(?:[ \t]+number)?|"
    r"cvv|cvc|(?:card[ \t]+)?security[ \t]+code|(?:my|our)[ \t]+pin(?:[ \t]+code)?|"
    r"one[ -]time[ \t]+(?:code|passcode|password)|(?:verification|authentication)[ \t]+code|"
    r"bank[ \t]+account[ \t]+number|social[ -]security(?:[ \t]+number)?|"
    r"national[ -]id(?:[ \t]+number)?|ssn)(?:" + _SEPARATOR + r"|\b[ \t]+)" +
    r"[\"']?(?P<value>\+?[0-9][0-9 \t.,-]{0,254}[0-9])"
    # A value must end before a real boundary, not before a wider gap or a
    # decimal/grouping separator and more digits. This does not validate PANs.
    r"(?=$|[ \t.,-]{1,64}(?:$|[^0-9 \t.,-])|[;!?\"'])", re.I,
)
_PASSWORD = re.compile(
    r"\b(?:password|passphrase)" + _SEPARATOR +
    r"(?:\"(?P<double>[^\"\r\n]{1,128})\"(?=$|\s|[.,;!?](?:\s|$))|"
    r"'(?P<single>[^'\r\n]{1,128})'(?=$|\s|[.,;!?](?:\s|$))|"
    r"(?P<token>(?![\"']|\[secret removed\](?=$|\s|[.,;!?](?:\s|$)))\S{1,128})(?!\S))", re.I,
)
# These describe a credential's state, not a value being offered. Quoted
# passwords are literal values even when their wording resembles a state.
_PASSWORD_STATES = frozenset({
    "not", "required", "optional", "missing", "wrong", "forgotten", "reset",
    "expired", "blocked", "locked", "invalid", "secure", "strong", "weak",
    "protected", "changed", "working", "long", "short", "needed", "incorrect",
    "a", "an", "the", "at", "too", "very", "stored", "hashed", "encrypted",
    "case-sensitive", "unknown", "unavailable",
    "correct", "valid", "fine", "okay", "ok", "unchanged", "empty", "blank",
})
_LENGTH_DESCRIPTION = re.compile(r"^[ \t]{1,16}(?:characters|digits|letters)[ \t]{1,16}long\b", re.I)


def _describes_length(value: str, following: str) -> bool:
    # A three-plus-digit credential must not escape by appending "digits long".
    # Only small canonical counts (no leading zero) receive this exemption.
    count = value.strip(".,;!?")
    return (len(count) <= 2 and count.isascii() and count.isdecimal()
            and not count.startswith("0") and int(count) <= 64
            and bool(_LENGTH_DESCRIPTION.match(following)))


def sanitize_explicit_secrets(text: str) -> str:
    """Replace only the value, preserving labels, quotation and adjacent facts."""
    def numeric(match):
        if _describes_length(match["value"], match.string[match.end():]):
            return match[0]
        start, end = match.span("value")
        return match[0][:start - match.start()] + SECRET_REMOVED + match[0][end - match.start():]

    def password(match):
        group = next(name for name in ("double", "single", "token") if match[name] is not None)
        if group == "token" and (
            match[group].strip(".,;!?").casefold() in _PASSWORD_STATES
            or _describes_length(match[group], match.string[match.end():])
        ):
            return match[0]
        start, end = match.span(group)
        return match[0][:start - match.start()] + SECRET_REMOVED + match[0][end - match.start():]

    return _PASSWORD.sub(password, _NUMERIC.sub(numeric, text))


def sanitize_transcript_metadata(metadata: dict | None) -> dict:
    """Copy only known speech-bearing fields; opaque provider identities survive.

    Canonical ASR revisions are authored by TranscriptService. An imported
    revision changed here stays unavailable rather than acquiring new proof.
    """
    result = dict(metadata) if isinstance(metadata, dict) else {}

    def text_fields(value):
        if isinstance(value, str):
            return sanitize_explicit_secrets(value)
        if isinstance(value, dict):
            return {key: sanitize_explicit_secrets(item)
                    if key in {"text", "transcript", "content", "original_content"} and isinstance(item, str)
                    else item for key, item in value.items()}
        return value

    result = text_fields(result)
    for key in ("alternatives", "transcript_alternatives"):
        values = result.get(key)
        if isinstance(values, (list, tuple)):
            cleaned = [text_fields(value) for value in values]
            result[key] = tuple(cleaned) if isinstance(values, tuple) else cleaned
    revision = result.get("asr_latest_revision")
    if isinstance(revision, dict):
        cleaned = text_fields(revision)
        if cleaned.get("content") != revision.get("content"):
            cleaned["content_sha256"] = None
        result["asr_latest_revision"] = cleaned
    return result

"""Redaction of secrets and caller PII out of log lines.

Two layers live here:

1. ``redact_redis_url`` — masks the password in a Redis connection URL that
   a few startup log statements print (see below).
2. ``install_pii_log_redaction`` — a **process-wide** redactor for caller PII
   (spoken transcripts, emails, phone numbers, card/account-shaped digit
   runs). Callers routinely say health and financial details out loud and the
   voice pipeline logs transcripts on the hot path; nothing else in the stack
   applies a retention limit to log content, so the raw utterance must never
   reach a log handler in the first place.

Why a single global install instead of editing every call site
--------------------------------------------------------------
There are already ~20 transcript-bearing log statements across the voice
pipeline and the set grows every time a turn-taking bug is diagnosed. Fixing
"the sites we remember" leaves the next one leaking. The redactor is therefore
installed **once**, by wrapping ``logging.Logger.makeRecord``:

* it sees every record from every logger, including ones added later;
* it runs *after* ``extra={...}`` has been merged onto the record (a
  ``logging.setLogRecordFactory`` hook does NOT — extras are applied by
  ``makeRecord`` after the factory returns), which matters because the worst
  offender logs the full utterance via ``extra``;
* it runs *before* ``callHandlers``, so formatters, journald **and Sentry's
  logging integration** (which turns WARNING+ records into breadcrumbs/events
  leaving the box) all see the redacted record;
* it is independent of handler configuration/order, so it cannot be silently
  defeated by a later ``basicConfig(force=True)``.

``PIIRedactionFilter`` is also exported for anyone who prefers to wire the
same logic declaratively into a ``dictConfig``; redaction is idempotent, so
having both active is harmless.

What survives, so operators can still debug
-------------------------------------------
* Sensitive *fields* (``transcript``, ``text``, ``response``, ...) collapse to
  ``[redacted chars=57 sha=1f3a9c2d]`` — the length and a stable short hash,
  which is enough to tell "same utterance repeated" from "utterance grew" and
  to correlate the same turn across log lines.
* Free text elsewhere keeps its shape: emails keep their domain
  (``***@example.com``), phone/card-shaped digit runs keep their digit count
  and last two digits (``[redacted digits=16 ..11]``). UK national numbers
  (``07700 900123``, ``020 7946 0958``) are masked like any other phone number.
* Credentials are masked wherever they appear in a line: ``Bearer`` tokens,
  JWTs, Stripe-style keys (``sk_live_``, ``sk_test_``, ``rk_live_``,
  ``rk_test_``, ``whsec_``) and the value of a ``password`` / ``passwd`` /
  ``secret`` / ``token`` / ``api_key`` / ``apikey`` / ``authorization`` field
  written ``key=value``, ``key: value`` or JSON ``"key": "value"``. They become
  ``[redacted secret]`` — no length and no hash, because a short hash of a weak
  password is a way to confirm a guess.
* Everything else — call ids, turn ids, timings, latency numbers, state
  names — is untouched.

Configuration
-------------
``LOG_REDACT_PII`` (default **on**, in every environment including
production; set ``0/false/no/off`` to disable for local debugging) and
``LOG_REDACT_PII_KEEP`` (default ``0``; if set to N>0, the first N characters
of a sensitive field are kept, themselves pattern-scrubbed).

Fail-safe: if the redactor itself raises, the record's message and arguments
are replaced with a fixed marker and every non-standard string attribute is
blanked, so a redactor bug can neither crash logging nor emit the raw value.

Several startup-time log statements print the full Redis connection URL
(``redis://:PASSWORD@host:port/db``) for diagnostics. That's useful for
confirming *which* host/db a service connected to, but leaking the password
into journald/log aggregators is an unnecessary credential-exposure surface.

``redact_redis_url`` keeps the host/port/db visible and masks only the
password segment, e.g.::

    redis://:s3cr3t@10.0.0.5:6379/0  ->  redis://:****@10.0.0.5:6379/0
    redis://10.0.0.5:6379/0          ->  redis://10.0.0.5:6379/0   (no auth, unchanged)

This only changes what gets logged — it must never be used for the URL a
client actually connects with.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from functools import lru_cache
from typing import Any, Optional

# Matches the userinfo portion of a redis/rediss URL: optional "user", a
# colon, then the password, up to the "@". Handles both
# "redis://:password@host" (no username) and "redis://user:password@host".
_REDIS_URL_AUTH_RE = re.compile(r"(?P<scheme>rediss?://)(?P<userinfo>[^@/]*:[^@/]*)@")


def redact_redis_url(url: str) -> str:
    """Return ``url`` with any embedded password replaced by ``****``.

    Safe to call on a URL with no credentials (returned unchanged) or on a
    non-Redis string (returned unchanged — the regex simply won't match).
    """
    if not url:
        return url

    def _mask(match: "re.Match[str]") -> str:
        userinfo = match.group("userinfo")
        if ":" not in userinfo:
            # No password segment (just a bare username) — nothing to mask.
            return match.group(0)
        user, _, _password = userinfo.partition(":")
        return f"{match.group('scheme')}{user}:****@"

    return _REDIS_URL_AUTH_RE.sub(_mask, url)


# ---------------------------------------------------------------------------
# Caller-PII redaction
# ---------------------------------------------------------------------------

ENV_ENABLED = "LOG_REDACT_PII"
ENV_KEEP_CHARS = "LOG_REDACT_PII_KEEP"

#: Marker prefix every redacted value starts with. Also makes redaction
#: idempotent — an already-redacted value is never re-processed.
REDACTED_PREFIX = "[redacted"

#: Emitted when the redactor itself blows up (fail-safe path).
REDACTION_FAILED = "[redacted: log redaction failed]"

#: What a credential collapses to. Starts with REDACTED_PREFIX, so a value that
#: is already masked is never processed twice.
REDACTED_SECRET = "[redacted secret]"

#: Field / keyword names whose *entire* value is caller speech (or model
#: speech derived from it) and must never be logged verbatim. Matched against
#: ``extra={...}`` keys and against ``name=%r`` placeholders in format strings.
#:
#: Deliberately excludes generic words that turned out to name non-speech
#: values in this codebase — ``partial=%s`` is a Groq usage flag and
#: ``summary=%s`` is a cleanup-stats dict. A name only belongs here if it
#: names *content*; over-claiming destroys log debuggability for no privacy
#: gain (the pattern rules below still cover any PII inside those values).
SENSITIVE_FIELD_NAMES = frozenset({
    "after",
    "answer",
    "assistant_text",
    "before",
    "caller_text",
    "content",
    "current_user_input",
    "final_transcript",
    "full_transcript",
    "greeting",
    "llm_response",
    "message_text",
    "partial_transcript",
    "prompt",
    "reply",
    "response",
    "response_text",
    "speech",
    "spoken",
    "text",
    "transcript",
    "user_input",
    "user_text",
    "utterance",
})

# LogRecord attributes the redactor must never touch: they are structural
# (levelname, pathname, ...) or known-safe identifiers. Skipping them keeps
# the per-record cost to "iterate the handful of extras".
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {
    "asctime",
    "call_id",
    "message",
    "request_id",
    "taskName",
    "turn_id",
}

# --- patterns (all precompiled; this runs on every log line of every turn) --

# Credentials come first, so that a JWT or "password=<digits>" collapses as a
# secret instead of being nibbled into digit runs by the PII pass below. Each
# rule has its own cheap trigger (see _scrub_secrets), so a line with no
# credential in it pays a lowercase copy and a few substring tests.
#
# These rules only see text. The template of a %-style call is never rewritten,
# so ``logger.info("password=%s", pw)`` still logs a bare ``pw`` that has no
# shape to match: a secret must not be handed to a log call at all.

# A field name that ENDS in a secret word: ``access_token``, ``DB_PASSWORD``,
# ``X-API-Key``. A name that only contains one (``max_tokens``, ``token_count``,
# ``password_length``) is not followed by ``=`` or ``:`` straight after the
# word, so it never matches and ordinary usage numbers stay readable.
_SECRET_NAME = r"(?:password|passwd|secret(?:[_-]?key)?|token|api[_-]?key|authorization)"
_AUTH_SCHEME = r"(?:bearer|basic|digest)"

# The key/value rule's trigger: the same name and separator, tested on the
# lowercased text with no flags (a plain-literal regex scans much faster than an
# ignore-case one). ``prompt_tokens=812`` never reaches the real rule.
_SECRET_KEY_HINT_RE = re.compile(_SECRET_NAME + r"[\"']?[ \t]*[:=]")

_SECRET_KV_RE = re.compile(
    r"(?P<key>" + _SECRET_NAME + r")"
    r"(?P<sep>[\"']?[ \t]*[:=][ \t]*)"
    r"(?:"
    # "quoted value" or 'quoted value' (never empty); an unterminated quote runs
    # to the end of the line, so a message cut off mid-value still hides it.
    # The body is the unrolled form of (?:[^"\\]|\\.)+, one class scan per run.
    r"(?P<dq>\"(?!\")[^\"\\\r\n]*(?:\\.[^\"\\\r\n]*)*(?:\"|(?=[\r\n])|$))"
    r"|(?P<sq>'(?!')[^'\\\r\n]*(?:\\.[^'\\\r\n]*)*(?:'|(?=[\r\n])|$))"
    # Authorization: Bearer <credential>; the scheme stays visible
    r"|(?P<scheme>" + _AUTH_SCHEME + r"[ \t]+)(?![\[{])(?P<cred>[^\s\"'&]+)"
    # a bare value, up to whitespace, a quote or "&". Not a value that is
    # already masked, and not None/null/true/false when that word is the whole
    # value (they say "unset", which is worth reading). Anything else is hidden,
    # including a value that starts with "[" or "{" or with one of those words.
    r"|(?!" + _AUTH_SCHEME + r"[ \t]|\[redacted|(?:none|null|true|false)(?=[\s,;)\]}&\"']|$))"
    r"(?P<raw>[^\s\"'&]+)"
    r")",
    re.IGNORECASE,
)

#: Punctuation that closes a sentence or a bracket rather than ending a secret:
#: ``(token=abc)`` and ``token=abc, user=bob`` keep their punctuation.
_SECRET_TRAILING_PUNCT = ",;.)]}"

# ``Bearer <token>`` anywhere, with or without a key in front of it: everything
# up to the next whitespace, quote, comma or semicolon is hidden, "=" included.
# This errs toward hiding: ``Bearer realm="api"`` and ``token_type=Bearer
# expires_in=3600`` lose the word after "Bearer", which is harmless, while a
# token is never left in clear because of its shape.
_BEARER_RE = re.compile(r"\b(bearer)([ \t]+)(?!\[redacted)[^\s\"',;]+", re.IGNORECASE)

# A JWT: three base64url segments, the first a JSON header (``{"`` encodes to
# ``eyJ``). An unsecured token has an empty signature. The lookbehind stops a
# run of ``eyJeyJeyJ...`` being retried at every offset.
_JWT_RE = re.compile(r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]*\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")

# Stripe secret and restricted keys and webhook signing secrets. The prefix
# stays, so a live key can still be told from a test key in the log.
_STRIPE_RE = re.compile(r"(?<![A-Za-z0-9])((?:sk|rk)_(?:live|test)_|whsec_)[A-Za-z0-9_\-+/=]+")

# Cheap gate for the PII pass. Nothing below can match without an "@", a
# "+<digit>", or two adjacent digits — which rules out the overwhelming
# majority of log lines (`"turn_complete call=%s turn_id=%d latency_ms=%.1f"`
# included) in a single C-level scan, so the real pattern never runs on them.
_QUICK_SCAN_RE = re.compile(r"@|\+\d|\d\d")


def _uk_rest(close: str) -> str:
    """Everything after ``0[1-9]`` in a UK national number.

    The rest of the area code, ``close`` (the bracket after it), then the
    subscriber number: 3-4-4, 4-3-4 and 5-3-3 digit layouts, 10 or 11 digits.
    """
    return (
        r"(?:\d" + close + r"[ \-]?\d{4}[ \-]?\d{4}"  # 020 7946 0958
        r"|\d{2}" + close + r"[ \-]?\d{3}[ \-]?\d{3,4}"  # 0161 496 0000, 0800 123 4567
        r"|\d{3}" + close + r"[ \-]?\d{3}[ \-]?\d{2,3})"  # 07700 900 123, 01632 960983
        r"(?!\d|\.\d)"
    )


# ONE combined pattern, one pass per string (four separate .sub() calls cost
# four full scans of every log line — measurably worse on the voice hot path).
#
# The digit rules are deliberately NOT a generic "digits and separators"
# pattern: that eats timestamps ("2026-07-29 12"), which would destroy log
# debuggability. Each alternative is anchored on a shape real phone numbers,
# cards and account numbers actually take.
_PII_RE = re.compile(
    # email — local part is dropped, domain kept. The lookbehind is for speed:
    # without it every offset inside a long run of local-part characters (a
    # base64 blob, 100 KB of digits) rescans the whole run looking for an "@",
    # which is quadratic (54 s for 100 KB of zeros). An address cannot start 64
    # characters deep into a run, because a local part is at most 64 long; that
    # would be the tail of one already matched from the start of the run.
    r"(?P<email>(?<![A-Za-z0-9._%+\-]{64})[A-Za-z0-9._%+\-]+@"
    r"(?P<domain>[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}))"
    # international / E.164-ish number
    r"|(?P<intl>\+\d[\d ().\-]{6,18}\d)"
    # US SSN shape (078-05-1120). 3-2-4 is unambiguous — an ISO date is 4-2-2.
    r"|(?P<ssn>(?<![\d.])\d{3}-\d{2}-\d{4}(?![\d.]))"
    # grouped runs: 4111 1111 1111 1111 (cards), 555 123 4567 (phones),
    # and digit-by-digit dictation ("4 1 1 1 ...")
    r"|(?P<grouped>(?<![\d.])(?:\d{4}(?:[ \-]\d{4}){2,4}"
    r"|\d{3}[ \-]\d{3}[ \-]\d{3,6}"
    r"|(?:\d[ \-]){9,}\d)(?![\d.]))"
    # bare runs of 11+ digits. 10 would swallow unix-second timestamps; 11
    # keeps cards (13-19), IBANs and long account numbers while leaving
    # ordinary counters, ports and millisecond durations alone.
    r"|(?P<longrun>(?<![\d.])\d{11,24}(?![\d.]))"
    # UK national format: a leading 0, an area code of 3-5 digits and the
    # subscriber number, 10 or 11 digits in all, grouped as they are printed
    # (020 7946 0958, 0161 496 0000, 01632 960983, 07700 900123), with or
    # without the separators, and with the area code bracketed or not. Fixed
    # layouts only, so a date, a duration or a counter cannot match. A trailing
    # "." ends a sentence and is allowed; ".5" would make it a decimal.
    #
    # Both alternatives open with a plain literal, which sre skips without
    # entering the alternative; opening with the capture group instead cost
    # about 10 us on every digit-bearing line (the lookbehind after the "0" is
    # the "not preceded by a digit or dot" check).
    r"|0(?<![\d.]0)(?P<uk>[1-9]" + _uk_rest(r"\)?") + r")"
    r"|\(0(?P<ukp>[1-9]" + _uk_rest(r"\)") + r")"
)

# printf-style conversion specifier, so `transcript=%r` can be mapped back to
# the positional argument that carries the transcript.
_CONV_SPEC_RE = re.compile(
    r"%(?:\((?P<mapping>[^)]*)\))?"
    r"[-+ #0]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?"
    r"(?P<conv>[diouxXeEfFgGcrsa%])"
)
_NAME_BEFORE_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*['\"]?$")

# Field names whose value is a hash / hex blob / opaque identifier — never a
# phone number, card or account number. A digit run sitting immediately after
# one of these is left alone.
#
# WHY (observed on the first live call after this shipped, 2026-07-29):
#
#     TTS_FMT_DEBUG ... first_bytes=2048 head=fcfffdff03000000   <- survived
#     TTS_FMT_DEBUG ... first_bytes=2048 head=[redacted digits=16 ..00]
#
# The same debug field was redacted or not depending on whether that call's
# audio header happened to contain a hex letter. All-digit hex ("0000000000000000")
# is indistinguishable from a 16-digit card to a pattern matcher, so the
# `longrun` rule ate it — destroying the one field that tells us whether the
# gateway is being handed the audio format it expects.
#
# NOTE THE DIRECTION OF THE RISK, which is the opposite of
# SENSITIVE_FIELD_NAMES above and is why enumerating names is acceptable here:
# forgetting a name in THAT set leaks PII; forgetting one in THIS set merely
# over-redacts a debug field. This list can only ever cost debuggability, never
# privacy — so it stays deliberately short, and a name belongs here only if its
# value is structurally incapable of being a real-world identifier.
_NON_PII_VALUE_FIELDS = frozenset({
    "checksum",
    "crc",
    "digest",
    "etag",
    "fingerprint",
    "hash",
    "head",
    "hex",
    "md5",
    "sha",
    "sha1",
    "sha256",
    "sig",
    "signature",
    "tail",
})

_NON_PII_CONTEXT_RE = re.compile(
    r"(?i)\b(?P<name>" + "|".join(sorted(_NON_PII_VALUE_FIELDS)) + r")\s*[=:]\s*['\"]?$"
)

#: How far back from a match the field name is looked for. The longest name is
#: 11 characters, so name + "=" + quote + some spaces fits with room to spare.
#: Searching the whole prefix of the line for every match made a line with many
#: matches quadratic: 100 KB of phone numbers took minutes.
_NON_PII_CONTEXT_WINDOW = 32

# Runtime profiles are JSON strings, and a SHA256 may contain a phone-shaped
# digit run in its middle. Exempt only complete 64-hex values in these exact
# provenance fields; a phone/email under the same key is still redacted.
_PROFILE_DIGEST_RE = re.compile(
    r'''(?<![\w])["']?(?:instructions_sha256|knowledge_block_sha256|'''
    r'''knowledge_versions_sha256|knowledge_snapshot_checksum|snapshot_sha256|'''
    r'''request_envelope_sha256|sources_sha256)["']?\s*[=:]\s*["']?'''
    r'''(?P<digest>[a-fA-F0-9]{64})(?![a-zA-Z0-9])'''
)

# Request ids, call ids and every other UUID are opaque identifiers, never a
# phone or card number, yet about 1 in 280 random UUIDs ends in an all-digit
# group, which `longrun` ate (observed in the unit suite, 2026-11-10):
#
#     HTTP operation failed request_id=7a08427a-4d5f-4aa2-b54f-[redacted digits=12 ..11]
#
# so the "Request reference" a customer reads no longer matched the server
# log. A digit run wholly inside a complete 8-4-4-4-12 hex token is left
# alone; one that only borders a UUID, or sits in a partial one, is masked.
_UUID_RE = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)


def _inside_uuid(match: "re.Match[str]") -> bool:
    text, start, end = match.string, match.start(), match.end()
    lo, hi = max(0, start - 36), min(len(text), end + 36)
    if text.count("-", lo, hi) < 4:  # a UUID has four dashes; most matches have none nearby
        return False
    for uuid in _UUID_RE.finditer(text, lo, hi):
        if uuid.start() <= start and end <= uuid.end():
            return True
    return False


#: Values that cannot carry speech — never rewritten, even in a sensitive slot.
_NON_TEXT_TYPES = (bool, int, float, complex, type(None))


# --- configuration ---------------------------------------------------------

_FALSEY = {"0", "false", "no", "off", "disable", "disabled"}

_enabled: Optional[bool] = None
_keep_chars: Optional[int] = None


def _enabled_from_env() -> bool:
    """Default ON everywhere (production included) — fail safe."""
    raw = (os.getenv(ENV_ENABLED) or "").strip().lower()
    if not raw:
        return True
    return raw not in _FALSEY


def _keep_chars_from_env() -> int:
    try:
        value = int((os.getenv(ENV_KEEP_CHARS) or "0").strip() or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, 24))


def pii_redaction_enabled() -> bool:
    """Whether PII redaction is active (env-driven, cached after first read)."""
    global _enabled
    if _enabled is None:
        _enabled = _enabled_from_env()
    return _enabled


def set_pii_redaction_enabled(value: Optional[bool]) -> None:
    """Override the flag (tests / runtime toggle). ``None`` re-reads the env."""
    global _enabled, _keep_chars
    _enabled = None if value is None else bool(value)
    if value is None:
        _keep_chars = None


def _keep_prefix_chars() -> int:
    global _keep_chars
    if _keep_chars is None:
        _keep_chars = _keep_chars_from_env()
    return _keep_chars


# --- value-level redaction -------------------------------------------------

def _mask_match(match: "re.Match[str]") -> str:
    domain = match.group("domain")
    if domain is not None:
        # Local part dropped; domain kept so an operator can still see that
        # STT heard the right provider without learning who the caller is.
        return f"***@{domain}"
    raw = match.group(0)

    # Precision guard: a bare digit run directly after a hash/hex field name is
    # a debug value, not an identifier. Restricted to the rules that can
    # actually collide with hex — `grouped`, `longrun` and `uk` (a run of
    # digits that happens to be laid out like a UK number). `email` is handled
    # above and `intl` requires a leading "+", neither of which appears in a
    # hex dump, and `ssn` is a 3-2-4 shape that hex never takes. See
    # _NON_PII_VALUE_FIELDS for why a miss here is harmless.
    if match.lastgroup in ("grouped", "longrun") or (
        match.group("grouped") is not None
        or match.group("longrun") is not None
        or match.group("uk") is not None
        or match.group("ukp") is not None
    ):
        text, start = match.string, match.start()
        lo = max(0, start - _NON_PII_CONTEXT_WINDOW)
        # The field name must be followed by "=" or ":", so most matches never
        # need the regex.
        after_hash_field = (
            (text.find("=", lo, start) >= 0 or text.find(":", lo, start) >= 0)
            and _NON_PII_CONTEXT_RE.search(text, lo, start)
        )
        if after_hash_field or _inside_uuid(match):
            return raw

    digits = [c for c in raw if c.isdigit()]
    # Last two digits kept: enough to correlate "same number repeated" across
    # turns, far short of anything usable.
    return f"[redacted digits={len(digits)} ..{''.join(digits[-2:])}]"


def _mask_secret_kv(match: "re.Match[str]") -> str:
    head = match.group("key") + match.group("sep")
    if match.group("dq") is not None:
        return f'{head}"{REDACTED_SECRET}"'
    if match.group("sq") is not None:
        return f"{head}'{REDACTED_SECRET}'"
    value = match.group("cred") or match.group("raw")
    core = value.rstrip(_SECRET_TRAILING_PUNCT)
    if not core:
        return match.group(0)  # nothing but punctuation: no value to hide
    return f"{head}{match.group('scheme') or ''}{REDACTED_SECRET}{value[len(core):]}"


def _scrub_secrets(value: str) -> str:
    # Each rule runs only if its trigger is in the text. ``low`` is taken once,
    # before any rule rewrites ``value``; a rule can only remove text, so a stale
    # ``low`` makes a later rule run needlessly, never skip.
    low = value.lower()
    # Key/value first: it understands ``Authorization: Bearer <token>`` as a
    # pair and keeps the scheme. The shape rules then catch the same tokens
    # when there is no key in front of them.
    if _SECRET_KEY_HINT_RE.search(low):
        value = _SECRET_KV_RE.sub(_mask_secret_kv, value)
    if "bearer" in low:
        value = _BEARER_RE.sub(r"\g<1>\g<2>" + REDACTED_SECRET, value)
    if "eyj" in low:
        value = _JWT_RE.sub(REDACTED_SECRET, value)
    if "_live_" in low or "_test_" in low or "whsec_" in low:
        value = _STRIPE_RE.sub(r"\g<1>" + REDACTED_SECRET, value)
    return value


def scrub_text(value: str) -> str:
    """Mask credentials / emails / phone numbers / long digit runs in free text.

    Everything that is not one of those shapes is returned untouched, so
    ordinary log lines keep their debuggability.
    """
    if not value:
        return value
    value = _scrub_secrets(value)
    if not _QUICK_SCAN_RE.search(value):
        return value
    pieces = []
    end = 0
    for digest in _PROFILE_DIGEST_RE.finditer(value):
        start, stop = digest.span("digest")
        pieces.append(_PII_RE.sub(_mask_match, value[end:start]))
        pieces.append(value[start:stop])
        end = stop
    pieces.append(_PII_RE.sub(_mask_match, value[end:]))
    return "".join(pieces)


_REFERENCE_LETTERS = "abcdefghjkmnpqrstuvwxyz"  # no i, l, o: easy to read back


def error_reference() -> str:
    """A short reference a user can quote for an error logged in full.

    Letters only. A hex id with 11+ digits in a row (about 1 in 140 of them)
    is a `longrun` above and gets masked, so the logged reference no longer
    matched the one the user was shown (CI flake, 12 Nov 2026). 12 letters
    from 23 is about 54 bits.
    """
    return "".join(secrets.choice(_REFERENCE_LETTERS) for _ in range(12))


def summarize_sensitive(value: str) -> str:
    """Replace caller speech with length + a stable short hash.

    The hash lets an operator correlate the same utterance across log lines
    (and spot a repeated/grown utterance) without the content ever being
    written down. ``LOG_REDACT_PII_KEEP=N`` additionally keeps the first N
    characters, themselves pattern-scrubbed.
    """
    if not value:
        return value
    if value.startswith(REDACTED_PREFIX):
        return value  # already redacted — idempotent
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]
    keep = _keep_prefix_chars()
    head = ""
    if keep:
        head = scrub_text(value[:keep])
    return f"[redacted chars={len(value)} sha={digest}{(' head=' + head) if head else ''}]"


def _redact_value(value: str, sensitive: bool) -> str:
    if not value or value.startswith(REDACTED_PREFIX):
        return value
    return summarize_sensitive(value) if sensitive else scrub_text(value)


@lru_cache(maxsize=1024)
def _sensitive_arg_slots(msg: str) -> "tuple[frozenset, frozenset]":
    """Map ``name=%r`` placeholders in a format string to argument slots.

    Returns ``(positional_indexes, mapping_keys)`` for placeholders whose
    preceding ``name=`` is a known sensitive field. Cached — format strings
    are constants, so this parse happens once per distinct message.
    """
    positional: set = set()
    mapping: set = set()
    index = 0
    for match in _CONV_SPEC_RE.finditer(msg):
        if match.group("conv") == "%":
            continue
        key = match.group("mapping")
        if key is not None:
            if key.lower() in SENSITIVE_FIELD_NAMES:
                mapping.add(key)
            continue
        # Only string-ish conversions: rewriting the argument of a %d/%f slot
        # would turn a working log statement into a formatting error.
        if match.group("conv") in ("s", "r", "a"):
            preceding = _NAME_BEFORE_RE.search(msg, 0, match.start())
            if preceding and preceding.group("name").lower() in SENSITIVE_FIELD_NAMES:
                positional.add(index)
        index += 1
    return frozenset(positional), frozenset(mapping)


# Arguments whose rendered text can carry personal data: an exception's message
# (a database error quotes the offending value) and a container's repr.
_RENDERED_AS_TEXT = (BaseException, dict, list, tuple, set, frozenset)


def _scrub_rendered(value: Any) -> Any:
    """The masked text of ``value`` when masking changes it, else ``value``
    itself, so a line with nothing to hide renders exactly as before."""
    text = str(value)
    scrubbed = scrub_text(text)
    return value if scrubbed == text else scrubbed


def _redact_record_inplace(record: logging.LogRecord) -> None:
    positional: frozenset = frozenset()
    mapping: frozenset = frozenset()

    msg = record.msg
    if isinstance(msg, str) and msg:
        if record.args:
            # A %-style format string is a source literal: the data lives in
            # the args, never in the template. Leave the template alone —
            # rewriting it could damage a placeholder (``"%s@example.com"``
            # reads as an email address) and break the substitution.
            positional, mapping = _sensitive_arg_slots(msg)
        else:
            # No args → an f-string / pre-formatted message, which carries its
            # data inline. This is the one that needs scrubbing.
            record.msg = scrub_text(msg)

    args: Any = record.args
    if isinstance(args, dict):
        redacted_map = {}
        changed = False
        for key, value in args.items():
            if isinstance(value, str):
                new_value = _redact_value(
                    value, key in mapping or str(key).lower() in SENSITIVE_FIELD_NAMES
                )
                changed = changed or new_value is not value
                redacted_map[key] = new_value
            elif isinstance(value, _RENDERED_AS_TEXT):
                new_value = _scrub_rendered(value)
                changed = changed or new_value is not value
                redacted_map[key] = new_value
            else:
                redacted_map[key] = value
        if changed:
            record.args = redacted_map
    elif isinstance(args, tuple) and args:
        redacted_args = list(args)
        changed = False
        for i, value in enumerate(args):
            if isinstance(value, str):
                new_value = _redact_value(value, i in positional)
            elif i in positional and not isinstance(value, _NON_TEXT_TYPES):
                # `transcript=%r` handed something that is not a str (a Message,
                # a list of turns, ...) — its repr would leak just the same.
                # Numbers/bools/None can't carry speech, and mangling them
                # (``partial=%s`` -> ``partial=[redacted...]``) is pure damage.
                new_value = summarize_sensitive(str(value))
            elif isinstance(value, _RENDERED_AS_TEXT):
                new_value = _scrub_rendered(value)
            else:
                continue
            if new_value is not value:
                redacted_args[i] = new_value
                changed = True
        if changed:
            record.args = tuple(redacted_args)

    # The handler's Formatter renders a traceback from exc_info after this
    # filter ran. Render it here once and keep the masked text, which the
    # standard Formatter reuses instead of rendering it again (QA F2-10).
    exc_info = record.exc_info
    if exc_info and exc_info[0] is not None and not record.exc_text:
        text = logging.Formatter().formatException(exc_info)
        scrubbed = scrub_text(text)
        if scrubbed != text:
            record.exc_text = scrubbed

    # extra={...} fields — where the full untruncated utterance lives.
    record_dict = record.__dict__
    for key, value in list(record_dict.items()):
        if key in _STANDARD_RECORD_ATTRS:
            continue
        sensitive = key.lower() in SENSITIVE_FIELD_NAMES
        if isinstance(value, str):
            record_dict[key] = _redact_value(value, sensitive)
        elif sensitive and not isinstance(value, _NON_TEXT_TYPES):
            # extra={"transcript": <Message>} / a list of turns — the handler
            # renders its repr, so it leaks exactly the same.
            record_dict[key] = summarize_sensitive(str(value))


def _panic_scrub(record: logging.LogRecord) -> None:
    """Last resort: the redactor raised, so nothing may be trusted.

    Drop the message and arguments entirely and blank every non-standard
    attribute (whatever its type — a dict or object extra leaks through its
    repr just as a string does). Never raises.
    """
    try:
        record.msg = REDACTION_FAILED
        record.args = None
        record_dict = record.__dict__
        for key in list(record_dict):
            if key in _STANDARD_RECORD_ATTRS:
                continue
            record_dict[key] = REDACTION_FAILED
    except Exception:  # pragma: no cover - defensive
        pass


def redact_log_record(record: logging.LogRecord) -> logging.LogRecord:
    """Redact caller PII from ``record`` in place. Never raises."""
    try:
        if not pii_redaction_enabled():
            return record
        _redact_record_inplace(record)
    except Exception:
        _panic_scrub(record)
    return record


class PIIRedactionFilter(logging.Filter):
    """``logging.Filter`` form, for declarative ``dictConfig`` wiring.

    Redundant when :func:`install_pii_log_redaction` is active — redaction is
    idempotent, so running both is safe.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        redact_log_record(record)
        return True


# --- global install --------------------------------------------------------

def install_pii_log_redaction() -> bool:
    """Install the redactor process-wide. Idempotent; never raises.

    Returns True if this call performed the install, False if it was already
    installed (or the install failed and logging was left untouched).
    """
    try:
        if getattr(logging.Logger.makeRecord, "_pii_redacting", False):
            return False
        original = logging.Logger.makeRecord

        def makeRecord(self, *args, **kwargs):  # noqa: N802 - stdlib name
            return redact_log_record(original(self, *args, **kwargs))

        makeRecord._pii_redacting = True  # type: ignore[attr-defined]
        makeRecord.__wrapped__ = original  # type: ignore[attr-defined]
        logging.Logger.makeRecord = makeRecord  # type: ignore[assignment]
        return True
    except Exception:  # pragma: no cover - logging must never break startup
        return False


def uninstall_pii_log_redaction() -> bool:
    """Restore the stock ``makeRecord`` (tests / emergency toggle)."""
    try:
        current = logging.Logger.makeRecord
        if not getattr(current, "_pii_redacting", False):
            return False
        logging.Logger.makeRecord = current.__wrapped__  # type: ignore[assignment]
        return True
    except Exception:  # pragma: no cover - defensive
        return False

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
  and last two digits (``[redacted digits=16 ..11]``).
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

# Cheap gate. Nothing below can match without an "@", a "+<digit>", or two
# adjacent digits — which rules out the overwhelming majority of log lines
# (`"turn_complete call=%s turn_id=%d latency_ms=%.1f"` included) in a single
# C-level scan, so the real pattern never runs on them.
_QUICK_SCAN_RE = re.compile(r"@|\+\d|\d\d")

# ONE combined pattern, one pass per string (four separate .sub() calls cost
# four full scans of every log line — measurably worse on the voice hot path).
#
# The digit rules are deliberately NOT a generic "digits and separators"
# pattern: that eats timestamps ("2026-07-29 12"), which would destroy log
# debuggability. Each alternative is anchored on a shape real phone numbers,
# cards and account numbers actually take.
_PII_RE = re.compile(
    # email — local part is dropped, domain kept
    r"(?P<email>[A-Za-z0-9._%+\-]+@(?P<domain>[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}))"
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
    # a debug value, not an identifier. Restricted to the two rules that can
    # actually collide with hex — `grouped` and `longrun`. `email` is handled
    # above and `intl` requires a leading "+", neither of which appears in a
    # hex dump, and `ssn` is a 3-2-4 shape that hex never takes. See
    # _NON_PII_VALUE_FIELDS for why a miss here is harmless.
    if match.lastgroup in ("grouped", "longrun") or (
        match.group("grouped") is not None or match.group("longrun") is not None
    ):
        if _NON_PII_CONTEXT_RE.search(match.string, 0, match.start()):
            return raw

    digits = [c for c in raw if c.isdigit()]
    # Last two digits kept: enough to correlate "same number repeated" across
    # turns, far short of anything usable.
    return f"[redacted digits={len(digits)} ..{''.join(digits[-2:])}]"


def scrub_text(value: str) -> str:
    """Mask emails / phone numbers / long digit runs inside free text.

    Everything that is not one of those shapes is returned untouched, so
    ordinary log lines keep their debuggability.
    """
    if not value or not _QUICK_SCAN_RE.search(value):
        return value
    return _PII_RE.sub(_mask_match, value)


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
            else:
                continue
            if new_value is not value:
                redacted_args[i] = new_value
                changed = True
        if changed:
            record.args = tuple(redacted_args)

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

"""Strip customer data from error reports before they leave the process.

Wired into ``sentry_sdk.init`` as ``before_send``, ``before_send_transaction``
and ``before_breadcrumb`` (see ``sentry_init``). Pure module: it imports no
Sentry code, never logs, and never raises.

What it does
------------
1. Values under a key that names a secret or free speech become
   ``"[redacted]"`` (see ``_SENSITIVE_TERMS``).
2. Every string anywhere in the event is pattern-scrubbed: emails, phone
   numbers, bearer tokens / JWTs, provider-style keys, URL credentials, URL
   query values, and quoted free speech after ``text=`` style markers.
3. ``request.cookies``, ``request.data`` and ``request.env`` are removed,
   ``request.headers`` keeps a small allow-list, ``user`` keeps only ``id``.
4. Recursion depth, container sizes and string length are capped. If
   scrubbing fails for any reason a minimal event is built from scratch; the
   original event is never returned. A breadcrumb that cannot be scrubbed is
   dropped.
"""
from __future__ import annotations

import datetime as _dt
import itertools
import re
from collections.abc import Mapping
from typing import Any, Optional

MAX_DEPTH = 10
MAX_ITEMS = 200
MAX_STR = 2000

_REDACTED = "[redacted]"
_WITHHELD = "event withheld: scrubbing failed"

# ---------------------------------------------------------------------------
# Key names
# ---------------------------------------------------------------------------
# Matched on the whole key or as a word run inside it, after camelCase and
# punctuation are normalised to underscores (``x-api-key`` -> ``x_api_key``).
_SENSITIVE_TERMS = (
    "password", "passwords", "passwd", "secret", "secrets", "token", "tokens",
    "authorization", "cookie", "cookies", "set_cookie", "api_key", "apikey",
    "dsn", "signature", "credential", "credentials", "session_id", "sessionid",
    "otp",
    "transcript", "transcripts", "utterance", "utterances", "user_text",
    "assistant_text", "prompt", "prompts", "system_prompt", "messages",
    "content", "body", "recording_url",
    # SQL text can carry literal customer values (span db.statement).
    "statement",
)
# Keys that contain a sensitive word but are plain metadata.
_SAFE_KEYS = frozenset({
    "content_type", "content_length", "content_encoding", "content_disposition",
})
_NUMERIC_OK_TERMS = frozenset({"token", "tokens"})  # max_tokens, input_tokens

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise_key(key: Any) -> str:
    k = _CAMEL.sub("_", str(key)[:200]).lower()
    return _NON_ALNUM.sub("_", k).strip("_")


def _sensitive_key_term(key: Any) -> Optional[str]:
    k = _normalise_key(key)
    if not k or k in _SAFE_KEYS:
        return None
    padded = f"_{k}_"
    for term in _SENSITIVE_TERMS:
        if f"_{term}_" in padded:
            return term
    return None


def is_sensitive_key(key: Any) -> bool:
    return _sensitive_key_term(key) is not None


# ---------------------------------------------------------------------------
# String patterns (compiled once)
# ---------------------------------------------------------------------------
_MARKERS = "user_text|assistant_text|transcript|utterance|text|said|reply"
_SPEECH = re.compile(
    rf"(?<![A-Za-z0-9])({_MARKERS})="
    r"(?:'(?:[^'\\]|\\.)*(?:'|$)|\"(?:[^\"\\]|\\.)*(?:\"|$))"
)
# Unquoted value: up to the next " word=" token or the end of the string. A
# leading "%" is a logging placeholder in a template, not speech.
_SPEECH_UNQUOTED = re.compile(
    rf"(?<![A-Za-z0-9])({_MARKERS})=(?![\s%'\"]|\[redacted\])[\s\S]*?(?= [A-Za-z_][A-Za-z0-9_]*=|$)"
)
# text just before a placeholder ends with "marker=" (optionally "marker='")
_AFTER_MARKER = re.compile(rf"(?<![A-Za-z0-9])(?:{_MARKERS})=['\"]?$")
_PLACEHOLDER = re.compile(
    r"%(?:\((?P<name>[^)]*)\))?[#0\- +]*(?P<w>\*|\d+)?(?:\.(?P<p>\*|\d+))?[hlL]?(?P<conv>[a-zA-Z%])"
)
_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s/?#@]+@")
_URL_QUERY = re.compile(r"([?&])([A-Za-z0-9_.\-\[\]%]+)=([^&#\s'\"]*)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")
_BEARER = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=\-]{6,}", re.IGNORECASE)
_KEYS = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?:sk|pk|rk)[-_](?:live_|test_|proj-)?[A-Za-z0-9_\-]{12,}"
    r"|whsec_[A-Za-z0-9_\-]{12,}"
    r"|xox[abprs]-[A-Za-z0-9\-]{12,}"
    r"|gh[pousr]_[A-Za-z0-9_]{16,}"
    r"|github_pat_[A-Za-z0-9_]{16,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_\-]{30,}"
    r")"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ISO = (
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
_IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
# keep-alternatives are consumed first so their digits are never read as a phone
_PHONE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    rf"(?P<keep>{_UUID}|{_ISO}|{_IPV4})(?![A-Za-z0-9_])"
    r"|(?P<plus>\+\(?\d(?:[ \-.()]{0,2}\d){6,14})(?!\d)"
    r"|(?P<run>\(?\d(?:[ \-.()]{0,2}\d){8,})(?![A-Za-z0-9_])"
    r")"
)


def _phone_sub(m: "re.Match[str]") -> str:
    return m.group(0) if m.group("keep") else "[phone]"


def _query_sub(m: "re.Match[str]") -> str:
    key, value = m.group(2), m.group(3)
    if is_sensitive_key(key):
        return f"{m.group(1)}{key}={_REDACTED}"
    return f"{m.group(1)}{key}={_scrub_plain(value)}"


def _scrub_plain(s: str) -> str:
    s = _JWT.sub("[token]", s)
    s = _BEARER.sub("[token]", s)
    s = _KEYS.sub("[key]", s)
    s = _EMAIL.sub("[email]", s)
    return _PHONE.sub(_phone_sub, s)


def scrub_string(s: str) -> str:
    if len(s) > MAX_STR:
        s = s[:MAX_STR]
    s = _SPEECH.sub(lambda m: f"{m.group(1)}={_REDACTED}", s)
    s = _SPEECH_UNQUOTED.sub(lambda m: f"{m.group(1)}={_REDACTED}", s)
    s = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{_REDACTED}@", s)
    s = _URL_QUERY.sub(_query_sub, s)
    return _scrub_plain(s)


# ---------------------------------------------------------------------------
# Recursive walk
# ---------------------------------------------------------------------------
def _walk(value: Any, depth: int) -> Any:
    if isinstance(value, str):
        return scrub_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value
    if depth >= MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, Mapping):
        return _walk_mapping(value, depth)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_walk(v, depth + 1) for v in itertools.islice(value, MAX_ITEMS)]
    if isinstance(value, (bytes, bytearray)):
        return "[bytes]"
    try:
        return scrub_string(str(value))
    except Exception:
        return "[unreadable]"


def _walk_mapping(m: Mapping, depth: int) -> dict:
    out: dict = {}
    for k, v in itertools.islice(m.items(), MAX_ITEMS):
        term = _sensitive_key_term(k)
        if term is not None and not (
            term in _NUMERIC_OK_TERMS
            and isinstance(v, (int, float))
            and not isinstance(v, bool)
        ):
            out[str(k)[:200]] = _REDACTED
        else:
            out[str(k)[:200]] = _walk(v, depth + 1)
    return out


def _redact_strings(value: Any) -> Any:
    """Fallback when placeholders cannot be matched to arguments: every string
    argument is redacted, other values go through the normal walk."""
    if isinstance(value, Mapping):
        return {str(k)[:200]: _redact_strings(v) for k, v in itertools.islice(value.items(), MAX_ITEMS)}
    if isinstance(value, (list, tuple)):
        return [_redact_strings(v) for v in itertools.islice(value, MAX_ITEMS)]
    if isinstance(value, str):
        return _REDACTED
    return _walk(value, 2)


def _scrub_logentry(le: Any) -> Any:
    """The SDK sends the template in ``message`` and the raw arguments in
    ``params``. An argument that fills a placeholder right after a speech
    marker (``text=%r``) is redacted whatever its type; the rest are
    pattern-scrubbed. If the template cannot be matched to the arguments,
    every string argument is redacted."""
    if not isinstance(le, Mapping):
        return _walk(le, 1)
    out: dict = {}
    template = le.get("message")
    for k, v in itertools.islice(le.items(), MAX_ITEMS):
        if k == "params":
            out["params"] = _scrub_params(template, v)
        elif _sensitive_key_term(k) is not None:
            out[str(k)[:200]] = _REDACTED
        else:
            out[str(k)[:200]] = _walk(v, 2)
    return out


def _scrub_params(template: Any, params: Any) -> Any:
    if not isinstance(template, str):
        return _redact_strings(params)
    positional: list = []
    named: list = []
    unparseable = False
    for m in _PLACEHOLDER.finditer(template):
        if m.group("conv") == "%":
            continue
        marker = _AFTER_MARKER.search(template[max(0, m.start() - 40):m.start()]) is not None
        if m.group("w") == "*" or m.group("p") == "*":
            unparseable = True
        if m.group("name") is not None:
            named.append((m.group("name"), marker))
        else:
            positional.append(marker)
    if unparseable or (named and positional):
        return _redact_strings(params)
    if isinstance(params, Mapping):
        if positional or not named and params:
            return _redact_strings(params)
        marked = {n for n, is_marker in named if is_marker}
        out: dict = {}
        for k, v in itertools.islice(params.items(), MAX_ITEMS):
            if str(k) in marked or _sensitive_key_term(k) is not None:
                out[str(k)[:200]] = _REDACTED
            else:
                out[str(k)[:200]] = _walk(v, 2)
        return out
    if isinstance(params, (list, tuple)):
        if named or len(params) != len(positional):
            return _redact_strings(params)
        return [
            _REDACTED if is_marker else _walk(v, 2)
            for is_marker, v in zip(positional, params)
        ]
    return _redact_strings(params)


_HEADER_ALLOW = frozenset({"user-agent", "content-type", "accept", "x-request-id"})


def _scrub_request(req: Any) -> Any:
    if not isinstance(req, Mapping):
        return _walk(req, 1)
    out: dict = {}
    for k, v in itertools.islice(req.items(), MAX_ITEMS):
        lk = str(k).lower()
        if lk in ("cookies", "data", "env"):
            continue
        if lk == "headers":
            if isinstance(v, Mapping):
                out[str(k)] = {
                    str(hk): _walk(hv, 3)
                    for hk, hv in itertools.islice(v.items(), MAX_ITEMS)
                    if str(hk).lower() in _HEADER_ALLOW
                }
            continue
        if lk == "query_string" and isinstance(v, str):
            out[str(k)] = scrub_string("?" + v)[1:]
            continue
        term = _sensitive_key_term(k)
        out[str(k)] = _REDACTED if term is not None else _walk(v, 2)
    return out


def _scrub_user(user: Any) -> dict:
    if isinstance(user, Mapping) and "id" in user:
        return {"id": _walk(user["id"], 2)}
    return {}


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, (int, float, _dt.datetime, _dt.date)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value[:200]
    return None


def _withheld_event(event: Any) -> dict:
    """Minimal event built from scratch; reads only a few scalar fields."""
    out: dict = {}

    def grab(key: str) -> None:
        try:
            v = _safe_scalar(event.get(key))  # type: ignore[union-attr]
        except Exception:
            return
        if v is not None:
            out[key] = v

    for key in ("event_id", "timestamp", "level", "release", "environment"):
        grab(key)
    try:
        names = []
        for item in itertools.islice(event.get("exception", {}).get("values", []), 20):  # type: ignore[union-attr]
            t = item.get("type")
            if isinstance(t, str):
                names.append({"type": t[:200]})
        if names:
            out["exception"] = {"values": names}
    except Exception:
        pass
    out["message"] = _WITHHELD
    return out


def _scrub_event_strict(event: Any) -> dict:
    if not isinstance(event, Mapping):
        raise TypeError("event is not a mapping")
    out: dict = {}
    for k, v in itertools.islice(event.items(), MAX_ITEMS):
        if k == "request":
            out[k] = _scrub_request(v)
        elif k == "user":
            out[k] = _scrub_user(v)
        elif k == "logentry":
            out[k] = _scrub_logentry(v)
        elif _sensitive_key_term(k) is not None:
            out[str(k)[:200]] = _REDACTED
        else:
            out[str(k)[:200]] = _walk(v, 1)
    return out


def scrub_transaction(event: Any, hint: Any = None) -> Optional[dict]:
    """``before_send_transaction`` hook. A transaction that cannot be scrubbed
    is dropped: a minimal error-shaped event would not be a valid one."""
    try:
        return _scrub_event_strict(event)
    except Exception:
        return None


def scrub_event(event: Any, hint: Any = None) -> Optional[dict]:
    """``before_send`` hook. Never raises and never returns the original
    event."""
    try:
        return _scrub_event_strict(event)
    except Exception:
        try:
            return _withheld_event(event)
        except Exception:
            return {"message": _WITHHELD}


def scrub_breadcrumb(crumb: Any, hint: Any = None) -> Optional[dict]:
    """``before_breadcrumb`` hook. A breadcrumb that cannot be scrubbed is
    dropped (``None``)."""
    try:
        if not isinstance(crumb, Mapping):
            return None
        return _walk_mapping(crumb, 0)
    except Exception:
        return None

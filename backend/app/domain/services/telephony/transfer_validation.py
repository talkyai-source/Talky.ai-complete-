"""Strict, reusable validation for PBX transfer command arguments.

Transfer values eventually become whitespace-delimited FreeSWITCH ESL or
Asterisk routing arguments.  Keep the accepted grammar deliberately small and
validate again at every provider boundary; API validation alone is not a
security boundary for internal callers.
"""

from __future__ import annotations

import os
import re
from typing import Final

from app.domain.services.telephony.inbound_router import normalize_did


_CALL_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_CONTEXT_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
_E164_RE: Final = re.compile(r"\+[1-9][0-9]{6,14}\Z", re.ASCII)
_NUMERIC_EXTENSION_RE: Final = re.compile(r"[0-9]{1,32}\Z", re.ASCII)
_FEATURE_CODE_RE: Final = re.compile(r"[*#][0-9*#]{0,31}\Z", re.ASCII)
_NAMED_EXTENSION_RE: Final = re.compile(
    r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z",
    re.ASCII,
)
_PHONE_PRESENTATION_RE: Final = re.compile(r"[+0-9(). -]{7,40}\Z", re.ASCII)
_SIP_URI_RE: Final = re.compile(
    r"sips?:"
    r"[A-Za-z0-9_+.-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"(?::[0-9]{1,5})?"
    r"(?:;[A-Za-z0-9_.-]+(?:=[A-Za-z0-9_.:+-]+)?)*\Z",
    re.ASCII | re.IGNORECASE,
)
_ATTENDED_KEY_RE: Final = re.compile(r"[0-9A-D*#]\Z", re.ASCII)
_IDEMPOTENCY_KEY_RE: Final = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{7,254}\Z",
    re.ASCII,
)
_ESL_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f]")
_MAX_ESL_COMMAND_BYTES: Final = 4096


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required")
    if _ESL_CONTROL_RE.search(value):
        raise ValueError(f"{field} contains a control character")
    if value != value.strip():
        raise ValueError(f"{field} must not contain surrounding whitespace")
    return value


def validate_transfer_call_id(value: object) -> str:
    candidate = _require_string(value, "call_id")
    if _CALL_ID_RE.fullmatch(candidate) is None:
        raise ValueError("call_id contains unsupported characters")
    return candidate


def allowed_transfer_contexts() -> frozenset[str]:
    configured = os.getenv("FREESWITCH_TRANSFER_ALLOWED_CONTEXTS", "")
    allowed = {"default", "public"}
    for raw in configured.split(","):
        candidate = raw.strip()
        if (
            candidate
            and candidate.lower() != "inline"
            and _CONTEXT_RE.fullmatch(candidate) is not None
        ):
            allowed.add(candidate)
    return frozenset(allowed)


def validate_transfer_context(value: object) -> str:
    candidate = _require_string(value, "context")
    if _CONTEXT_RE.fullmatch(candidate) is None:
        raise ValueError("context contains unsupported characters")
    if candidate.lower() == "inline" or candidate not in allowed_transfer_contexts():
        raise ValueError("context is not approved for transfers")
    return candidate


def _canonicalize_dialplan_destination(value: object) -> str:
    candidate = _require_string(value, "destination")
    if any(
        pattern.fullmatch(candidate) is not None
        for pattern in (
            _E164_RE,
            _NUMERIC_EXTENSION_RE,
            _FEATURE_CODE_RE,
            _NAMED_EXTENSION_RE,
        )
    ):
        return candidate

    # Normalize only a grammar made entirely of ordinary phone-presentation
    # characters.  URI schemes, parameters, domains, escapes, and dialplan
    # syntax never enter the normalizer.
    if _PHONE_PRESENTATION_RE.fullmatch(candidate) is not None:
        normalized = normalize_did(candidate)
        if normalized is not None and _E164_RE.fullmatch(normalized) is not None:
            return normalized
    raise ValueError("destination is not an approved transfer target")


def _canonicalize_deflect_destination(value: object) -> str:
    candidate = _require_string(value, "destination")
    if _SIP_URI_RE.fullmatch(candidate) is None:
        raise ValueError("deflect destination must be a strict SIP URI")
    port_match = re.search(r"@[^;]+:([0-9]{1,5})(?:;|\Z)", candidate)
    if port_match is not None and not 1 <= int(port_match.group(1)) <= 65535:
        raise ValueError("deflect destination contains an invalid SIP port")
    return candidate


def canonicalize_transfer_destination(value: object, mode: object) -> str:
    mode_value = getattr(mode, "value", mode)
    if mode_value in {"blind", "attended"}:
        return _canonicalize_dialplan_destination(value)
    if mode_value == "deflect":
        return _canonicalize_deflect_destination(value)
    raise ValueError("unsupported transfer mode")


def validate_any_transfer_destination(value: object) -> str:
    """Validate a payload whose route supplies the authoritative mode."""

    try:
        return _canonicalize_dialplan_destination(value)
    except ValueError:
        return _canonicalize_deflect_destination(value)


def validate_attended_key(value: object, field: str) -> str:
    candidate = _require_string(value, field)
    if _ATTENDED_KEY_RE.fullmatch(candidate) is None:
        raise ValueError(f"{field} must be one DTMF key")
    return candidate


def validate_transfer_idempotency_key(value: object) -> str:
    candidate = _require_string(value, "Idempotency-Key")
    if _IDEMPOTENCY_KEY_RE.fullmatch(candidate) is None:
        raise ValueError(
            "Idempotency-Key must be 8-255 safe ASCII characters"
        )
    return candidate


def validate_esl_command_frame(command: object) -> str:
    candidate = _require_string(command, "ESL command")
    if len(candidate.encode("utf-8")) > _MAX_ESL_COMMAND_BYTES:
        raise ValueError("ESL command is too long")
    return candidate

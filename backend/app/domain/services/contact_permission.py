"""Customer confirmation that they may contact the people they import.

Pure functions, no database. A confirmation is stored on the ``contact_lists``
row an import creates (migration 0096). Endpoints call
``validate_contact_permission`` and turn ``ContactPermissionError`` into a 422.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

CONTACT_PERMISSION_STATEMENT_VERSION = "2026-11-v1"
SYSTEM_CALLBACK_STATEMENT_VERSION = "system-callback-v1"

# Bases a customer may choose. ``pre_existing_unverified`` exists in the
# database CHECK for legacy rows only and is deliberately not offered.
CUSTOMER_PERMISSION_BASES = (
    "express_consent",
    "existing_customer",
    "made_an_enquiry",
    "other_documented",
)

SOURCE_MIN_LENGTH = 5
SOURCE_MAX_LENGTH = 500

CODE_REQUIRED = "contact_permission_required"
CODE_INVALID = "contact_permission_invalid"
REQUIRED_MESSAGE = "Confirm you have permission to call these contacts before adding them."

_OFF_VALUES = {"0", "false", "no", "off"}


class ContactPermissionError(Exception):
    """Missing or invalid confirmation. ``field`` is None when it is missing."""

    def __init__(self, code: str, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message

    def detail(self) -> dict:
        if self.code == CODE_REQUIRED:
            return {"code": self.code, "message": self.message}
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class ContactPermission:
    basis: str
    source: str
    permission_date: date
    statement_version: str


def contact_permission_required() -> bool:
    """On by default; only 0/false/no/off (any case) turn it off. Read per call."""
    return os.environ.get("CONTACT_PERMISSION_REQUIRED", "").strip().lower() not in _OFF_VALUES


def permission_required_error() -> ContactPermissionError:
    return ContactPermissionError(CODE_REQUIRED, REQUIRED_MESSAGE)


def _invalid(field: str, message: str) -> ContactPermissionError:
    return ContactPermissionError(CODE_INVALID, message, field)


def validate_contact_permission(
    raw: Any, *, today: Optional[date] = None,
) -> ContactPermission:
    """Return a validated confirmation or raise ``ContactPermissionError``."""
    if raw is None:
        raise permission_required_error()
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if not isinstance(raw, Mapping):
        raise _invalid("basis", "Send the permission confirmation as an object.")

    basis = raw.get("basis")
    if basis not in CUSTOMER_PERMISSION_BASES:
        raise _invalid("basis", "Choose how you got permission to contact these people.")

    source = raw.get("source")
    trimmed = source.strip() if isinstance(source, str) else ""
    if not (SOURCE_MIN_LENGTH <= len(trimmed) <= SOURCE_MAX_LENGTH):
        raise _invalid(
            "source",
            f"Say where the permission came from, in {SOURCE_MIN_LENGTH} to "
            f"{SOURCE_MAX_LENGTH} characters.",
        )

    raw_date = raw.get("permission_date")
    parsed: Optional[date] = None
    if isinstance(raw_date, datetime):
        parsed = None
    elif isinstance(raw_date, date):
        parsed = raw_date
    elif isinstance(raw_date, str) and len(raw_date.strip()) == 10:
        try:
            parsed = date.fromisoformat(raw_date.strip())
        except ValueError:
            parsed = None
    if parsed is None:
        raise _invalid("permission_date", "Enter the date permission was given as YYYY-MM-DD.")
    limit = today or datetime.now(timezone.utc).date()
    if parsed > limit:
        raise _invalid("permission_date", "The permission date cannot be in the future.")

    if raw.get("statement_version") != CONTACT_PERMISSION_STATEMENT_VERSION:
        raise _invalid(
            "statement_version",
            "The confirmation wording is out of date; reload the page and confirm again.",
        )

    return ContactPermission(
        basis=basis,
        source=trimmed,
        permission_date=parsed,
        statement_version=CONTACT_PERMISSION_STATEMENT_VERSION,
    )

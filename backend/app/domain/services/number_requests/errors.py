"""Error types for number requests.

None of these carry a file name or file content in their message.
"""
from __future__ import annotations

import re
from typing import Any

from app.domain.services.number_requests.config import KIND_LABELS, max_file_bytes

REUSED_KEY_MESSAGE = "Your files have changed since your last try. Please press Submit again."
INVALID_KEY_MESSAGE = "Your request could not be sent. Please try again."
UNREADABLE_FORM_MESSAGE = "Your documents could not be read. Please choose the files again and press Submit."
TOO_MANY_PARTS_MESSAGE = "Please send only the two documents for this request."
NOT_A_FILE_MESSAGE = "Please choose a file for {label}."


class NumberRequestRefused(Exception):
    """A refusal the customer can act on. The router maps it to an HTTP error."""

    def __init__(self, status: int, code: str, message: str, *, field: str | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.extra = dict(extra or {})


class StorageUnavailable(Exception):
    """The store could not write; nothing was kept. The router answers 503."""


class SaveUnconfirmed(Exception):
    """The save may or may not have committed. The router answers 503 save_unconfirmed."""


class StoragePathError(ValueError):
    """A storage key or path that leaves the documents root, or is malformed."""


class DocumentUnreadable(Exception):
    """A stored file failed its header or authentication check."""


class DocumentFileMissing(Exception):
    """The row exists but the file is not on disk."""


def label(kind: str) -> str:
    return KIND_LABELS.get(kind, "This document")


def clamp_field_name(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "", raw)[:64]


def invalid_form(message: str = UNREADABLE_FORM_MESSAGE) -> NumberRequestRefused:
    return NumberRequestRefused(422, "invalid_form", message, field="body")


def invalid_use_type() -> NumberRequestRefused:
    return NumberRequestRefused(422, "invalid_use_type", "Choose business use or personal use.",
                                field="use_type")


def missing_document(kind: str) -> NumberRequestRefused:
    return NumberRequestRefused(422, "missing_document", f"Add {label(kind)}.", field=kind)


def unexpected_file(kind: str | None, use_type: str | None) -> NumberRequestRefused:
    if kind in KIND_LABELS and use_type:
        message = f"{label(kind)} is not part of {use_type} use."
    else:
        message = "Remove the extra file."
    return NumberRequestRefused(422, "unexpected_file", message, field=clamp_field_name(kind or ""))


def too_many_open_requests(limit: int) -> NumberRequestRefused:
    return NumberRequestRefused(
        429, "too_many_open_requests",
        "Your earlier requests are still waiting for review. Please wait for a decision before sending more documents.",
        extra={"max_open_requests": limit})


def not_a_file(kind: str) -> NumberRequestRefused:
    return NumberRequestRefused(422, "invalid_form", NOT_A_FILE_MESSAGE.format(label=label(kind)), field=kind)


def duplicate_document(kind: str) -> NumberRequestRefused:
    return NumberRequestRefused(422, "duplicate_document", f"Send {label(kind)} once.", field=kind)


def empty_file(kind: str) -> NumberRequestRefused:
    return NumberRequestRefused(422, "empty_file", f"{label(kind)} is empty.", field=kind)


def file_too_large(kind: str, limit: int | None = None) -> NumberRequestRefused:
    limit = max_file_bytes() if limit is None else limit
    return NumberRequestRefused(413, "file_too_large", f"{label(kind)} is larger than {limit // 1048576} MB.",
                                field=kind, extra={"max_file_bytes": limit})


def unsupported_file_type(kind: str, accepted_types: list[str]) -> NumberRequestRefused:
    return NumberRequestRefused(415, "unsupported_file_type", f"{label(kind)} must be a PDF, JPG or PNG file.",
                                field=kind, extra={"accepted_types": list(accepted_types)})

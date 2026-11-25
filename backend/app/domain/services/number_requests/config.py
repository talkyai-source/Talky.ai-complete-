"""Every owner decision for number requests, in one place.

Constants are decisions; functions read environment variables when called.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import timedelta

# Decision 1 (A): any signed-in member of the account may submit.
# Option B would be frozenset({"tenant_admin"}).
SUBMIT_ROLES: frozenset[str] | None = None


@dataclass(frozen=True)
class AcceptedType:
    content_type: str
    extensions: tuple[str, ...]
    download_ext: str
    magic: tuple[bytes, ...]
    offset: int = 0


# Decision 2 (A): PDF, JPG, PNG. HEIC (B) would be one more entry with offset 4
# and magic (b"ftypheic", b"ftypheix", b"ftypmif1").
ACCEPTED_TYPES: tuple[AcceptedType, ...] = (
    AcceptedType("application/pdf", (".pdf",), ".pdf", (b"%PDF-",)),
    AcceptedType("image/jpeg", (".jpg", ".jpeg"), ".jpg", (b"\xff\xd8\xff",)),
    AcceptedType("image/png", (".png",), ".png", (b"\x89PNG\r\n\x1a\n",)),
)

# Decision 3 (A): 10 MB, clamped to 1 KiB - 25 MiB.
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024
MIN_MAX_FILE_BYTES = 1024
MAX_MAX_FILE_BYTES = 25 * 1024 * 1024
_BODY_OVERHEAD_BYTES = 262144

# Decision 5 (A)
EMAIL_INCLUDES_REJECT_NOTE = True

SUBMISSIONS_PER_USER = "10/hour"

# Owner decision: how many requests one account may have waiting for review at once.
MAX_OPEN_REQUESTS_PER_ACCOUNT = 3

DEFAULT_MIN_FREE_DISK_BYTES = 5 * 1024 * 1024 * 1024

USE_TYPES: dict[str, tuple[str, str]] = {
    "business": ("business_registration", "business_photo_id"),
    "personal": ("utility_bill", "personal_photo_id"),
}

KIND_LABELS: dict[str, str] = {
    "business_registration": "Business registration",
    "business_photo_id": "Photo ID matching the business registration",
    "utility_bill": "Utility bill (proof of address)",
    "personal_photo_id": "Photo ID matching the utility bill",
}

ORPHAN_GRACE = timedelta(hours=1)

# Leftover published files come only from failed uploads (a handful at most). If one
# night's sweep would remove more than this, something is wrong (wrong or partly
# copied database): it removes none and reports.
SWEEP_MAX_REMOVALS_PER_RUN = 20

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def max_file_bytes() -> int:
    raw = _env("NUMBER_REQUEST_MAX_FILE_BYTES")
    try:
        value = int(raw) if raw else DEFAULT_MAX_FILE_BYTES
    except ValueError:
        value = DEFAULT_MAX_FILE_BYTES
    return max(MIN_MAX_FILE_BYTES, min(MAX_MAX_FILE_BYTES, value))


def min_free_disk_bytes() -> int:
    """Free space the documents disk must keep. Invalid or negative -> default; 0 -> check off."""
    raw = _env("NUMBER_REQUEST_MIN_FREE_DISK_BYTES")
    if not raw:
        return DEFAULT_MIN_FREE_DISK_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MIN_FREE_DISK_BYTES
    return value if value >= 0 else DEFAULT_MIN_FREE_DISK_BYTES


def submit_body_limit_bytes() -> int:
    """Whole-body cap for the submit path: two files plus form overhead."""
    return 2 * max_file_bytes() + _BODY_OVERHEAD_BYTES


def accepted_content_types() -> list[str]:
    return [t.content_type for t in ACCEPTED_TYPES]


def accepted_extensions() -> list[str]:
    return [ext for t in ACCEPTED_TYPES for ext in t.extensions]


_REJECTED_RETENTION_NAME = "NUMBER_REQUEST_REJECTED_RETENTION_DAYS"
_RELEASED_RETENTION_NAME = "NUMBER_REQUEST_RELEASED_RETENTION_DAYS"
_RETENTION_DAYS_MAX = 36500
_RETENTION_DIGITS = re.compile(r"[0-9]{1,5}")  # ASCII only; no sign, no separators


def _parse_retention(name: str) -> tuple[int | None, bool]:
    """Returns (days or None for keep, understood).

    Counts only 1 to 5 plain ASCII digits worth 1..36500. Unset, blank, 'keep'
    and '0' mean keep, silently. Anything else means keep AND is not understood
    (reported by ``retention_setting_problems``): a typo never deletes.
    """
    raw = _env(name)
    if not raw or raw.lower() == "keep":
        return None, True
    if not _RETENTION_DIGITS.fullmatch(raw):
        return None, False
    days = int(raw)
    if days == 0:
        return None, True
    if days > _RETENTION_DAYS_MAX:
        return None, False
    return days, True


def _retention_days(name: str) -> int | None:
    return _parse_retention(name)[0]


def retention_setting_problems() -> list[str]:
    """Names (never values) of retention settings that are set but not understood."""
    return [n for n in (_REJECTED_RETENTION_NAME, _RELEASED_RETENTION_NAME)
            if not _parse_retention(n)[1]]


def rejected_retention_days() -> int | None:
    return _retention_days(_REJECTED_RETENTION_NAME)


def released_retention_days() -> int | None:
    return _retention_days(_RELEASED_RETENTION_NAME)


def decision_email_enabled() -> bool:
    raw = _env("NUMBER_REQUEST_DECISION_EMAIL").lower()
    if not raw:
        return True
    return raw not in _FALSE


def require_clean_scan() -> bool:
    return _env("NUMBER_REQUEST_REQUIRE_CLEAN_SCAN").lower() in _TRUE


def documents_root() -> str:
    raw = _env("NUMBER_REQUEST_DOCUMENTS_DIR") or "./number_request_documents"
    return os.path.abspath(raw)

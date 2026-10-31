"""Opaque keyset cursors over (updated_at DESC, id DESC)."""
from __future__ import annotations

import base64
from datetime import datetime
from uuid import UUID

from app.domain.services.support.errors import SupportValidationError


def encode_cursor(updated_at: datetime, record_id: UUID) -> str:
    raw = f"{updated_at.isoformat()}|{record_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, record_id = base64.urlsafe_b64decode(padded.encode()).decode().split("|", 1)
        parsed = datetime.fromisoformat(stamp)
        if parsed.tzinfo is None:
            raise ValueError("naive cursor")
        return parsed, UUID(record_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SupportValidationError("The cursor is not valid.", code="invalid_cursor") from exc


def next_cursor(rows: list[dict], limit: int) -> str | None:
    """Rows were fetched with limit + 1; a surplus row means another page."""
    if len(rows) <= limit:
        return None
    last = rows[limit - 1]
    return encode_cursor(last["updated_at"], last["id"])

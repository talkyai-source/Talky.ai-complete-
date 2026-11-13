"""Bounded call-history continuation values; never an authorization token."""
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID


def history_scope(tenant_id: str, filters: dict) -> str:
    return hashlib.sha256(json.dumps([str(tenant_id), filters], sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def encode_history_cursor(call_id, created_at, scope: str) -> str:
    stamp = None
    if created_at is not None:
        stamp = created_at if isinstance(created_at, datetime) else datetime.fromisoformat(str(created_at))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Call history timestamp lacks a timezone")
    body = {"v": 1, "id": str(UUID(str(call_id))),
            "at": stamp.astimezone(timezone.utc).isoformat(timespec="microseconds") if stamp is not None else None,
            "scope": scope}
    return base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_history_cursor(value: str, scope: str):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 1024:
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        body = json.loads(raw)
        if (not isinstance(body, dict) or set(body) != {"v", "id", "at", "scope"}
                or type(body['v']) is not int or body['v'] != 1 or body['scope'] != scope
                or not isinstance(body['id'], str)
                or (body['at'] is not None and not isinstance(body['at'], str))):
            raise ValueError
        call_id = UUID(body['id'])
        if body['at'] is None:
            return None, call_id
        stamp = datetime.fromisoformat(body['at'])
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(timezone.utc), call_id
    except (ValueError, TypeError, OverflowError, binascii.Error, UnicodeError) as exc:
        raise ValueError("Invalid call history cursor for these filters") from exc

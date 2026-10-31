"""Credential-free identity of the PBX control namespace that admitted a call.

This is deliberately not derived during termination. Missing historical bindings
remain unknown; changing a host, account, application or PBX process cannot turn
a 404 on the replacement into proof about the original call.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


class ControlBackendIdentityError(RuntimeError):
    pass


def valid_control_backend(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"pbx1:[0-9a-f]{64}", value) else None


def asterisk_control_backend(
    *, host: str, port: int, account: str, application: str, system: Any, startup: Any
) -> str | None:
    # No password, token, raw URL or credential material is hashed or persisted.
    parts = [
        "asterisk",
        "http",
        host.strip().lower().rstrip("."),
        str(port),
        account,
        application,
        system,
        startup,
    ]
    if any(
        not isinstance(part, str) or not part or len(part) > 512 or any(ord(c) < 32 for c in part)
        for part in parts
    ):
        return None
    if not 0 < port < 65536 or any(c in parts[2] for c in "/@?#"):
        return None
    return "pbx1:" + hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def saved_control_backend(snapshot: Any) -> str | None:
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except (ValueError, TypeError):
            return None
    return (
        valid_control_backend(snapshot.get("control_backend"))
        if isinstance(snapshot, Mapping)
        else None
    )


def control_backend_error(adapter: Any, expected: Any) -> str | None:
    expected = valid_control_backend(expected)
    if expected is None:
        return "control_backend_identity_unconfirmed"
    actual = valid_control_backend(getattr(adapter, "control_backend_identity", None))
    if actual is None:
        return "adapter_backend_identity_unconfirmed"
    if actual != expected:
        return "control_backend_identity_mismatch"
    return None

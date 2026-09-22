"""A session that becomes suspicious is reported once, not on every request.

The flag is sticky, and both the session validator and the middleware logged
on every request a flagged session made. In the week to 2026-09-23 that was
2,994 "Suspicious session activity detected" plus 1,442 "Session fingerprint
mismatch" warnings, one session alone 1,197, all from two legitimate users on
one IP. Nobody was logged out (strict binding is off; every revocation in 30
days was idle_timeout, logout or password_reset), but a real hijack would have
been one line among thousands.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.security.sessions import lifecycle


class _FakeConn:
    """Holds one session row across 'requests', like the real table."""

    def __init__(self):
        now = datetime.now(timezone.utc)
        self.row = {
            "id": "sess-1", "user_id": "user-1", "ip_address": "1.2.3.4",
            "user_agent": "UA", "device_fingerprint": "v2:original",
            "device_name": None, "device_type": None, "browser": None, "os": None,
            "bound_ip": None, "ip_binding_enforced": False,
            "fingerprint_binding_enforced": False,
            "is_suspicious": False, "suspicious_reason": None,
            "requires_verification": False,
            "created_at": now, "last_active_at": now,
            "expires_at": now + timedelta(days=1), "revoked": False,
            "user_active": True, "tenant_status": "active",
            "partner_status": None, "tenant_id": "tenant-1",
        }

    async def fetchrow(self, *_a, **_k):
        return dict(self.row)

    async def execute(self, sql, *args):
        if "is_suspicious" in sql:
            self.row["is_suspicious"] = True
            self.row["suspicious_reason"] = "fingerprint_mismatch"
        return "UPDATE 1"

    async def fetchval(self, *_a, **_k):
        return None


@pytest.mark.asyncio
async def test_a_flapping_fingerprint_warns_once_per_session(caplog):
    conn = _FakeConn()
    results = []
    with caplog.at_level(logging.DEBUG, logger=lifecycle.logger.name):
        for n in range(5):
            results.append(
                await lifecycle.validate_session(
                    conn, "token", current_fingerprint=f"v2:different-{n}"
                )
            )
    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "fingerprint mismatch" in r.getMessage()
    ]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    # every request still sees the true state
    assert all(s["is_suspicious"] for s in results)
    # but only the request that flipped it is marked as the transition
    assert [bool(s.get("became_suspicious")) for s in results] == [True, False, False, False, False]


@pytest.mark.asyncio
async def test_repeats_are_still_visible_at_debug(caplog):
    conn = _FakeConn()
    with caplog.at_level(logging.DEBUG, logger=lifecycle.logger.name):
        for n in range(3):
            await lifecycle.validate_session(conn, "token", current_fingerprint=f"v2:x{n}")
    debug = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "fingerprint mismatch" in r.getMessage()
    ]
    assert len(debug) == 2


def test_the_middleware_reports_the_transition_not_the_state():
    src = (
        Path(__file__).resolve().parents[2] / "app" / "core" / "session_security_middleware.py"
    ).read_text(encoding="utf-8")
    call = src.index("await self._handle_suspicious_session(")
    guard = src[call - 200 : call]
    assert 'session.get("became_suspicious")' in guard
    assert 'if session.get("is_suspicious"):\n                await self._handle_suspicious_session' not in src
    # the state is still exposed to endpoints
    assert 'request.state.session_is_suspicious = session.get("is_suspicious")' in src

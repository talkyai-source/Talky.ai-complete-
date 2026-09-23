"""A session with a flapping bound IP is reported once, not on every request.

session-ip-mismatch-spam (day0923 forensics): 8f7096fb fixed this exact
double-log-plus-redundant-DB-write pattern for the fingerprint branch but
never touched the IP-mismatch branch a few lines above it, so a session with
a changing IP kept logging "Session IP mismatch detected" WARNING plus
"Session marked suspicious" on every single request: 52 lines on 2026-09-23
(36x session=82cfcdcc, 16x session=f13d4a24), 11+ hours after the fingerprint
fix was live. Same style as test_suspicious_session_logged_once.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security.sessions import lifecycle


class _FakeConn:
    """Holds one session row across 'requests', like the real table."""

    def __init__(self):
        now = datetime.now(timezone.utc)
        self.row = {
            "id": "sess-1", "user_id": "user-1", "ip_address": "1.2.3.4",
            "user_agent": "UA", "device_fingerprint": "v2:same",
            "device_name": None, "device_type": None, "browser": None, "os": None,
            "bound_ip": "1.2.3.4", "ip_binding_enforced": True,
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
            self.row["suspicious_reason"] = "ip_mismatch:different_subnet"
        return "UPDATE 1"

    async def fetchval(self, *_a, **_k):
        return None


@pytest.mark.asyncio
async def test_a_flapping_ip_warns_once_per_session(caplog):
    conn = _FakeConn()
    results = []
    with caplog.at_level(logging.DEBUG, logger=lifecycle.logger.name):
        for n in range(5):
            # A different /24 subnet each time — always "significant" per
            # is_ip_change_significant's non-strict subnet check.
            results.append(
                await lifecycle.validate_session(
                    conn, "token", current_ip=f"{10 + n}.0.0.1"
                )
            )
    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "IP mismatch detected" in r.getMessage()
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
            await lifecycle.validate_session(conn, "token", current_ip=f"{20 + n}.0.0.1")
    debug = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "IP mismatch detected" in r.getMessage()
    ]
    assert len(debug) == 2


@pytest.mark.asyncio
async def test_already_suspicious_session_is_not_re_marked_in_the_db(caplog, monkeypatch):
    """The redundant UPDATE (52x on 09-23) must stop once already flagged."""
    conn = _FakeConn()
    mark_calls = []
    original = lifecycle._mark_session_suspicious

    async def _counting_mark(*args, **kwargs):
        mark_calls.append(args)
        return await original(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "_mark_session_suspicious", _counting_mark)

    with caplog.at_level(logging.DEBUG, logger=lifecycle.logger.name):
        for n in range(4):
            await lifecycle.validate_session(conn, "token", current_ip=f"{30 + n}.0.0.1")

    assert len(mark_calls) == 1

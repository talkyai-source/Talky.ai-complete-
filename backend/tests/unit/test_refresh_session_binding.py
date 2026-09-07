"""POST /auth/refresh keeps the login session bound and alive (0045).

Root cause pinned: the refreshed access JWT had no ``sid``, so REST (which
accepts a bare JWT) kept working while the Test-agent WebSocket (which requires
a live session-bound token) said "Your session has expired" from the first
refresh onwards. Separately the security_session expired 24 h after login while
the refresh family lived 7 days — the session now slides on every refresh.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.endpoints.auth import refresh as refresh_ep


class _Conn:
    def __init__(self, session_row):
        self.session_row = session_row
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        assert "FROM   security_sessions" in sql or "FROM security_sessions" in sql
        return self.session_row

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_no_session_on_family_mints_without_sid_as_before():
    conn = _Conn(session_row=None)
    sid, alive = await refresh_ep.bind_refresh_to_session(conn, {"user_id": "u1", "session_id": None})
    assert (sid, alive) == (None, True)
    assert conn.executed == []


@pytest.mark.asyncio
async def test_live_session_is_carried_forward_and_slid():
    session_id = str(uuid.uuid4())
    conn = _Conn(session_row={"id": session_id, "user_id": "u1", "revoked": False})
    before = datetime.now(timezone.utc)

    sid, alive = await refresh_ep.bind_refresh_to_session(conn, {"user_id": "u1", "session_id": session_id})

    assert (sid, alive) == (session_id, True)
    (sql, args), = conn.executed
    assert sql.startswith("UPDATE security_sessions SET last_active_at = $2, expires_at = GREATEST(expires_at, $3)")
    assert args[0] == session_id
    assert args[1] >= before
    # Extended by the configured lifetime from now, never shortened (GREATEST).
    assert timedelta(hours=23, minutes=59) <= args[2] - args[1] <= timedelta(hours=24, minutes=1)


@pytest.mark.asyncio
async def test_revoked_or_expired_session_ends_the_refresh():
    """get_session_by_id filters revoked/expired rows itself → None here."""
    session_id = str(uuid.uuid4())
    conn = _Conn(session_row=None)
    sid, alive = await refresh_ep.bind_refresh_to_session(conn, {"user_id": "u1", "session_id": session_id})
    assert (sid, alive) == (session_id, False)
    assert conn.executed == []

"""change_password takes the same profile-row lock as turning two-factor off.

The lock is taken inside the transaction, before the UPDATE, and the typed old
password is checked against the hash read under that lock. Only the connection
factory, the password helpers and the lock itself are replaced; the revocation
helpers run for real against a recording fake connection.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints.auth import password as password_ep
from app.api.v1.endpoints.auth.schemas import ChangePasswordRequest

USER = "11111111-1111-1111-1111-111111111111"
TENANT = "cccccccc-0000-4000-8000-000000000001"
SESSION = "aaaaaaaa-0000-0000-0000-000000000001"

user = SimpleNamespace(id=USER, tenant_id=TENANT)
db = SimpleNamespace(pool=object())
body = ChangePasswordRequest(old_password="old password here 1", new_password="orchid lantern cobalt 2026!")
audit = SimpleNamespace(log=AsyncMock())


class Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.events.append("tx_enter")
        return self

    async def __aexit__(self, *exc):
        self.conn.events.append("tx_exit")
        return False


class Conn:
    def __init__(self):
        self.events: list[str] = []

    def transaction(self):
        return Tx(self)

    async def fetch(self, sql, *args):
        assert "FROM   security_sessions" in sql
        now = datetime.now(timezone.utc)
        return [
            {"id": sid, "created_at": now, "last_active_at": now,
             "expires_at": now + timedelta(hours=1), "is_current": False}
            for sid in (SESSION, "aaaaaaaa-0000-0000-0000-000000000002")
        ]

    async def fetchrow(self, sql, *args):
        if "password_hash FROM user_profiles" in sql:
            return {"password_hash": "h"}
        if "SELECT family_id FROM refresh_tokens" in sql:
            return None
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if "UPDATE user_profiles" in sql:
            self.events.append("update_password")
        elif "UPDATE security_sessions" in sql:
            self.events.append("revoke_session")
        elif "UPDATE refresh_tokens" in sql:
            self.events.append("revoke_refresh")
        return "UPDATE 1"


def make_request() -> Request:
    request = Request({"type": "http", "headers": [], "client": ("203.0.113.9", 1), "method": "POST", "path": "/"})
    request.state.authenticated_session_id = SESSION
    request.state.authenticated_user_id = USER
    return request


@pytest.fixture()
def conn(monkeypatch):
    c = Conn()

    @asynccontextmanager
    async def factory(*args, **kwargs):
        yield c

    async def lock(conn_, request, current_user):
        c.events.append("lock")
        return {"password_hash": "h"}

    monkeypatch.setattr(password_ep, "acquire_with_tenant", factory)
    # the typed password matches the stored hash "h" and its re-hash, nothing else
    monkeypatch.setattr(
        password_ep, "verify_password",
        lambda typed, stored: typed == body.old_password and stored in ("h", "h-rehashed"))
    monkeypatch.setattr(password_ep, "hash_password", lambda p: "new-hash")
    monkeypatch.setattr(password_ep, "lock_authenticated_credential_change", lock, raising=False)
    audit.log.reset_mock()
    return c


async def change(request=None):
    return await password_ep.change_password(
        request or make_request(), body, SimpleNamespace(), user, db, audit, talky_sid=None, talky_rt=None)


def set_lock(monkeypatch, c, *, returns=None, raises=None):
    async def lock(conn_, request, current_user):
        c.events.append("lock")
        if raises is not None:
            raise raises
        return returns

    monkeypatch.setattr(password_ep, "lock_authenticated_credential_change", lock, raising=False)


async def test_lock_is_taken_once_inside_the_transaction_before_the_update(conn):
    await change()
    assert conn.events.count("lock") == 1
    assert conn.events.index("tx_enter") < conn.events.index("lock") < conn.events.index("update_password")


async def test_a_changed_hash_under_the_lock_refuses_and_writes_nothing(conn, monkeypatch):
    set_lock(monkeypatch, conn, returns={"password_hash": "someone-else-changed-it"})
    with pytest.raises(HTTPException) as refused:
        await change()
    assert refused.value.status_code == 401
    assert refused.value.detail == "Credentials changed. Please sign in again."
    assert "update_password" not in conn.events
    assert "revoke_session" not in conn.events and "revoke_refresh" not in conn.events
    audit.log.assert_not_called()


async def test_a_rehash_of_the_same_password_under_the_lock_is_not_a_refusal(conn, monkeypatch):
    # A sign-in elsewhere upgraded the stored hash of the SAME password between the read and
    # the lock. The typed password still matches what is stored now, so the change goes through.
    set_lock(monkeypatch, conn, returns={"password_hash": "h-rehashed"})
    await change()
    assert conn.events.count("lock") == 1
    assert conn.events.count("update_password") == 1
    assert conn.events.index("lock") < conn.events.index("update_password")


async def test_an_ended_session_under_the_lock_is_401_and_writes_nothing(conn, monkeypatch):
    ended = HTTPException(401, "Your login session has ended. Please sign in again.")
    set_lock(monkeypatch, conn, raises=ended)
    with pytest.raises(HTTPException) as refused:
        await change()
    assert refused.value is ended
    assert refused.value.status_code == 401
    assert "update_password" not in conn.events
    assert "revoke_session" not in conn.events and "revoke_refresh" not in conn.events


async def test_an_unavailable_lock_is_503_and_writes_nothing(conn, monkeypatch):
    set_lock(monkeypatch, conn, raises=HTTPException(503, "Account verification is temporarily unavailable"))
    with pytest.raises(HTTPException) as refused:
        await change()
    assert refused.value.status_code == 503
    assert "update_password" not in conn.events
    assert "revoke_session" not in conn.events and "revoke_refresh" not in conn.events


async def test_the_happy_path_is_unchanged(conn):
    result = await change()
    assert result == {"detail": "Password changed successfully. All other sessions have been revoked."}
    assert conn.events.count("update_password") == 1
    assert "revoke_session" in conn.events and "revoke_refresh" in conn.events
    audit.log.assert_awaited_once()


async def test_a_wrong_old_password_is_400_and_takes_no_lock(conn, monkeypatch):
    monkeypatch.setattr(password_ep, "verify_password", lambda *a: False)
    with pytest.raises(HTTPException) as refused:
        await change()
    assert refused.value.status_code == 400
    assert refused.value.detail["code"] == "current_password_invalid"
    assert conn.events == []

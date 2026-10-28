"""auth-sessions-7: passkey login rejections must commit their failure accounting.

``acquire_with_tenant`` wraps the handler body in one transaction, so an
HTTPException raised inside it used to roll back the clone-detection DELETE,
the consumed challenge and the login_attempts row. The fake pool below models
exactly that: writes made inside the context are kept only if it exits cleanly.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.v1.endpoints import passkeys as api
from app.core.security import passkeys

USER_ID = str(uuid4())


class FakeConn:
    def __init__(self):
        self.pending: list[str] = []

    def _write(self, sql):
        self.pending.append(" ".join(str(sql).split()))

    def is_in_transaction(self):
        return True

    async def execute(self, sql, *args):
        self._write(sql)
        return "DELETE 1" if sql.lstrip().upper().startswith("DELETE") else "UPDATE 1"

    async def fetchrow(self, sql, *args):
        self._write(sql)
        if "FROM user_profiles" in sql and "FOR UPDATE" in sql:
            return {"id": USER_ID, "password_hash": None}
        if "RETURNING" in str(sql).upper():
            return {"user_id": USER_ID}
        return None

    async def fetchval(self, sql, *args):
        self._write(sql)
        return None

    @asynccontextmanager
    async def transaction(self):  # savepoint: nested, same pending buffer
        yield


class FakeDb:
    def __init__(self):
        self.committed: list[str] = []
        self.pool = object()

    @asynccontextmanager
    async def acquire(self, pool, tenant_id, **_):
        conn = FakeConn()
        yield conn  # an exception here propagates and discards conn.pending
        self.committed.extend(conn.pending)

    def wrote(self, needle):
        return any(needle in sql for sql in self.committed)


def _request():
    return Request({"type": "http", "method": "POST", "path": "/x", "headers": [],
                    "client": ("127.0.0.1", 1234), "scheme": "https",
                    "server": ("app.example", 443)})


@pytest.fixture
def db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(api, "acquire_with_tenant", db.acquire)
    monkeypatch.setattr(api, "_validate_passkey_origin", lambda _: "https://app.example")
    monkeypatch.setattr(api, "get_credential_by_id", AsyncMock(return_value={
        "user_id": USER_ID, "credential_id": "cred-1",
        "credential_public_key": "a2V5", "sign_count": 5}))
    monkeypatch.setattr(api, "load_current_principal", AsyncMock(return_value={
        "email": "u@example.com", "tenant_id": uuid4(), "role": "tenant_admin",
        "business_name": "Biz"}))
    monkeypatch.setattr(api, "assert_expected_identity", lambda *_: None)
    monkeypatch.setattr(api, "check_account_locked", AsyncMock(return_value=None))
    # Crypto + challenge store are synthetic; the consume is a real write.
    async def consume(conn, ceremony_id):
        await conn.execute("UPDATE webauthn_challenges SET used = TRUE")
        return True
    monkeypatch.setattr(passkeys, "get_and_validate_challenge", AsyncMock(return_value="Y2hhbGw"))
    monkeypatch.setattr(passkeys, "consume_challenge", consume)
    monkeypatch.setattr(passkeys, "base64url_to_bytes", lambda s: b"k")
    return db


def _body():
    return api.LoginCompleteRequest(ceremony_id=str(uuid4()),
                                    credential_response={"id": "cred-1"})


async def _login():
    return await api.login_complete.__wrapped__(
        _request(), Response(), _body(), SimpleNamespace(pool=object()))


async def test_clone_detection_delete_and_failure_row_survive_the_401(db, monkeypatch):
    # Counter regressed: stored 5, authenticator reports 3.
    monkeypatch.setattr(passkeys, "verify_authentication_response",
                        lambda **_: SimpleNamespace(new_sign_count=3, user_verified=True))
    with pytest.raises(HTTPException) as caught:
        await _login()
    assert caught.value.status_code == 401
    assert caught.value.detail == "Invalid credentials."
    assert db.wrote("DELETE FROM user_passkeys"), db.committed
    assert db.wrote("passkey_count"), "passkey_count must be decremented with the delete"
    assert db.wrote("INSERT INTO login_attempts"), db.committed
    assert db.wrote("UPDATE webauthn_challenges"), "single-use challenge stays consumed"


async def test_verification_failure_row_is_committed(db, monkeypatch):
    monkeypatch.setattr(passkeys, "get_and_validate_challenge", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await _login()
    assert caught.value.status_code == 401
    assert db.wrote("INSERT INTO login_attempts")


async def test_locked_account_attempt_row_is_committed(db, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(api, "check_account_locked", AsyncMock(
        return_value=datetime.now(timezone.utc) + timedelta(seconds=30)))
    with pytest.raises(HTTPException) as caught:
        await _login()
    assert caught.value.status_code == 401
    assert db.wrote("INSERT INTO login_attempts")


async def test_unexpected_error_still_rolls_back(db, monkeypatch):
    monkeypatch.setattr(passkeys, "verify_authentication_response",
                        lambda **_: SimpleNamespace(new_sign_count=3, user_verified=True))
    monkeypatch.setattr(api, "record_login_attempt", AsyncMock(side_effect=RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        await _login()
    assert db.committed == []


async def test_zero_sign_count_synced_passkey_is_not_flagged_as_clone(db, monkeypatch):
    monkeypatch.setattr(passkeys, "verify_authentication_response",
                        lambda **_: SimpleNamespace(new_sign_count=0, user_verified=True))
    conn = FakeConn()
    result = await passkeys.verify_authentication(conn, "c", {}, "cred-1", "a2V5", 0)
    assert result.new_sign_count == 0
    assert not any("DELETE FROM user_passkeys" in s for s in conn.pending)

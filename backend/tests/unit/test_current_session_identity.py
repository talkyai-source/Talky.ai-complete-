"""Which session is "the current one": the authenticated session id, not only the
24-hour ``talky_sid`` cookie.

Real revocation helpers run against a recording fake connection; only the
connection factories and the password check are replaced.
"""
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints import sessions as sessions_ep
from app.api.v1.endpoints.auth import password as password_ep
from app.api.v1.endpoints.auth.schemas import ChangePasswordRequest
from app.api.v1.endpoints.mfa import status as mfa_ep
from app.api.v1.endpoints.mfa.schemas import MFADisableRequest
from app.core.security.sessions import hash_session_token

USER = "11111111-1111-1111-1111-111111111111"
CURRENT = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER_1 = "aaaaaaaa-0000-0000-0000-000000000002"
OTHER_2 = "aaaaaaaa-0000-0000-0000-000000000003"
RT = "synthetic-refresh-cookie"
FAMILY = "fam-current"


class Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Conn:
    """Records session revokes ('id', sid) / ('bulk', excluded_hash) and refresh args."""

    def __init__(self):
        self.session_revokes: list[tuple] = []
        self.refresh_updates: list[tuple] = []
        self.family_lookups: list[tuple] = []
        now = datetime.now(timezone.utc)
        self.rows = [
            {"id": sid, "created_at": now, "last_active_at": now,
             "expires_at": now + timedelta(hours=1), "is_current": False}
            for sid in (CURRENT, OTHER_1, OTHER_2)
        ]

    def transaction(self):
        return Tx()

    async def fetch(self, sql, *args):
        assert "FROM   security_sessions" in sql
        return [dict(r) for r in self.rows]

    async def fetchrow(self, sql, *args):
        if "password_hash FROM user_profiles" in sql:
            return {"password_hash": "h"}
        if "SELECT family_id FROM refresh_tokens" in sql:
            self.family_lookups.append((sql, args))
            # the family belongs to USER (in TENANT): the lookup must ask for both
            if (args[0] == hashlib.sha256(RT.encode()).hexdigest() and str(args[1]) == USER
                    and (len(args) < 3 or args[2] == TENANT)):
                return {"family_id": FAMILY}
            return None
        if "FROM user_mfa" in sql:
            return {"id": 1, "enabled": True}
        if "SELECT id FROM security_sessions WHERE session_token_hash" in sql:
            return {"id": CURRENT}
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if "UPDATE security_sessions" in sql:
            if "WHERE id" in sql:
                self.session_revokes.append(("id", args[2]))
            else:
                self.session_revokes.append(("bulk", args[3] if "!=" in sql else None))
            return "UPDATE 1"
        if "UPDATE refresh_tokens" in sql:
            self.refresh_updates.append(args)
            return "UPDATE 1"
        return "UPDATE 1"


def make_request(session_id=None) -> Request:
    request = Request({"type": "http", "headers": [], "client": ("203.0.113.9", 1), "method": "POST", "path": "/"})
    if session_id is not None:
        request.state.authenticated_session_id = session_id
    return request


@pytest.fixture()
def conn(monkeypatch):
    c = Conn()

    @asynccontextmanager
    async def factory(*args, **kwargs):
        yield c

    for module in (password_ep, mfa_ep):
        monkeypatch.setattr(module, "acquire_with_tenant", factory)
        monkeypatch.setattr(module, "verify_password", lambda *a: True)
    monkeypatch.setattr(password_ep, "hash_password", lambda p: "new-hash")
    monkeypatch.setattr(mfa_ep, "lock_authenticated_credential_change", AsyncMock(return_value={"password_hash": "h"}))
    monkeypatch.setattr(password_ep, "lock_authenticated_credential_change", AsyncMock(return_value={"password_hash": "h"}))
    monkeypatch.setattr(mfa_ep, "invalidate_all_codes", AsyncMock())
    monkeypatch.setattr(sessions_ep, "acquire_connection", factory)
    return c


TENANT = "cccccccc-0000-4000-8000-000000000001"
user = SimpleNamespace(id=USER, tenant_id=TENANT)
staff = SimpleNamespace(id=USER, tenant_id=None)  # platform staff: no account
db = SimpleNamespace(pool=object())
body = ChangePasswordRequest(old_password="old password here 1", new_password="orchid lantern cobalt 2026!")
audit = SimpleNamespace(log=AsyncMock())


async def change(request, sid=None, rt=None):
    return await password_ep.change_password(
        request, body, SimpleNamespace(), user, db, audit, talky_sid=sid, talky_rt=rt)


def revoked_ids(c):
    return sorted(v for kind, v in c.session_revokes if kind == "id")


# ---- 1. change password ---------------------------------------------------

async def test_change_password_keeps_authenticated_session_without_cookie(conn):
    await change(make_request(CURRENT), sid=None, rt=RT)
    assert revoked_ids(conn) == [OTHER_1, OTHER_2]
    assert not [r for r in conn.session_revokes if r[0] == "bulk"]
    assert conn.refresh_updates[0][3] == FAMILY  # family exclusion unchanged


async def test_change_password_with_cookie_and_no_session_id_is_as_before(conn):
    await change(make_request(None), sid="cookie-token", rt=RT)
    assert conn.session_revokes == [("bulk", hash_session_token("cookie-token"))]
    assert conn.refresh_updates[0][3] == FAMILY


async def test_change_password_with_nothing_known_revokes_everything(conn):
    await change(make_request(None), sid=None, rt=None)
    assert conn.session_revokes == [("bulk", None)]
    assert len(conn.refresh_updates[0]) == 3  # no family excluded


# ---- 2/3. device list + self-revoke guard -----------------------------------

async def test_device_list_marks_authenticated_session_without_cookie(conn):
    result = await sessions_ep.list_active_sessions(make_request(CURRENT), user, db, talky_sid=None)
    assert result.current_session_id == CURRENT
    assert {s.id: s.is_current for s in result.sessions} == {CURRENT: True, OTHER_1: False, OTHER_2: False}


async def test_device_list_with_no_way_to_know_marks_none(conn):
    result = await sessions_ep.list_active_sessions(make_request(None), user, db, talky_sid=None)
    assert result.current_session_id is None
    assert not any(s.is_current for s in result.sessions)


async def test_self_revoke_refused_for_authenticated_session_without_cookie(conn):
    with pytest.raises(HTTPException) as refused:
        await sessions_ep.revoke_specific_session(
            CURRENT, user, db, talky_sid=None, request=make_request(CURRENT))
    assert refused.value.status_code == 400
    assert conn.session_revokes == []


@pytest.mark.parametrize("spelled", [CURRENT.upper(), CURRENT.replace("-", "")])
async def test_self_revoke_is_refused_however_the_own_id_is_spelled(conn, spelled):
    # The database matches a session id in any letter case, so the guard compares values.
    for kwargs in ({"talky_sid": None, "request": make_request(CURRENT)},
                   {"talky_sid": "synthetic-sid", "request": make_request(None)}):
        with pytest.raises(HTTPException) as refused:
            await sessions_ep.revoke_specific_session(spelled, user, db, **kwargs)
        assert refused.value.status_code == 400
    assert conn.session_revokes == []


async def test_a_refresh_cookie_of_another_user_keeps_no_family(conn):
    # Someone else's refresh cookie names no family of THIS user: every token is revoked.
    other = SimpleNamespace(id="dddddddd-0000-4000-8000-000000000009", tenant_id=TENANT)
    await mfa_ep.disable_mfa(make_request(CURRENT), MFADisableRequest(password="pw"), other, db,
                             talky_rt=RT)
    assert len(conn.refresh_updates[-1]) == 3
    await password_ep.change_password(
        make_request(CURRENT), body, SimpleNamespace(), other, db, audit, talky_sid=None, talky_rt=RT)
    assert len(conn.refresh_updates[-1]) == 3


async def test_revoking_another_session_still_works_without_cookie(conn):
    result = await sessions_ep.revoke_specific_session(
        OTHER_1, user, db, talky_sid=None, request=make_request(CURRENT))
    assert result.revoked is True
    assert conn.session_revokes == [("id", OTHER_1)]


async def test_self_revoke_cookie_fallback_still_refuses(conn):
    with pytest.raises(HTTPException) as refused:
        await sessions_ep.revoke_specific_session(
            CURRENT, user, db, talky_sid="cookie-token", request=make_request(None))
    assert refused.value.status_code == 400


# ---- 4. two-factor off ------------------------------------------------------

async def test_mfa_disable_keeps_current_session_and_family(conn):
    await mfa_ep.disable_mfa(make_request(CURRENT), MFADisableRequest(password="pw"), user, db,
                             talky_rt=RT)
    assert revoked_ids(conn) == [OTHER_1, OTHER_2]
    assert not [r for r in conn.session_revokes if r[0] == "bulk"]
    assert conn.refresh_updates[0][3] == FAMILY
    # the lookup of the current refresh family names the account in its own SQL
    sql, args = conn.family_lookups[-1]
    assert "tenant_id = $3::uuid" in sql and args[2] == TENANT


async def test_mfa_disable_for_a_user_without_an_account_keeps_no_family(conn):
    # No account to name in the lookup: no family is kept, every refresh token is revoked,
    # as before. The current SESSION is still kept.
    await mfa_ep.disable_mfa(make_request(CURRENT), MFADisableRequest(password="pw"), staff, db,
                             talky_rt=RT)
    assert revoked_ids(conn) == [OTHER_1, OTHER_2]
    assert conn.family_lookups == []
    assert len(conn.refresh_updates[0]) == 3


async def test_mfa_disable_with_no_session_id_revokes_everything(conn):
    await mfa_ep.disable_mfa(make_request(None), MFADisableRequest(password="pw"), user, db,
                             talky_rt=None)
    assert conn.session_revokes == [("bulk", None)]
    assert len(conn.refresh_updates[0]) == 3

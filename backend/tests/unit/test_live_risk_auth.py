"""Live-risk auth packet: client IP trust, lockout tiers, signup code caps, session-bound call control."""
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints.auth import signup
from app.core.security import lockout


def make_request(peer, headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 1234) if peer else None,
                    "method": "GET", "path": "/", "query_string": b""})


# ---------------- auth-2 ----------------
def test_resolve_client_ip_ignores_spoofed_xff_behind_nginx():
    from app.core.api_security_middleware import resolve_client_ip
    # nginx on loopback: X-Real-IP is overwritten by nginx, XFF leftmost is attacker-chosen
    req = make_request("127.0.0.1", {"X-Real-IP": "198.51.100.9",
                                     "X-Forwarded-For": "1.2.3.4, 198.51.100.9"})
    assert resolve_client_ip(req) == "198.51.100.9"


def test_resolve_client_ip_xff_rightmost_untrusted_when_no_real_ip():
    from app.core.api_security_middleware import resolve_client_ip
    req = make_request("127.0.0.1", {"X-Forwarded-For": "6.6.6.6, 198.51.100.9, 10.0.0.2"})
    assert resolve_client_ip(req) == "198.51.100.9"


def test_resolve_client_ip_untrusted_peer_headers_ignored():
    from app.core.api_security_middleware import resolve_client_ip
    req = make_request("203.0.113.5", {"X-Real-IP": "9.9.9.9", "X-Forwarded-For": "9.9.9.9"})
    assert resolve_client_ip(req) == "203.0.113.5"


def test_resolve_client_ip_garbage_and_missing_client():
    from app.core.api_security_middleware import resolve_client_ip
    assert resolve_client_ip(make_request("127.0.0.1", {"X-Forwarded-For": "not-an-ip"})) == "127.0.0.1"
    assert resolve_client_ip(make_request(None)) == "unknown"


def test_dependencies_client_ip_uses_resolver():
    from app.api.v1.dependencies import _get_client_ip
    req = make_request("127.0.0.1", {"X-Forwarded-For": "1.2.3.4, 198.51.100.9"})
    assert _get_client_ip(req) == "198.51.100.9"


# ---------------- auth-3 ----------------
class _Conn:
    """Evaluates the lockout queries' window predicate over in-memory failures."""

    def __init__(self, times):
        self.times = times

    async def fetchval(self, sql, email, window_start):
        rows = [t for t in self.times if t >= window_start]
        return len(rows) if "COUNT" in sql else (max(rows) if rows else None)


async def test_long_tiers_are_reachable_when_failures_are_spread_out():
    now = datetime.now(timezone.utc)
    # 20 failures, one every 10 minutes: never 10 inside a 15 min window.
    times = [now - timedelta(minutes=10 * i + 1) for i in range(20)]
    conn = _Conn(times)
    assert await lockout.get_consecutive_failures(conn, "a@b.com") == 20
    until = await lockout.check_account_locked(conn, "a@b.com")
    assert until is not None and (until - now).total_seconds() > 600  # 30 min tier, not 5


def test_observation_window_covers_longest_tier():
    assert lockout.OBSERVATION_WINDOW_MINUTES * 60 >= max(s for _, s in lockout.LOCKOUT_THRESHOLDS)


# ---------------- auth-4 ----------------
class _FakeRedis:
    """Implements the two Lua scripts' semantics (INCR counter, cap, burn)."""

    def __init__(self, payload):
        self.store = {"signup:pending:o@x.com": json.dumps(payload)}
        self.counts = {}

    async def get(self, k):
        return self.store.get(k)

    async def delete(self, k):
        self.store.pop(k, None)

    async def setex(self, k, ttl, v):
        self.store[k] = v

    async def eval(self, script, numkeys, *args):
        keys, argv = args[:numkeys], args[numkeys:]
        n = self.counts[keys[-1]] = self.counts.get(keys[-1], 0) + 1
        if n > int(argv[0]):
            if numkeys == 2:
                self.store.pop(keys[0], None)
            return -1
        return n


async def test_wrong_code_guesses_are_capped_and_burn_the_code(monkeypatch):
    redis = _FakeRedis({"code_hash": signup._hash_signup_code("123456"), "name": "N", "business_name": "B"})
    monkeypatch.setattr(signup, "_get_redis_or_503", lambda: redis)
    fn = signup.signup_verify_code
    fn = getattr(fn, "__wrapped__", fn)
    for _ in range(5):
        with pytest.raises(HTTPException) as e:
            await fn(request=None, body=SimpleNamespace(email="o@x.com", code="000000"))
        assert "Invalid verification code" in e.value.detail
    with pytest.raises(HTTPException) as e:  # 6th: burned, even the right code fails
        await fn(request=None, body=SimpleNamespace(email="o@x.com", code="123456"))
    assert "expired or not found" in e.value.detail
    assert "signup:pending:o@x.com" not in redis.store


async def test_signup_start_has_per_email_send_cap():
    redis = _FakeRedis({})
    for _ in range(signup._SIGNUP_MAX_SENDS_PER_WINDOW):
        await signup._claim_signup_send(redis, "o@x.com")
    with pytest.raises(HTTPException) as e:
        await signup._claim_signup_send(redis, "O@x.com")
    assert e.value.status_code == 429


# ---------------- auth-6 ----------------
def _patch_session(monkeypatch, session):
    from app.core.security import internal_auth
    monkeypatch.setattr(internal_auth, "decode_and_validate_token",
                        lambda t: {"sub": "u1", "sid": "s1", "tenant_id": "t1"})

    @asynccontextmanager
    async def acq(pool, tenant):
        yield object()

    monkeypatch.setattr(internal_auth, "acquire_with_tenant", acq)
    monkeypatch.setattr(internal_auth, "get_session_by_id", AsyncMock(return_value=session))
    monkeypatch.setattr(internal_auth, "load_current_principal", AsyncMock(return_value={}))
    return internal_auth


async def test_revoked_session_is_rejected_for_user_path(monkeypatch):
    internal_auth = _patch_session(monkeypatch, None)
    req = make_request("127.0.0.1", {"Authorization": "Bearer abc"})
    with pytest.raises(HTTPException) as e:
        await internal_auth.require_active_session(
            req, internal_auth.CallerContext(is_internal=False, tenant_id="t1"), db_pool=object())
    assert e.value.status_code == 401


async def test_active_session_passes_and_internal_skips(monkeypatch):
    internal_auth = _patch_session(monkeypatch, {"id": "s1"})
    req = make_request("127.0.0.1", {"Authorization": "Bearer abc"})
    await internal_auth.require_active_session(
        req, internal_auth.CallerContext(False, "t1"), db_pool=object())
    await internal_auth.require_active_session(
        req, internal_auth.CallerContext(True, None), db_pool=None)

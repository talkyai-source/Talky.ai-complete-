"""A password-reset code survives only a handful of guesses.

Bug hunt 2026-10-10:
  * auth-sessions-2: a wrong code raised 400 and left the pending record in
    place, /reset-password has no rate limit and /api/v1/auth/* is exempt from
    the API rate limiter. A 6-digit code (1e6 values) could be brute-forced
    inside its 15-minute life, taking over any account without MFA.
  * auth-sessions-11: a completed reset did not clear the login lockout,
    which is computed from login_attempts rows, so the user who just
    recovered the account could still be locked out of it.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.auth import password_reset as reset

EMAIL = "owner@example.com"
USER_ID = "00000000-0000-4000-8000-000000000001"
STORED_HASH = "synthetic-stored-hash"


async def _issue(redis, code):
    await redis.setex(reset._reset_redis_key(EMAIL), 900, json.dumps({
        "user_id": USER_ID, "email": EMAIL, "code_hash": reset._hash_reset_code(code),
        "password_generation": reset._password_generation(STORED_HASH),
    }))


@pytest.fixture()
def harness(monkeypatch):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    conn = SimpleNamespace(
        fetchrow=AsyncMock(return_value={"id": USER_ID, "password_hash": STORED_HASH}),
        execute=AsyncMock(return_value="OK"),
    )

    @asynccontextmanager
    async def transaction():
        yield

    conn.transaction = transaction

    @asynccontextmanager
    async def acquire(*_):
        yield conn

    monkeypatch.setattr(reset, "acquire_with_tenant", acquire)
    monkeypatch.setattr(reset, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(reset, "revoke_all_user_sessions", AsyncMock(return_value=0))
    monkeypatch.setattr(reset, "revoke_all_user_refresh_tokens", AsyncMock(return_value=0))
    monkeypatch.setattr(reset, "hash_password", lambda _: "new-hash")
    monkeypatch.setattr(reset, "get_client_ip", lambda _: "203.0.113.9")
    monkeypatch.setattr(reset, "get_user_agent", lambda _: "synthetic")
    return redis, conn


async def _attempt(code):
    return await reset.reset_password(
        None, reset.ResetPasswordRequest(email=EMAIL, code=code, new_password="Synthetic#Passw0rd-2026"),
        SimpleNamespace(pool=None), SimpleNamespace(log=AsyncMock()))


async def test_the_code_is_burned_after_five_wrong_guesses(harness):
    redis, conn = harness
    await _issue(redis, "424242")
    for guess in ("000001", "000002", "000003", "000004", "000005"):
        with pytest.raises(HTTPException):
            await _attempt(guess)
    with pytest.raises(HTTPException) as exc:
        await _attempt("424242")  # the right code, one guess too late
    assert exc.value.status_code == 400
    assert await redis.get(reset._reset_redis_key(EMAIL)) is None
    conn.execute.assert_not_awaited()

    await _issue(redis, "515151")  # a newly issued code starts its own count
    assert "reset" in (await _attempt("515151"))["message"]


async def test_parallel_guesses_cannot_exceed_the_cap(harness):
    redis, _ = harness
    await _issue(redis, "424242")
    compared = []
    real_hash = reset._hash_reset_code

    def counting_hash(code):
        compared.append(code)
        return real_hash(code)

    reset._hash_reset_code = counting_hash
    try:
        await asyncio.gather(*(_attempt(f"{n:06d}") for n in range(40)), return_exceptions=True)
    finally:
        reset._hash_reset_code = real_hash
    assert len(compared) <= 5


async def test_a_completed_reset_clears_the_login_lockout(harness):
    redis, conn = harness
    await _issue(redis, "424242")
    await _attempt("424242")
    inserts = [c for c in conn.execute.await_args_list if "INSERT INTO login_attempts" in c.args[0]]
    assert len(inserts) == 1
    assert inserts[0].args[1:] == (EMAIL, USER_ID, "203.0.113.9", True, "password_reset")

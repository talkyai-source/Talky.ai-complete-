"""A3: recovery failures are scoped to the source, with a bounded shared ceiling."""
import asyncio
import itertools
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.auth import password_reset as reset
from app.core.security.auth_abuse import budget_key


EMAIL = "recovery@example.com"
USER_ID = "00000000-0000-4000-8000-000000000031"
PASSWORD = "Synthetic#Recovery-2026"


@pytest.fixture
def flow(monkeypatch):
    server = fakeredis.FakeServer()
    clients = [fakeredis.FakeAsyncRedis(server=server, decode_responses=True) for _ in range(2)]
    rotation = itertools.cycle(clients)
    state = SimpleNamespace(clients=clients, redis=clients[0], password_hash="synthetic-old-hash", updates=0)

    async def fetchrow(*_):
        return {"id": USER_ID, "password_hash": state.password_hash}

    async def execute(sql, *args):
        if "UPDATE user_profiles" in sql:
            state.password_hash = args[0]
            state.updates += 1
        return "OK"

    @asynccontextmanager
    async def transaction():
        yield

    conn = SimpleNamespace(fetchrow=fetchrow, execute=execute, transaction=transaction)

    @asynccontextmanager
    async def acquire(*_):
        yield conn

    monkeypatch.setattr(reset, "_get_redis_or_503", lambda: next(rotation))
    monkeypatch.setattr(reset, "acquire_with_tenant", acquire)
    monkeypatch.setattr(reset, "get_client_ip", lambda request: request.test_ip)
    monkeypatch.setattr(reset, "get_user_agent", lambda _: "synthetic")
    monkeypatch.setattr(reset, "hash_password", lambda _: "synthetic-new-hash")
    monkeypatch.setattr(reset, "revoke_all_user_sessions", AsyncMock(return_value=0))
    monkeypatch.setattr(reset, "revoke_all_user_refresh_tokens", AsyncMock(return_value=0))
    return state


async def issue(flow, code="424242"):
    await flow.redis.setex(reset._reset_redis_key(EMAIL), 900, json.dumps({
        "user_id": USER_ID, "email": EMAIL, "code_hash": reset._hash_reset_code(code),
        "password_generation": reset._password_generation(flow.password_hash),
    }))


async def attempt(ip, code="000000"):
    return await reset.reset_password(
        SimpleNamespace(test_ip=ip),
        reset.ResetPasswordRequest(email=EMAIL, code=code, new_password=PASSWORD),
        SimpleNamespace(pool=None), SimpleNamespace(log=AsyncMock()),
    )


async def outcome(ip, code="000000"):
    try:
        await attempt(ip, code)
    except HTTPException as exc:
        return exc.status_code
    return 200


async def next_minute(flow, ip):
    # Advance only the existing minute bucket; daily failure budgets survive.
    await flow.redis.delete(budget_key("reset-ip", ip))


async def test_junk_from_one_address_does_not_lock_out_correct_code_from_another(flow):
    attacker = "203.0.113.10"
    for n in range(25):
        if n % 5 == 0:
            await next_minute(flow, attacker)
        assert await outcome(attacker) == (400 if n < 20 else 429)

    await issue(flow)
    assert await outcome("203.0.113.11", "424242") == 200
    assert flow.updates == 1


async def test_success_does_not_consume_any_of_the_hundred_failure_slots(flow):
    await issue(flow)
    assert await outcome("203.0.113.20", "424242") == 200

    for n in range(100):
        assert await outcome(f"198.51.100.{n + 1}") == 400
    assert await outcome("198.51.100.201") == 429
    assert flow.updates == 1


async def test_success_does_not_create_a_failed_budget(flow):
    await issue(flow)
    ip = "203.0.113.30"
    assert await outcome(ip, "424242") == 200
    keys = [key async for key in flow.redis.scan_iter("auth:budget:*")]
    assert keys == [budget_key("reset-ip", ip)]


async def test_concurrent_clients_share_exactly_one_hundred_failure_outcomes(flow):
    results = await asyncio.gather(*(outcome(f"198.51.100.{n + 1}") for n in range(200)))
    assert results.count(400) == 100
    assert results.count(429) == 100
    assert flow.updates == 0

    # Knowing the code must not provide an extra success oracle after the cap.
    await issue(flow)
    assert await outcome("203.0.113.31", "424242") == 429
    assert flow.updates == 0


@pytest.mark.parametrize("interruption", ["global_cap", "redis_outage"])
async def test_matching_code_cannot_use_a_stale_precheck_to_write_password(flow, monkeypatch, interruption):
    await issue(flow)
    reached_database = asyncio.Event()
    resume = asyncio.Event()
    real_acquire = reset.acquire_with_tenant

    @asynccontextmanager
    async def paused_acquire(*args):
        reached_database.set()
        await resume.wait()
        async with real_acquire(*args) as conn:
            yield conn

    monkeypatch.setattr(reset, "acquire_with_tenant", paused_acquire)
    matching = asyncio.create_task(outcome("203.0.113.32", "424242"))
    try:
        await asyncio.wait_for(reached_database.wait(), timeout=5)
        if interruption == "global_cap":
            results = [await outcome(f"198.51.100.{n + 1}") for n in range(100)]
            assert results == [400] * 100
        else:
            for client in flow.clients:
                monkeypatch.setattr(client, "eval", AsyncMock(side_effect=ConnectionError("synthetic")))
    finally:
        resume.set()
        result = await matching
    assert result == (429 if interruption == "global_cap" else 503)
    assert flow.updates == 0


async def test_code_reissue_and_client_rotation_do_not_reset_global_failed_budget(flow):
    for n in range(101):
        await issue(flow, f"{n + 1:06d}")
        assert await outcome(f"198.51.100.{n + 1}") == (400 if n < 100 else 429)
    assert flow.updates == 0


async def test_failed_budget_expiry_is_bounded_and_not_extended_by_more_failures(flow):
    ip = "203.0.113.40"
    assert await outcome(ip) == 400
    failure_keys = [key async for key in flow.redis.scan_iter("auth:budget:*")
                    if key != budget_key("reset-ip", ip)]
    assert len(failure_keys) == 2  # address/email pair plus cross-address email cap
    for key in failure_keys:
        assert EMAIL not in key and ip not in key
        assert 0 < await flow.redis.ttl(key) <= 86400
        await flow.redis.expire(key, 50)
    assert await outcome(ip) == 400
    assert all([0 < await flow.redis.ttl(key) <= 50 for key in failure_keys])


async def test_redis_failure_still_refuses_before_password_update(flow, monkeypatch):
    await issue(flow)
    for client in flow.clients:
        monkeypatch.setattr(client, "eval", AsyncMock(side_effect=ConnectionError("synthetic")))
    assert await outcome("203.0.113.50", "424242") == 503
    assert flow.updates == 0


async def test_five_tries_per_code_and_five_requests_per_ip_are_preserved(flow):
    await issue(flow)
    for _ in range(5):
        assert await outcome("203.0.113.60") == 400
    assert await outcome("203.0.113.60", "424242") == 429
    assert await outcome("203.0.113.61", "424242") == 400
    assert await flow.redis.get(reset._reset_redis_key(EMAIL)) is None
    assert flow.updates == 0


async def test_three_codes_per_hour_is_preserved_across_clients(flow):
    assert await reset._check_reset_rate_limit(flow.clients[0], EMAIL)
    assert await reset._check_reset_rate_limit(flow.clients[1], EMAIL.upper())
    assert await reset._check_reset_rate_limit(flow.clients[0], EMAIL)
    assert not await reset._check_reset_rate_limit(flow.clients[1], EMAIL)

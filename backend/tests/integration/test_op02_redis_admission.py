"""Opt-in actual standalone Redis proof; never loads application credentials.

Use TEST_REDIS_URL only for a disposable loopback Redis. Each test uses a
new UUID namespace, and removes only its own keys; no FLUSH/foreign-key scan.
"""
import asyncio
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.domain.services import global_concurrency as slots


@pytest_asyncio.fixture(params=["fakeredis_lua", "standalone"])
async def redis_slots(monkeypatch, request):
    if request.param == "standalone":
        url = os.getenv("TEST_REDIS_URL")
        if not url:
            pytest.skip("TEST_REDIS_URL disposable standalone Redis not configured")
        parsed = urlsplit(url)
        assert parsed.scheme == "redis" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        assert not parsed.username and not parsed.password, "only the synthetic passwordless fixture is accepted"
        client = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    else:
        pytest.importorskip("lupa", reason="fakeredis Lua test extra is required")
        from fakeredis.aioredis import FakeRedis
        client = FakeRedis()
    prefix = f"op02-test:{uuid4()}:"
    monkeypatch.setattr(slots, "_ACTIVE_SET_KEY", prefix + "active")
    monkeypatch.setattr(slots, "_LEASE_KEY_PREFIX", prefix + "lease:")
    await client.ping()
    names = set()

    async def claim(call_id, cap=1):
        names.add(call_id)
        return await slots.acquire_lease(client, call_id=call_id, pod_id="synthetic", cap=cap, fail_closed=True)

    try:
        yield client, claim, names
    finally:
        await client.delete(slots._ACTIVE_SET_KEY, *(slots._lease_key(name) for name in names))
        await client.aclose()


@pytest.mark.asyncio
async def test_actual_redis_concurrent_claims_respect_one_slot(redis_slots):
    client, claim, names = redis_slots
    results = await asyncio.gather(*(claim(f"call-{i}") for i in range(20)))
    assert sum(result.acquired for result in results) == 1
    assert await slots.current_count(client) == 1
    winner = f"call-{next(i for i, result in enumerate(results) if result.acquired)}"
    assert (await claim(winner)).acquired
    assert await client.ttl(slots._lease_key(winner)) > 0
    await slots.release_lease_strict(client, call_id=winner)
    assert (await claim("later")).acquired
    assert await slots.current_count(client) == 1


@pytest.mark.asyncio
async def test_actual_redis_reconcile_cannot_remove_a_refreshed_candidate(redis_slots, monkeypatch):
    client, claim, _names = redis_slots
    assert (await claim("live")).acquired
    await client.delete(slots._lease_key("live"))  # only this fixture's own lease
    real_eval = client.eval
    removed_candidate = asyncio.Event()
    refresh_done = asyncio.Event()

    async def delayed_eval(script, *args):
        if script == slots._RECONCILE_SCRIPT:
            removed_candidate.set()
            await refresh_done.wait()
        return await real_eval(script, *args)

    monkeypatch.setattr(client, "eval", delayed_eval)
    reconcile = asyncio.create_task(slots.reconcile_orphans(client))
    try:
        await asyncio.wait_for(removed_candidate.wait(), 2)
        await slots.refresh_lease(client, call_id="live")
        refresh_done.set()
        assert await asyncio.wait_for(reconcile, 2) == 0
        assert not (await claim("other")).acquired
        assert await slots.current_count(client) == 1
    finally:
        refresh_done.set()
        await asyncio.gather(reconcile, return_exceptions=True)


@pytest.mark.asyncio
async def test_actual_redis_recovers_own_missing_lease_without_duplicate_membership(redis_slots):
    client, claim, _names = redis_slots
    assert (await claim("live")).acquired
    await client.delete(slots._ACTIVE_SET_KEY, slots._lease_key("live"))
    await slots.refresh_lease(client, call_id="live")
    await slots.refresh_lease(client, call_id="live")
    assert await slots.reconcile_orphans(client) == 0
    assert await slots.current_count(client) == 1
    assert not (await claim("other")).acquired
    await slots.release_lease_strict(client, call_id="live")
    assert (await claim("other")).acquired


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error_before_commit", "cancel_after_commit"])
async def test_existing_live_lease_survives_uncertain_same_id_retry(redis_slots, monkeypatch, failure):
    client, claim, _names = redis_slots
    assert (await claim("live")).acquired
    original = await client.get(slots._lease_key("live"))
    entered = asyncio.Event()
    real_eval = client.eval

    async def uncertain_eval(script, *args):
        if script == slots._ACQUIRE_SCRIPT:
            if failure == "error_before_commit":
                raise ConnectionError("synthetic reply failure")
            await real_eval(script, *args)
            entered.set()
            await asyncio.Event().wait()
        return await real_eval(script, *args)

    monkeypatch.setattr(client, "eval", uncertain_eval)
    if failure == "error_before_commit":
        assert not (await claim("live")).acquired
    else:
        task = asyncio.create_task(claim("live"))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await client.get(slots._lease_key("live")) == original
    assert await slots.current_count(client) == 1


@pytest.mark.asyncio
async def test_cancelled_new_attempt_cleans_only_its_committed_token(redis_slots, monkeypatch):
    client, claim, _names = redis_slots
    entered = asyncio.Event()
    real_eval = client.eval

    async def blocked_reply(script, *args):
        result = await real_eval(script, *args)
        if script == slots._ACQUIRE_SCRIPT:
            entered.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(client, "eval", blocked_reply)
    task = asyncio.create_task(claim("new"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await slots.current_count(client) == 0
    assert await client.exists(slots._lease_key("new")) == 0


@pytest.mark.asyncio
async def test_heartbeat_revokes_old_attempt_cleanup_authority(redis_slots, monkeypatch):
    client, claim, _names = redis_slots
    entered = asyncio.Event()
    real_eval = client.eval

    async def blocked_reply(script, *args):
        result = await real_eval(script, *args)
        if script == slots._ACQUIRE_SCRIPT:
            entered.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(client, "eval", blocked_reply)
    task = asyncio.create_task(claim("now-active"))
    await asyncio.wait_for(entered.wait(), 2)
    await slots.refresh_lease(client, call_id="now-active")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await slots.current_count(client) == 1
    assert await client.get(slots._lease_key("now-active")) == b"refreshed"


@pytest.mark.asyncio
async def test_known_live_lease_missing_membership_is_restored_even_when_cap_is_full(redis_slots):
    client, claim, _names = redis_slots
    assert (await claim("known-live")).acquired
    original = await client.get(slots._lease_key("known-live"))
    await client.srem(slots._ACTIVE_SET_KEY, "known-live")
    assert (await claim("other")).acquired
    # Redis already lost accounting for a live call. Restoring its existing
    # proof can exceed cap; it must count both and block a third new call.
    restored = await claim("known-live")
    assert restored.acquired and restored.current == 2
    assert await client.get(slots._lease_key("known-live")) == original
    assert not (await claim("third")).acquired

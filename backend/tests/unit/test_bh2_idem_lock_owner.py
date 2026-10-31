"""auth-sessions-8 residual: the idempotency in-progress lock is released by OWNER, not by KEY.

Before: ``check_idempotency`` took ``SET NX EX 60`` whose value was only the request
body hash, and both ``idempotency_dependency``'s teardown and ``store_response``
deleted the lock KEY without looking at who held it. A handler that outlived the
60 s TTL let a second identical request take the lock; the first request's
teardown then deleted the SECOND request's lock, so a third retry ran concurrently
with the second (duplicate side effects on a retry-safe endpoint).

Now the lock value carries a unique owner token (plus the body hash, so a
concurrent request with a different body is still answered 409 "different
request") and every release is one atomic compare-and-delete (Lua EVAL).
"""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.security import idempotency
from app.core.security.idempotency import IdempotencyManager, idempotency_dependency

KEY = "client-key-12345"
BODY = b'{"name":"A"}'


@pytest.fixture(params=[True, False], ids=["decode_responses", "bytes"])
async def redis(request):
    """Production's client decodes responses; the manager also tolerates bytes."""
    r = fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=request.param)
    yield r
    await r.aclose()


def make_request(*, user="u-a", tenant="t-a", body=BODY, path="/api/v1/campaigns/", key=KEY):
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    req = Request({"type": "http", "method": "POST", "path": path, "query_string": b"",
                   "headers": [(b"idempotency-key", key.encode())],
                   "client": ("127.0.0.1", 1), "scheme": "https", "server": ("a", 443)}, receive)
    req.state.authenticated_user_id = user
    req.state.tenant_id = tenant
    return req


@pytest.fixture
def wire(monkeypatch, redis):
    idempotency.reset_idempotency_manager()
    monkeypatch.setattr("app.core.container.get_container",
                        lambda: SimpleNamespace(redis=redis, is_initialized=True))
    yield
    idempotency.reset_idempotency_manager()


async def _enter(request):
    """Run the dependency up to its yield; returns (generator, outcome)."""
    gen = idempotency_dependency(request)
    try:
        return gen, await gen.__anext__()
    except HTTPException as exc:
        return None, exc


async def _lock_value(redis):
    keys = [k async for k in redis.scan_iter("idempotency:lock:*")]
    assert len(keys) <= 1, keys
    if not keys:
        return None
    value = await redis.get(keys[0])
    return value.decode() if isinstance(value, bytes) else value


async def _expire_lock(redis):
    """The handler ran past the 60 s lock TTL: let the real Redis expiry fire."""
    keys = [k async for k in redis.scan_iter("idempotency:lock:*")]
    assert len(keys) == 1
    await redis.pexpire(keys[0], 1)
    for _ in range(200):
        if not await redis.exists(keys[0]):
            return
        await asyncio.sleep(0.005)
    raise AssertionError("lock did not expire")


def _record_commands(redis):
    commands: list[str] = []
    original = redis.execute_command

    async def spy(*args, **kwargs):
        commands.append(str(args[0]).upper())
        return await original(*args, **kwargs)

    redis.execute_command = spy
    return commands


# --------------------------------------------------------------------------
# The bug: an expired first request must not delete the second request's lock
# --------------------------------------------------------------------------
async def test_failed_teardown_of_an_expired_first_request_keeps_the_second_requests_lock(wire, redis):
    first = make_request()
    gen_a, val = await _enter(first)
    assert val == KEY
    await _expire_lock(redis)  # first handler is still running after the TTL

    second = make_request()
    gen_b, val_b = await _enter(second)
    assert gen_b is not None and val_b == KEY, "the expired lock is free; the second request takes it"
    held_by_second = await _lock_value(redis)
    assert held_by_second

    with pytest.raises(HTTPException):  # the first handler finally fails
        await gen_a.athrow(HTTPException(status_code=500, detail="boom"))

    assert await _lock_value(redis) == held_by_second, (
        "the first request's teardown deleted the SECOND request's lock"
    )
    third = make_request()
    gen_c, outcome = await _enter(third)
    assert gen_c is None, "a third identical request must not run while the second is in progress"
    assert outcome.status_code == 409 and "in progress" in outcome.detail

    await gen_b.aclose()  # the owner still releases its own lock
    assert await _lock_value(redis) is None


async def test_late_store_response_of_an_expired_first_request_keeps_the_second_requests_lock(wire, redis):
    first = make_request()
    gen_a, _ = await _enter(first)
    await _expire_lock(redis)
    second = make_request()
    gen_b, _ = await _enter(second)
    held_by_second = await _lock_value(redis)
    assert held_by_second

    await idempotency.store_idempotent_response(first, 200, '{"campaign":{"id":"c1"}}')
    await gen_a.aclose()  # stored -> teardown must not release anything either

    assert await _lock_value(redis) == held_by_second, (
        "the first request's late store_response deleted the SECOND request's lock"
    )
    # ...and the late response is still recorded, so a retry is replayed rather than re-run
    retry = make_request()
    gen_r, exc = await _enter(retry)
    assert gen_r is None and isinstance(exc, HTTPException)
    assert exc.headers == {"Idempotent-Replay": "true"}

    await gen_b.aclose()


# --------------------------------------------------------------------------
# Normal path unchanged
# --------------------------------------------------------------------------
async def test_owner_releases_its_own_lock_on_failure_and_on_success(wire, redis):
    failing = make_request()
    gen, _ = await _enter(failing)
    assert await _lock_value(redis)
    with pytest.raises(HTTPException):
        await gen.athrow(HTTPException(status_code=400, detail="bad input"))
    assert await _lock_value(redis) is None

    ok = make_request()
    gen2, _ = await _enter(ok)
    assert await _lock_value(redis)
    await idempotency.store_idempotent_response(ok, 200, '{"campaign":{"id":"c1"}}')
    assert await _lock_value(redis) is None, "store_response releases the lock it owns"
    await gen2.aclose()

    replay = make_request()
    gen3, exc = await _enter(replay)
    assert gen3 is None and exc.headers == {"Idempotent-Replay": "true"}


async def test_explicit_release_helper_releases_only_the_requests_own_lock(wire, redis):
    first = make_request()
    gen_a, _ = await _enter(first)
    await _expire_lock(redis)
    second = make_request()
    gen_b, _ = await _enter(second)
    held_by_second = await _lock_value(redis)

    await idempotency.release_idempotency_lock(first)  # what campaigns.py does on error paths
    assert await _lock_value(redis) == held_by_second

    await idempotency.release_idempotency_lock(second)
    assert await _lock_value(redis) is None
    await gen_a.aclose()
    await gen_b.aclose()


async def test_lock_value_has_a_unique_owner_token_and_keeps_the_body_hash(wire, redis):
    body_hash = hashlib.sha256(BODY).hexdigest()
    values = []
    for _ in range(2):
        req = make_request()
        gen, _ = await _enter(req)
        values.append(await _lock_value(redis))
        await gen.aclose()
    for value in values:
        assert body_hash in value and value != body_hash, "owner token missing from the lock value"
    assert values[0] != values[1], "two acquisitions must not share an owner token"


async def test_body_discrimination_survives_the_new_lock_value(wire, redis):
    holder = make_request(body=BODY)
    gen_a, _ = await _enter(holder)

    different = make_request(body=b'{"name":"DIFFERENT"}')
    gen_d, exc_d = await _enter(different)
    assert gen_d is None and exc_d.status_code == 409 and "different request" in exc_d.detail

    same = make_request(body=BODY)
    gen_s, exc_s = await _enter(same)
    assert gen_s is None and exc_s.status_code == 409 and "in progress" in exc_s.detail
    await gen_a.aclose()


async def test_lock_left_by_a_pre_upgrade_process_is_still_understood(wire, redis):
    """A rolling deploy can leave a lock whose value is the bare body hash."""
    manager = IdempotencyManager(redis_client=redis)
    scoped_key = json.dumps(["t-a", "u-a", KEY])
    await redis.set(manager._lock_key(scoped_key), hashlib.sha256(BODY).hexdigest(), nx=True, ex=60)

    gen_s, exc_s = await _enter(make_request(body=BODY))
    assert gen_s is None and exc_s.status_code == 409 and "in progress" in exc_s.detail
    gen_d, exc_d = await _enter(make_request(body=b'{"name":"DIFFERENT"}'))
    assert gen_d is None and exc_d.status_code == 409 and "different request" in exc_d.detail


async def test_concurrent_first_requests_still_only_one_proceeds(wire):
    reqs = [make_request() for _ in range(8)]
    results = await asyncio.gather(*[_enter(r) for r in reqs])
    proceeded = [g for g, _ in results if g is not None]
    assert len(proceeded) == 1
    for g, outcome in results:
        if g is None:
            assert outcome.status_code == 409 and "in progress" in outcome.detail
    await proceeded[0].aclose()


# --------------------------------------------------------------------------
# Manager API: release is by owner value, atomically
# --------------------------------------------------------------------------
async def test_release_lock_deletes_only_the_matching_owner_value(redis):
    mgr = IdempotencyManager(redis_client=redis)
    lock_key = mgr._lock_key("k")
    await redis.set(lock_key, "owner-b:hash", ex=60)

    await mgr.release_lock("k", "owner-a:hash")  # a stale owner
    assert await redis.exists(lock_key) == 1
    await mgr.release_lock("k")  # no owner value -> this caller never held a lock
    assert await redis.exists(lock_key) == 1
    await mgr.release_lock("k", "owner-b:hash")
    assert await redis.exists(lock_key) == 0


async def test_release_is_one_atomic_script_not_get_then_delete(redis):
    mgr = IdempotencyManager(redis_client=redis)
    lock_key = mgr._lock_key("k")
    await redis.set(lock_key, "owner-a:hash", ex=60)
    commands = _record_commands(redis)

    await mgr.release_lock("k", "owner-a:hash")

    assert commands == ["EVAL"], f"release must be a single server-side compare-and-delete, got {commands}"
    assert await redis.exists(lock_key) == 0

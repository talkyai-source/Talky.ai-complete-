"""auth-sessions-8: idempotency keys are scoped, body-checked and atomically locked.

Before: the Redis key was sha256(Idempotency-Key) alone, so another tenant
reusing a key on the same method+path replayed the first tenant's cached body;
the request_body_hash was never compared; the in-progress lock was EXISTS then
SETEX (two concurrent first requests both ran); and an HTTPException after the
lock left it held for 60 s.
"""
import asyncio
import json
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.security import idempotency
from app.core.security.idempotency import IdempotencyManager, idempotency_dependency

KEY = "client-key-12345"


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


def make_request(*, user, tenant, body=b'{"name":"A"}', path="/api/v1/campaigns/", key=KEY):
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
        if hasattr(gen, "__anext__"):
            return gen, await gen.__anext__()
        return _NoTeardown(), await gen  # pre-fix: plain coroutine dependency
    except HTTPException as exc:
        return None, exc


class _NoTeardown:
    async def aclose(self):
        return None

    async def athrow(self, exc):
        raise exc


async def _finish_ok(gen, request, result='{"campaign":{"id":"c1"}}'):
    await idempotency.store_idempotent_response(request, 200, result)
    await gen.aclose()


async def test_other_tenants_same_key_is_not_replayed(wire):
    a = make_request(user="u-a", tenant="t-a")
    gen_a, val = await _enter(a)
    assert val == KEY
    await _finish_ok(gen_a, a, '{"campaign":{"id":"A-CAMPAIGN"}}')

    b = make_request(user="u-b", tenant="t-b")
    gen_b, outcome = await _enter(b)
    assert gen_b is not None, f"tenant B was short-circuited with tenant A's response: {outcome!r}"
    assert outcome == KEY


async def test_same_user_replay_returns_cached_response(wire):
    first = make_request(user="u-a", tenant="t-a")
    gen, _ = await _enter(first)
    await _finish_ok(gen, first)
    again = make_request(user="u-a", tenant="t-a")
    gen2, exc = await _enter(again)
    assert gen2 is None and isinstance(exc, HTTPException)
    assert exc.headers == {"Idempotent-Replay": "true"}


async def test_same_key_with_a_different_body_is_rejected(wire):
    first = make_request(user="u-a", tenant="t-a", body=b'{"name":"A"}')
    gen, _ = await _enter(first)
    await _finish_ok(gen, first)
    changed = make_request(user="u-a", tenant="t-a", body=b'{"name":"DIFFERENT"}')
    gen2, exc = await _enter(changed)
    assert gen2 is None and isinstance(exc, HTTPException)
    assert exc.status_code == 409
    assert "different request" in exc.detail


async def test_concurrent_first_requests_only_one_proceeds(wire):
    reqs = [make_request(user="u-a", tenant="t-a") for _ in range(8)]
    results = await asyncio.gather(*[_enter(r) for r in reqs])
    proceeded = [g for g, _ in results if g is not None]
    assert len(proceeded) == 1
    for g, outcome in results:
        if g is None:
            assert outcome.status_code == 409 and "in progress" in outcome.detail


async def test_http_error_after_the_lock_releases_it(wire):
    first = make_request(user="u-a", tenant="t-a")
    gen, _ = await _enter(first)
    with pytest.raises(HTTPException):
        await gen.athrow(HTTPException(status_code=400, detail="bad input"))
    retry = make_request(user="u-a", tenant="t-a")
    gen2, val = await _enter(retry)
    assert gen2 is not None and val == KEY, "retry after a failed request must not 409"


async def test_in_progress_lock_is_taken_with_ttl(wire, redis):
    req = make_request(user="u-a", tenant="t-a")
    gen, _ = await _enter(req)
    locks = [k async for k in redis.scan_iter("idempotency:lock:*")]
    assert len(locks) == 1
    assert 0 < await redis.ttl(locks[0]) <= 60
    await gen.aclose()

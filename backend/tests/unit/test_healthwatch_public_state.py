"""GET /healthz/watch: what the outside uptime watch may read about the server's own health watch.

14 Nov 2026: the nightly backup, the hourly inbound test call and the alert
sender were all failing, and the only alert path was the email that was down.
The outside watch (GitHub Actions) reads this coarse state instead.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import fakeredis.aioredis
import pytest
from fastapi import Response

from app.core import operational_alerts as alerts

BACKEND = Path(__file__).resolve().parents[2]
RAN = "1794650000.0"


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class _Broken:
    async def get(self, key):
        raise ConnectionError("redis unavailable")


async def test_clear_only_after_a_recent_run_with_no_failing_check(redis):
    # A watch that has not run is not mistaken for a healthy server.
    assert await alerts.healthwatch_public_state(redis) == {"clear": False, "failing": 0, "ran_recently": False}
    await redis.set(alerts.HEALTHWATCH_LAST_RUN_KEY, RAN, ex=600)
    assert await alerts.healthwatch_public_state(redis) == {"clear": True, "failing": 0, "ran_recently": True}


async def test_failing_checks_are_counted_and_never_named(redis):
    await redis.set(alerts.HEALTHWATCH_LAST_RUN_KEY, RAN, ex=600)
    await redis.set(alerts.HEALTHWATCH_FAILING_KEY, "payments,trunks,units", ex=600)
    state = await alerts.healthwatch_public_state(redis)
    assert state == {"clear": False, "failing": 3, "ran_recently": True}
    assert not any(name in repr(state) for name in ("payments", "trunks", "units"))


async def test_a_client_that_returns_bytes_is_understood():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        await client.set(alerts.HEALTHWATCH_LAST_RUN_KEY, RAN, ex=600)
        await client.set(alerts.HEALTHWATCH_FAILING_KEY, "payments", ex=600)
        assert await alerts.healthwatch_public_state(client) == {"clear": False, "failing": 1, "ran_recently": True}
    finally:
        await client.aclose()


@pytest.mark.parametrize("client", [None, _Broken()])
async def test_an_unreadable_state_is_never_reported_as_clear(client):
    assert await alerts.healthwatch_public_state(client) == {"clear": False, "failing": None, "ran_recently": False}


def test_the_watch_script_and_the_api_use_the_same_two_keys():
    spec = importlib.util.spec_from_file_location("qa_healthwatch_public", BACKEND / "scripts/healthwatch.py")
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    assert watch.LAST_RUN_KEY == alerts.HEALTHWATCH_LAST_RUN_KEY
    assert watch.FAILING_KEY == alerts.HEALTHWATCH_FAILING_KEY


async def test_the_endpoint_returns_the_coarse_state_and_is_never_cached(redis, monkeypatch):
    from app.api.v1.endpoints import health

    await redis.set(alerts.HEALTHWATCH_LAST_RUN_KEY, RAN, ex=600)
    await redis.set(alerts.HEALTHWATCH_FAILING_KEY, "units", ex=600)
    monkeypatch.setattr("app.core.container.get_container", lambda: NS(is_initialized=True, redis=redis))
    response = Response()
    assert await health.healthwatch_probe(response) == {"clear": False, "failing": 1, "ran_recently": True}
    assert response.headers["Cache-Control"] == "no-store"

    # Before the application container is up there is no state to read: not clear.
    monkeypatch.setattr("app.core.container.get_container", lambda: NS(is_initialized=False))
    assert await health.healthwatch_probe(Response()) == {"clear": False, "failing": None, "ran_recently": False}

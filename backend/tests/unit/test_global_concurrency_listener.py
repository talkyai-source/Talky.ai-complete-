"""Tests for app.domain.services.global_concurrency_listener.

Uses a tiny fake Redis client (just enough surface for what the
listeners exercise) so the tests don't need a live Redis."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.domain.services import global_concurrency_listener as L


class _FakePubSub:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages
        self._subscribed: list[str] = []

    async def psubscribe(self, pattern: str) -> None:
        self._subscribed.append(pattern)

    async def subscribe(self, channel: str) -> None:
        self._subscribed.append(channel)

    async def punsubscribe(self, pattern: str) -> None:
        return None

    async def unsubscribe(self, channel: str) -> None:
        return None

    async def close(self) -> None:
        return None

    async def listen(self):
        for m in self._messages:
            yield m
        # Block forever after delivering scripted messages so the
        # listener exits via stop_event, not StopAsyncIteration.
        forever = asyncio.Event()
        await forever.wait()


class _FakeRedis:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages
        self.sremoved: list[str] = []
        self.config_cmds: list[tuple] = []

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._messages)

    async def config_get(self, key: str) -> dict:
        return {key: "Ex"}  # already enabled

    async def config_set(self, key: str, value: str) -> None:
        self.config_cmds.append(("set", key, value))

    async def srem(self, set_key: str, member: str) -> int:
        self.sremoved.append(member)
        return 1


@pytest.mark.asyncio
async def test_keyspace_listener_reaps_only_lease_keys():
    messages = [
        {"type": "pmessage", "data": b"telephony:lease:abc123"},
        {"type": "pmessage", "data": b"unrelated:cache:foo"},
        {"type": "pmessage", "data": b"telephony:lease:def456"},
    ]
    redis = _FakeRedis(messages)
    stop = asyncio.Event()

    task = asyncio.create_task(
        L.keyspace_expiry_listener(redis, stop_event=stop)
    )
    # Let the listener consume the messages.
    await asyncio.sleep(0.05)
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert redis.sremoved == ["abc123", "def456"]


@pytest.mark.asyncio
async def test_quota_alerts_listener_caches_decision():
    payload = json.dumps(
        {"tenant_id": "t-1", "action": "THROTTLE", "ttl_seconds": 30}
    )
    messages = [{"type": "message", "data": payload}]
    redis = _FakeRedis(messages)
    stop = asyncio.Event()

    task = asyncio.create_task(
        L.quota_alerts_listener(redis, stop_event=stop)
    )
    await asyncio.sleep(0.05)
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    cached = L.get_cached_quota_decision("t-1")
    assert cached is not None
    assert cached["action"] == "THROTTLE"

    # An unknown tenant has no decision cached.
    assert L.get_cached_quota_decision("t-other") is None


@pytest.mark.asyncio
async def test_listener_noop_when_redis_is_none():
    stop = asyncio.Event()
    # Both listeners must return promptly when Redis is unavailable.
    await asyncio.wait_for(
        L.keyspace_expiry_listener(None, stop_event=stop),
        timeout=1.0,
    )
    await asyncio.wait_for(
        L.quota_alerts_listener(None, stop_event=stop),
        timeout=1.0,
    )

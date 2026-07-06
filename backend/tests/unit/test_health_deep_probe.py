"""P1-11 — deep readiness probe (GET /api/v1/healthz/deep).

The shallow /healthz/ready only reports drain/capacity and would return
ready=true against a dead DB. The deep probe actively pings Postgres + Redis so
a dead-dependency pod reports NOT ready. These tests mock the container's
db_pool / redis so nothing real is touched.

Policy under test:
  - DB ping fails/times out  -> ready=false, HTTP 503.
  - Redis ping fails/times out -> reported "down" but ready stays true (Redis
    has an in-memory fallback; failing closed would evict healthy pods).
  - Both ok                   -> ready=true, HTTP 200.
  - Container not initialized -> ready=false, HTTP 503.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.api.v1.endpoints import health


class _FakePool:
    def __init__(self, *, fail: bool = False, hang: bool = False):
        self._fail = fail
        self._hang = hang

    async def fetchval(self, query: str):
        if self._hang:
            await asyncio.sleep(5)  # exceeds the probe's 0.5s budget
        if self._fail:
            raise RuntimeError("db down")
        return 1


class _FakeRedis:
    def __init__(self, *, fail: bool = False, hang: bool = False):
        self._fail = fail
        self._hang = hang

    async def ping(self):
        if self._hang:
            await asyncio.sleep(5)
        if self._fail:
            raise RuntimeError("redis down")
        return True


def _make_container(*, initialized=True, pool=None, redis=None):
    return SimpleNamespace(is_initialized=initialized, db_pool=pool, redis=redis)


def _patch_container(monkeypatch, container):
    monkeypatch.setattr(
        "app.core.container.get_container", lambda: container
    )


def _new_response() -> Response:
    return Response()


async def test_deep_probe_ready_when_both_ok(monkeypatch):
    container = _make_container(pool=_FakePool(), redis=_FakeRedis())
    _patch_container(monkeypatch, container)

    resp = _new_response()
    result = await health.deep_readiness_probe(resp)

    assert result["ready"] is True
    assert result["db"] == "ok"
    assert result["redis"] == "ok"
    assert resp.status_code != 503


async def test_deep_probe_not_ready_when_db_ping_fails(monkeypatch):
    container = _make_container(pool=_FakePool(fail=True), redis=_FakeRedis())
    _patch_container(monkeypatch, container)

    resp = _new_response()
    result = await health.deep_readiness_probe(resp)

    assert result["ready"] is False
    assert result["db"] == "down"
    assert result["redis"] == "ok"
    assert resp.status_code == 503


async def test_deep_probe_not_ready_when_db_ping_times_out(monkeypatch):
    container = _make_container(pool=_FakePool(hang=True), redis=_FakeRedis())
    _patch_container(monkeypatch, container)

    resp = _new_response()
    result = await health.deep_readiness_probe(resp)

    assert result["ready"] is False
    assert result["db"] == "down"
    assert resp.status_code == 503


async def test_deep_probe_redis_down_stays_ready(monkeypatch):
    # Redis has an in-memory fallback -> a dead Redis is degraded, not fatal.
    container = _make_container(pool=_FakePool(), redis=_FakeRedis(fail=True))
    _patch_container(monkeypatch, container)

    resp = _new_response()
    result = await health.deep_readiness_probe(resp)

    assert result["ready"] is True
    assert result["db"] == "ok"
    assert result["redis"] == "down"
    assert resp.status_code != 503


async def test_deep_probe_redis_timeout_stays_ready(monkeypatch):
    container = _make_container(pool=_FakePool(), redis=_FakeRedis(hang=True))
    _patch_container(monkeypatch, container)

    resp = _new_response()
    result = await health.deep_readiness_probe(resp)

    assert result["ready"] is True
    assert result["redis"] == "down"


async def test_deep_probe_redis_disabled_reports_disabled(monkeypatch):
    container = _make_container(pool=_FakePool(), redis=None)
    _patch_container(monkeypatch, container)

    resp = _new_response()
    result = await health.deep_readiness_probe(resp)

    assert result["ready"] is True
    assert result["redis"] == "disabled"
    assert resp.status_code != 503


async def test_deep_probe_not_ready_when_container_uninitialized(monkeypatch):
    container = _make_container(initialized=False, pool=None, redis=None)
    _patch_container(monkeypatch, container)

    resp = _new_response()
    result = await health.deep_readiness_probe(resp)

    assert result["ready"] is False
    assert result["db"] == "not_initialized"
    assert result["redis"] == "not_initialized"
    assert resp.status_code == 503

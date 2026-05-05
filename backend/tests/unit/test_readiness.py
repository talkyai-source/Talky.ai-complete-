"""Tests for app.core.readiness and the /healthz/ready endpoint."""
from __future__ import annotations

import importlib

import pytest
from starlette.responses import Response


@pytest.fixture
def fresh_readiness():
    """Reload readiness module so each test starts with a clean slate."""
    from app.core import readiness as R
    importlib.reload(R)
    return R


@pytest.mark.asyncio
async def test_ready_when_no_providers_wired(fresh_readiness):
    R = fresh_readiness
    assert R.is_pod_ready() is True
    assert R.is_pod_at_capacity() is False


@pytest.mark.asyncio
async def test_capacity_blocks_readiness(fresh_readiness):
    R = fresh_readiness
    R.set_capacity_providers(
        active_count=lambda: 50,
        max_capacity=lambda: 50,
    )
    assert R.is_pod_at_capacity() is True
    assert R.is_pod_ready() is False


@pytest.mark.asyncio
async def test_drain_blocks_readiness_with_room_to_spare(fresh_readiness):
    R = fresh_readiness
    R.set_capacity_providers(
        active_count=lambda: 0,
        max_capacity=lambda: 50,
    )
    assert R.is_pod_ready() is True
    R.begin_drain()
    assert R.is_pod_ready() is False
    assert R.is_draining() is True


@pytest.mark.asyncio
async def test_readiness_probe_returns_503_when_not_ready(fresh_readiness):
    from app.api.v1.endpoints.health import readiness_probe

    R = fresh_readiness
    R.set_capacity_providers(active_count=lambda: 0, max_capacity=lambda: 10)
    R.begin_drain()
    resp = Response()
    body = await readiness_probe(resp)
    assert resp.status_code == 503
    assert resp.headers.get("retry-after") is not None
    assert body["ready"] is False
    assert body["draining"] is True

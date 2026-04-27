"""T2.2 integration — DIALER_QUEUE_BACKEND factory tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.queue_factory import (
    BACKEND_LIST,
    BACKEND_STREAMS,
    get_enqueue_service,
    resolve_queue_backend,
)
from app.domain.services.streams_queue_service import (
    DialerStreamsQueueService,
)


# ──────────────────────────────────────────────────────────────────────────
# resolve_queue_backend
# ──────────────────────────────────────────────────────────────────────────

def test_default_backend_is_list(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DIALER_QUEUE_BACKEND", raising=False)
    assert resolve_queue_backend() == BACKEND_LIST


def test_explicit_streams_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DIALER_QUEUE_BACKEND", "streams")
    assert resolve_queue_backend() == BACKEND_STREAMS


def test_explicit_list_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DIALER_QUEUE_BACKEND", "LIST")
    assert resolve_queue_backend() == BACKEND_LIST


def test_garbage_value_falls_back_to_list(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DIALER_QUEUE_BACKEND", "rabbitmq")
    assert resolve_queue_backend() == BACKEND_LIST


# ──────────────────────────────────────────────────────────────────────────
# get_enqueue_service
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_default_returns_list_service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DIALER_QUEUE_BACKEND", raising=False)
    fake_list = MagicMock(initialize=AsyncMock())
    svc = await get_enqueue_service(legacy_list_service=fake_list)
    assert svc is fake_list


@pytest.mark.asyncio
async def test_streams_backend_with_redis_returns_streams_service(monkeypatch):
    monkeypatch.setenv("DIALER_QUEUE_BACKEND", "streams")
    fake_redis = MagicMock()

    with patch.object(
        DialerStreamsQueueService, "ensure_groups", new_callable=AsyncMock,
    ) as ensure:
        svc = await get_enqueue_service(redis_client=fake_redis)

    assert isinstance(svc, DialerStreamsQueueService)
    ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_streams_without_redis_falls_back_to_list(monkeypatch):
    monkeypatch.setenv("DIALER_QUEUE_BACKEND", "streams")
    fake_list = MagicMock(initialize=AsyncMock())
    svc = await get_enqueue_service(redis_client=None, legacy_list_service=fake_list)
    # Must have fallen back; never built a streams service.
    assert svc is fake_list


@pytest.mark.asyncio
async def test_list_backend_initialises_when_not_supplied(monkeypatch):
    """When the caller doesn't pre-build a list service, the factory
    constructs and initialises one."""
    monkeypatch.delenv("DIALER_QUEUE_BACKEND", raising=False)
    fake_instance = MagicMock(initialize=AsyncMock())
    fake_cls = MagicMock(return_value=fake_instance)

    with patch(
        "app.domain.services.queue_factory.DialerQueueService", fake_cls,
    ):
        svc = await get_enqueue_service()

    assert svc is fake_instance
    fake_instance.initialize.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────────────
# Compatibility shims on the streams service
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_streams_close_is_noop():
    svc = DialerStreamsQueueService(MagicMock())
    # Must not raise; returns None.
    assert await svc.close() is None


@pytest.mark.asyncio
async def test_streams_clear_campaign_jobs_is_logging_noop():
    svc = DialerStreamsQueueService(MagicMock())
    cleared = await svc.clear_campaign_jobs("camp-1")
    # Streams backend can't bulk-delete; returns 0 and logs.
    assert cleared == 0

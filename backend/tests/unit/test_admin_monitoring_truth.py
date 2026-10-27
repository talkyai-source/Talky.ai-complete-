"""Truthful monitoring with synthetic dependency faults; no external probes."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from app.api.v1.endpoints import health, telephony_bridge
from app.api.v1.endpoints.admin import base
from app.api.v1.endpoints.admin.health import _shared, database, queues, workers
from app.core import container


def inject_conn(monkeypatch, module, conn):
    @asynccontextmanager
    async def acquire(*args, **kwargs):
        yield conn
    monkeypatch.setattr(module, "acquire_with_tenant", acquire)


@pytest.mark.asyncio
async def test_dashboard_uses_one_aggregate_and_keeps_zero_active_tenants(monkeypatch):
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={"active_calls": 9, "active_tenants": 0, "total_calls_24h": 20000, "failed_calls_24h": 500}))
    inject_conn(monkeypatch, base, conn)
    result = await base.get_dashboard_stats(SimpleNamespace(), SimpleNamespace(pool=object()))
    assert result.active_tenants == 0
    assert result.failed_call_rate_24h == "2.5%"
    assert result.failed_calls_24h == 500
    assert not hasattr(result, "api_errors_24h")
    sql = conn.fetchrow.await_args.args[0]
    assert "COUNT(*) FILTER" in sql and "no_answer" not in sql
    assert conn.fetchrow.await_count == 1


@pytest.mark.asyncio
async def test_dashboard_no_traffic_has_no_success_or_failure_rate(monkeypatch):
    inject_conn(monkeypatch, base, SimpleNamespace(fetchrow=AsyncMock(return_value={"active_calls": 0, "active_tenants": 0, "total_calls_24h": 0, "failed_calls_24h": 0})))
    result = await base.get_dashboard_stats(SimpleNamespace(), SimpleNamespace(pool=object()))
    assert result.failed_call_rate_24h is None


@pytest.mark.asyncio
async def test_dashboard_dependency_error_is_unavailable_not_zero(monkeypatch):
    inject_conn(monkeypatch, base, SimpleNamespace(fetchrow=AsyncMock(side_effect=RuntimeError("synthetic failure"))))
    with pytest.raises(HTTPException) as error:
        await base.get_dashboard_stats(SimpleNamespace(), SimpleNamespace(pool=object()))
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,reason", [("1000", "recent_heartbeat"), ("800", "heartbeat_stale"), ("1011", "heartbeat_in_future"), ("nan", "heartbeat_invalid"), ("inf", "heartbeat_invalid"), ("1e100", "heartbeat_in_future"), ("bad", "heartbeat_invalid"), (None, "heartbeat_missing")])
async def test_heartbeat_freshness_and_future_guard(raw, reason):
    result = await health.read_worker_heartbeats(SimpleNamespace(get=AsyncMock(return_value=raw)), now=1010)
    assert all(item["reason"] == reason for item in result)
    assert all(item["healthy"] == (reason == "recent_heartbeat") for item in result)


@pytest.mark.asyncio
async def test_missing_redis_is_unknown_role_inventory_never_fake_api_worker(monkeypatch):
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=False))
    result = await workers.get_workers_status(SimpleNamespace())
    assert {item.id for item in result.workers} == {"dialer", "voice", "reminder"}
    assert all(item.status == "unknown" for item in result.workers)
    assert all(item.processed_count is None and item.success_rate is None for item in result.workers)
    assert result.active_workers == 0 and result.busy_workers is None


@pytest.mark.asyncio
async def test_provider_probe_checks_error_envelope():
    result = await _shared._check_provider_health("DB", lambda: SimpleNamespace(error="synthetic response.error"))
    assert result.status == "down" and result.latency_ms is None


@pytest.mark.asyncio
async def test_provider_data_has_measured_dependency_scope_and_unknown_voice_accounts(monkeypatch):
    monkeypatch.setattr(_shared, "_database_probe", AsyncMock(return_value=12))
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=True, redis=SimpleNamespace(ping=AsyncMock(return_value=True))))
    adapter = SimpleNamespace(name="asterisk", health_check=AsyncMock(return_value=True))
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    result = {item.name: item for item in await _shared._internal_dependency_health(object())}
    assert result["Database"].latency_ms == 12
    assert result["Telephony"].status == "operational" and "not tenant carrier" in result["Telephony"].scope
    adapter.health_check.assert_awaited_once()
    for name in ("STT", "LLM", "TTS"):
        assert result[name].status == "unknown"
        assert result[name].latency_ms is None and result[name].last_check is None


@pytest.mark.asyncio
async def test_provider_negative_acknowledgement_is_down(monkeypatch):
    monkeypatch.setattr(_shared, "_database_probe", AsyncMock(side_effect=RuntimeError("unreachable")))
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=True, redis=SimpleNamespace(ping=AsyncMock(return_value=False))))
    monkeypatch.setattr(telephony_bridge, "_adapter", SimpleNamespace(name="asterisk", health_check=AsyncMock(return_value=False)))
    items = await _shared._internal_dependency_health(object())
    assert all(item.status == "down" and item.latency_ms is None for item in items[:3])


class Redis:
    async def scan(self, *_args, **_kwargs): return 0, ["tenant-A", "tenant-A", "tenant-B"]
    async def llen(self, key): return {"dialer:priority:queue": 2, "tenant-A": 3, "tenant-B": 5}[key]
    async def zcard(self, key): return {"dialer:scheduled": 7, "dialer:processing": 11}[key]


@pytest.mark.asyncio
async def test_dialer_depth_includes_tenant_priority_and_scheduled_without_duplicate_scan_count():
    result = await queues._dialer_backlog(Redis())
    assert result.status == "measured" and result.pending == 17 and result.processing == 11
    assert result.avg_processing_time_ms is None and result.success_rate_24h is None


@pytest.mark.asyncio
async def test_queue_read_error_and_unfinished_scan_never_become_zero():
    result = await queues._dialer_backlog(None)
    assert result.status == "unavailable" and result.pending is None
    redis = Redis()
    redis.scan = AsyncMock(return_value=(1, []))
    result = await queues._dialer_backlog(redis)
    assert result.pending is None and redis.scan.await_count == 25


@pytest.mark.asyncio
async def test_streams_use_group_lag_and_pending_not_retained_stream_history():
    redis = SimpleNamespace(type=AsyncMock(return_value="stream"),
        xinfo_groups=AsyncMock(return_value=[{"name": "dialers", "lag": 3, "pending": 4}]))
    result = await queues._streams_backlog(redis)
    assert result.pending == 6 and result.processing == 8
    redis.xinfo_groups.return_value = [{"name": "dialers", "lag": None, "pending": 4}]
    assert (await queues._streams_backlog(redis)).pending is None


@pytest.mark.asyncio
async def test_action_counts_include_actual_running_state_and_no_sample_rate_is_unknown(monkeypatch):
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={"pending": 2, "processing": 3, "completed": 0, "failed": 0, "avg_ms": None}))
    inject_conn(monkeypatch, queues, conn)
    result = await queues._action_backlog(object())
    assert result.processing == 3 and result.success_rate_24h is None and result.avg_processing_time_ms is None
    assert "'running','processing'" in conn.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_database_returns_measured_pool_and_size_not_constants(monkeypatch):
    inject_conn(monkeypatch, database, SimpleNamespace(fetchrow=AsyncMock(return_value={"size_bytes": 1024**2*37, "table_count": 42})))
    pool = SimpleNamespace(get_size=lambda: 6, get_idle_size=lambda: 4)
    result = await database.get_database_status(SimpleNamespace(), SimpleNamespace(pool=pool))
    assert result.connected and result.pool_size == 6 and result.active_connections == 2
    assert result.database_size_mb == 37 and result.table_count == 42
    inject_conn(monkeypatch, database, SimpleNamespace(fetchrow=AsyncMock(side_effect=RuntimeError("down"))))
    result = await database.get_database_status(SimpleNamespace(), SimpleNamespace(pool=pool))
    assert not result.connected and result.pool_size is None and result.database_size_mb is None

"""Actual expiry scheduler/service with synthetic database and delivery ports."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException

from app.domain.services import suspension_expiry, suspension_service


def event(number, target="user"):
    return {"suspension_id": UUID(int=number), "target_type": target,
            "target_id": UUID(int=number + 1000), "active": True}


def install(monkeypatch, rows):
    queries = []
    class Connection:
        async def fetch(self, query, after, limit):
            queries.append((query, after, limit))
            return [r for r in rows if r["active"] and (after is None or r["suspension_id"] > after)][:limit]
    connection = Connection()
    @asynccontextmanager
    async def acquire(pool, tenant, **kwargs):
        assert tenant is None and 0 < kwargs["timeout"] <= 3
        yield connection
    monkeypatch.setattr(suspension_service, "acquire_with_tenant", acquire)
    service = suspension_service.SuspensionService(object())
    effects = []
    async def restore(target, actor, reason, *, expected_suspension_id):
        assert actor is None
        row = next(r for r in rows if r["suspension_id"] == expected_suspension_id)
        row["active"] = False
        effects.append(expected_suspension_id)
        return SimpleNamespace(audit_event_id=UUID(int=9999))
    service.restore_user = restore
    service.restore_tenant = restore
    service.restore_partner = restore
    return SimpleNamespace(service=service, rows=rows, queries=queries, effects=effects, conn=connection)


@pytest.mark.asyncio
async def test_no_database_starts_no_task_and_performs_no_work():
    assert suspension_expiry.start_suspension_expiry_watchdog(None) is None
    assert await suspension_service.SuspensionService(None).process_auto_restores() == []


@pytest.mark.asyncio
async def test_bad_items_do_not_starve_later_holds_and_cursor_wraps(monkeypatch):
    fixture = install(monkeypatch, [event(i) for i in range(1, 6)])
    real_restore = fixture.service.restore_user
    async def restore(*args, **kwargs):
        if kwargs["expected_suspension_id"].int <= 2:
            raise HTTPException(409, "Historical ownership requires review")
        return await real_restore(*args, **kwargs)
    fixture.service.restore_user = restore
    results = [await fixture.service.process_auto_restores(batch_limit=2) for _ in range(4)]
    assert fixture.effects == [UUID(int=i) for i in (3, 4, 5)]
    assert all(r["restored"] is False for r in results[0])
    assert all(r["restored"] is False for r in results[3])
    assert all("ORDER BY suspension_id LIMIT $2" in q[0] for q in fixture.queries)


@pytest.mark.asyncio
async def test_unknown_target_cannot_be_reported_restored(monkeypatch):
    fixture = install(monkeypatch, [event(1, "unknown")])
    result = await fixture.service.process_auto_restores()
    assert result[0]["restored"] is False and fixture.effects == []


@pytest.mark.asyncio
async def test_timeout_after_commit_is_unknown_and_durable_reread_does_not_replay(monkeypatch):
    fixture = install(monkeypatch, [event(1)])
    restore = fixture.service.restore_user
    async def committed_then_lost(*args, **kwargs):
        await restore(*args, **kwargs)  # Synthetic durable commit precedes lost delivery response.
        await asyncio.Event().wait()
    fixture.service.restore_user = committed_then_lost
    result = await fixture.service.process_auto_restores(item_timeout_seconds=.01)
    assert result[0]["restored"] is None and result[0]["outcome"] == "unknown"
    assert fixture.effects == [UUID(int=1)] and not fixture.rows[0]["active"]
    assert await fixture.service.process_auto_restores() == []
    assert fixture.effects == [UUID(int=1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("commit", [False, True])
async def test_cancellation_and_restart_follow_durable_state(monkeypatch, commit):
    fixture = install(monkeypatch, [event(1)])
    entered = asyncio.Event()
    restore = fixture.service.restore_user
    async def interrupted(*args, **kwargs):
        if commit:
            await restore(*args, **kwargs)
        entered.set()
        await asyncio.Event().wait()
    fixture.service.restore_user = interrupted
    task = asyncio.create_task(fixture.service.process_auto_restores())
    await asyncio.wait_for(entered.wait(), .5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fixture.service.restore_user = restore
    fixture.service._auto_restore_cursor = None  # A fresh process starts from current durable events.
    result = await fixture.service.process_auto_restores()
    assert len(result) == int(not commit)
    assert fixture.effects == [UUID(int=1)]


@pytest.mark.asyncio
async def test_pass_budget_leaves_unattempted_items_for_next_rotation(monkeypatch):
    fixture = install(monkeypatch, [event(1), event(2)])
    restore = fixture.service.restore_user
    async def slow(*args, **kwargs):
        if kwargs["expected_suspension_id"].int == 1:
            await asyncio.Event().wait()
        return await restore(*args, **kwargs)
    fixture.service.restore_user = slow
    first = await fixture.service.process_auto_restores(time_budget_seconds=.01)
    assert len(first) == 1 and first[0]["outcome"] == "unknown"
    second = await fixture.service.process_auto_restores()
    assert len(second) == 1 and second[0]["restored"] is True
    assert fixture.effects == [UUID(int=2)]


@pytest.mark.asyncio
async def test_read_wait_is_bounded(monkeypatch):
    fixture = install(monkeypatch, [event(1)])
    async def wait_forever(*_args):
        await asyncio.Event().wait()
    fixture.conn.fetch = wait_forever
    with pytest.raises(TimeoutError):
        await fixture.service.process_auto_restores(read_timeout_seconds=.01)
    assert fixture.effects == []


@pytest.mark.asyncio
async def test_loop_first_pass_restart_and_database_recovery(monkeypatch):
    fixture = install(monkeypatch, [event(1)])
    actual_pass = fixture.service.process_auto_restores
    attempts, restored = [], asyncio.Event()
    async def recover():
        attempts.append(True)
        if len(attempts) == 1:
            raise ConnectionError("synthetic unavailable database")
        result = await actual_pass()
        restored.set()
        return result
    fixture.service.process_auto_restores = recover
    watchdog = suspension_expiry.SuspensionExpiryWatchdog(fixture.service, interval_seconds=.01)
    await asyncio.wait_for(restored.wait(), .5)
    await watchdog.stop()
    count = len(attempts)
    await asyncio.sleep(.02)
    assert len(attempts) == count and watchdog.task.done()
    fixture.rows.append(event(2))
    fixture.service.process_auto_restores = actual_pass
    restarted = suspension_expiry.SuspensionExpiryWatchdog(fixture.service, interval_seconds=60)
    for _ in range(20):
        if not fixture.rows[-1]["active"]:
            break
        await asyncio.sleep(0)
    await restarted.stop()
    assert fixture.effects == [UUID(int=1), UUID(int=2)]


@pytest.mark.asyncio
@pytest.mark.parametrize("database", [False, True])
async def test_actual_application_lifespan_starts_and_stops_expiry_without_telephony(monkeypatch, database):
    import importlib
    from app import main
    from tests.unit.test_transfer_restart_recovery_wiring import _DelayedOwnerState, _Container
    from app.domain.services.telephony import state_backend, inbound_admission
    from app.api.v1.endpoints import telephony_bridge
    from app.core import readiness
    container = _Container()
    container.db_pool = object() if database else None
    app = FastAPI()
    app.state._loop_lag_heartbeat_started = True
    observed = asyncio.Event()
    async def expiry_pass(self):
        assert self.db_pool is container.db_pool
        observed.set()
        return []
    async def shutdown():
        watchdog = app.state.suspension_expiry_watchdog
        assert watchdog is None or watchdog.task.done()
    container.shutdown = shutdown
    monkeypatch.setattr(suspension_service.SuspensionService, "process_auto_restores", expiry_pass)
    patches = {
        "app.core.container": {"get_container": lambda: container},
        "app.core.prod_gate": {"enforce_production_gate": lambda: None},
        "app.core.sentry_init": {"init_sentry": lambda: None},
        "app.core.telemetry": {"setup_telemetry": lambda _app: None, "shutdown_telemetry": lambda: None},
        "app.core.validation": {"validate_providers_on_startup": lambda **kwargs: None},
        "app.core.redis_durability": {"probe_redis_durability": AsyncMock()},
        "app.core.legacy_campaign_audit": {"audit_legacy_campaigns": AsyncMock(), "log_audit_summary": lambda value: None},
        "app.services.scripts.prompts.bodies": {"record_current_versions": AsyncMock(return_value=0), "load_cache": AsyncMock(return_value=0)},
        "app.domain.services.provider_cost_ledger": {"start_flusher": AsyncMock(), "stop_flusher": AsyncMock()},
        "app.domain.services.voice_tuning": {"get_voice_tuning_resolver": lambda: SimpleNamespace(set_db_lookup=lambda value: None)},
        "app.domain.services.tenant_ai_config_resolver": {"get_tenant_ai_config_resolver": lambda: SimpleNamespace(set_db_lookup=lambda value: None)},
        "app.domain.services.event_emitter": {"cleanup_expired_events_loop": AsyncMock()},
        "app.core.inbound_startup": {"platform_inbound_enabled": AsyncMock(return_value=False),
                                    "validate_production_inbound_database_role": AsyncMock()},
    }
    for module, values in patches.items():
        for name, value in values.items():
            monkeypatch.setattr(importlib.import_module(module), name, value)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("TELEPHONY_ENABLED", "false")
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: _DelayedOwnerState())
    monkeypatch.setattr(telephony_bridge, "_adapter", None)
    monkeypatch.setattr(inbound_admission, "start_inbound_admission_watchdog", lambda *a, **kw: SimpleNamespace(stop=AsyncMock()))
    monkeypatch.setattr(readiness, "begin_drain", lambda: None)
    monkeypatch.setattr(main, "_flush_audit_logger", AsyncMock())
    async with main.lifespan(app):
        if database:
            await asyncio.wait_for(observed.wait(), .5)
        else:
            assert app.state.suspension_expiry_watchdog is None
    assert observed.is_set() is database

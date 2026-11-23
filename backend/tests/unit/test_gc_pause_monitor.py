"""gc_pause_monitor: proof (log every full collection) and fix (freeze start-up heap).

Every test that installs the hook uninstalls it, and every test that freezes
unfreezes and resets the module flag (the ``_clean`` fixture), so the rest of
the suite is unaffected; the last test of the file asserts exactly that.
"""
from __future__ import annotations

import asyncio
import gc
import importlib
import logging
import os
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

# Imported here, not inside a test: importing the application calls
# logging.basicConfig(force=True), which removes every root handler, including
# the log capture of a test that is already running.
import app.main as app_main
from app.utils import gc_pause_monitor as gpm

_LOGGER = "app.utils.gc_pause_monitor"
# Importing the application leaves a few hundred objects frozen on its own (377
# measured: not our code), while a start-up heap freeze is ~100,000. "Nothing
# frozen" therefore means "below this".
_BASELINE = 5000


@pytest.fixture(autouse=True)
def _clean():
    gpm.uninstall()
    gpm.reset_startup_freeze_flag()
    yield
    gpm.uninstall()
    gc.unfreeze()
    gpm.reset_startup_freeze_flag()


def _lines(caplog, text):
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER and text in r.getMessage()]


# ── the hook ─────────────────────────────────────────────────────────────────

def test_a_full_collection_is_recorded_by_the_hook_and_logged_from_outside_it(caplog, monkeypatch):
    monkeypatch.setattr(gpm, "_FULL_LOG_THRESHOLD_MS", 0.0)   # every full collection gets a line
    gpm.install()
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        gc.collect(2)
        # The hook never logs: it runs inside the collector, in any thread, at
        # any allocation, possibly in the middle of another log call.
        assert not _lines(caplog, "gc_pause")
        assert len(gpm._pending) == 1
        assert gpm.log_pending() == 1      # the event loop's heartbeat does this
        assert gpm.log_pending() == 0 and gpm._pending == []
    lines = _lines(caplog, "gc_pause generation=2")
    assert len(lines) == 1
    assert re.fullmatch(
        r"gc_pause generation=2 ms=\d+\.\d collected=\d+ uncollectable=\d+ frozen=\d+ logged_after_ms=\d+",
        lines[0],
    ), lines[0]
    last = gpm.last_full_pause()
    assert last is not None and last[1] >= 0.0


def test_the_hook_itself_never_calls_the_logger(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the collector hook must not log")

    monkeypatch.setattr(gpm.logger, "info", boom)
    monkeypatch.setattr(gpm.logger, "warning", boom)
    monkeypatch.setattr(gpm.logger, "debug", boom)
    monkeypatch.setattr(gpm, "_FULL_LOG_THRESHOLD_MS", 0.0)
    gpm.install()
    gc.collect(2)
    gpm._hook("start", {})
    gpm._t0 = time.perf_counter() - 0.060
    gpm._hook("stop", {"generation": 1, "collected": 3, "uncollectable": 0})
    assert len(gpm._pending) == 2


def test_unlogged_pauses_are_bounded_when_nothing_drains_them(monkeypatch):
    monkeypatch.setattr(gpm, "_FULL_LOG_THRESHOLD_MS", 0.0)
    for _ in range(gpm._MAX_PENDING + 50):
        gpm._hook("start", {})
        gpm._hook("stop", {"generation": 2, "collected": 0, "uncollectable": 0})
    assert len(gpm._pending) == gpm._MAX_PENDING
    assert gpm.last_full_pause() is not None     # the newest pause is still remembered


@pytest.mark.asyncio
async def test_the_heartbeat_writes_the_recorded_pauses_within_a_tick(caplog, monkeypatch):
    monkeypatch.setattr(gpm, "_FULL_LOG_THRESHOLD_MS", 0.0)
    _event_loop_lag_heartbeat = app_main._event_loop_lag_heartbeat
    gpm._hook("start", {})
    gpm._hook("stop", {"generation": 2, "collected": 7, "uncollectable": 0})
    stop = asyncio.Event()
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        task = asyncio.ensure_future(_event_loop_lag_heartbeat(stop))
        for _ in range(40):
            await asyncio.sleep(0.01)
            if _lines(caplog, "gc_pause generation=2"):
                break
        stop.set()
        await asyncio.wait_for(task, 1.0)
    lines = _lines(caplog, "gc_pause generation=2")
    assert len(lines) == 1 and "collected=7" in lines[0]
    assert gpm._pending == []


def test_a_young_collection_under_the_threshold_logs_nothing(caplog):
    gpm.install()
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        gc.collect(0)
        assert gpm.log_pending() == 0
    assert not _lines(caplog, "gc_pause")
    assert gpm.last_full_pause() is None


def test_a_slow_young_collection_is_logged_but_is_not_a_full_pause(caplog):
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        gpm._hook("start", {})
        gpm._t0 = time.perf_counter() - 0.060
        gpm._hook("stop", {"generation": 1, "collected": 3, "uncollectable": 0})
        assert gpm.log_pending() == 1
    assert len(_lines(caplog, "gc_pause generation=1")) == 1
    assert gpm.last_full_pause() is None


def test_install_twice_registers_one_hook_and_uninstall_removes_it():
    assert gpm.install() is True
    assert gpm.install() is False
    assert gc.callbacks.count(gpm._hook) == 1
    gpm.uninstall()
    assert gpm._hook not in gc.callbacks


def test_the_hook_swallows_its_own_errors(monkeypatch):
    monkeypatch.setattr(gpm, "_FULL_LOG_THRESHOLD_MS", 0.0)
    gpm._hook("start", {})
    gpm._hook("stop", None)  # info.get raises inside the hook: swallowed
    assert gpm._pending == []

    def boom():
        raise RuntimeError("clock broke")

    monkeypatch.setattr(gpm.time, "monotonic", boom)
    gpm._hook("start", {})
    gpm._hook("stop", {"generation": 2, "collected": 0, "uncollectable": 0})  # must not raise
    assert gpm._pending == []


def test_full_pause_between_finds_a_pause_that_ended_inside_the_window_only():
    gpm._last_full = (100.0, 212.4)
    assert gpm.full_pause_between(99.0, 101.0) == pytest.approx(212.4)
    assert gpm.full_pause_between(100.0, 100.0) == pytest.approx(212.4)
    assert gpm.full_pause_between(101.0, 102.0) is None
    assert gpm.full_pause_between(90.0, 99.9) is None
    gpm._last_full = None
    assert gpm.full_pause_between(0.0, 1e9) is None


# ── the freeze ───────────────────────────────────────────────────────────────

def test_freeze_startup_heap_freezes_logs_and_does_nothing_twice(caplog, monkeypatch):
    monkeypatch.delenv("GC_FREEZE_STARTUP_HEAP", raising=False)  # default ON
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        assert gpm.freeze_startup_heap() is True
        assert gc.get_freeze_count() > _BASELINE
        assert len(_lines(caplog, "gc_startup_heap_freeze enabled=true")) == 1
        frozen = _lines(caplog, "gc_startup_heap_frozen")
        assert len(frozen) == 1
        assert re.fullmatch(r"gc_startup_heap_frozen objects=\d+ routes_prebuilt=0 prebuild_ms=\d+\.\d ms=\d+\.\d", frozen[0]), frozen[0]
        before = gc.get_freeze_count()
        caplog.clear()
        assert gpm.freeze_startup_heap() is False
        assert gc.get_freeze_count() <= before  # frozen objects may be freed, never added
        assert not [r for r in caplog.records if r.name == _LOGGER]


@pytest.mark.parametrize("value", ["0", "false", "No", "OFF"])
def test_switch_off_freezes_nothing_and_the_wiring_line_says_so(caplog, monkeypatch, value):
    monkeypatch.setenv("GC_FREEZE_STARTUP_HEAP", value)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        assert gpm.freeze_startup_heap() is False
    assert gc.get_freeze_count() < _BASELINE
    assert len(_lines(caplog, "gc_startup_heap_freeze enabled=false")) == 1
    assert not _lines(caplog, "gc_startup_heap_frozen")


# ── the real lifespan ────────────────────────────────────────────────────────

async def _run_lifespan(monkeypatch, heartbeat_already_started=True, inside=None):
    from app import main
    from app.api.v1.endpoints import telephony_bridge
    from app.core import readiness
    from app.domain.services.telephony import inbound_admission, state_backend
    from tests.unit.test_transfer_restart_recovery_wiring import _Container, _DelayedOwnerState

    container = _Container()
    container.db_pool = None
    app = FastAPI()
    app.state._loop_lag_heartbeat_started = heartbeat_already_started

    async def shutdown():
        return None

    container.shutdown = shutdown
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
        if inside is not None:
            inside()


@pytest.mark.asyncio
async def test_a_lifespan_start_under_the_test_configuration_freezes_nothing(monkeypatch):
    # No setenv here: this is the tests' own configuration (tests/conftest.py).
    assert os.environ.get("GC_FREEZE_STARTUP_HEAP") == "0"
    await _run_lifespan(monkeypatch)
    assert gc.get_freeze_count() < _BASELINE  # the lifespan itself froze nothing


@pytest.mark.asyncio
async def test_the_lifespan_freezes_when_the_switch_is_on(monkeypatch):
    """Non-vacuity: the lifespan really calls the freeze (so the test above means something)."""
    monkeypatch.setenv("GC_FREEZE_STARTUP_HEAP", "1")
    await _run_lifespan(monkeypatch)
    assert gc.get_freeze_count() > _BASELINE


@pytest.mark.asyncio
async def test_the_lifespan_installs_the_hook_when_it_starts_the_heartbeat(monkeypatch):
    # Every other lifespan test marks the heartbeat as already started, so the
    # line that installs the hook was never executed by a test: deleting it
    # failed nothing.
    seen = []
    await _run_lifespan(
        monkeypatch, heartbeat_already_started=False,
        inside=lambda: seen.append(gpm._hook in gc.callbacks),
    )
    assert seen == [True]


# ── full collections under the line threshold are counted, not logged one by one ──

def _full_pause(ms):
    gpm._hook("start", {})
    gpm._t0 = time.perf_counter() - ms / 1000.0
    gpm._hook("stop", {"generation": 2, "collected": 1, "uncollectable": 0})


def test_short_full_collections_give_one_summary_line_a_minute_and_a_long_one_its_own(caplog, monkeypatch):
    clock = [5000.0]
    monkeypatch.setattr(gpm.time, "monotonic", lambda: clock[0])
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        for _ in range(100):
            _full_pause(2.0)
        assert gpm._pending == []                  # none of them is queued for a line
        assert gpm.last_full_pause() is not None   # but the newest is still remembered
        assert gpm.log_pending() == 0              # the minute starts now
        clock[0] += 59.0
        assert gpm.log_pending() == 0
        _full_pause(150.0)                         # a long one gets its own line at once
        assert gpm.log_pending() == 1
        clock[0] += 2.0
        assert gpm.log_pending() == 1              # the summary, once the minute is over
        assert gpm.log_pending() == 0
    assert len(_lines(caplog, "gc_pause generation=2 ms=15")) == 1
    summary = _lines(caplog, "gc_pause_summary")
    assert len(summary) == 1
    assert re.fullmatch(
        r"gc_pause_summary full_under_20ms=100 total_ms=\d+\.\d max_ms=\d+\.\d window_s=61 frozen=\d+",
        summary[0],
    ), summary[0]


def test_a_stop_whose_start_was_never_seen_records_nothing():
    # install() can land in the middle of a collection: the "stop" then has no
    # start time, and the pause would be computed from zero (hundreds of hours).
    gpm._hook("stop", {"generation": 2, "collected": 0, "uncollectable": 0})
    assert gpm.last_full_pause() is None and gpm._pending == [] and gpm._quiet[0] == 0
    gpm._hook("start", {})
    gpm._hook("stop", {"generation": 2, "collected": 0, "uncollectable": 0})
    gpm._hook("stop", {"generation": 2, "collected": 0, "uncollectable": 0})   # a second stop: nothing more
    assert gpm._quiet[0] == 1


# ── the web framework's per-route objects are built before the freeze ────────

class _Item(BaseModel):
    name: str
    size: int = 0


def _small_app():
    Item = _Item
    small = FastAPI()

    @small.get("/items/{item_id}")
    async def read_item(item_id: int, q: str = "") -> _Item:
        return Item(name=q)

    @small.post("/items")
    async def create_item(item: _Item) -> _Item:
        return item

    return small


def test_prebuilding_the_routes_asks_every_route_and_one_odd_route_stops_nothing():
    small = _small_app()

    class _Odd:
        def matches(self, scope):
            raise RuntimeError("odd route")

    small.router.routes.insert(0, _Odd())
    asked = gpm.prebuild_route_objects(small)
    assert asked == len(small.router.routes) - 1     # every route but the odd one
    assert gpm.prebuild_route_objects(object()) == 0  # not an application: nothing, no error


def test_prebuilding_means_the_first_request_builds_almost_nothing():
    from fastapi.testclient import TestClient

    def tracked_added_by_first_request(prebuild):
        small = _small_app()
        client = TestClient(small)
        if prebuild:
            gpm.prebuild_route_objects(small)
        gc.collect()
        before = len(gc.get_objects())
        assert client.get("/items/1").status_code == 200
        gc.collect()
        return len(gc.get_objects()) - before

    cold = tracked_added_by_first_request(prebuild=False)
    warm = tracked_added_by_first_request(prebuild=True)
    assert warm < cold, (cold, warm)


def test_freeze_startup_heap_prebuilds_the_routes_first(caplog, monkeypatch):
    monkeypatch.setenv("GC_FREEZE_STARTUP_HEAP", "1")
    small = _small_app()
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        assert gpm.freeze_startup_heap(small) is True
    frozen = _lines(caplog, "gc_startup_heap_frozen")
    assert len(frozen) == 1
    assert f"routes_prebuilt={len(small.router.routes)} " in frozen[0]


# ── must stay LAST: nothing above may leak into the rest of the suite ────────

def test_zz_nothing_leaks_out_of_this_file():
    """Every earlier test uninstalled/unfroze by itself or via ``_clean``; after
    this file the global state is the one the rest of the suite expects."""
    assert gc.get_freeze_count() < _BASELINE
    assert gpm._hook not in gc.callbacks
    assert gpm.last_full_pause() is None

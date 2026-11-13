"""Abuse detection runs record-only and off the outbound call path."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import telephony_bridge as tb
from app.domain.services import abuse_detection as ad
from app.domain.services import abuse_observer as ao

TENANT = "11111111-1111-1111-1111-111111111111"
CALL = "22222222-2222-2222-2222-222222222222"


class _Conn:
    def __init__(self, count=0, numbers=()):
        self.count, self.numbers, self.inserts, self.sqls = count, numbers, [], []

    async def fetchval(self, sql, *args):
        assert "tenant_id" in sql
        return self.count

    async def fetch(self, sql, *args):
        assert "tenant_id" in sql
        return [{"phone_number": n} for n in self.numbers]

    async def fetchrow(self, sql, *args):
        assert "INSERT INTO abuse_events" in sql and "tenant_id" in sql
        self.inserts.append(args)
        self.sqls.append(sql)
        return {"id": "e"}


@pytest.fixture
def conn(monkeypatch):
    c = _Conn()

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id):
        yield c

    monkeypatch.setattr(ao, "acquire_with_tenant", fake_acquire)
    monkeypatch.setattr(ad, "acquire_with_tenant", fake_acquire)
    return c


def _svc():
    return ao.AbuseObserver(db_pool=object(), redis_client=None)


async def test_premium_rate_number_records_an_event_as_flagged(conn):
    events = await _svc().observe_outbound_call(TENANT, "+19005550100", CALL)
    assert [e.abuse_type for e in events] == [ad.AbuseType.PREMIUM_RATE]
    args = conn.inserts[0]
    assert args[0] == TENANT and args[2] == "premium_rate"
    assert "flagged" in conn.sqls[0]  # record-only: never "blocked"
    assert '"recommended_action": "block"' in args[8]


async def test_rapid_repeat_number_records(conn):
    conn.count = 3
    events = await _svc().observe_outbound_call(TENANT, "+14155550100", CALL)
    assert ad.AbuseType.REPEAT_NUMBER in [e.abuse_type for e in events]
    assert conn.inserts


async def test_normal_call_records_nothing(conn):
    assert await _svc().observe_outbound_call(TENANT, "+442046132300", CALL) == []
    assert conn.inserts == []


async def test_detector_error_does_not_propagate(monkeypatch):
    async def boom(self, *a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ao.AbuseObserver, "observe_outbound_call", boom)
    await tb._observe_abuse(SimpleNamespace(db_pool=object()), TENANT, "+1", CALL)


async def test_detector_timeout_does_not_propagate(monkeypatch):
    async def slow(self, *a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(ao.AbuseObserver, "observe_outbound_call", slow)
    monkeypatch.setattr(tb, "_ABUSE_OBSERVE_TIMEOUT_S", 0.01)
    await tb._observe_abuse(SimpleNamespace(db_pool=object()), TENANT, "+1", CALL)


async def test_spawn_returns_immediately_and_runs_in_background(monkeypatch):
    seen = []

    async def fake(container, tenant_id, phone, call_id):
        await asyncio.sleep(0.01)
        seen.append((tenant_id, phone, call_id))

    monkeypatch.setattr(tb, "_observe_abuse", fake)
    monkeypatch.delenv("ABUSE_DETECTION_ENABLED", raising=False)
    tb._spawn_abuse_observation(object(), tenant_id=TENANT, phone_number="+1", call_id=CALL)
    assert seen == []  # not awaited by the caller
    await asyncio.gather(*list(tb._abuse_observation_tasks))
    assert seen == [(TENANT, "+1", CALL)]


async def test_kill_switch_turns_it_off(monkeypatch):
    called = []

    async def fake(*a):
        called.append(a)

    monkeypatch.setattr(tb, "_observe_abuse", fake)
    monkeypatch.setenv("ABUSE_DETECTION_ENABLED", "false")
    tb._spawn_abuse_observation(object(), tenant_id=TENANT, phone_number="+1", call_id=CALL)
    await asyncio.sleep(0)
    assert called == [] and not tb._abuse_observation_tasks


def test_no_enforcement_in_the_observe_path():
    import inspect

    src = inspect.getsource(ao.AbuseObserver.observe_outbound_call)
    src += inspect.getsource(ao.AbuseObserver._record_event)
    for word in ("UPDATE tenants", "suspend", "raise HTTPException", "DELETE"):
        assert word not in src


@pytest.fixture(autouse=True)
def _reset_observer_state():
    ao._in_flight = 0
    ao._dropped = 0
    ao._last_drop_log = float("-inf")
    ao._dedup_local.clear()
    yield


class _Redis:
    def __init__(self, fail=False):
        self.keys, self.fail = set(), fail

    async def set(self, key, value, nx=False, ex=None):
        if self.fail:
            raise ConnectionError("down")
        assert nx and ex == 3600
        if key in self.keys:
            return None
        self.keys.add(key)
        return True


def _event(tenant=TENANT, kind=ad.AbuseType.PREMIUM_RATE, number="+19005550100"):
    return ad.AbuseEvent(abuse_type=kind, severity=ad.Severity.HIGH, tenant_id=tenant, phone_number=number)


async def test_third_concurrent_observation_is_dropped_without_touching_db(conn):
    ao._in_flight = ao.MAX_IN_FLIGHT
    assert await _svc().observe_outbound_call(TENANT, "+19005550100", CALL) == []
    assert conn.inserts == [] and conn.sqls == [] and ao._dropped == 1


async def test_slot_is_released_after_a_failure(monkeypatch):
    async def boom(self, *a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(ao.AbuseObserver, "analyze_call_initiated", boom)
    with pytest.raises(RuntimeError):
        await _svc().observe_outbound_call(TENANT, "+1", CALL)
    assert ao._in_flight == 0


async def test_same_event_within_the_hour_is_recorded_once(conn):
    svc = ao.AbuseObserver(db_pool=object(), redis_client=_Redis())
    await svc.observe_outbound_call(TENANT, "+19005550100", CALL)
    await svc.observe_outbound_call(TENANT, "+19005550100", CALL)
    assert len(conn.inserts) == 1


async def test_other_tenant_or_event_type_is_still_recorded():
    svc = ao.AbuseObserver(db_pool=object(), redis_client=_Redis())
    assert await svc._claim(_event()) is True
    assert await svc._claim(_event()) is False
    assert await svc._claim(_event(tenant="33333333-3333-3333-3333-333333333333")) is True
    assert await svc._claim(_event(kind=ad.AbuseType.TOLL_FRAUD)) is True


async def test_redis_failure_fails_open_then_is_capped_locally():
    svc = ao.AbuseObserver(db_pool=object(), redis_client=_Redis(fail=True))
    assert await svc._claim(_event()) is True
    assert await svc._claim(_event()) is False  # no flood during a Redis outage

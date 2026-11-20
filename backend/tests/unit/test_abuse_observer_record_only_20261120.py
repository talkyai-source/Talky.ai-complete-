"""The abuse observer is record-only: its rows can never make CallGuard block."""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import telephony_bridge as tb
from app.domain.services import abuse_detection as ad
from app.domain.services import abuse_observer as ao
from app.domain.services import call_guard as cg

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER = "33333333-3333-3333-3333-333333333333"

# Literal constraint lists: Alembic 0093 (action_taken) and
# database/fresh_schema.sql abuse_events_severity_check (severity).
ACTION_CHECK = ("flagged", "warned", "throttled", "blocked", "suspended")
SEVERITY_CHECK = ("low", "medium", "high", "critical")


class _VelocityConn:
    """Evaluates the guard's SQL against rows: tenant, severity, resolved and
    the observer exclusion are applied only if the statement says so."""

    def __init__(self, rows):
        self.rows = rows

    async def fetchval(self, sql, tenant_id):
        flat = " ".join(sql.split())
        assert "tenant_id = $1" in flat
        excludes = bool(re.search(
            r"COALESCE\(action_details->>'source', ''\) <> 'abuse_observer'", flat))
        n = 0
        for r in self.rows:
            if r["tenant_id"] != tenant_id or r["severity"] not in ("high", "critical"):
                continue
            if r["resolved_at"] is not None:
                continue
            if excludes and (r["action_details"] or {}).get("source") == "abuse_observer":
                continue
            n += 1
        return n


def _guard(monkeypatch, rows):
    @asynccontextmanager
    async def fake(pool, tenant_id):
        yield _VelocityConn(rows)

    monkeypatch.setattr(cg, "acquire_with_tenant", fake)
    return cg.CallGuard(db_pool=object(), redis_client=None)


def _row(source=None, tenant=TENANT, severity="high"):
    return {"tenant_id": tenant, "severity": severity, "resolved_at": None,
            "action_details": {"source": source} if source else {}}


async def test_observer_high_row_does_not_block_next_call(monkeypatch):
    g = _guard(monkeypatch, [_row("abuse_observer")])
    res = await g._check_velocity(TENANT)
    assert res.passed is True


async def test_non_observer_high_row_still_blocks(monkeypatch):
    g = _guard(monkeypatch, [_row(), _row("abuse_observer")])
    res = await g._check_velocity(TENANT)
    assert res.passed is False
    assert res.details["recent_high_severity_events"] == 1


async def test_other_tenants_event_does_not_block(monkeypatch):
    g = _guard(monkeypatch, [_row(tenant=OTHER)])
    assert (await g._check_velocity(TENANT)).passed is True


async def test_observer_insert_marks_row_and_satisfies_checks(monkeypatch):
    captured = {}

    class Conn:
        async def fetchrow(self, sql, *args):
            captured["sql"], captured["args"] = sql, args
            return {"id": "e"}

    @asynccontextmanager
    async def fake(pool, tenant_id):
        yield Conn()

    monkeypatch.setattr(ao, "acquire_with_tenant", fake)
    svc = ao.AbuseObserver(db_pool=object(), redis_client=None)
    # Build the event through the detector so the field set is the real one.
    events = await _detect(monkeypatch, "+19005550100")
    ev = events[0]
    await svc._record_event(ev, call_id=None)
    m = re.search(r"VALUES \(.*?'(\w+)', \$9", " ".join(captured["sql"].split()))
    assert m and m.group(1) in ACTION_CHECK
    assert ev.severity.value in SEVERITY_CHECK
    assert {s.value for s in ad.Severity} <= set(SEVERITY_CHECK)
    assert json.loads(captured["args"][8])["source"] == ao.OBSERVER_SOURCE == "abuse_observer"


async def _detect(monkeypatch, number):
    class Conn:
        async def fetchval(self, sql, *a):
            return 0

        async def fetch(self, sql, *a):
            return []

    @asynccontextmanager
    async def fake(pool, tenant_id):
        yield Conn()

    monkeypatch.setattr(ad, "acquire_with_tenant", fake)
    return await ao.AbuseObserver(db_pool=object(), redis_client=None).analyze_call_initiated(
        TENANT, number)


async def test_failing_db_in_observer_is_swallowed(monkeypatch, caplog):
    @asynccontextmanager
    async def boom(pool, tenant_id):
        raise ConnectionError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(ao, "acquire_with_tenant", boom)
    monkeypatch.setattr(ad, "acquire_with_tenant", boom)
    with caplog.at_level("WARNING"):
        # The wrapper the call path spawns: must return normally.
        await tb._observe_abuse(SimpleNamespace(db_pool=object()), TENANT, "+19005550100", None)
    assert ao._in_flight == 0
    assert "abuse_observe_failed" in caplog.text or "Failed to record" in caplog.text


async def test_spawn_returns_before_observer_finishes(monkeypatch):
    gate = asyncio.Event()
    finished = []

    async def slow(container, tenant_id, phone, call_id):
        await gate.wait()
        finished.append(1)

    monkeypatch.setattr(tb, "_observe_abuse", slow)
    monkeypatch.delenv("ABUSE_DETECTION_ENABLED", raising=False)
    tb._spawn_abuse_observation(object(), tenant_id=TENANT, phone_number="+1", call_id=None)
    assert finished == [] and len(tb._abuse_observation_tasks) == 1
    gate.set()
    await asyncio.gather(*list(tb._abuse_observation_tasks))
    assert finished == [1]


@pytest.mark.parametrize("value", ["false", "0", "off", "no"])
async def test_detection_disabled_switches_observer_off(monkeypatch, value):
    called = []

    async def fake(*a):
        called.append(a)

    monkeypatch.setattr(tb, "_observe_abuse", fake)
    monkeypatch.setenv("ABUSE_DETECTION_ENABLED", value)
    tb._spawn_abuse_observation(object(), tenant_id=TENANT, phone_number="+1", call_id=None)
    await asyncio.sleep(0)
    assert called == [] and not tb._abuse_observation_tasks


@pytest.fixture(autouse=True)
def _reset():
    ao._in_flight = 0
    ao._dedup_local.clear()
    yield

"""campaigns-contacts-crm-12: stats are aggregated in SQL, tenant-scoped, and
keep the exact response shape and goals_achieved semantics."""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from app.api.v1.endpoints import campaigns as campaigns_ep


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Q:
    def __init__(self, db, table):
        self._db, self._table = db, table

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a):
        return self

    def neq(self, *_a):
        return self

    def execute(self):
        if self._table in ("calls", "dialer_jobs"):
            raise AssertionError(f"stats must not row-scan {self._table} in Python")
        if self._table == "campaigns":
            return _Resp([{"id": "c1", "tenant_id": "t1", "status": "running"}])
        return _Resp([], count=0)


class _DB:
    pool = object()

    def table(self, name):
        return _Q(self, name)


class _Conn:
    def __init__(self, log, string_rows):
        self._log, self._string_rows = log, string_rows

    async def fetch(self, sql, *args):
        self._log.append((sql, args))
        if "FROM dialer_jobs" in sql:
            return [{"status": "completed", "n": 7}, {"status": "failed", "n": 2}]
        if "jsonb_typeof(summary_json) = 'string'" in sql:
            return self._string_rows
        return [{"outcome": "answered", "n": 5, "goals": 2},
                {"outcome": "no_answer", "n": 4, "goals": 0}]


class _User:
    tenant_id = "t1"
    id = "u1"


def _run(monkeypatch, string_rows=()):
    log = []

    @contextlib.asynccontextmanager
    async def _acq(pool, tenant_id):
        log.append(("tenant", tenant_id))
        yield _Conn(log, list(string_rows))

    monkeypatch.setattr(campaigns_ep, "acquire_with_tenant", _acq)
    out = asyncio.run(campaigns_ep.get_campaign_stats(
        campaign_id="c1", current_user=_User(), db_client=_DB(),
    ))
    return out, log


def test_stats_use_tenant_scoped_sql_aggregates(monkeypatch):
    out, log = _run(monkeypatch)
    assert ("tenant", "t1") in log
    queries = [e for e in log if e[0] != "tenant"]
    assert len(queries) == 3
    for sql, args in queries:
        assert "tenant_id = $2::uuid" in sql
        assert args == ("c1", "t1")
    assert out["job_status_counts"] == {"completed": 7, "failed": 2}
    assert out["call_outcome_counts"] == {"answered": 5, "no_answer": 4}
    assert out["goals_achieved"] == 2
    assert set(out) == {
        "campaign_id", "campaign_status", "total_leads", "qualified_leads",
        "job_status_counts", "call_outcome_counts", "goals_achieved",
        "blocking_reason", "activity",
    }


def test_string_encoded_summary_json_still_counts(monkeypatch):
    rows = [
        {"raw": json.dumps({"outcome": "Qualified | wants demo"})},
        {"raw": json.dumps({"outcome": " callback: tue"})},
        {"raw": json.dumps({"outcome": "not interested"})},
        {"raw": "not json"},
    ]
    out, _ = _run(monkeypatch, rows)
    assert out["goals_achieved"] == 2 + 2

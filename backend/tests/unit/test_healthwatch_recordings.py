"""healthwatch `recordings` check: answered calls that ended without a saved recording."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID

import pytest

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def watch(monkeypatch):
    spec = importlib.util.spec_from_file_location("qa_healthwatch_rec", BACKEND / "scripts/healthwatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.recording_spool, "list_entries", lambda: [])
    return module


def _install(watch, monkeypatch, *, answered=0, matched=0, mode=None, recorded=0, unconfirmed=0,
             transfer_missing=0, columns=2, fail=False, rows_fail=False):
    seen = []

    class Conn:
        def __init__(self, tenant):
            self.tenant = tenant

        async def fetchval(self, sql, *args):
            if "information_schema.columns" in sql:
                return columns
            if "to_regclass" in sql:
                return False
            return 0

        async def fetch(self, sql, *args):
            if "FROM tenants" in sql:
                return [{"id": UUID(TENANT)}]
            return []

        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchrow(self, sql, *args):
            seen.append(sql)
            assert self.tenant == TENANT and args == (TENANT,)
            if rows_fail:
                raise RuntimeError("column does not exist")
            if "FROM calls" in sql:
                return {"answered": answered, "mode": mode}
            return {"recorded": recorded, "unconfirmed": unconfirmed,
                    "transfer_missing": transfer_missing, "matched": matched}

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kw):
        if fail:
            raise RuntimeError("down")
        yield Conn(str(tenant_id) if tenant_id else None)

    monkeypatch.setattr(watch, "acquire_with_tenant", acquire)
    return seen


async def _report(watch, redis=None):
    return (await watch.check_database(object(), redis, now=NOW, max_tenants=10))["recordings"]


def _entry(age_seconds, awaiting=None):
    return NS(created_at=(NOW - timedelta(seconds=age_seconds)).isoformat(), awaiting=awaiting)


async def test_nothing_missing_is_ok(watch, monkeypatch):
    _install(watch, monkeypatch, answered=4, matched=4, recorded=4)
    assert await _report(watch) == {"status": "ok", "missing": 0, "pending_stuck": 0,
                                    "transfer_missing": 0, "unconfirmed": 0, "recorded": 4}


async def test_an_answered_call_without_a_recording_alerts(watch, monkeypatch):
    _install(watch, monkeypatch, answered=2, matched=1, recorded=1)
    report = await _report(watch)
    assert report["status"] == "alert" and report["missing"] == 1


async def test_a_tenant_that_disabled_recording_is_skipped(watch, monkeypatch):
    seen = _install(watch, monkeypatch, answered=5, matched=0, mode="disabled")
    report = await _report(watch)
    assert report["status"] == "ok" and report["missing"] == 0
    assert not any("FROM recordings_s3" in sql for sql in seen)


async def test_the_statements_exclude_calls_that_ended_less_than_fifteen_minutes_ago(watch, monkeypatch):
    seen = _install(watch, monkeypatch)
    await _report(watch)
    for sql in seen:
        assert "interval '15 minutes'" in sql and "interval '24 hours'" in sql
        assert sql.rsplit("WHERE", 1)[1].lstrip().startswith(("tenant_id = $1::uuid", "r.tenant_id = $1::uuid"))
        assert "direction = 'outbound'" in sql and "duration_seconds >= 5" in sql
        assert "is_test = false" in sql, "test calls are not counted as missing"


async def test_a_stuck_pending_entry_alerts(watch, monkeypatch):
    _install(watch, monkeypatch)
    monkeypatch.setattr(watch.recording_spool, "list_entries", lambda: [_entry(901)])
    report = await _report(watch)
    assert report["status"] == "alert" and report["pending_stuck"] == 1


async def test_a_young_pending_entry_and_a_ten_minute_awaiting_entry_are_not_stuck(watch, monkeypatch):
    _install(watch, monkeypatch)
    monkeypatch.setattr(watch.recording_spool, "list_entries",
                        lambda: [_entry(600, awaiting="transfer_part"), _entry(100)])
    report = await _report(watch)
    assert report["status"] == "ok" and report["pending_stuck"] == 0


async def test_an_awaiting_entry_older_than_ninety_minutes_is_stuck(watch, monkeypatch):
    _install(watch, monkeypatch)
    monkeypatch.setattr(watch.recording_spool, "list_entries", lambda: [_entry(5401, awaiting="transfer_part")])
    assert (await _report(watch))["pending_stuck"] == 1


async def test_an_unreadable_pending_folder_is_reported_as_unavailable(watch, monkeypatch):
    _install(watch, monkeypatch)

    def boom():
        raise OSError("x")
    monkeypatch.setattr(watch.recording_spool, "list_entries", boom)
    report = await _report(watch)
    assert report["pending_stuck"] is None
    assert report["status"] == "unavailable" and report["reason"] == "pending_folder_unreadable"


@pytest.mark.parametrize("unconfirmed,recorded,status", [
    (3, 6, "alert"), (3, 5, "alert"), (3, 7, "ok"), (2, 2, "ok"), (0, 0, "ok")])
async def test_unconfirmed_ratio_rule(watch, monkeypatch, unconfirmed, recorded, status):
    _install(watch, monkeypatch, unconfirmed=unconfirmed, recorded=recorded)
    report = await _report(watch)
    assert report["status"] == status and report["unconfirmed"] == unconfirmed


async def test_a_missing_transfer_part_alerts(watch, monkeypatch):
    _install(watch, monkeypatch, transfer_missing=1, recorded=10)
    report = await _report(watch)
    assert report["status"] == "alert" and report["transfer_missing"] == 1


async def test_before_the_migration_the_new_columns_report_zero(watch, monkeypatch):
    seen = _install(watch, monkeypatch, columns=0, recorded=3, answered=1, matched=1)
    report = await _report(watch)
    assert report["unconfirmed"] == 0 and report["transfer_missing"] == 0 and report["status"] == "ok"
    assert not any("notice_state" in sql or "transfer_part" in sql for sql in seen)


async def test_database_error_is_unavailable(watch, monkeypatch):
    _install(watch, monkeypatch, fail=True)
    assert await _report(watch) == {"status": "unavailable", "reason": "observation_unavailable"}


async def test_a_fault_in_the_recording_read_does_not_take_the_phone_line_check_with_it(watch, monkeypatch):
    """Both checks share one database read per tenant. The recording statements
    run inside a savepoint: when they fail, only this check is unavailable."""
    _install(watch, monkeypatch, rows_fail=True)

    async def lines(rows, redis, *, now):
        return {"status": "ok", "alert_count": 0, "pending_count": 0, "checked": len(rows)}

    monkeypatch.setattr(watch, "check_trunks", lines)
    report = await watch.check_database(object(), None, now=NOW, max_tenants=10)
    assert report["recordings"]["status"] == "unavailable"
    assert report["trunks"]["status"] == "ok"

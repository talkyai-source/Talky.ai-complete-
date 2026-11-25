"""healthwatch `recordings`: an answered INCOMING call without a recording is counted
(only while the platform-wide switch platform_runtime_controls.inbound_recording_enabled is on)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from uuid import UUID

import pytest

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def watch(monkeypatch):
    spec = importlib.util.spec_from_file_location("qa_healthwatch_inbound_rec", BACKEND / "scripts/healthwatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.recording_spool, "list_entries", lambda: [])
    return module


def _install(watch, monkeypatch, *, switch=True, answered=0, matched=0, mode=None,
             in_answered=0, in_matched=0, inbound_fail=False):
    """switch: True / False / "boom" (the read raises)."""
    seen = []

    class Conn:
        def __init__(self, tenant):
            self.tenant = tenant

        async def fetchval(self, sql, *args):
            seen.append(sql)
            if "information_schema.columns" in sql:
                return 2
            if "to_regclass" in sql:
                return False
            if "platform_runtime_controls" in sql:
                if switch == "boom":
                    raise RuntimeError("no such column")
                return switch
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
            if "answered_inbound" in sql:
                if inbound_fail:
                    raise RuntimeError("boom")
                return {"answered_inbound": in_answered, "matched_inbound": in_matched}
            if "FROM calls" in sql:
                return {"answered": answered, "mode": mode}
            return {"recorded": answered, "unconfirmed": 0, "transfer_missing": 0, "matched": matched}

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kw):
        yield Conn(str(tenant_id) if tenant_id else None)

    monkeypatch.setattr(watch, "acquire_with_tenant", acquire)
    return seen


async def _report(watch):
    return (await watch.check_database(object(), None, now=NOW, max_tenants=10))["recordings"]


async def test_an_answered_incoming_call_without_a_recording_alerts_when_the_switch_is_on(watch, monkeypatch):
    _install(watch, monkeypatch, switch=True, in_answered=1, in_matched=0)
    report = await _report(watch)
    assert report["missing_inbound"] == 1
    assert report["status"] == "alert"
    assert report["missing"] == 0


async def test_the_same_call_does_not_alert_when_the_switch_is_off(watch, monkeypatch):
    seen = _install(watch, monkeypatch, switch=False, in_answered=1, in_matched=0)
    report = await _report(watch)
    assert report["missing_inbound"] == 0 and report["status"] == "ok"
    assert not any("answered_inbound" in sql for sql in seen), "no incoming statement while the switch is off"


async def test_an_unreadable_switch_is_none_and_the_outgoing_numbers_are_still_reported(watch, monkeypatch):
    _install(watch, monkeypatch, switch="boom", answered=2, matched=1, in_answered=5)
    report = await _report(watch)
    assert report["missing_inbound"] is None
    assert report["missing"] == 1 and report["status"] == "alert"


async def test_an_unreadable_switch_alone_is_not_an_alert(watch, monkeypatch):
    _install(watch, monkeypatch, switch="boom", answered=1, matched=1)
    report = await _report(watch)
    assert report["missing_inbound"] is None and report["status"] == "ok" and report["missing"] == 0


async def test_a_tenant_with_recording_disabled_is_not_counted(watch, monkeypatch):
    seen = _install(watch, monkeypatch, switch=True, mode="disabled", in_answered=3, in_matched=0)
    report = await _report(watch)
    assert report["missing_inbound"] == 0 and report["status"] == "ok"
    assert not any("answered_inbound" in sql for sql in seen)


async def test_an_incoming_call_with_a_matched_recording_is_zero(watch, monkeypatch):
    _install(watch, monkeypatch, switch=True, in_answered=2, in_matched=2)
    report = await _report(watch)
    assert report["missing_inbound"] == 0 and report["status"] == "ok"


async def test_a_fault_in_the_incoming_statement_makes_only_the_incoming_part_unreadable(watch, monkeypatch):
    _install(watch, monkeypatch, switch=True, inbound_fail=True, answered=2, matched=1)
    report = await _report(watch)
    assert report["missing_inbound"] is None
    assert report["missing"] == 1 and report["status"] == "alert"


async def test_the_unreadable_pending_folder_shape_carries_the_key_too(watch, monkeypatch):
    _install(watch, monkeypatch, switch=True)

    def boom():
        raise OSError("x")
    monkeypatch.setattr(watch.recording_spool, "list_entries", boom)
    report = await _report(watch)
    assert report["status"] == "unavailable" and report["reason"] == "pending_folder_unreadable"
    assert report["missing_inbound"] == 0


async def test_the_outgoing_statements_are_unchanged_and_an_incoming_alert_does_not_leak_into_them(watch, monkeypatch):
    seen = _install(watch, monkeypatch, switch=False, answered=4, matched=3)
    report = await _report(watch)
    assert report["missing"] == 1 and report["missing_inbound"] == 0
    outgoing = [sql for sql in seen if sql in (watch._CALLS_ANSWERED_SQL, watch._RECORDINGS_SQL)]
    assert len(outgoing) == 2
    assert "direction = 'outbound'" in watch._CALLS_ANSWERED_SQL and "inbound" not in watch._CALLS_ANSWERED_SQL
    assert "inbound" not in watch._RECORDINGS_SQL and "inbound" not in watch._RECORDINGS_OLD_SQL


def test_the_incoming_statement_uses_the_same_window_and_filters_as_the_outgoing_one(watch):
    sql = watch._INBOUND_SQL
    for part in ("direction = 'inbound'", "is_test = false", "duration_seconds >= 5",
                 "interval '15 minutes'", "interval '24 hours'", "c.tenant_id = $1::uuid",
                 "r.tenant_id = c.tenant_id"):
        assert part in sql
    # Only calls certainly recorded: the notice was spoken and recording allowed.
    assert "c.consent_status = 'granted'" in sql


def test_the_failure_notice_names_a_missing_incoming_recording(watch):
    # The notice is sent again only when this text changes. An alert caused by
    # incoming recordings alone used to read "recordings:alert:0+0+0+0".
    state = {"status": "alert", "missing": 0, "pending_stuck": 0, "transfer_missing": 0,
             "unconfirmed": 0, "recorded": 3}
    assert watch.fingerprint("recordings", {**state, "missing_inbound": 2}) == "recordings:alert:0+0+0+0+in2"
    assert watch.fingerprint("recordings", {**state, "missing_inbound": 3}) == "recordings:alert:0+0+0+0+in3"


@pytest.mark.parametrize("inbound", [0, None, "absent"])
def test_the_failure_notice_is_unchanged_when_no_incoming_recording_is_missing(watch, inbound):
    # So the release itself sends no repeat notice for a state that did not change.
    state = {"status": "alert", "missing": 2, "pending_stuck": 0, "transfer_missing": 1,
             "unconfirmed": 0, "recorded": 9}
    if inbound != "absent":
        state["missing_inbound"] = inbound
    assert watch.fingerprint("recordings", state) == "recordings:alert:2+0+1+0"

"""healthwatch `recordings`: the notice alarm judges calls where the person STAYED ON.

About half of answered calls are hung up within seconds, mid-notice, and are
recorded with notice_state 'unconfirmed' by design (27 Nov 2026: 17 of 38). The
old 50% rule on all answered calls sat at its threshold and flapped (30 of 57,
37 of 76). Now: alert when people who stayed 30 s or more mostly never got the
notice, when four in five (and at least 15) of all answered calls have none, or when the last
4 hours hold 12 or more answered calls and not one confirmed notice.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID

import pytest

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def watch(monkeypatch):
    spec = importlib.util.spec_from_file_location("qa_healthwatch_long", BACKEND / "scripts/healthwatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.recording_spool, "list_entries", lambda: [])
    return module


def _install(watch, monkeypatch, *, recorded=100, unconfirmed=0, recorded_answered=0, unconfirmed_answered=0,
             answered_long=0, unconfirmed_long=0, answered_recent=0, unconfirmed_recent=0, transfer_missing=0,
             missing=0, in_answered=0, switch=False):
    class Conn:
        async def fetchval(self, sql, *args):
            if "information_schema.columns" in sql:
                return 2
            if "to_regclass" in sql:
                return False
            if "platform_runtime_controls" in sql:
                return switch
            return 0

        async def fetch(self, sql, *args):
            return [{"id": UUID(TENANT)}] if "FROM tenants" in sql else []

        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchrow(self, sql, *args):
            if "answered_inbound" in sql:
                return {"answered_inbound": in_answered, "matched_inbound": 0}
            if "FROM calls" in sql and "recordings_s3" not in sql:
                return {"answered": missing, "mode": None}
            return {"recorded": recorded, "unconfirmed": unconfirmed, "transfer_missing": transfer_missing,
                    "matched": 0, "recorded_answered": recorded_answered,
                    "unconfirmed_answered": unconfirmed_answered,
                    "answered_long": answered_long, "unconfirmed_long": unconfirmed_long,
                    "answered_recent": answered_recent, "unconfirmed_recent": unconfirmed_recent}

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kw):
        yield Conn()

    monkeypatch.setattr(watch, "acquire_with_tenant", acquire)


async def _report(watch):
    return (await watch.check_database(object(), None, now=NOW, max_tenants=10))["recordings"]


async def test_27_november_baseline_is_ok(watch, monkeypatch):
    # 27 Nov had 8 long calls, all confirmed (6 at once, 2 by the late repeat).
    _install(watch, monkeypatch, recorded_answered=57, unconfirmed_answered=30, answered_long=8, unconfirmed_long=0)
    assert (await _report(watch))["status"] == "ok"


async def test_long_calls_half_unconfirmed_alert(watch, monkeypatch):
    _install(watch, monkeypatch, recorded_answered=57, unconfirmed_answered=30, answered_long=6, unconfirmed_long=3)
    assert (await _report(watch))["status"] == "alert"


@pytest.mark.parametrize("answered_long,unconfirmed_long,status", [
    (6, 2, "ok"), (7, 3, "ok"), (6, 3, "alert"), (5, 3, "alert"), (2, 2, "ok"), (0, 0, "ok")])
async def test_long_call_ratio_edges(watch, monkeypatch, answered_long, unconfirmed_long, status):
    _install(watch, monkeypatch, recorded_answered=57, unconfirmed_answered=30,
             answered_long=answered_long, unconfirmed_long=unconfirmed_long)
    assert (await _report(watch))["status"] == status


@pytest.mark.parametrize("recorded_answered,unconfirmed_answered,status", [
    # four in five or more, and at least 15 of them
    (45, 40, "alert"), (20, 16, "alert"), (18, 15, "alert"), (15, 15, "alert"),
    # under four in five
    (20, 15, "ok"), (100, 79, "ok"),
    # four in five, but fewer than 15
    (17, 14, "ok"), (14, 14, "ok"),
    # the two measured ordinary days
    (57, 30, "ok"), (76, 37, "ok")])
async def test_nobody_gets_the_notice_arm(watch, monkeypatch, recorded_answered, unconfirmed_answered, status):
    _install(watch, monkeypatch, recorded_answered=recorded_answered, unconfirmed_answered=unconfirmed_answered,
             answered_long=0, unconfirmed_long=0)
    assert (await _report(watch))["status"] == status


@pytest.mark.parametrize("answered_recent,unconfirmed_recent,status", [
    (12, 12, "alert"), (20, 20, "alert"), (12, 11, "ok"), (11, 11, "ok"), (0, 0, "ok")])
async def test_the_notice_has_stopped_arm(watch, monkeypatch, answered_recent, unconfirmed_recent, status):
    # everything else is an ordinary day
    _install(watch, monkeypatch, recorded_answered=57, unconfirmed_answered=30, answered_long=8, unconfirmed_long=0,
             answered_recent=answered_recent, unconfirmed_recent=unconfirmed_recent)
    assert (await _report(watch))["status"] == status


async def test_a_missing_recording_still_alerts_alone(watch, monkeypatch):
    _install(watch, monkeypatch, missing=1)
    assert (await _report(watch))["status"] == "alert"


async def test_a_missing_transfer_part_still_alerts_alone(watch, monkeypatch):
    _install(watch, monkeypatch, transfer_missing=1)
    assert (await _report(watch))["status"] == "alert"


async def test_a_stuck_pending_entry_still_alerts_alone(watch, monkeypatch):
    _install(watch, monkeypatch)
    old = NS(created_at=(NOW - timedelta(seconds=901)).isoformat(), awaiting=None)
    monkeypatch.setattr(watch.recording_spool, "list_entries", lambda: [old])
    assert (await _report(watch))["status"] == "alert"


async def test_a_missing_incoming_recording_still_alerts_alone(watch, monkeypatch):
    _install(watch, monkeypatch, switch=True, in_answered=1)
    report = await _report(watch)
    assert report["status"] == "alert" and report["missing_inbound"] == 1


async def test_the_result_carries_the_long_call_counts(watch, monkeypatch):
    _install(watch, monkeypatch, recorded_answered=57, unconfirmed_answered=30, answered_long=8, unconfirmed_long=2)
    report = await _report(watch)
    assert (report["answered_long"], report["unconfirmed_long"]) == (8, 2)
    assert (report["recorded_answered"], report["unconfirmed_answered"]) == (57, 30)


async def test_the_result_carries_the_recent_counts(watch, monkeypatch):
    _install(watch, monkeypatch, answered_recent=9, unconfirmed_recent=4)
    report = await _report(watch)
    assert (report["answered_recent"], report["unconfirmed_recent"]) == (9, 4)


async def test_the_unreadable_pending_folder_shape_carries_them_too(watch, monkeypatch):
    _install(watch, monkeypatch, answered_long=8, unconfirmed_long=2, answered_recent=9, unconfirmed_recent=4)

    def boom():
        raise OSError("x")
    monkeypatch.setattr(watch.recording_spool, "list_entries", boom)
    report = await _report(watch)
    assert report["status"] == "unavailable" and report["reason"] == "pending_folder_unreadable"
    assert (report["answered_long"], report["unconfirmed_long"]) == (8, 2)
    assert (report["answered_recent"], report["unconfirmed_recent"]) == (9, 4)


def _fp(watch, **kw):
    value = {"status": "alert", "missing": 0, "pending_stuck": 0, "transfer_missing": 0, "unconfirmed": 0,
             "unconfirmed_answered": 0, "unconfirmed_long": 0, "recorded": 0, **kw}
    return watch.fingerprint("recordings", value)


def test_fingerprint_follows_long_calls_not_all_answered_calls(watch):
    assert _fp(watch, unconfirmed_answered=30) == _fp(watch, unconfirmed_answered=31)
    assert _fp(watch, unconfirmed_long=3) != _fp(watch, unconfirmed_long=4)
    assert _fp(watch, unconfirmed_long=3).endswith("+3")


def test_fingerprint_marks_the_nobody_gets_it_arm_with_a_fixed_word(watch):
    # 40 of 45: the arm holds, the text ends "+all"; the count itself is NOT in the text,
    # so the 41st such call does not send the alert again.
    a = _fp(watch, unconfirmed_answered=40, recorded_answered=45)
    b = _fp(watch, unconfirmed_answered=41, recorded_answered=46)
    assert a.endswith("+all") and a == b
    # an ordinary day (30 of 57) carries no such word
    assert not _fp(watch, unconfirmed_answered=30, recorded_answered=57).endswith("+all")
    # counts that are missing or not numbers never raise and never add the word
    value = {"status": "alert", "missing": 0, "pending_stuck": 0, "transfer_missing": 0, "unconfirmed_long": 0,
             "unconfirmed_answered": None, "recorded_answered": "45"}
    assert watch.fingerprint("recordings", value) == "recordings:alert:0+0+0+0"
    # with an incoming recording missing, "all" comes after the "in<N>" part
    both = watch.fingerprint("recordings", {"status": "alert", "missing": 0, "pending_stuck": 0,
                                            "transfer_missing": 0, "unconfirmed_long": 0, "missing_inbound": 2,
                                            "unconfirmed_answered": 40, "recorded_answered": 45})
    assert both == "recordings:alert:0+0+0+0+in2+all"


def test_fingerprint_marks_the_notice_has_stopped_arm_with_a_fixed_word(watch):
    a = _fp(watch, unconfirmed_recent=12, answered_recent=12)
    assert a.endswith("+now") and a == _fp(watch, unconfirmed_recent=13, answered_recent=13)
    assert not _fp(watch, unconfirmed_recent=12, answered_recent=13).endswith("+now")
    assert not _fp(watch, unconfirmed_recent=11, answered_recent=11).endswith("+now")
    # a count that is not a number never raises and never adds the word
    assert not _fp(watch, unconfirmed_recent=None, answered_recent=12).endswith("+now")
    assert not _fp(watch, unconfirmed_recent="12", answered_recent="12").endswith("+now")
    every = watch.fingerprint("recordings", {"status": "alert", "missing": 0, "pending_stuck": 0,
                                             "transfer_missing": 0, "unconfirmed_long": 0, "missing_inbound": 2,
                                             "unconfirmed_answered": 40, "recorded_answered": 45,
                                             "unconfirmed_recent": 14, "answered_recent": 14})
    assert every == "recordings:alert:0+0+0+0+in2+all+now"


def test_fingerprint_tolerates_a_value_without_the_long_count(watch):
    value = {"status": "alert", "missing": 1, "pending_stuck": 0, "transfer_missing": 0, "unconfirmed_answered": 5}
    assert watch.fingerprint("recordings", value) == "recordings:alert"


def test_guard_sql_text_selects_the_long_counts(watch):
    """Guard (text match), not proof of behaviour: see the _pg.py test for that."""
    sql = watch._RECORDINGS_SQL
    assert "AS answered_long" in sql and "AS unconfirmed_long" in sql
    assert sql.count("c.ended_at - c.answered_at >= interval '30 seconds'") == 2
    old = watch._RECORDINGS_OLD_SQL
    assert "0 AS answered_long" in old and "0 AS unconfirmed_long" in old
    assert "0 AS answered_recent" in old and "0 AS unconfirmed_recent" in old


def test_guard_sql_text_selects_the_recent_counts_and_limits_denominators(watch):
    """Guard (text match), not proof of behaviour: see the _pg.py test for that."""
    sql = watch._RECORDINGS_SQL
    assert "AS answered_recent" in sql and "AS unconfirmed_recent" in sql
    assert sql.count("interval '4 hours'") == 2
    # recorded_answered, answered_long and answered_recent count only recordings that need a notice
    assert sql.count("r.notice_state IN ('delivered', 'unconfirmed')") == 3

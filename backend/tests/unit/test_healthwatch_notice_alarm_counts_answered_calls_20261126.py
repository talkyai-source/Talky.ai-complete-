"""healthwatch `recordings`: the notice alarm looks at calls a PERSON answered.

A voicemail, an unanswered call or an instant hang-up is recorded with notice
state `unconfirmed` because the notice was never spoken. That is most
recordings (25 Nov 2026: 155 of 210), so counting them kept the check red
every day and hid 8 really lost recordings.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from uuid import UUID

import pytest

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
TENANT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def watch(monkeypatch):
    spec = importlib.util.spec_from_file_location("qa_healthwatch_notice", BACKEND / "scripts/healthwatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.recording_spool, "list_entries", lambda: [])
    return module


def _install(watch, monkeypatch, *, recorded=0, unconfirmed=0, recorded_answered=0, unconfirmed_answered=0,
             missing=0):
    seen = []

    class Conn:
        def __init__(self, tenant):
            self.tenant = tenant

        async def fetchval(self, sql, *args):
            return 2 if "information_schema.columns" in sql else False if "to_regclass" in sql else 0

        async def fetch(self, sql, *args):
            return [{"id": UUID(TENANT)}] if "FROM tenants" in sql else []

        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchrow(self, sql, *args):
            seen.append(sql)
            if "FROM calls" in sql and "recordings_s3" not in sql:
                return {"answered": missing, "mode": None}
            return {"recorded": recorded, "unconfirmed": unconfirmed, "transfer_missing": 0, "matched": 0,
                    "recorded_answered": recorded_answered, "unconfirmed_answered": unconfirmed_answered}

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kw):
        yield Conn(str(tenant_id) if tenant_id else None)

    monkeypatch.setattr(watch, "acquire_with_tenant", acquire)
    return seen


async def _report(watch):
    return (await watch.check_database(object(), None, now=NOW, max_tenants=10))["recordings"]


async def test_25_november_voicemails_alone_do_not_alarm(watch, monkeypatch):
    _install(watch, monkeypatch, recorded=210, unconfirmed=155, recorded_answered=57, unconfirmed_answered=4)
    report = await _report(watch)
    assert report["status"] == "ok"
    assert (report["unconfirmed"], report["recorded"]) == (155, 210)
    assert (report["recorded_answered"], report["unconfirmed_answered"]) == (57, 4)


async def test_the_notice_failing_on_answered_calls_alarms(watch, monkeypatch):
    _install(watch, monkeypatch, recorded=210, unconfirmed=155, recorded_answered=57, unconfirmed_answered=30)
    assert (await _report(watch))["status"] == "alert"


@pytest.mark.parametrize("unconfirmed_answered,recorded_answered,status", [
    (2, 2, "ok"), (3, 6, "alert"), (3, 5, "alert"), (3, 7, "ok"), (0, 0, "ok")])
async def test_answered_ratio_edges(watch, monkeypatch, unconfirmed_answered, recorded_answered, status):
    _install(watch, monkeypatch, recorded=100, unconfirmed=90, recorded_answered=recorded_answered,
             unconfirmed_answered=unconfirmed_answered)
    assert (await _report(watch))["status"] == status


async def test_a_missing_recording_still_alerts(watch, monkeypatch):
    _install(watch, monkeypatch, recorded=10, recorded_answered=5, missing=1)
    report = await _report(watch)
    assert report["status"] == "alert" and report["missing"] == 1


def test_the_statement_counts_calls_picked_up_and_not_a_machine(watch):
    """Picked up and not a machine: a call our own agent failed (`failed`) or whose
    outcome was never saved (NULL) must still count, or the outage that silences
    the notice would also silence this alarm."""
    from app.domain.services.call_status import CallOutcome
    pick = "c.answered_at IS NOT NULL AND COALESCE(c.outcome, '') NOT IN ('voicemail', 'no_answer')"
    for sql in (watch._RECORDINGS_SQL,):
        assert sql.count(pick) == 2
        assert "ANSWERED_OUTCOMES" not in sql and "'failed'" not in sql
    assert not hasattr(watch, "ANSWERED_OUTCOMES")
    # the literals are pinned to the application's own names (a rename is caught)
    assert (CallOutcome.VOICEMAIL.value, CallOutcome.NO_ANSWER.value) == ("voicemail", "no_answer")


async def test_an_outage_of_our_own_agent_alarms(watch, monkeypatch):
    # 40 picked-up calls failed by our pipeline (outcome failed / none), notice unconfirmed,
    # plus 5 answered and delivered: all 45 count as picked up.
    _install(watch, monkeypatch, recorded=120, unconfirmed=110, recorded_answered=45, unconfirmed_answered=40)
    assert (await _report(watch))["status"] == "alert"


def test_fingerprint_ignores_voicemails_and_follows_answered_count(watch):
    def fp(**kw):
        value = {"missing": 0, "pending_stuck": 0, "transfer_missing": 0, "unconfirmed": 0,
                 "unconfirmed_answered": 0, "recorded": 0, "recorded_answered": 0, **kw}
        return watch.fingerprint("recordings", {"status": "alert", **value})

    # two days that differ only in voicemails recorded: same identity
    assert fp(unconfirmed=40, recorded=60, unconfirmed_answered=4) == fp(
        unconfirmed=155, recorded=210, unconfirmed_answered=4)
    assert fp(unconfirmed_answered=4) != fp(unconfirmed_answered=5)


def test_sentence_names_answered_calls():
    from app.core import operational_alerts as oa
    text = oa._SENTENCES["recordings"]("alert", "0+0+0+7")
    assert "7 recordings of answered calls were saved without a confirmed notice" in text

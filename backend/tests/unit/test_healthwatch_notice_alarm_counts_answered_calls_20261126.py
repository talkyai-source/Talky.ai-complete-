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
             answered_long=0, unconfirmed_long=0, answered_recent=0,
             unconfirmed_recent=0, missing=0):
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
                    "recorded_answered": recorded_answered, "unconfirmed_answered": unconfirmed_answered,
                    "answered_long": answered_long, "unconfirmed_long": unconfirmed_long,
                    "answered_recent": answered_recent, "unconfirmed_recent": unconfirmed_recent}

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
    # Callers who stayed 30 s or more and never got the notice (8 of 10): alarm.
    _install(watch, monkeypatch, recorded=210, unconfirmed=155, recorded_answered=57, unconfirmed_answered=30,
             answered_long=10, unconfirmed_long=8)
    assert (await _report(watch))["status"] == "alert"


@pytest.mark.parametrize("unconfirmed_answered,recorded_answered,status", [
    (2, 2, "ok"), (3, 6, "alert"), (3, 5, "alert"), (3, 7, "ok"), (0, 0, "ok")])
async def test_long_call_arm_ratio_edges(watch, monkeypatch, unconfirmed_answered, recorded_answered, status):
    # The ratio now reads calls the person stayed on for 30 s or more.
    _install(watch, monkeypatch, recorded=100, unconfirmed=90, recorded_answered=recorded_answered,
             unconfirmed_answered=unconfirmed_answered, answered_long=recorded_answered,
             unconfirmed_long=unconfirmed_answered)
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
        assert sql.count(pick) == 6  # recorded/unconfirmed_answered + answered/unconfirmed_long + answered/unconfirmed_recent
        assert "ANSWERED_OUTCOMES" not in sql and "'failed'" not in sql
    assert not hasattr(watch, "ANSWERED_OUTCOMES")
    # the literals are pinned to the application's own names (a rename is caught)
    assert (CallOutcome.VOICEMAIL.value, CallOutcome.NO_ANSWER.value) == ("voicemail", "no_answer")


async def test_an_outage_of_our_own_agent_alarms(watch, monkeypatch):
    # 40 picked-up calls failed by our pipeline (outcome failed / none), notice unconfirmed,
    # plus 5 answered and delivered: all 45 count as picked up.
    # Nobody stays on a call the agent says nothing on, so there are NO long calls here: the
    # alarm must come from the share of all answered calls (40 of 45, four in five or more).
    _install(watch, monkeypatch, recorded=120, unconfirmed=110, recorded_answered=45, unconfirmed_answered=40)
    assert (await _report(watch))["status"] == "alert"


def test_fingerprint_ignores_voicemails_and_follows_long_call_count(watch):
    def fp(**kw):
        value = {"missing": 0, "pending_stuck": 0, "transfer_missing": 0, "unconfirmed": 0,
                 "unconfirmed_answered": 0, "unconfirmed_long": 0, "recorded": 0, "recorded_answered": 0, **kw}
        return watch.fingerprint("recordings", {"status": "alert", **value})

    # two days that differ only in voicemails recorded: same identity
    assert fp(unconfirmed=40, recorded=60, unconfirmed_long=4) == fp(
        unconfirmed=155, recorded=210, unconfirmed_long=4)
    assert fp(unconfirmed_long=4) != fp(unconfirmed_long=5)


def test_sentence_names_long_calls():
    # The fourth number is now the calls the person stayed on for 30 s or more.
    from app.core import operational_alerts as oa
    text = oa._SENTENCES["recordings"]("alert", "0+0+0+7")
    assert "7 recordings of calls of 30 seconds or more were saved without a confirmed notice." in text
    assert "answered calls were saved" not in text
    assert "Four in five" not in text
    one = oa._SENTENCES["recordings"]("alert", "0+0+0+1")
    assert "1 recording of a call of 30 seconds or more was saved without a confirmed notice." in one


def test_sentence_says_when_the_notice_reaches_nobody():
    from app.core import operational_alerts as oa
    nobody = "Four in five answered calls have no confirmed recording notice."
    assert oa._SENTENCES["recordings"]("alert", "0+0+0+0+all").endswith(nobody)
    # with the incoming part before it, both are still read
    both = oa._SENTENCES["recordings"]("alert", "0+0+0+0+in2+all")
    assert "2 answered incoming calls have no recording." in both and both.endswith(nobody)
    # "all" in one of the four count places is not the word: it reads as 0 there
    assert nobody not in oa._SENTENCES["recordings"]("alert", "all+0+0+0")
    # the detail text with the new part still passes the alert's own format check
    assert oa._DETAIL.match("recordings:alert:0+0+0+0+in2+all,trunks:alert:1")


def test_sentence_says_when_the_notice_has_stopped():
    from app.core import operational_alerts as oa
    stopped = " None of the answered calls of the last 4 hours got a confirmed recording notice."
    assert oa._SENTENCES["recordings"]("alert", "0+0+0+0+now").endswith(stopped)
    assert "Four in five" not in oa._SENTENCES["recordings"]("alert", "0+0+0+0+now")
    # all three parts, in the order the health watch writes them
    every = oa._SENTENCES["recordings"]("alert", "0+0+0+0+in2+all+now")
    assert "2 answered incoming calls have no recording." in every
    assert "Four in five answered calls have no confirmed recording notice." in every and every.endswith(stopped)
    assert stopped not in oa._SENTENCES["recordings"]("alert", "now+0+0+0")
    assert oa._DETAIL.match("recordings:alert:0+0+0+0+in2+all+now")

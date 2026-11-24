"""24 Nov 2026: a corrected number is never dialled under its old value.

The website edit and ``manage_lead`` change ``leads.phone_number`` and cancel the
contact's not-yet-dialled jobs. Two things used to leave a job carrying the OLD
number: a retry booked at settlement copied the job row's number (not the
contact's), and a payload already in Redis kept dialling the number it was queued
with. Two guards now close that:

* dial time: the worker reads the contact's number (a new tenant-scoped read,
  outside ``_stale_payload_reason``) and drops the job when it differs;
* settlement: the retry carries the contact's saved number and the job row is
  re-pointed to it.

"Cannot tell" (an empty side, a job number that is not strict E.164, a number that
does not parse) always means: dial as before, one log line, never drop.

Voice callbacks (``action_execution.py:402-407``): a callback is booked only when
the confirmed phone equals the lead's number, so a callback to a different,
caller-given number cannot exist as a job. A changed contact number therefore
drops a callback job exactly like any other.

Test numbers are libphonenumber example numbers: the fixture ``+447700900123`` is
libphonenumber-invalid (its comparison key is empty), so it would pass vacuously.
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import phonenumbers
import pytest

from app.domain.models.dialer_job import CallOutcome, JobStatus
from app.domain.services.call_redial_service import redial_job_id
from app.domain.services.dialer.job_states import ACTIVE_STATUSES
from app.domain.services.call_service import CallService
from app.domain.services.phone_number_normalizer import normalize_suppression_key
from app.services.voice_callback_service import callback_job_id
from tests.unit.test_dialer_stuck_and_lost_leads_20261121 import LEAD, CAMPAIGN, _intent
from tests.unit.test_live_risk_settlement import _Queue, _SettleConn
from tests.unit.test_w1_dialer_job_lifecycle import (
    TENANT_A,
    TENANT_B,
    _conn_pool,
    _job,
    _worker,
)


def _example(region: str, kind) -> str:
    number = phonenumbers.example_number_for_type(region, kind)
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


OLD = _example("GB", phonenumbers.PhoneNumberType.FIXED_LINE)
NEW = _example("GB", phonenumbers.PhoneNumberType.MOBILE)
US = _example("US", phonenumbers.PhoneNumberType.FIXED_LINE)

# Never pass vacuously: each example must be its own comparison key.
for _n in (OLD, NEW, US):
    assert normalize_suppression_key(_n) == _n
assert OLD != NEW

DRIFT_LOGGER = "app.domain.services.dialer.contact_number_drift"
NUMBER_LOG = "dialer_contact_number_"


def _job_with(number: str, *, tenant: str = TENANT_A, job_id: str | None = None):
    job = _job(tenant=tenant)
    job.phone_number = number
    if job_id is not None:
        job.job_id = job_id
    return job


def _number_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(NUMBER_LOG)]


def _dialled_number(w) -> str:
    w._make_call.assert_awaited_once()
    return w._make_call.call_args.args[0].phone_number


# ═══ 1. the contact's number differs: the old number is never dialled ═══


@pytest.mark.asyncio
async def test_a_job_whose_number_differs_from_the_contact_is_dropped(caplog):
    caplog.set_level(logging.DEBUG)
    w = _worker()
    conn = _conn_pool(w, fetchrow={"phone_number": NEW})

    await w.process_job(_job_with(OLD))

    w._make_call.assert_not_awaited()
    w._create_call_intent.assert_not_awaited()
    w.queue_service.mark_skipped.assert_awaited_once()
    assert w.queue_service.mark_skipped.call_args.kwargs["reason"] == "lead_number_changed"
    w._update_job_status.assert_awaited_once()
    assert w._update_job_status.call_args.args[1] == JobStatus.SKIPPED
    assert w._update_job_status.call_args.kwargs["error"] == "lead_number_changed"
    conn.execute.assert_awaited_once()
    sql, *args = conn.execute.call_args.args
    assert "status = 'called'" in sql and "SET status = 'pending'" in sql
    assert args == [LEAD, TENANT_A, CAMPAIGN, list(ACTIVE_STATUSES)]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert OLD[-6:] not in text and NEW[-6:] not in text


# ═══ 2. anti-zero-calls: the same number written another way is dialled ═══


def _international(number: str) -> str:
    return phonenumbers.format_number(
        phonenumbers.parse(number, None), phonenumbers.PhoneNumberFormat.INTERNATIONAL
    )


def _national_digits(number: str) -> str:
    return phonenumbers.format_number(
        phonenumbers.parse(number, None), phonenumbers.PhoneNumberFormat.NATIONAL
    ).replace(" ", "").replace("-", "").replace("(", "").replace(")", "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job_number, contact_number",
    [
        (OLD, OLD),
        (US, US),
        (OLD, _international(OLD)),
        (US, _national_digits(US)),
    ],
)
async def test_the_same_number_written_another_way_is_still_dialled(job_number, contact_number):
    w = _worker()
    _conn_pool(w, fetchrow={"phone_number": contact_number})

    await w.process_job(_job_with(job_number))

    assert _dialled_number(w) == job_number
    w.queue_service.mark_skipped.assert_not_awaited()


# ═══ 3. cannot tell: dial as before, exactly one number log line ═══


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job_number, row",
    [
        (NEW, None),
        (NEW, {"phone_number": None}),
        (NEW, {"phone_number": ""}),
        (NEW, {"phone_number": "abc"}),
        ("+10000000000", {"phone_number": NEW}),
    ],
)
async def test_when_it_cannot_tell_the_job_is_dialled_as_before(caplog, job_number, row):
    caplog.set_level(logging.DEBUG, logger=DRIFT_LOGGER)
    w = _worker()
    _conn_pool(w, fetchrow=row)

    await w.process_job(_job_with(job_number))

    assert _dialled_number(w) == job_number
    w.queue_service.mark_skipped.assert_not_awaited()
    lines = _number_lines(caplog)
    assert len(lines) == 1 and lines[0].startswith("dialer_contact_number_unknown")


@pytest.mark.asyncio
async def test_an_unreadable_contact_never_stops_the_dial(caplog):
    caplog.set_level(logging.DEBUG, logger=DRIFT_LOGGER)
    w = _worker()
    _conn_pool(w, error=RuntimeError("synthetic read failure"))

    await w.process_job(_job_with(NEW))

    assert _dialled_number(w) == NEW
    lines = _number_lines(caplog)
    assert len(lines) == 1 and lines[0].startswith("dialer_contact_number_unread")
    assert "RuntimeError" in lines[0] and "synthetic" not in lines[0]


@pytest.mark.asyncio
async def test_a_non_e164_job_number_goes_to_the_invalid_number_skip_without_a_contact_read():
    w = _worker()
    conn = _conn_pool(w, fetchrow={"phone_number": NEW})

    await w.process_job(_job_with("07400123456"))

    w._make_call.assert_not_awaited()
    assert w.queue_service.mark_skipped.call_args.kwargs["reason"] == "invalid_phone_number"
    conn.fetchrow.assert_not_awaited()


# ═══ 4. every job kind follows the same rule ═══


def _kind_ids():
    return {
        "redial": redial_job_id(str(uuid.uuid4())),
        "callback": callback_job_id(uuid.uuid4()),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["redial", "callback"])
async def test_redial_and_callback_jobs_follow_the_same_rule(kind):
    job_id = _kind_ids()[kind]

    same = _worker()
    _conn_pool(same, fetchrow={"phone_number": OLD})
    await same.process_job(_job_with(OLD, job_id=job_id))
    assert _dialled_number(same) == OLD

    changed = _worker()
    _conn_pool(changed, fetchrow={"phone_number": NEW})
    await changed.process_job(_job_with(OLD, job_id=job_id))
    changed._make_call.assert_not_awaited()
    assert changed.queue_service.mark_skipped.call_args.kwargs["reason"] == "lead_number_changed"


# ═══ 5. tenant scoping ═══


@pytest.mark.asyncio
async def test_the_contact_read_is_scoped_to_the_jobs_tenant():
    w = _worker()
    conn = _conn_pool(w, fetchrow={"phone_number": OLD})

    await w.process_job(_job_with(OLD, tenant=TENANT_B))

    conn.fetchrow.assert_awaited_once()
    sql, *args = conn.fetchrow.call_args.args
    assert "FROM leads" in sql and "tenant_id = $2::uuid" in sql
    assert args == [LEAD, TENANT_B]


class _RetryConn(_SettleConn):
    """_SettleConn whose lead / job rows carry the numbers the test chooses."""

    def __init__(self, *, contact, job_number=OLD, repoint="job-1", read_error=False):
        super().__init__()
        self.contact = contact
        self.job_number = job_number
        self.repoint = repoint
        self.read_error = read_error
        self.reads = []
        self.repoints = []

    async def fetchrow(self, sql, *args):
        if "SELECT phone_number FROM leads" in sql:
            self._check()
            self.reads.append((sql, args))
            if self.read_error:
                self._fail("synthetic contact read failure")
            return self.contact
        if "FROM dialer_jobs" in sql:
            self._check()
            return {"attempt_number": 1, "tenant_id": "t-1", "priority": 5,
                    "phone_number": self.job_number}
        return await super().fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        if "SET phone_number" in sql:
            self._check()
            self.repoints.append((sql, args))
            return self.repoint
        return await super().fetchval(sql, *args)


async def _settle(monkeypatch, conn):
    svc = CallService(db_client=SimpleNamespace())
    monkeypatch.setattr(svc, "_effective_attempt_number", AsyncMock(return_value=1))
    terminal, retry_args = await svc._handle_job_completion_pooled(
        conn, job_id="job-1", outcome=CallOutcome.BUSY, campaign_id="camp-1",
        lead_id="lead-1", scope_tenant_id="t-1", call_attempt_number=1,
        caller_opted_out=False,
    )
    assert terminal is False and retry_args is not None
    return svc, retry_args


@pytest.mark.asyncio
async def test_the_settlement_read_and_repoint_are_tenant_scoped(monkeypatch):
    conn = _RetryConn(contact={"phone_number": NEW})
    await _settle(monkeypatch, conn)

    read_sql, read_args = conn.reads[0]
    assert "tenant_id = $2::uuid" in read_sql and read_args == ("lead-1", "t-1")
    update_sql, update_args = conn.repoints[0]
    assert "tenant_id = $2::uuid" in update_sql
    assert "status = 'retry_scheduled'" in update_sql
    assert update_args == ("job-1", "t-1", NEW, OLD)


# ═══ 6. the resume path ═══


@pytest.mark.asyncio
async def test_an_unclaimed_intent_is_not_dialled_under_a_changed_number():
    w = _worker()
    _conn_pool(w, fetchrow={"phone_number": NEW})
    w._load_existing_call_intent = AsyncMock(return_value=_intent("initiated"))

    await w.process_job(_job_with(OLD))

    w._make_call.assert_not_awaited()
    w._mark_call_intent_not_originated.assert_awaited_once()
    assert (
        w._mark_call_intent_not_originated.call_args.kwargs["reason"]
        == "stale_payload:lead_number_changed"
    )
    assert w.queue_service.mark_skipped.call_args.kwargs["reason"] == "lead_number_changed"


@pytest.mark.asyncio
async def test_an_unclaimed_intent_with_the_same_number_is_dialled():
    w = _worker()
    _conn_pool(w, fetchrow={"phone_number": OLD})
    w._load_existing_call_intent = AsyncMock(return_value=_intent("initiated"))
    w._require_open_window_for_unclaimed_intent = AsyncMock()

    await w.process_job(_job_with(OLD))

    assert _dialled_number(w) == OLD


# ═══ 7. the new number gets dialled ═══


@pytest.mark.asyncio
async def test_a_retry_booked_after_a_correction_carries_the_new_number(monkeypatch):
    conn = _RetryConn(contact={"phone_number": NEW})
    svc, retry_args = await _settle(monkeypatch, conn)

    assert retry_args[1]["phone_number"] == NEW
    assert len(conn.repoints) == 1
    assert conn.aborted is False

    queue = _Queue()
    svc._queue_service = queue
    await svc._schedule_retry(*retry_args)
    retry_job = queue.jobs[0]
    assert retry_job.phone_number == NEW

    w = _worker()
    _conn_pool(w, fetchrow={"phone_number": NEW})
    await w.process_job(retry_job)
    assert _dialled_number(w) == NEW


# ═══ 8. settlement doubt keeps the old copy ═══


@pytest.mark.asyncio
async def test_settlement_with_the_same_number_does_not_repoint(monkeypatch):
    conn = _RetryConn(contact={"phone_number": OLD})
    _, retry_args = await _settle(monkeypatch, conn)
    assert conn.repoints == []
    assert retry_args[1]["phone_number"] == OLD


@pytest.mark.asyncio
@pytest.mark.parametrize("contact", [None, {"phone_number": "abc"}, {"phone_number": ""}])
async def test_settlement_that_cannot_tell_keeps_the_copy(monkeypatch, caplog, contact):
    caplog.set_level(logging.DEBUG, logger=DRIFT_LOGGER)
    conn = _RetryConn(contact=contact)
    _, retry_args = await _settle(monkeypatch, conn)
    assert conn.repoints == []
    assert retry_args[1]["phone_number"] == OLD
    lines = _number_lines(caplog) + [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("dialer_retry_number_")
    ]
    assert len(lines) == 1 and lines[0].startswith("dialer_retry_number_unknown")


@pytest.mark.asyncio
async def test_a_repoint_that_matches_no_row_keeps_the_copy(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=DRIFT_LOGGER)
    conn = _RetryConn(contact={"phone_number": NEW}, repoint=None)
    _, retry_args = await _settle(monkeypatch, conn)
    assert retry_args[1]["phone_number"] == OLD
    assert any(r.getMessage().startswith("dialer_retry_number_not_moved") for r in caplog.records)


@pytest.mark.asyncio
async def test_a_failed_contact_read_never_aborts_the_settlement(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=DRIFT_LOGGER)
    conn = _RetryConn(contact={"phone_number": NEW}, read_error=True)
    _, retry_args = await _settle(monkeypatch, conn)
    assert conn.aborted is False
    assert retry_args[1]["phone_number"] == OLD
    assert await conn.fetchval("SELECT 1") == "job-1"
    failed = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("dialer_retry_number_check_failed")]
    assert len(failed) == 1 and "RuntimeError" in failed[0] and "synthetic" not in failed[0]


# ═══ 9. order: the existing stale reasons win and the new read is not made ═══


@pytest.mark.asyncio
async def test_a_stale_payload_reason_is_decided_before_the_number_read():
    w = _worker()
    w._stale_payload_reason = AsyncMock(return_value="lead_not_dialable")
    w._contact_number_changed = AsyncMock(return_value=True)

    await w.process_job(_job_with(OLD))

    w._contact_number_changed.assert_not_awaited()
    assert w.queue_service.mark_skipped.call_args.kwargs["reason"] == "lead_not_dialable"

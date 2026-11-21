"""21 Nov 2026 dialler worker: three ways a person the customer paid to call is
silently never called (plan items G-F9, G-F10, G-F7a).

* G-F9: the API process was unreachable for more than ~10 minutes, the reaper
  flipped the never-sent call row to ``termination_pending``, and from then on
  the bridge answered 409 for ever. Nothing settles a row with no provider id,
  so the job stayed active for ever and logged an ERROR every 120 s.
* G-F10: stopping a campaign while a call was ringing let the settled call
  schedule a retry; with another running campaign in the tenant the retry was
  dequeued, the job was ended SKIPPED and the contact stayed ``called``, which
  neither Start nor its reset ever selects.
* G-F7a: an invalid stored number ended its job but left the contact
  ``pending`` with no job, so the campaign never completed.

Hermetic: the worker's collaborators are mocks (same fixtures as the other
dialler worker tests); SQL is checked on a recording connection.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.domain.models.dialer_job import JobStatus
from app.domain.services.dialer.block_reasons import BlockCode
from app.domain.services.dialer.job_states import ACTIVE_STATUSES
from app.workers.dialer_worker import DialerCallIntent, DialerWorker
from tests.unit.test_w1_dialer_job_lifecycle import (
    TENANT_A,
    _conn_pool,
    _job,
    _published_codes,
    _worker,
)

LEAD = "55555555-5555-5555-5555-555555555555"
CAMPAIGN = "44444444-4444-4444-4444-444444444444"


def _intent(status: str, provider_call_id=None) -> DialerCallIntent:
    return DialerCallIntent("call-1", "tk-1", "leg-1", status, provider_call_id, False)


def _redefer_delays(w) -> list[int]:
    return [c.kwargs["delay_seconds"] for c in w.queue_service._redefer_inflight.call_args_list]


# ═══ G-F9: a reaped, never-sent attempt is released, not parked for ever ═══


@pytest.mark.asyncio
async def test_reaped_unclaimed_intent_is_released_and_waits_on_the_same_attempt():
    w = _worker()
    _conn_pool(w, fetchval=1)  # backoff count: the row just released
    w._load_existing_call_intent = AsyncMock(return_value=_intent("termination_pending"))
    job = _job(attempt=2)

    await w.process_job(job)

    w._make_call.assert_not_awaited()  # nothing is sent to the bridge (it would answer 409)
    w._park_uncertain_origination.assert_not_awaited()
    w._release_intent_line_not_ready.assert_awaited_once()
    assert w._release_intent_line_not_ready.call_args.kwargs["reason"] == "telephony_unavailable"
    assert _redefer_delays(w) == [300]
    assert job.attempt_number == 2  # the same attempt: nothing consumed
    assert BlockCode.TELEPHONY_UNAVAILABLE in _published_codes(w)


@pytest.mark.asyncio
@pytest.mark.parametrize("released, expected", [(1, 300), (2, 600), (3, 1200), (9, 3600)])
async def test_released_job_is_retried_no_sooner_than_todays_backoff(released, expected):
    # Today the job is parked every 120 s for ever. After the fix it waits the
    # existing outage back-off (5 min doubling to 1 h): never faster than 120 s.
    w = _worker()
    _conn_pool(w, fetchval=released)
    w._load_existing_call_intent = AsyncMock(return_value=_intent("termination_pending"))

    await w.process_job(_job(attempt=2))

    (delay,) = _redefer_delays(w)
    assert delay == expected
    assert delay >= 300 > 120


@pytest.mark.asyncio
async def test_reaped_intent_with_a_provider_id_is_never_released():
    w = _worker()
    intent = _intent("termination_pending", provider_call_id="talky-out-planned")
    w._load_existing_call_intent = AsyncMock(return_value=intent)
    w._make_call = AsyncMock(return_value=DialerWorker._ORIGINATION_UNCERTAIN)

    await w.process_job(_job(attempt=2))

    w._release_intent_line_not_ready.assert_not_awaited()
    w._make_call.assert_awaited_once()  # unchanged path: the bridge decides
    w._park_uncertain_origination.assert_awaited_once()
    w.queue_service._redefer_inflight.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_cas_miss_parks_and_never_guesses():
    w = _worker()
    _conn_pool(w, fetchval=1)
    w._release_intent_line_not_ready = AsyncMock(return_value=False)
    w._load_existing_call_intent = AsyncMock(return_value=_intent("termination_pending"))

    await w.process_job(_job(attempt=2))

    w._make_call.assert_not_awaited()
    w._park_uncertain_origination.assert_awaited_once()
    w.queue_service._redefer_inflight.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("campaign_status", ["stopped", "deleted", "completed", "paused"])
async def test_non_runnable_campaign_ends_a_reaped_unclaimed_job(campaign_status):
    w = _worker()
    _conn_pool(w)
    w._get_campaign_status = AsyncMock(return_value=campaign_status)
    w._load_existing_call_intent = AsyncMock(return_value=_intent("termination_pending"))

    await w.process_job(_job(attempt=2))

    w._mark_call_intent_not_originated.assert_awaited_once()
    w._park_uncertain_origination.assert_not_awaited()
    w.queue_service.mark_skipped.assert_awaited_once()
    assert w._update_job_status.call_args.args[1] == JobStatus.SKIPPED


@pytest.mark.asyncio
async def test_non_runnable_campaign_keeps_a_provider_bound_reaped_row():
    # The rule of test_dialer_origination_durability: a provider-bound row stays owned.
    w = _worker()
    w._get_campaign_status = AsyncMock(return_value="stopped")
    w._load_existing_call_intent = AsyncMock(
        return_value=_intent("termination_pending", provider_call_id="talky-out-planned")
    )

    await w.process_job(_job(attempt=2))

    w._mark_call_intent_not_originated.assert_not_awaited()
    w._park_uncertain_origination.assert_awaited_once()
    w._update_job_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_sql_still_requires_both_provider_ids_null_and_tenant():
    w = DialerWorker()
    conn = _conn_pool(w)
    await w._release_intent_line_not_ready(_job(), _intent("termination_pending"), reason="telephony_unavailable")
    release = conn.execute.call_args.args[0]
    await w._mark_call_intent_not_originated(_job(), _intent("termination_pending"), reason="x")
    mark = conn.execute.call_args.args[0]
    for sql in (release, mark):
        assert "status IN ('queued', 'initiated', 'termination_pending')" in sql
        assert "provider_call_id IS NULL" in sql and "external_call_uuid IS NULL" in sql
        assert "tenant_id = $2::uuid" in sql and "dialer_job_id = $3::uuid" in sql
        assert "dialer_attempt_number = $4" in sql and "direction = 'outbound'" in sql


@pytest.mark.asyncio
async def test_healthy_ordinary_job_is_processed_exactly_as_before_g_f9():
    w = _worker()
    conn = _conn_pool(w)
    job = _job()

    await w.process_job(job)

    w._make_call.assert_awaited_once()
    w._release_intent_line_not_ready.assert_not_awaited()
    w._park_uncertain_origination.assert_not_awaited()
    assert conn.execute.await_count == 0 and conn.fetchval.await_count == 0
    # Golden, recorded on the unchanged worker.
    assert [c[0] for c in w.queue_service.mock_calls] == _HEALTHY_QUEUE_CALLS
    assert [(c.args[1], sorted(c.kwargs)) for c in w._update_job_status.call_args_list] == _HEALTHY_STATUS_CALLS


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "initiated"])
async def test_held_unclaimed_intent_replays_exactly_as_before(status):
    w = _worker()
    _conn_pool(w)
    w._load_existing_call_intent = AsyncMock(return_value=_intent(status))

    await w.process_job(_job(attempt=2))

    w._make_call.assert_awaited_once()
    w._release_intent_line_not_ready.assert_not_awaited()
    w._park_uncertain_origination.assert_not_awaited()


# ═══ G-F10: a stopped campaign's skipped retry gives its contact back ═══


def _stopped_worker():
    w = _worker()
    order: list[str] = []
    conn = _conn_pool(w)
    real_execute = conn.execute

    async def execute(*a, **k):
        order.append("lead_sql")
        return await real_execute(*a, **k)

    conn.execute = AsyncMock(side_effect=execute)
    w._update_job_status = AsyncMock(side_effect=lambda *a, **k: order.append("job_ended"))
    w._get_campaign_status = AsyncMock(return_value="stopped")
    return w, conn, order


@pytest.mark.asyncio
async def test_a_retry_skipped_for_a_stopped_campaign_returns_its_lead_to_pending():
    w, conn, order = _stopped_worker()
    job = _job()

    await w.process_job(job)

    w.queue_service.mark_skipped.assert_awaited_once()
    assert order == ["job_ended", "lead_sql"]  # the job ends first, then the lead is freed
    sql, *args = conn.execute.call_args.args
    assert "UPDATE leads" in sql and "SET status = 'pending'" in sql
    assert "id = $1::uuid" in sql and "tenant_id = $2::uuid" in sql and "campaign_id = $3::uuid" in sql
    assert "status = 'called'" in sql  # only a called lead moves
    assert "do_not_call" in sql  # a do-not-call contact is never touched
    assert "NOT EXISTS" in sql and "j.tenant_id = $2::uuid" in sql and "j.status = ANY($4::text[])" in sql
    assert args == [job.lead_id, job.tenant_id, job.campaign_id, list(ACTIVE_STATUSES)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["deleted", "completed", "cancelled", "draft", "archived", None])
async def test_other_final_statuses_leave_the_lead_alone(status):
    w, conn, order = _stopped_worker()
    w._get_campaign_status = AsyncMock(return_value=status)

    await w.process_job(_job())

    assert order == ["job_ended"]
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_paused_campaign_still_holds_and_writes_no_lead():
    w, conn, order = _stopped_worker()
    w._get_campaign_status = AsyncMock(return_value="paused")

    await w.process_job(_job())

    w.queue_service.mark_skipped.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_lead_release_failure_never_breaks_the_skip(caplog):
    w, conn, order = _stopped_worker()
    conn.execute = AsyncMock(side_effect=RuntimeError("db down"))

    with caplog.at_level(logging.WARNING, logger="app.workers.dialer_worker"):
        await w.process_job(_job())

    w.queue_service.mark_skipped.assert_awaited_once()
    assert order == ["job_ended"]
    assert any("dialer_lead_release_failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_lead_release_is_a_noop_without_a_database_pool():
    # _worker() has no pool: the real _acquire_db raises; the skip must still end cleanly.
    w = _worker()
    w._get_campaign_status = AsyncMock(return_value="stopped")

    await w.process_job(_job())

    w.queue_service.mark_skipped.assert_awaited_once()
    w._update_job_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_healthy_running_campaign_writes_no_lead_row_g_f10():
    w, conn, order = _stopped_worker()
    w._get_campaign_status = AsyncMock(return_value="running")

    await w.process_job(_job())

    w._make_call.assert_awaited_once()
    conn.execute.assert_not_awaited()
    assert [c[0] for c in w.queue_service.mock_calls] == _HEALTHY_QUEUE_CALLS


# ═══ G-F7a: an invalid number marks the contact skipped before the job ends ═══


def _invalid_worker():
    w = _worker()
    order: list[str] = []
    conn = _conn_pool(w)
    real_execute = conn.execute

    async def execute(*a, **k):
        order.append("lead_sql")
        return await real_execute(*a, **k)

    conn.execute = AsyncMock(side_effect=execute)
    w._update_job_status = AsyncMock(side_effect=lambda *a, **k: order.append("job_ended"))
    return w, conn, order


def _bad_number_job():
    j = _job()
    j.phone_number = "+07700900123"
    return j


@pytest.mark.asyncio
async def test_invalid_number_skip_marks_the_lead_skipped_before_the_job_ends():
    w, conn, order = _invalid_worker()
    job = _bad_number_job()

    await w.process_job(job)

    assert order == ["lead_sql", "job_ended"]
    sql, *args = conn.execute.call_args.args
    assert "UPDATE leads" in sql and "SET status = 'skipped'" in sql
    assert "last_call_result = 'invalid_phone_number'" in sql
    assert "id = $1::uuid" in sql and "tenant_id = $2::uuid" in sql and "campaign_id = $3::uuid" in sql
    assert "status IN ('pending', 'calling')" in sql  # nothing else is overwritten
    assert args == [job.lead_id, job.tenant_id, job.campaign_id]
    assert w._update_job_status.await_count == 1  # the job end stays ONE call
    assert w._update_job_status.call_args.args[1] == JobStatus.SKIPPED
    w._make_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_number_lead_write_failure_still_skips_the_job(caplog):
    w, conn, order = _invalid_worker()
    conn.execute = AsyncMock(side_effect=RuntimeError("db down"))

    with caplog.at_level(logging.WARNING, logger="app.workers.dialer_worker"):
        await w.process_job(_bad_number_job())

    assert order == ["job_ended"]
    w.queue_service.mark_skipped.assert_awaited_once_with(_job().job_id, reason="invalid_phone_number")
    assert any("dialer_invalid_number_lead_failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_valid_number_writes_no_lead_row_g_f7a():
    w, conn, order = _invalid_worker()

    await w.process_job(_job())

    w._make_call.assert_awaited_once()
    conn.execute.assert_not_awaited()
    assert [c[0] for c in w.queue_service.mock_calls] == _HEALTHY_QUEUE_CALLS


# Golden values: recorded by running the healthy cases above on the worker
# BEFORE any change in this packet.
_HEALTHY_QUEUE_CALLS: list = []
_HEALTHY_STATUS_CALLS = [(JobStatus.PROCESSING, ["call_id"])]

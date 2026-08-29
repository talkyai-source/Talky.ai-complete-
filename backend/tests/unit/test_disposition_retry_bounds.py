"""Retries must be BOUNDED. Compliance regression suite (2026-07-28).

The bug this file locks down: ``dialer_jobs.attempt_number`` was written
once at job creation and never updated again, while the post-answer
teardown path re-read that frozen column and handed it to
``disposition_policy.decide``. Every completed call therefore claimed to
be attempt 1, ``attempt_number >= cap`` (every cap is >= 2) could never
be true, and a retryable lead was redialled forever — busy every 5
minutes, no-answer / voicemail / unavailable every 24 hours, with no end.
That is repeated-call harassment under TCPA / Ofcom, so these tests are
compliance tests, not style tests.

Three properties are pinned here:

1. Looping the real teardown path over a lead that is always BUSY stops
   after exactly ``cap`` dials (and the same for every other retryable
   disposition). The loop is driven through
   ``CallService._handle_call_status_pooled`` against an in-memory
   Postgres stand-in, so it exercises the actual SQL that advances the
   counter — not a hand-fed integer.
2. The attempt counter is incremented exactly ONCE per attempt. The
   retry job handed to the queue carries the just-completed attempt and
   ``DialerQueueService.schedule_retry`` performs the single bump; the
   two used to compound into a double increment.
3. Campaign counters count leads, not attempts: a lead that is going to
   be redialled must not bump ``calls_completed`` / ``calls_failed``.

Non-vacuity: reverting the fix (either by freezing the counter or by
restoring the double increment) fails these tests — see
``test_frozen_counter_reproduces_the_infinite_redial_bug``, which
simulates the OLD behaviour and asserts it never terminates.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from app.domain.models.dialer_job import CallOutcome, DialerJob, JobStatus
from app.domain.services.call_service import CallService
from app.domain.services.queue_service import DialerQueueService
from app.workers import disposition_policy
from app.workers.disposition_policy import decide
from app.core.security.tenant_isolation import (
    clear_tenant_context,
    set_bypass_rls,
    set_current_tenant_id,
)


# Every retryable disposition and its agreed TOTAL attempt ceiling
# (initial dial + retries). Mirrors disposition_policy._ATTEMPT_CAPS.
RETRYABLE_CAPS = [
    (CallOutcome.BUSY, 4),
    (CallOutcome.NO_ANSWER, 3),
    (CallOutcome.VOICEMAIL, 2),
    # UNAVAILABLE was here at cap 3 until 2026-07-28; it is a DNC outcome
    # now (see test_terminal_dispositions_are_never_redialled below).
    (CallOutcome.FAILED, 3),
    (CallOutcome.TIMEOUT, 3),
]


# ──────────────────────────────────────────────────────────────────
# In-memory Postgres stand-in (same style as
# test_call_service_pooled_teardown.py, plus attempt_number support).
# ──────────────────────────────────────────────────────────────────


class _FakeConn:
    def __init__(self, calls_row: dict, leads_row: dict, dialer_jobs_row: dict) -> None:
        self.calls_row = calls_row
        self.calls_row.setdefault("outcome", None)
        self.calls_row.setdefault("ended_at", None)
        self.calls_row.setdefault("duration_seconds", None)
        self.calls_row.setdefault("terminal_settled_at", None)
        self.calls_row.setdefault("terminal_retry_payload", None)
        self.calls_row.setdefault("terminal_retry_enqueued_at", None)
        self.leads_row = leads_row
        self.dialer_jobs_row = dialer_jobs_row
        self.campaigns_row = {"calls_completed": 0, "calls_failed": 0}

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self):
                return outer

            async def __aexit__(self, *a):
                return None

        return _Tx()

    async def execute(self, query: str, *args: Any) -> None:
        q = " ".join(query.split())
        if q.startswith("SET LOCAL"):
            return
        if "UPDATE calls" in q and "status = 'completed'" in q:
            if self.calls_row["id"] == args[0]:
                self.calls_row["status"] = "completed"
                self.calls_row["outcome"] = args[1]
            return
        if "UPDATE leads" in q:
            lead_id, lead_status, last_call_result, call_attempts = args
            if self.leads_row["id"] == lead_id:
                self.leads_row["status"] = lead_status
                self.leads_row["last_call_result"] = last_call_result
                self.leads_row["call_attempts"] = call_attempts
            return
        if "UPDATE campaigns" in q:
            key = "calls_failed" if "calls_failed" in q else "calls_completed"
            self.campaigns_row[key] += 1
            return

    async def fetchrow(self, query: str, *args: Any):
        q = " ".join(query.split())
        if q.startswith("UPDATE calls"):
            if self.calls_row["id"] != args[0]:
                return None
            if "terminal_settled_at = COALESCE" in q:
                self.calls_row["terminal_settled_at"] = "NOW"
                self.calls_row["terminal_retry_payload"] = args[1]
                self.calls_row["terminal_retry_enqueued_at"] = None
                return dict(self.calls_row)
            if "status = 'completed'" in q:
                self.calls_row["status"] = "completed"
                self.calls_row["outcome"] = args[1]
                if args[2] is not None:
                    self.calls_row["duration_seconds"] = args[2]
                self.calls_row["ended_at"] = self.calls_row["ended_at"] or "NOW"
                return dict(self.calls_row)
            self.calls_row["outcome"] = self.calls_row["outcome"] or args[1]
            if self.calls_row["duration_seconds"] is None and args[2] is not None:
                self.calls_row["duration_seconds"] = args[2]
            self.calls_row["ended_at"] = self.calls_row["ended_at"] or "NOW"
            return dict(self.calls_row)
        if "FROM calls WHERE id" in q:
            return dict(self.calls_row) if self.calls_row["id"] == args[0] else None
        if q.startswith("SELECT * FROM dialer_jobs WHERE id"):
            row = self.dialer_jobs_row
            return dict(row) if row["id"] == args[0] else None
        return None

    async def fetchval(self, query: str, *args: Any):
        q = " ".join(query.split())
        if "SELECT call_attempts FROM leads" in q:
            return self.leads_row.get("call_attempts", 0)
        if q.startswith("UPDATE dialer_jobs") and "RETURNING id" in q:
            job_id, status_val, outcome_val, reason, allowed_statuses = args
            row = self.dialer_jobs_row
            if (
                row["id"] != job_id
                or row.get("status") not in set(allowed_statuses)
            ):
                return None  # idempotency guard
            row["status"] = status_val
            row["last_outcome"] = outcome_val
            row["failure_reason"] = reason
            if "attempt_number" in q:
                # Mirrors: attempt_number = GREATEST(COALESCE(x, 1), 1) + 1
                row["attempt_number"] = max(row.get("attempt_number") or 1, 1) + 1
            if "completed_at" in q:
                row["completed_at"] = "NOW"
            return job_id
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self, timeout: Optional[float] = None):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                return outer._conn

            async def __aexit__(self, *a):
                return None

        return _Ctx()


class _FakeRedisHash:
    """Just enough Redis for DialerQueueService's inflight payload index."""

    def __init__(self) -> None:
        self.hash: dict[str, str] = {}

    async def hget(self, key: str, field: str):
        return self.hash.get(field)


class _RecordingQueue:
    """Queue stub that applies the REAL ``schedule_retry`` attempt-number
    accounting (the single canonical increment) and exposes the resulting
    live attempt count the way the real inflight payload does."""

    def __init__(self) -> None:
        self.scheduled: list[DialerJob] = []
        self.live: dict[str, int] = {}

    async def schedule_retry(self, job: DialerJob, delay_seconds: int = 7200) -> bool:
        # The one line of real behaviour we must reproduce faithfully
        # (queue_service.schedule_retry line 1: `job.attempt_number += 1`).
        job.attempt_number += 1
        job.status = JobStatus.RETRY_SCHEDULED
        self.scheduled.append(job)
        self.live[job.job_id] = job.attempt_number
        return True

    async def get_live_attempt_number(self, job_id: str) -> Optional[int]:
        return self.live.get(job_id)


def _make_case(outcome_value: str = "busy") -> tuple[_FakeConn, _RecordingQueue, CallService]:
    conn = _FakeConn(
        calls_row={
            "id": "call-1", "lead_id": "lead-1", "campaign_id": "camp-1",
            "dialer_job_id": "job-1", "status": "initiated",
        },
        leads_row={"id": "lead-1", "call_attempts": 0, "status": "calling"},
        dialer_jobs_row={
            "id": "job-1", "status": "processing", "attempt_number": 1,
            "tenant_id": "tenant-1", "priority": 5, "phone_number": "+15550001111",
        },
    )
    queue = _RecordingQueue()
    service = CallService(
        db_client=AsyncMock(),
        queue_service=queue,
        call_repo=AsyncMock(),
        lead_repo=AsyncMock(),
        db_pool=_FakePool(conn),
    )
    return conn, queue, service


@pytest.fixture(autouse=True)
def _rls_bypass_context():
    set_bypass_rls(True)
    set_current_tenant_id("00000000-0000-0000-0000-000000000000")
    yield
    clear_tenant_context()


async def _dial_until_exhausted(
    conn: _FakeConn, service: CallService, outcome: CallOutcome, limit: int = 50,
) -> int:
    """Drive the real teardown path repeatedly, exactly as production does:
    dial → teardown → if a retry was booked, the worker re-dials the SAME
    dialer_jobs row (status back to 'processing') and we go round again.

    Returns the number of dials placed. ``limit`` is a runaway tripwire —
    hitting it means the retry loop is unbounded, which is the bug.
    """
    dials = 0
    while dials < limit:
        # The dialer worker re-arms the row for each attempt.
        conn.calls_row["status"] = "initiated"
        conn.calls_row["outcome"] = None
        conn.calls_row["ended_at"] = None
        conn.calls_row["terminal_settled_at"] = None
        conn.calls_row["terminal_retry_payload"] = None
        conn.calls_row["terminal_retry_enqueued_at"] = None
        conn.dialer_jobs_row["status"] = "processing"
        dials += 1

        _, _, retry_args = await service._handle_call_status_pooled(
            "call-1", outcome, outcome.value, duration=0,
        )
        if retry_args is None:
            return dials
        await service._schedule_retry(*retry_args)
    return dials


# ──────────────────────────────────────────────────────────────────
# 1. The headline property: a busy lead stops after exactly `cap` dials
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_busy_lead_stops_after_exactly_the_cap():
    conn, queue, service = _make_case()

    dials = await _dial_until_exhausted(conn, service, CallOutcome.BUSY)

    assert dials == 4, (
        f"a busy lead must be dialled exactly 4 times (cap), got {dials}. "
        "More than the cap is repeated-call harassment; fewer wastes a "
        "legitimate retry."
    )
    # 4 dials = 3 retries booked.
    assert len(queue.scheduled) == 3
    # ...spaced on the agreed busy cadence, in order.
    assert [j.attempt_number for j in queue.scheduled] == [2, 3, 4]
    # The job ended finalised, not left retry_scheduled.
    assert conn.dialer_jobs_row["status"] == "failed"
    assert conn.dialer_jobs_row["failure_reason"] == "busy_max_attempts"


@pytest.mark.parametrize("outcome,cap", RETRYABLE_CAPS)
@pytest.mark.asyncio
async def test_every_retryable_disposition_is_bounded_at_its_cap(outcome, cap):
    conn, queue, service = _make_case()

    dials = await _dial_until_exhausted(conn, service, outcome)

    assert dials == cap, (
        f"{outcome.value} must stop after exactly {cap} dials, got {dials}"
    )
    assert len(queue.scheduled) == cap - 1


@pytest.mark.asyncio
async def test_terminal_dispositions_are_never_redialled():
    for outcome in (
        CallOutcome.REJECTED,
        CallOutcome.ANSWERED,
        CallOutcome.GOAL_ACHIEVED,
        CallOutcome.GOAL_NOT_ACHIEVED,
        CallOutcome.SPAM,
        CallOutcome.INVALID,
        CallOutcome.DISCONNECTED,
        CallOutcome.UNAVAILABLE,
    ):
        conn, queue, service = _make_case()
        dials = await _dial_until_exhausted(conn, service, outcome)
        assert dials == 1, f"{outcome.value} must never be redialled"
        assert queue.scheduled == []


# ──────────────────────────────────────────────────────────────────
# 2. Non-vacuity — the OLD behaviour must fail these assertions
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_frozen_counter_reproduces_the_infinite_redial_bug(monkeypatch):
    """Revert the fix (freeze the persisted counter AND hide the live one,
    i.e. exactly the pre-fix state) and prove the loop never terminates.

    Without this, ``test_busy_lead_stops_after_exactly_the_cap`` could pass
    for the wrong reason (e.g. the loop ending because of some unrelated
    guard rather than the cap).
    """
    conn, queue, service = _make_case()

    # Pre-fix state #1: nothing ever advanced dialer_jobs.attempt_number.
    original_fetchval = conn.fetchval

    async def frozen_fetchval(query: str, *args: Any):
        before = conn.dialer_jobs_row.get("attempt_number")
        result = await original_fetchval(query, *args)
        conn.dialer_jobs_row["attempt_number"] = before
        return result

    monkeypatch.setattr(conn, "fetchval", frozen_fetchval)
    # Pre-fix state #2: the live Redis count was never consulted.
    monkeypatch.setattr(
        queue, "get_live_attempt_number", AsyncMock(return_value=None),
    )

    dials = await _dial_until_exhausted(conn, service, CallOutcome.BUSY, limit=25)

    assert dials == 25, (
        "with the frozen counter the loop must run away — if it stopped, "
        "these tests are not actually exercising the cap"
    )
    assert conn.dialer_jobs_row["attempt_number"] == 1


@pytest.mark.asyncio
async def test_double_increment_would_skip_an_attempt(monkeypatch):
    """The second bug, pinned from the other side: pre-incrementing in
    ``_schedule_retry`` on top of ``schedule_retry``'s own bump makes the
    retry job claim an attempt it never made, so the lead loses a dial."""
    conn, queue, service = _make_case()

    original = CallService._schedule_retry

    async def double_incrementing_schedule_retry(self, *args, **kwargs):
        args = list(args)
        args[6] = args[6] + 1  # the old `attempt_number + 1` at the call site
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(
        CallService, "_schedule_retry", double_incrementing_schedule_retry,
    )

    dials = await _dial_until_exhausted(conn, service, CallOutcome.BUSY)

    assert dials < 4, (
        "the double increment must cost the lead attempts — if this still "
        "reaches the cap the increment is not being exercised"
    )
    assert [j.attempt_number for j in queue.scheduled] == [3, 5]


# ──────────────────────────────────────────────────────────────────
# 3. Attempt accounting: exactly one increment per attempt
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_job_carries_exactly_one_increment():
    conn, queue, service = _make_case()

    conn.dialer_jobs_row["status"] = "processing"
    _, _, retry_args = await service._handle_call_status_pooled(
        "call-1", CallOutcome.BUSY, "busy", duration=0,
    )
    assert retry_args is not None
    assert retry_args[6] == 1, "the just-completed attempt is passed through"
    assert retry_args[7] == 5 * 60, "busy's first retry delay is 5 minutes"

    await service._schedule_retry(*retry_args)

    assert queue.scheduled[0].attempt_number == 2, (
        "the retry job must be stamped attempt 2 — not 3 (double increment)"
    )
    assert conn.dialer_jobs_row["attempt_number"] == 2, (
        "the persisted counter must advance too, or the cap can never fire"
    )


@pytest.mark.asyncio
async def test_persisted_counter_is_not_advanced_on_a_terminal_outcome():
    conn, _queue, service = _make_case()
    conn.dialer_jobs_row["status"] = "processing"

    await service._handle_call_status_pooled(
        "call-1", CallOutcome.ANSWERED, "answered", duration=30,
    )

    assert conn.dialer_jobs_row["attempt_number"] == 1
    assert conn.dialer_jobs_row.get("completed_at") is not None


@pytest.mark.asyncio
async def test_duplicate_teardown_does_not_advance_the_counter():
    """The idempotency guard and the increment share one UPDATE, so a
    replayed teardown must not burn an attempt."""
    conn, queue, service = _make_case()
    conn.dialer_jobs_row["status"] = "processing"

    await service._handle_call_status_pooled(
        "call-1", CallOutcome.BUSY, "busy", duration=0,
    )
    assert conn.dialer_jobs_row["attempt_number"] == 2

    # Replay: `calls.status` is already 'completed' AND the job left
    # 'processing' — both guards must hold.
    _, _, retry_args = await service._handle_call_status_pooled(
        "call-1", CallOutcome.BUSY, "busy", duration=0,
    )
    # DB settlement is already once-only, but its unacknowledged Redis outbox
    # intentionally replays the same payload until schedule_retry_once commits.
    assert retry_args is not None
    assert conn.dialer_jobs_row["attempt_number"] == 2


@pytest.mark.asyncio
async def test_live_redis_count_wins_when_it_is_ahead_of_the_database():
    """Pre-answer retries (originate failures) bump only the Redis job, so
    the live count can legitimately exceed the persisted one. The cap must
    be enforced against the HIGHER of the two — never the stale lower one."""
    conn, queue, service = _make_case()
    conn.dialer_jobs_row["status"] = "processing"
    # DB still says 1; the live job has already burned 4 busy attempts.
    queue.live["job-1"] = 4

    _, _, retry_args = await service._handle_call_status_pooled(
        "call-1", CallOutcome.BUSY, "busy", duration=0,
    )

    assert retry_args is None, "cap must fire on the live count, not the stale DB one"
    assert conn.dialer_jobs_row["failure_reason"] == "busy_max_attempts"


@pytest.mark.asyncio
async def test_missing_live_count_falls_back_to_the_database():
    conn, queue, service = _make_case()
    conn.dialer_jobs_row["status"] = "processing"
    conn.dialer_jobs_row["attempt_number"] = 4  # DB alone says the cap is hit
    queue.live.clear()  # Redis has nothing (reaped / flushed)

    _, _, retry_args = await service._handle_call_status_pooled(
        "call-1", CallOutcome.BUSY, "busy", duration=0,
    )

    assert retry_args is None
    assert conn.dialer_jobs_row["failure_reason"] == "busy_max_attempts"


@pytest.mark.asyncio
async def test_live_lookup_failure_never_breaks_teardown():
    conn, queue, service = _make_case()
    conn.dialer_jobs_row["status"] = "processing"

    async def boom(_job_id):
        raise RuntimeError("redis is down")

    queue.get_live_attempt_number = boom

    _, _, retry_args = await service._handle_call_status_pooled(
        "call-1", CallOutcome.BUSY, "busy", duration=0,
    )

    # Degrades to the DB value (1) — retry still booked, teardown intact.
    assert retry_args is not None
    assert retry_args[6] == 1


# ──────────────────────────────────────────────────────────────────
# 4. Campaign counters count LEADS, not attempts
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_campaign_counters_bump_once_per_lead_not_per_attempt():
    conn, _queue, service = _make_case()

    dials = await _dial_until_exhausted(conn, service, CallOutcome.BUSY)

    assert dials == 4
    total = conn.campaigns_row["calls_completed"] + conn.campaigns_row["calls_failed"]
    assert total == 1, (
        f"one lead must move the counters by exactly 1, not {total} — "
        "calls_completed + calls_failed could otherwise exceed total_leads"
    )


@pytest.mark.asyncio
async def test_counters_not_bumped_while_a_retry_is_pending():
    conn, _queue, service = _make_case()
    conn.dialer_jobs_row["status"] = "processing"

    await service._handle_call_status_pooled(
        "call-1", CallOutcome.BUSY, "busy", duration=0,
    )

    assert conn.campaigns_row == {"calls_completed": 0, "calls_failed": 0}


@pytest.mark.asyncio
async def test_terminal_outcome_still_bumps_the_counters():
    conn, _queue, service = _make_case()
    conn.dialer_jobs_row["status"] = "processing"

    await service._handle_call_status_pooled(
        "call-1", CallOutcome.ANSWERED, "answered", duration=30,
    )

    assert conn.campaigns_row["calls_completed"] == 1


@pytest.mark.asyncio
async def test_non_reachable_terminal_outcome_bumps_calls_failed():
    conn, _queue, service = _make_case()
    conn.dialer_jobs_row["status"] = "processing"

    await service._handle_call_status_pooled(
        "call-1", CallOutcome.REJECTED, "rejected", duration=0,
    )

    assert conn.campaigns_row["calls_failed"] == 1
    assert conn.campaigns_row["calls_completed"] == 0


# ──────────────────────────────────────────────────────────────────
# 5. Policy-table invariants + fail-closed inputs
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("outcome,cap", RETRYABLE_CAPS)
def test_schedule_length_matches_the_cap(outcome, cap):
    """CAP is total attempts, so the delay schedule must hold CAP-1 entries.
    If these ever disagree the shorter one silently wins and the documented
    cadence is a lie."""
    schedule = disposition_policy._RETRY_SCHEDULES[outcome]
    assert len(schedule) == cap - 1
    assert disposition_policy._ATTEMPT_CAPS[outcome] == cap


@pytest.mark.parametrize("outcome,cap", RETRYABLE_CAPS)
def test_decide_stops_at_and_beyond_the_cap(outcome, cap):
    for attempt in range(1, cap):
        assert decide(outcome, attempt).should_retry, (
            f"{outcome.value} attempt {attempt}/{cap} should still retry"
        )
    for attempt in (cap, cap + 1, cap + 10, 10_000):
        d = decide(outcome, attempt)
        assert not d.should_retry
        assert d.reason == f"{outcome.value}_max_attempts"


@pytest.mark.parametrize("bad", [None, 0, -1, "two", object(), True])
def test_decide_fails_closed_on_an_unusable_attempt_number(bad):
    """Losing count must mean STOP CALLING, never call forever."""
    d = decide(CallOutcome.BUSY, bad)
    assert not d.should_retry
    assert d.delay_seconds == 0


# ──────────────────────────────────────────────────────────────────
# 6. The queue-side half of the accounting contract
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_get_live_attempt_number_reads_the_inflight_payload():
    q = DialerQueueService(redis_client=_FakeRedisHash())
    job = DialerJob(
        job_id="job-9", campaign_id="c", lead_id="l", tenant_id="t",
        phone_number="+15550009999", attempt_number=3,
    )
    q._redis.hash["job-9"] = json.dumps(job.to_redis_dict())

    assert await q.get_live_attempt_number("job-9") == 3
    assert await q.get_live_attempt_number("job-missing") is None


@pytest.mark.asyncio
async def test_queue_get_live_attempt_number_is_fail_soft():
    class _Broken:
        async def hget(self, *a):
            raise RuntimeError("redis down")

    q = DialerQueueService(redis_client=_Broken())
    assert await q.get_live_attempt_number("job-9") is None

    class _Garbage:
        async def hget(self, *a):
            return "not-json"

    q2 = DialerQueueService(redis_client=_Garbage())
    assert await q2.get_live_attempt_number("job-9") is None

    q3 = DialerQueueService(redis_client=None)
    assert await q3.get_live_attempt_number("job-9") is None

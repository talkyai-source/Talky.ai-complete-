"""Runs `_lead_has_live_or_answered_call` / `_persist_job_attempt_number`
against a REAL Postgres connection, not a mock of the unit under test.

Code review of commit e8e93870 (scratchpad issues_all.txt,
lead-cooldown-always-cleared) found that the cooldown query used
`make_interval(hours => $3::float8)`. `make_interval`'s `hours` parameter
is `int`, and float8->int is only an assignment cast, so Postgres raises

    UndefinedFunctionError: function make_interval(hours => double
    precision) does not exist

on every single lead_cooldown hit. Every existing test for this fix
(test_dialer_lead_cooldown_gate.py) replaces `_lead_has_live_or_answered_call`
and `_persist_job_attempt_number` with AsyncMocks at the DB boundary, so the
SQL text itself was never executed by any test and the defect shipped once
already. These tests exercise the real query text over a real connection so
that class of defect can't hide behind a mocked helper again.

Every case here was independently verified against a throwaway local
PostgreSQL 16.1 cluster before and after the fix (see the review's
`suggested_fix`): the four `_lead_has_live_or_answered_call` cases match the
reviewer's own True/False matrix, and the tenant-scoping case proves the
`_persist_job_attempt_number` UPDATE's WHERE predicate is load-bearing, not
just present in the SQL text.

Skips (does not fail) unless TALKY_DIALER_COOLDOWN_TEST_DATABASE_URL points
at a disposable Postgres already holding these two tables:

    CREATE TABLE calls (
        id uuid DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
        campaign_id uuid NOT NULL, lead_id uuid NOT NULL,
        phone_number varchar(20) NOT NULL,
        status varchar(50) NOT NULL DEFAULT 'initiated',
        answered_at timestamptz, created_at timestamptz DEFAULT now(),
        updated_at timestamptz DEFAULT now());
    CREATE TABLE dialer_jobs (
        id uuid DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
        campaign_id uuid NOT NULL, lead_id uuid NOT NULL, call_id uuid,
        phone_number varchar(20) NOT NULL,
        status varchar(50) DEFAULT 'pending', attempt_number int DEFAULT 1,
        created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now());
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models.dialer_job import DialerJob
from app.workers.dialer_worker import DialerWorker

pytestmark = pytest.mark.asyncio

_ENV_VAR = "TALKY_DIALER_COOLDOWN_TEST_DATABASE_URL"
PHONE = "+16478471491"


async def _pool_or_skip():
    try:
        import asyncpg
    except ImportError:
        pytest.skip("asyncpg not installed")
    url = os.getenv(_ENV_VAR)
    if not url:
        pytest.skip(
            f"set {_ENV_VAR} to a disposable Postgres with the `calls`/"
            "`dialer_jobs` tables described in this file's docstring"
        )
    try:
        pool = await asyncpg.create_pool(url, min_size=1, max_size=2, timeout=5)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable at {_ENV_VAR}: {exc}")
    return pool


@pytest.fixture
async def pool():
    p = await _pool_or_skip()
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE calls, dialer_jobs")
    yield p
    await p.close()


def _worker(pool) -> DialerWorker:
    worker = DialerWorker()
    worker._db_pool = pool
    return worker


def _ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


async def _insert_call(
    pool, *, tenant_id, campaign_id, lead_id, status, answered_at, created_at
):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO calls
                (tenant_id, campaign_id, lead_id, phone_number, status,
                 answered_at, created_at)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7)
            """,
            tenant_id,
            campaign_id,
            lead_id,
            PHONE,
            status,
            answered_at,
            created_at,
        )


async def test_answered_call_inside_cooldown_window_blocks_the_clear(pool):
    """Production case: +16478471491 answered 3.6 minutes before the
    unconditional branch cleared and redialled it (job 4cc194a0 -> 620d9ac5,
    call 8b3176ca). This must read as "do not clear"."""
    tenant_id, campaign_id, lead_id = _ids()
    await _insert_call(
        pool,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        lead_id=lead_id,
        status="ended",
        answered_at=datetime.now(timezone.utc) - timedelta(minutes=4),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    job = DialerJob(
        job_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        lead_id=lead_id,
        tenant_id=tenant_id,
        phone_number=PHONE,
    )

    assert await _worker(pool)._lead_has_live_or_answered_call(job, 2) is True


async def test_live_non_terminal_call_blocks_the_clear(pool):
    """A call still ringing (no answered_at yet, non-terminal status) must
    also count as "not stale" — the workaround is for a timestamp with no
    call behind it at all, not one that simply hasn't resolved yet."""
    tenant_id, campaign_id, lead_id = _ids()
    await _insert_call(
        pool,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        lead_id=lead_id,
        status="ringing",
        answered_at=None,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    job = DialerJob(
        job_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        lead_id=lead_id,
        tenant_id=tenant_id,
        phone_number=PHONE,
    )

    assert await _worker(pool)._lead_has_live_or_answered_call(job, 2) is True


async def test_stale_never_answered_call_does_not_block_the_clear(pool):
    """The genuine case the workaround exists for: a terminal, never-answered
    call inside the window (last_called_at set at origination, no real
    connection ever happened) must still allow the clear+redial."""
    tenant_id, campaign_id, lead_id = _ids()
    await _insert_call(
        pool,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        lead_id=lead_id,
        status="no_answer",
        answered_at=None,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    job = DialerJob(
        job_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        lead_id=lead_id,
        tenant_id=tenant_id,
        phone_number=PHONE,
    )

    assert await _worker(pool)._lead_has_live_or_answered_call(job, 2) is False


async def test_answered_call_outside_the_window_does_not_block_the_clear(pool):
    """An answered call from 3 hours ago is outside a 2h cooldown window and
    must not hold the lead forever."""
    tenant_id, campaign_id, lead_id = _ids()
    await _insert_call(
        pool,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        lead_id=lead_id,
        status="ended",
        answered_at=datetime.now(timezone.utc) - timedelta(hours=3),
        created_at=datetime.now(timezone.utc) - timedelta(hours=3, minutes=1),
    )
    job = DialerJob(
        job_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        lead_id=lead_id,
        tenant_id=tenant_id,
        phone_number=PHONE,
    )

    assert await _worker(pool)._lead_has_live_or_answered_call(job, 2) is False


async def test_persist_job_attempt_number_updates_the_real_row(pool):
    tenant_id, campaign_id, lead_id = _ids()
    job = DialerJob(
        job_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        lead_id=lead_id,
        tenant_id=tenant_id,
        phone_number=PHONE,
        attempt_number=2,
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO dialer_jobs
                (id, tenant_id, campaign_id, lead_id, phone_number, attempt_number)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 1)
            """,
            job.job_id,
            tenant_id,
            campaign_id,
            lead_id,
            PHONE,
        )

    await _worker(pool)._persist_job_attempt_number(job)

    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT attempt_number FROM dialer_jobs WHERE id = $1::uuid", job.job_id
        )
    assert stored == 2


async def test_persist_job_attempt_number_is_tenant_scoped(pool):
    """The UPDATE's WHERE carries tenant_id/campaign_id/lead_id predicates
    (CLAUDE.md rule: every write needs an explicit tenant_id predicate).
    Prove it is load-bearing: a row that exists under a DIFFERENT tenant_id
    than the job claims must not be touched, and the guarded 'UPDATE 1'
    check must raise instead of silently no-op'ing."""
    tenant_id, campaign_id, lead_id = _ids()
    other_tenant_id = str(uuid.uuid4())
    job = DialerJob(
        job_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        lead_id=lead_id,
        tenant_id=tenant_id,
        phone_number=PHONE,
        attempt_number=2,
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO dialer_jobs
                (id, tenant_id, campaign_id, lead_id, phone_number, attempt_number)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 1)
            """,
            job.job_id,
            other_tenant_id,
            campaign_id,
            lead_id,
            PHONE,
        )

    with pytest.raises(RuntimeError):
        await _worker(pool)._persist_job_attempt_number(job)

    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT attempt_number FROM dialer_jobs WHERE id = $1::uuid", job.job_id
        )
    assert stored == 1

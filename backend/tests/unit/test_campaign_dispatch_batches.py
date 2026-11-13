"""QA F6-04: bounded Redis batches and exact tenant-scoped pooled ACKs."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import fakeredis.aioredis
import pytest

from app.domain.services.campaign_service import CampaignDispatchError, CampaignService
from app.domain.services.queue_service import DialerQueueService
from app.domain.services.streams_queue_service import (
    DialerStreamsQueueService, STREAM_NORMAL, STREAM_PRIORITY,
)
from tests.unit.test_campaign_dispatch_ack_race import CAMPAIGN, TENANT, _job


class _Redis(fakeredis.aioredis.FakeRedis):
    def __init__(self):
        super().__init__(decode_responses=True)
        self.executions = []

    def pipeline(self, transaction=True, **kwargs):
        pipeline = super().pipeline(transaction=transaction, **kwargs)
        execute = pipeline.execute

        async def record(*args, **options):
            self.executions.append((transaction, len(pipeline.command_stack)))
            return await execute(*args, **options)

        pipeline.execute = record
        return pipeline


class _Connection:
    def __init__(self, jobs):
        self.rows = {
            job.job_id: dict(id=job.job_id, lead_id=job.lead_id,
                             tenant_id=job.tenant_id, campaign_id=job.campaign_id,
                             status='pending') for job in jobs
        }
        self.queries = []
        self.hold = None
        self.entered = asyncio.Event()
        self.drop_update_ids = set()

    @asynccontextmanager
    async def transaction(self):
        snapshot = deepcopy(self.rows)
        try:
            yield
        except BaseException:
            self.rows = snapshot
            raise

    async def execute(self, sql, *args):
        assert 'set_config' in sql or 'SET LOCAL' in sql

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        self.entered.set()
        if self.hold is not None:
            await self.hold.wait()
        # Assert the exact paired SQL contract, not merely any bulk update.
        ids, lead_ids, tenant, campaign = args
        assert all(isinstance(value, UUID) for value in [*ids, *lead_ids, tenant, campaign])
        assert 'tenant_id = $3' in sql and 'campaign_id = $4' in sql
        assert 'unnest($1::uuid[], $2::uuid[])' in sql
        assert 'j.id = sent.id' in sql and 'j.lead_id = sent.lead_id' in sql
        result = []
        for identity, lead in zip(ids, lead_ids, strict=True):
            row = self.rows.get(str(identity))
            if not row or (row['tenant_id'], row['campaign_id'], row['lead_id']) != (
                str(tenant), str(campaign), str(lead)
            ):
                continue
            if sql.lstrip().startswith('UPDATE'):
                if row['status'] != 'pending' or str(identity) in self.drop_update_ids:
                    continue
                row['status'] = 'queued'
            result.append(dict(row))
        return result


class _Pool:
    def __init__(self, conn):
        self.conn = conn
        self.leases = 0

    @asynccontextmanager
    async def acquire(self):
        self.leases += 1
        yield self.conn


def _service(jobs):
    conn = _Connection(jobs)
    service = CampaignService(SimpleNamespace(pool=_Pool(conn)))
    service._campaign_still_running = AsyncMock(return_value=True)

    async def legacy_ack(job):
        conn.rows[job.job_id]['status'] = 'queued'

    # A safe double for the pre-fix per-job path; no real adapter connections.
    service._confirm_job_dispatched = AsyncMock(side_effect=legacy_ack)
    return service, conn


def _list_queue(redis):
    queue = DialerQueueService(redis)
    queue._initialized = True
    return queue


@pytest.mark.asyncio
async def test_2000_jobs_use_bounded_pipelines_and_one_pooled_ack_per_batch():
    jobs = [_job(n) for n in range(1, 2001)]
    service, conn = _service(jobs)
    redis = _Redis()
    try:
        assert await service._dispatch_durable_jobs(_list_queue(redis), jobs) == 2000
        assert redis.executions == [(False, 100)] * 20
        assert len(conn.queries) == 20
        assert all(sql.lstrip().startswith('UPDATE') for sql, _ in conn.queries)
        assert service._confirm_job_dispatched.await_count == 0
        assert all(row['status'] == 'queued' for row in conn.rows.values())
        assert await redis.hget(DialerQueueService.STATS_KEY, 'total_enqueued') == '2000'
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_list_batch_preserves_single_enqueue_payload_order_routing_and_stats():
    jobs = [_job(n).model_copy(update={'priority': 9 if n % 2 else 5}) for n in range(1, 8)]
    old, new = _Redis(), _Redis()
    try:
        for job in jobs:
            assert await _list_queue(old).enqueue_job(job)
        assert await _list_queue(new).enqueue_jobs(jobs) == [True] * len(jobs)
        for key in [DialerQueueService.PRIORITY_QUEUE,
                    DialerQueueService.TENANT_QUEUE_PREFIX.format(tenant_id=TENANT)]:
            assert await new.lrange(key, 0, -1) == await old.lrange(key, 0, -1)
        assert await new.hgetall(DialerQueueService.STATS_KEY) == await old.hgetall(DialerQueueService.STATS_KEY)
        assert new.executions == [(False, 7)]
    finally:
        await old.aclose()
        await new.aclose()


@pytest.mark.asyncio
async def test_stream_batch_preserves_payloads_and_priority_routing():
    redis = _Redis()
    jobs = [_job(n).model_copy(update={'priority': 9 if n % 2 else 5}) for n in range(1, 8)]
    try:
        assert await DialerStreamsQueueService(redis).enqueue_jobs(jobs) == [True] * 7
        for stream, parity in [(STREAM_PRIORITY, 1), (STREAM_NORMAL, 0)]:
            entries = await redis.xrange(stream)
            assert [json.loads(fields['job']) for _, fields in entries] == [
                job.to_redis_dict() for n, job in enumerate(jobs, 1) if n % 2 == parity
            ]
        assert redis.executions == [(False, 7)]
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_partial_pushes_report_known_acceptance_and_leave_the_whole_batch_replayable():
    jobs = [_job(n).model_copy(update={'priority': 9 if n == 2 else 5}) for n in range(1, 4)]
    service, conn = _service(jobs)
    redis = _Redis()
    try:
        await redis.set(DialerQueueService.PRIORITY_QUEUE, 'wrong-type')
        with pytest.raises(CampaignDispatchError) as caught:
            await service._dispatch_durable_jobs(_list_queue(redis), jobs)
        assert caught.value.jobs_enqueued == 2
        assert caught.value.jobs_pending == 3
        assert conn.queries == []
        assert all(row['status'] == 'pending' for row in conn.rows.values())
        assert await redis.hget(DialerQueueService.STATS_KEY, 'total_enqueued') == '2'
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_stats_failure_cannot_discard_confirmed_pushes():
    redis = _Redis()
    try:
        await redis.set(DialerQueueService.STATS_KEY, 'wrong-type')
        assert await _list_queue(redis).enqueue_jobs([_job(1)]) == [True]
        assert await redis.llen(DialerQueueService.TENANT_QUEUE_PREFIX.format(tenant_id=TENANT)) == 1
    finally:
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['retry_scheduled', 'calling', 'processing', 'completed', 'cancelled'])
async def test_bulk_ack_preserves_rows_advanced_by_a_worker(status):
    jobs = [_job(n) for n in range(1, 4)]
    service, conn = _service(jobs)
    conn.rows[jobs[1].job_id]['status'] = status
    await service._confirm_jobs_dispatched(jobs)
    assert [row['status'] for row in conn.rows.values()] == ['queued', status, 'queued']
    assert len(conn.queries) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['tenant_id', 'campaign_id', 'lead_id', 'missing'])
async def test_bulk_ack_rolls_back_when_any_exact_owned_tuple_is_unproved(field):
    jobs = [_job(1), _job(2)]
    service, conn = _service(jobs)
    if field == 'missing':
        del conn.rows[jobs[1].job_id]
    else:
        conn.rows[jobs[1].job_id][field] = '99999999-9999-4999-8999-999999999999'
    before = deepcopy(conn.rows)
    with pytest.raises(RuntimeError, match='not exact'):
        await service._confirm_jobs_dispatched(jobs)
    assert conn.rows == before


@pytest.mark.asyncio
async def test_pooled_ack_yields_the_loop_while_the_database_is_busy():
    jobs = [_job(1)]
    service, conn = _service(jobs)
    conn.hold = asyncio.Event()
    task = asyncio.create_task(service._confirm_jobs_dispatched(jobs))
    try:
        await asyncio.wait_for(conn.entered.wait(), timeout=1)
        assert not task.done()
        conn.hold.set()
        await asyncio.wait_for(task, timeout=1)
        assert conn.rows[jobs[0].job_id]['status'] == 'queued'
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['duplicate', 'tenant_id', 'campaign_id'])
async def test_invalid_dispatch_scope_is_refused_before_any_push(change):
    jobs = [_job(1), _job(2)]
    if change == 'duplicate':
        jobs[1] = jobs[0]
    else:
        jobs[1] = jobs[1].model_copy(update={change: '99999999-9999-4999-8999-999999999999'})
    service, conn = _service(jobs)
    queue = SimpleNamespace(enqueue_jobs=AsyncMock(), enqueue_job=AsyncMock(return_value=True))
    with pytest.raises(CampaignDispatchError, match='scope'):
        await service._dispatch_durable_jobs(queue, jobs)
    queue.enqueue_jobs.assert_not_awaited()
    queue.enqueue_job.assert_not_awaited()
    assert conn.queries == []


@pytest.mark.asyncio
async def test_lost_reply_after_publication_is_not_retried_or_reported_as_zero_delivery():
    jobs = [_job(n) for n in range(1, 103)]
    service, conn = _service(jobs)
    redis = _Redis()
    queue = _list_queue(redis)
    queue.enqueue_job = AsyncMock(side_effect=AssertionError('legacy retry after batch I/O'))
    pipeline = redis.pipeline

    def lose_second_reply(*args, **kwargs):
        current = pipeline(*args, **kwargs)
        execute = current.execute

        async def execute_then_disconnect(*args, **kwargs):
            replies = await execute(*args, **kwargs)
            if len(redis.executions) == 2:
                raise ConnectionError('reply lost after Redis accepted payloads')
            return replies

        current.execute = execute_then_disconnect
        return current

    redis.pipeline = lose_second_reply
    try:
        with pytest.raises(CampaignDispatchError, match='could not be confirmed') as caught:
            await service._dispatch_durable_jobs(queue, jobs)
        assert caught.value.jobs_enqueued == 100  # confirmed count, not total delivered
        assert caught.value.jobs_pending == 2  # unacknowledged/replay-required count
        assert await redis.llen(queue.TENANT_QUEUE_PREFIX.format(tenant_id=TENANT)) == 102
        assert [row['status'] for row in conn.rows.values()].count('queued') == 100
        assert [row['status'] for row in conn.rows.values()].count('pending') == 2
        queue.enqueue_job.assert_not_awaited()
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_bulk_ack_cannot_claim_a_still_pending_row():
    jobs = [_job(1), _job(2)]
    service, conn = _service(jobs)
    conn.drop_update_ids.add(jobs[1].job_id)
    with pytest.raises(RuntimeError, match='not exact'):
        await service._confirm_jobs_dispatched(jobs)
    assert all(row['status'] == 'pending' for row in conn.rows.values())


@pytest.mark.asyncio
async def test_batch_dispatch_checks_stop_without_expanding_the_100_job_window():
    jobs = [_job(n) for n in range(1, 451)]
    service, conn = _service(jobs)
    service._campaign_still_running.side_effect = [True, False]
    redis = _Redis()
    try:
        assert await service._dispatch_durable_jobs(_list_queue(redis), jobs) == 200
        assert redis.executions == [(False, 100)] * 2
        assert sum(row['status'] == 'pending' for row in conn.rows.values()) == 250
    finally:
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('backend', ['list', 'streams'])
async def test_batches_reject_oversize_before_any_publish(backend):
    redis = _Redis()
    queue = _list_queue(redis) if backend == 'list' else DialerStreamsQueueService(redis)
    try:
        with pytest.raises(ValueError, match='exceeds 100'):
            await queue.enqueue_jobs([_job(n) for n in range(1, 102)])
        assert redis.executions == []
        assert await redis.keys('*') == []
    finally:
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply', ['not-an-entry-id', b'not-an-entry-id', '', None, True])
async def test_streams_needs_an_actual_xadd_id_to_confirm_acceptance(reply):
    redis = _Redis()
    original = redis.pipeline

    def malformed_reply(*args, **kwargs):
        pipeline = original(*args, **kwargs)
        pipeline.execute = AsyncMock(return_value=[reply])
        return pipeline

    redis.pipeline = malformed_reply
    try:
        assert await DialerStreamsQueueService(redis).enqueue_jobs([_job(1)]) == [False]
    finally:
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply', [[True], [1, True], [True, None], (True, True)])
async def test_malformed_batch_results_cannot_acknowledge_any_row(reply):
    jobs = [_job(1), _job(2)]
    service, conn = _service(jobs)
    queue = SimpleNamespace(enqueue_jobs=AsyncMock(return_value=reply), enqueue_job=AsyncMock())
    with pytest.raises(CampaignDispatchError, match='acceptance was not exact'):
        await service._dispatch_durable_jobs(queue, jobs)
    assert conn.queries == []
    assert all(row['status'] == 'pending' for row in conn.rows.values())
    queue.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_uuid_aliases_cannot_publish_the_same_durable_identity_twice():
    identity = 'abcdefab-1234-4123-8123-123456789abc'
    jobs = [_job(1).model_copy(update={'job_id': identity}),
            _job(2).model_copy(update={'job_id': identity.upper()})]
    service, conn = _service(jobs)
    queue = SimpleNamespace(enqueue_jobs=AsyncMock(), enqueue_job=AsyncMock())
    with pytest.raises(CampaignDispatchError, match='scope'):
        await service._dispatch_durable_jobs(queue, jobs)
    queue.enqueue_jobs.assert_not_awaited()
    assert conn.queries == []

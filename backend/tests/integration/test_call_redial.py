"""Redial durability and caller-ID projection against the migrated test database.

Only TEST_DATABASE_URL is accepted. No carrier, Redis or customer call is used;
the queue double models an atomic publication and a lost acknowledgement.
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.api.v1.endpoints.calls import list_live_calls
from app.core.db_utils import acquire_with_tenant
from app.domain.services import call_redial_service as service
from app.domain.services.dialer.job_states import ACTIVE_STATUSES
from app.domain.services.telephony import trunk_resolver

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def redial_db(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit TEST_DATABASE_URL is required")
    parsed = urlparse(dsn)
    if not parsed.path.removeprefix("/").endswith("_test"):
        pytest.fail("Redial integration requires an explicit disposable *_test database")
    # Fail if an explicitly configured database is unreachable or unmigrated:
    # CI must never silently skip a broken migration/acceptance contract.
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, timeout=5, command_timeout=10)
    tenant, other_tenant, campaign, lead, first_call, second_call = [str(uuid4()) for _ in range(6)]
    fixture = SimpleNamespace(pool=pool, tenant=tenant, other_tenant=other_tenant,
        campaign=campaign, lead=lead, calls=[first_call, second_call], phone="+14165550123")
    try:
        async with acquire_with_tenant(pool, None) as conn:
            await conn.execute(
                """INSERT INTO tenants(id,business_name,subscription_status,status,calling_rules)
                   VALUES ($1::uuid,'Redial integration','active','active',$3::jsonb),
                          ($2::uuid,'Other redial integration','active','active',$3::jsonb)""",
                tenant, other_tenant, json.dumps({"caller_id": "+442079460000"}),
            )
            await conn.execute(
                """INSERT INTO campaigns(id,tenant_id,name,status,direction,calling_config)
                   VALUES ($1::uuid,$2::uuid,'Redial integration','running','outbound','{}')""",
                campaign, tenant,
            )
            await conn.execute(
                """INSERT INTO leads(id,tenant_id,campaign_id,phone_number,status,do_not_call,first_name)
                   VALUES ($1::uuid,$2::uuid,$3::uuid,$4,'completed',FALSE,'Fixture')""",
                lead, tenant, campaign, fixture.phone,
            )
            for call_id in fixture.calls:
                await conn.execute(
                    """INSERT INTO calls(id,tenant_id,campaign_id,lead_id,phone_number,status,direction,outcome,ended_at)
                       VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,'completed','outbound','answered',NOW())""",
                    call_id, tenant, campaign, lead, fixture.phone,
                )

        async def requires_sip(*_args, **_kwargs):
            return False

        # Exercise actual DNC SQL and all durable job/lead transactions. The
        # provider admission boundary is independently covered by SIP tests.
        monkeypatch.setattr(trunk_resolver, "requires_sip_readiness", requires_sip)
        yield fixture
    finally:
        # No append-only call events are written by these read/job operations.
        # Delete only UUID-scoped fixture rows, including circular job/call FKs.
        try:
            async with acquire_with_tenant(pool, None) as conn:
                await conn.execute("DELETE FROM dialer_jobs WHERE tenant_id=ANY($1::uuid[])", [tenant, other_tenant])
                await conn.execute("DELETE FROM calls WHERE tenant_id=ANY($1::uuid[])", [tenant, other_tenant])
                await conn.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", [tenant, other_tenant])
        finally:
            await pool.close()


class AtomicQueue:
    def __init__(self, fixture, *, lose_first_ack=False):
        self.fixture = fixture
        self.lose_first_ack = lose_first_ack
        self.keys = set()
        self.requests = []
        self.confirmed = []
        self.lock = asyncio.Lock()

    async def schedule_job_once(self, job, *, idempotency_key):
        # A different PostgreSQL connection must see the pending row before
        # anything could be published, proving the service committed first.
        async with acquire_with_tenant(self.fixture.pool, self.fixture.tenant) as conn:
            durable = await conn.fetchrow("SELECT id,phone_number FROM dialer_jobs WHERE id=$1::uuid", job.job_id)
        assert durable is not None
        assert durable["phone_number"] == job.phone_number
        async with self.lock:
            self.requests.append((job.job_id, idempotency_key, job.phone_number))
            self.keys.add(idempotency_key)
            if self.lose_first_ack:
                self.lose_first_ack = False
                raise TimeoutError("Synthetic lost queue acknowledgement after publication")
        return True

    async def confirm_retry_once(self, key):
        self.confirmed.append(key)


async def jobs(fixture):
    async with acquire_with_tenant(fixture.pool, fixture.tenant) as conn:
        return await conn.fetch("SELECT id,status,phone_number FROM dialer_jobs WHERE tenant_id=$1::uuid AND lead_id=$2::uuid", fixture.tenant, fixture.lead)


@pytest.mark.parametrize("same_original", [True, False])
async def test_concurrent_requests_create_one_durable_active_job(redial_db, monkeypatch, same_original):
    h = redial_db
    queue = AtomicQueue(h)
    barrier = asyncio.Barrier(2)

    async def synchronized_readiness(*_args, **_kwargs):
        await asyncio.wait_for(barrier.wait(), timeout=5)
        return False

    monkeypatch.setattr(trunk_resolver, "requires_sip_readiness", synchronized_readiness)
    call_ids = [h.calls[0], h.calls[0] if same_original else h.calls[1]]
    results = await asyncio.wait_for(asyncio.gather(*[
        service.request_redial(h.pool, queue, tenant_id=h.tenant, call_id=call_id)
        for call_id in call_ids
    ], return_exceptions=True), timeout=15)
    records = await jobs(h)
    assert len(records) == 1
    assert records[0]["status"] in ACTIVE_STATUSES
    assert records[0]["phone_number"] == h.phone
    assert len(queue.keys) == 1
    if same_original:
        assert all(isinstance(result, dict) for result in results), [type(result).__name__ for result in results]
        assert {result["job_id"] for result in results} == {str(records[0]["id"])}
    else:
        assert sum(isinstance(result, dict) for result in results) == 1
        blocked = next(result for result in results if isinstance(result, Exception))
        assert isinstance(blocked, service.RedialError)
        assert blocked.code == "contact_busy"


async def test_lost_queue_ack_retries_same_durable_identity(redial_db):
    h = redial_db
    queue = AtomicQueue(h, lose_first_ack=True)
    first = await service.request_redial(h.pool, queue, tenant_id=h.tenant, call_id=h.calls[0])
    assert first["status"] == "pending"
    assert (await jobs(h))[0]["status"] == "pending"
    assert queue.confirmed == []
    second = await service.request_redial(h.pool, queue, tenant_id=h.tenant, call_id=h.calls[0])
    assert second["status"] == "queued"
    assert second["job_id"] == first["job_id"] == service.redial_job_id(h.calls[0])
    assert len(await jobs(h)) == len(queue.keys) == 1
    assert queue.requests[0] == queue.requests[1]
    assert queue.confirmed == [queue.requests[0][1]]


async def test_another_tenant_cannot_preview_or_request_a_redial(redial_db):
    h = redial_db
    queue = AtomicQueue(h)
    for operation in (
        service.preview_redial(h.pool, tenant_id=h.other_tenant, call_id=h.calls[0]),
        service.request_redial(h.pool, queue, tenant_id=h.other_tenant, call_id=h.calls[0]),
    ):
        with pytest.raises(service.RedialError) as caught:
            await operation
        assert caught.value.status_code == 404
    assert await jobs(h) == []
    assert queue.requests == []


async def test_live_calls_use_actual_canadian_leg_not_tenant_uk_default(redial_db):
    h = redial_db
    async with acquire_with_tenant(h.pool, h.tenant) as conn:
        await conn.execute("UPDATE calls SET status='ringing',started_at=NOW(),ended_at=NULL WHERE id=ANY($1::uuid[])", h.calls)
        await conn.execute(
            """INSERT INTO call_legs(id,call_id,leg_type,direction,provider,from_number,to_number)
               VALUES ($1::uuid,$2::uuid,'outbound','outbound','asterisk','+14165550199',$3)""",
            str(uuid4()), h.calls[0], h.phone,
        )
    result = await list_live_calls(campaign_id=None, direction=None, recent_window_seconds=0,
        current_user=SimpleNamespace(tenant_id=h.tenant), db_client=SimpleNamespace(pool=h.pool))
    actual = {item.id: item.caller_id for item in result.items}
    assert actual == {h.calls[0]: "+14165550199", h.calls[1]: None}
    other = await list_live_calls(campaign_id=None, direction=None, recent_window_seconds=0,
        current_user=SimpleNamespace(tenant_id=h.other_tenant), db_client=SimpleNamespace(pool=h.pool))
    assert other.items == []

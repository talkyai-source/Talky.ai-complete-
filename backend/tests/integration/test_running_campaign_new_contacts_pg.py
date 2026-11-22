"""Campaign completion and customer-facing call counts ignore Test-agent calls.

Runs the real ``_COMPLETE_IF_FINISHED_SQL`` and the real stats/list-live-call
statements against a migrated PostgreSQL (disposable localhost ``*_test`` database
only). Written to the style of the neighbouring files; it skips without
TEST_DATABASE_URL.
"""
import os
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.api.v1.endpoints.campaigns import _STATS_CALLS_SQL
from app.domain.services import campaign_service as svc

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("accepts only a disposable localhost *_test database")
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    tenant, other = uuid4(), uuid4()
    campaign, other_campaign = uuid4(), uuid4()
    try:
        for t in (tenant, other):
            await admin.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic running contacts')", t)
        for c, t in ((campaign, tenant), (other_campaign, other)):
            await admin.execute(
                "INSERT INTO campaigns(id,tenant_id,name,status,direction) VALUES($1,$2,'Synthetic','running','outbound')",
                c, t)
        yield admin, tenant, other, campaign, other_campaign
    finally:
        for t in (tenant, other):
            async with admin.transaction():
                await admin.execute("SET LOCAL app.bypass_rls='on'")
                await admin.execute(
                    "DELETE FROM call_events WHERE call_id IN (SELECT id FROM calls WHERE tenant_id=$1)", t)
                await admin.execute(
                    "DELETE FROM call_legs WHERE call_id IN (SELECT id FROM calls WHERE tenant_id=$1)", t)
                await admin.execute("DELETE FROM calls WHERE tenant_id=$1", t)
            await admin.execute("DELETE FROM leads WHERE tenant_id=$1", t)
            await admin.execute("DELETE FROM campaigns WHERE tenant_id=$1", t)
            await admin.execute("DELETE FROM tenants WHERE id=$1", t)
        await admin.close()


async def _call(admin, tenant, campaign, status, *, is_test, outcome=None):
    await admin.execute(
        "INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status,is_test,outcome) "
        "VALUES($1,$2,$3,'+15550003001',$4,$5,$6)",
        uuid4(), tenant, campaign, status, is_test, outcome)


async def _status(admin, campaign):
    return await admin.fetchval("SELECT status FROM campaigns WHERE id=$1", campaign)


async def test_a_stuck_test_agent_row_does_not_keep_the_campaign_running(db):
    admin, tenant, _other, campaign, _oc = db
    await _call(admin, tenant, campaign, "in_progress", is_test=True)

    assert await svc.complete_campaign_if_finished(admin, str(campaign), str(tenant)) is True
    assert await _status(admin, campaign) == "completed"


async def test_a_real_live_call_still_keeps_the_campaign_running(db):
    admin, tenant, _other, campaign, _oc = db
    await _call(admin, tenant, campaign, "in_call", is_test=False)
    await _call(admin, tenant, campaign, "in_progress", is_test=True)

    assert await svc.complete_campaign_if_finished(admin, str(campaign), str(tenant)) is False
    assert await _status(admin, campaign) == "running"


async def test_another_tenants_live_call_is_ignored(db):
    admin, tenant, other, campaign, other_campaign = db
    await _call(admin, other, other_campaign, "in_call", is_test=False)

    assert await svc.complete_campaign_if_finished(admin, str(campaign), str(tenant)) is True


async def test_a_pending_lead_still_blocks_completion(db):
    admin, tenant, _other, campaign, _oc = db
    await admin.execute(
        "INSERT INTO leads(id,tenant_id,campaign_id,phone_number,status) VALUES($1,$2,$3,'+15550003002','pending')",
        uuid4(), tenant, campaign)

    assert await svc.complete_campaign_if_finished(admin, str(campaign), str(tenant)) is False


async def test_stats_and_list_live_count_ignore_test_agent_calls(db):
    admin, tenant, _other, campaign, _oc = db
    await _call(admin, tenant, campaign, "completed", is_test=False, outcome="answered")
    await _call(admin, tenant, campaign, "completed", is_test=True, outcome="answered")
    await _call(admin, tenant, campaign, "in_progress", is_test=True)

    rows = await admin.fetch(_STATS_CALLS_SQL, campaign, tenant)
    assert {r["outcome"]: r["n"] for r in rows} == {"answered": 1}

    live = await admin.fetch(svc._LIST_LIVE_CALLS_SQL, tenant, [campaign], ["in_progress"])
    assert live == []

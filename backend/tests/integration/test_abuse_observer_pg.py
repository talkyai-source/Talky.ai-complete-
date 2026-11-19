"""AbuseObserver's INSERT against a real abuse_events table migrated by 0093.

Also pins the bug it works around: AbuseDetectionService._record_event writes the
detector's suggestion ("flag") into action_taken, which the 0093 CHECK rejects.
Runs only with TEST_DATABASE_URL (the shared `database` fixture skips otherwise).
"""
from uuid import uuid4

import asyncpg
import pytest

from app.domain.services import abuse_observer as ao
from app.domain.services.abuse_detection import AbuseDetectionService
from tests.integration.test_abuse_event_columns_pg import LINEAGE, migration
from tests.integration.test_touch_trigger_repair_pg import database  # noqa: F401

pytestmark = pytest.mark.integration

CALLS = (
    """CREATE TABLE calls (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
       phone_number varchar(20) NOT NULL, created_at timestamptz DEFAULT now(),
       is_test boolean NOT NULL DEFAULT false)""",
)


async def test_observed_premium_rate_call_inserts_one_flagged_row(database):  # noqa: F811
    await database.build(LINEAGE + CALLS)
    await database.apply(migration())
    tenant = uuid4()
    ao._dedup_local.clear()
    async with database.pool.acquire() as conn:
        await conn.execute("INSERT INTO tenants (id, business_name) VALUES ($1, 'Observer fixture')", tenant)

    observer = ao.AbuseObserver(db_pool=database.pool, redis_client=None)
    events = await observer.observe_outbound_call(str(tenant), "+19005550100", None)
    assert [e.abuse_type.value for e in events] == ["premium_rate"]

    async with database.pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM abuse_events WHERE tenant_id=$1", tenant)
    assert len(rows) == 1
    assert rows[0]["action_taken"] == "flagged" and rows[0]["event_type"] == "premium_rate"
    assert rows[0]["phone_number_called"] == "+19005550100"


async def test_the_original_record_event_value_is_rejected_by_the_check(database):  # noqa: F811
    await database.build(LINEAGE + CALLS)
    await database.apply(migration())
    tenant = uuid4()
    async with database.pool.acquire() as conn:
        await conn.execute("INSERT INTO tenants (id, business_name) VALUES ($1, 'Observer fixture')", tenant)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO abuse_events (tenant_id, event_type, severity, action_taken) "
                "VALUES ($1, 'premium_rate', 'high', 'flag')",
                tenant,
            )
    # The original method swallows that rejection and stores nothing.
    original = AbuseDetectionService(db_pool=database.pool, redis_client=None)
    event = (await original.analyze_call_initiated(str(tenant), "+19005550100"))[0]
    await original._record_event(event)
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM abuse_events WHERE tenant_id=$1", tenant) == 0

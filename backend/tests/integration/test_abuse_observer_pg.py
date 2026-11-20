"""AbuseObserver's INSERT against a real abuse_events table migrated by 0093.

Also pins the bug it works around: AbuseDetectionService._record_event writes the
detector's suggestion ("flag") into action_taken, which the 0093 CHECK rejects.
Runs only with TEST_DATABASE_URL (the shared `database` fixture skips otherwise).
"""
import json
from uuid import uuid4

import asyncpg
import pytest

from app.domain.services import abuse_observer as ao
from app.domain.services.call_guard import CallGuard
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


async def test_velocity_check_ignores_observer_rows_but_counts_real_events(database):  # noqa: F811
    """Runs the real CallGuard._check_velocity statement on a real pool."""
    await database.build(LINEAGE + CALLS)
    await database.apply(migration())
    tenant, other = uuid4(), uuid4()
    guard = CallGuard(db_pool=database.pool, redis_client=None)
    async with database.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, business_name) VALUES ($1, 'Velocity fixture'), ($2, 'Other fixture')",
            tenant, other,
        )
    try:
        # (a) a row exactly as AbuseObserver writes it: high severity, flagged, marked
        async with database.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO abuse_events (tenant_id, event_type, severity, action_taken, action_details) "
                "VALUES ($1, 'premium_rate', 'high', 'flagged', $2::jsonb)",
                tenant, json.dumps({"recommended_action": "block", "source": ao.OBSERVER_SOURCE}),
            )
        result = await guard._check_velocity(str(tenant))
        assert result.passed is True, result.reason

        # (c) another tenant's unresolved high event is not counted for this tenant
        async with database.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO abuse_events (tenant_id, event_type, severity, action_taken, action_details) "
                "VALUES ($1, 'velocity_anomaly', 'critical', 'flagged', NULL)",
                other,
            )
        assert (await guard._check_velocity(str(tenant))).passed is True

        # (b) an ordinary high event with NULL action_details still blocks
        async with database.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO abuse_events (tenant_id, event_type, severity, action_taken, action_details) "
                "VALUES ($1, 'velocity_anomaly', 'high', 'flagged', NULL)",
                tenant,
            )
        result = await guard._check_velocity(str(tenant))
        assert result.passed is False
        assert result.details["recent_high_severity_events"] == 1  # the observer row is not counted
    finally:
        async with database.pool.acquire() as conn:
            await conn.execute("DELETE FROM abuse_events WHERE tenant_id=ANY($1::uuid[])", [tenant, other])
            await conn.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", [tenant, other])

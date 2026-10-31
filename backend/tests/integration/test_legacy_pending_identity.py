"""Identity recovery SQL proof using connection-local tables, never real calls."""
import os
import uuid

import asyncpg
import pytest

from app.domain.services.telephony.legacy_pending_identity import reconcile_pending_identity


async def verify_recovery_sql(conn):
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("""
            CREATE TEMP TABLE calls (
                id uuid PRIMARY KEY, tenant_id uuid, status text, direction text,
                provider text, provider_call_id text, external_call_uuid text,
                updated_at timestamptz DEFAULT now(), outcome text, duration_seconds int
            ) ON COMMIT DROP;
            CREATE TEMP TABLE call_legs (
                call_id uuid, leg_type text, provider text, provider_leg_id text
            ) ON COMMIT DROP;
        """)
        for name in ("calls", "call_legs"):
            assert await conn.fetchval(
                "SELECT relnamespace=pg_my_temp_schema() FROM pg_class WHERE oid=to_regclass($1)", name
            )
        tenant = uuid.uuid4()
        ids = [uuid.uuid4() for _ in range(6)]
        for i, call_id in enumerate(ids):
            await conn.execute(
                "INSERT INTO calls (id,tenant_id,status,direction,external_call_uuid) VALUES ($1,$2,'termination_pending','outbound',$3)",
                call_id, tenant, f"physical-{i}",
            )
        # Matching primary-leg evidence: only rows 0 and 1 are candidates;
        # row 1 also carries contradictory evidence and must stay quarantined.
        for i, kind, provider, key in [
            (0, "pstn_outbound", "asterisk", "physical-0"),
            (1, "pstn_outbound", "asterisk", "physical-1"),
            (1, "pstn_outbound", "vonage", "physical-1"),
            (2, "transfer", "asterisk", "physical-2"),
            (3, "pstn_outbound", "asterisk", "different-key"),
            (4, "pstn_outbound", None, "physical-4"),
        ]:
            await conn.execute("INSERT INTO call_legs VALUES ($1,$2,$3,$4)", ids[i], kind, provider, key)
        await reconcile_pending_identity(conn, "asterisk")
        repaired = await conn.fetch("SELECT * FROM calls WHERE provider IS NOT NULL")
        assert len(repaired) == 1 and repaired[0]["id"] == ids[0]
        assert repaired[0]["provider_call_id"] == "physical-0"
        assert repaired[0]["status"] == "termination_pending"
        assert repaired[0]["outcome"] is None and repaired[0]["duration_seconds"] is None
        await reconcile_pending_identity(conn, "asterisk")
        assert await conn.fetchval("SELECT count(*) FROM calls WHERE provider IS NOT NULL") == 1
    finally:
        await tx.rollback()


@pytest.mark.asyncio
async def test_matching_durable_leg_is_required_for_legacy_identity_recovery():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL required; fixtures use temporary tables only")
    conn = await asyncpg.connect(dsn)
    try:
        await verify_recovery_sql(conn)
    finally:
        await conn.close()

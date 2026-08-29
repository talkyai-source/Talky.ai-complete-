"""Real-PostgreSQL proof that reassignment preserves historical call FKs."""

from __future__ import annotations

import os
import asyncio
import uuid
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.core.db_utils import acquire_with_tenant
from app.domain.services.inbound_campaign_service import InboundCampaignService


@pytest.mark.asyncio
async def test_cross_tenant_reassignment_keeps_historical_call_on_source_assignment():
    dsn = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, timeout=3)
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable: {exc}")

    ids = {
        name: uuid.uuid4()
        for name in (
            "source_tenant",
            "target_tenant",
            "requester",
            "approver",
            "source_campaign",
            "target_campaign",
            "source_config",
            "target_config",
            "source_phone",
            "target_phone",
            "source_trunk",
            "target_trunk",
            "source_assignment",
            "historical_call",
            "request",
        )
    }
    did = f"+1555{int(ids['request'].hex[:7], 16) % 10_000_000:07d}"
    request_key = f"integration-{ids['request']}"
    approve_key = f"integration-{ids['approver']}"

    try:
        async with acquire_with_tenant(pool, None) as conn:
            ready = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='inbound_reassignment_requests'
                      AND column_name='approved_assignment_id'
                )
                """
            )
            if not ready:
                pytest.skip("database is not at the current 0022 schema")
            await conn.execute(
                "INSERT INTO tenants (id,business_name,subscription_status,status) "
                "VALUES ($1,'Inbound source','active','active'),"
                "($2,'Inbound target','active','active')",
                ids["source_tenant"],
                ids["target_tenant"],
            )
            await conn.execute(
                """
                INSERT INTO user_profiles (id,email,role)
                VALUES ($1,$3,'platform_admin'),($2,$4,'platform_admin')
                """,
                ids["requester"],
                ids["approver"],
                f"requester-{ids['requester']}@example.test",
                f"approver-{ids['approver']}@example.test",
            )
            await conn.execute(
                """
                INSERT INTO campaigns (
                    id,tenant_id,name,status,system_prompt,voice_id,direction
                ) VALUES
                    ($1,$2,'Source inbound','running','prompt','voice',$5),
                    ($3,$4,'Target inbound','running','prompt','voice',$5)
                """,
                ids["source_campaign"],
                ids["source_tenant"],
                ids["target_campaign"],
                ids["target_tenant"],
                "inbound",
            )
            await conn.execute(
                """
                INSERT INTO tenant_phone_numbers (
                    id,tenant_id,e164,provider,status,verification_method,verified_at
                ) VALUES
                    ($1,$2,$5,'manual_admin','verified','manual_admin',NOW()),
                    ($3,$4,$5,'manual_admin','verified','manual_admin',NOW())
                """,
                ids["source_phone"],
                ids["source_tenant"],
                ids["target_phone"],
                ids["target_tenant"],
                did,
            )
            await conn.execute(
                """
                INSERT INTO tenant_sip_trunks (
                    id,tenant_id,trunk_name,sip_domain,direction,is_active,
                    live_registration_status,live_status_checked_at
                ) VALUES
                    ($1,$2,'source-inbound','source.invalid','inbound',TRUE,'loaded',NOW()),
                    ($3,$4,'target-inbound','target.invalid','inbound',TRUE,'loaded',NOW())
                """,
                ids["source_trunk"],
                ids["source_tenant"],
                ids["target_trunk"],
                ids["target_tenant"],
            )
            await conn.execute(
                """
                INSERT INTO inbound_campaign_configs (
                    id,tenant_id,campaign_id,name,status,config_checksum,
                    created_by,updated_by
                ) VALUES
                    ($1,$2,$3,'Source config','paused',$7,$8,$8),
                    ($4,$5,$6,'Target config','paused',$7,$8,$8)
                """,
                ids["source_config"],
                ids["source_tenant"],
                ids["source_campaign"],
                ids["target_config"],
                ids["target_tenant"],
                ids["target_campaign"],
                "a" * 64,
                ids["requester"],
            )
            await conn.execute(
                """
                INSERT INTO inbound_did_assignments (
                    id,tenant_id,phone_number_id,campaign_id,config_id,sip_trunk_id,
                    canonical_did,status,status_before_quarantine,version,
                    created_by,updated_by
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,'quarantined','paused',4,$8,$8)
                """,
                ids["source_assignment"],
                ids["source_tenant"],
                ids["source_phone"],
                ids["source_campaign"],
                ids["source_config"],
                ids["source_trunk"],
                did,
                ids["requester"],
            )
            await conn.execute(
                """
                INSERT INTO calls (
                    id,tenant_id,campaign_id,phone_number,status,talklee_call_id,
                    direction,provider,provider_call_id,called_did,called_did_id,
                    assignment_id,route_version,config_version,route_snapshot,
                    admission_status,consent_status,processing_status,billing_status,
                    reserved_seconds,duration_seconds
                ) VALUES (
                    $1,$2,$3,$4::text,'completed',$5,'inbound','asterisk',$6,$4::text,$7,
                    $8,3,1,'{}','allowed','not_required','completed','finalized',0,30
                )
                """,
                ids["historical_call"],
                ids["source_tenant"],
                ids["source_campaign"],
                did,
                ("IN" + ids["historical_call"].hex[:18]),
                f"history-{ids['historical_call']}",
                ids["source_phone"],
                ids["source_assignment"],
            )
            await conn.execute(
                """
                INSERT INTO inbound_reassignment_requests (
                    id,tenant_id,source_tenant_id,assignment_id,target_tenant_id,
                    target_campaign_id,target_config_id,expected_assignment_version,
                    reason,requested_by,idempotency_key
                ) VALUES ($1,$2,$2,$3,$4,$5,$6,4,'ownership transfer',$7,$8)
                """,
                ids["request"],
                ids["source_tenant"],
                ids["source_assignment"],
                ids["target_tenant"],
                ids["target_campaign"],
                ids["target_config"],
                ids["requester"],
                request_key,
            )

        # The append-only audit trigger deliberately prevents test teardown
        # from deleting or anonymising a real audit event.  Audit persistence
        # is covered separately; this proof is narrowly about preserving the
        # historical call/assignment tenant FKs during reassignment.
        service = InboundCampaignService(pool)
        service._audit = AsyncMock()
        result = await service.approve_reassignment(
            request_id=str(ids["request"]),
            reason="second administrator approved evidence",
            actor_id=str(ids["approver"]),
            actor_role="platform_admin",
            idempotency_key=approve_key,
        )
        assert result["status"] == "approved"
        new_assignment = uuid.UUID(result["approved_assignment_id"])

        async with acquire_with_tenant(pool, None) as conn:
            historical = await conn.fetchrow(
                "SELECT tenant_id,assignment_id FROM calls WHERE id=$1",
                ids["historical_call"],
            )
            source = await conn.fetchrow(
                "SELECT tenant_id,status FROM inbound_did_assignments WHERE id=$1",
                ids["source_assignment"],
            )
            target = await conn.fetchrow(
                "SELECT tenant_id,status FROM inbound_did_assignments WHERE id=$1",
                new_assignment,
            )
            assert historical["tenant_id"] == ids["source_tenant"]
            assert historical["assignment_id"] == ids["source_assignment"]
            assert dict(source) == {
                "tenant_id": ids["source_tenant"],
                "status": "archived",
            }
            assert dict(target) == {
                "tenant_id": ids["target_tenant"],
                "status": "paused",
            }
    finally:
        try:
            async with acquire_with_tenant(pool, None) as conn:
                await conn.execute(
                    "DELETE FROM inbound_reassignment_requests WHERE id=$1",
                    ids["request"],
                )
                await conn.execute("DELETE FROM calls WHERE id=$1", ids["historical_call"])
                await conn.execute(
                    "DELETE FROM inbound_did_assignments WHERE canonical_did=$1", did
                )
                await conn.execute(
                    "DELETE FROM inbound_campaign_configs WHERE id=ANY($1::uuid[])",
                    [ids["source_config"], ids["target_config"]],
                )
                await conn.execute(
                    "DELETE FROM tenant_phone_numbers WHERE id=ANY($1::uuid[])",
                    [ids["source_phone"], ids["target_phone"]],
                )
                await conn.execute(
                    "DELETE FROM tenant_sip_trunks WHERE id=ANY($1::uuid[])",
                    [ids["source_trunk"], ids["target_trunk"]],
                )
                await conn.execute(
                    "DELETE FROM campaigns WHERE id=ANY($1::uuid[])",
                    [ids["source_campaign"], ids["target_campaign"]],
                )
                await conn.execute(
                    "DELETE FROM inbound_operation_idempotency WHERE actor_id=ANY($1::uuid[])",
                    [ids["requester"], ids["approver"]],
                )
                await conn.execute(
                    "DELETE FROM user_profiles WHERE id=ANY($1::uuid[])",
                    [ids["requester"], ids["approver"]],
                )
                # tenant_sip_trunks is covered by the product's immutable
                # tenant_policy_audit_log trigger. Deleting these tenants
                # would cascade into that log and correctly be rejected by
                # its DELETE guard, rolling back every cleanup statement.
                # Leave the two random, now-empty tenant tombstones so the
                # shared-DB test honors the audit-retention invariant. Fully
                # ephemeral CI databases are dropped wholesale by the runner.
        finally:
            await pool.close()


@pytest.mark.asyncio
async def test_concurrent_paused_assignments_cannot_claim_the_same_did():
    """The partial unique index, not a read-before-write check, wins the race."""
    dsn = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, timeout=3)
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable: {exc}")

    tenant_id = uuid.uuid4()
    campaign_ids = [uuid.uuid4(), uuid.uuid4()]
    config_ids = [uuid.uuid4(), uuid.uuid4()]
    assignment_ids = [uuid.uuid4(), uuid.uuid4()]
    phone_id = uuid.uuid4()
    trunk_id = uuid.uuid4()
    did = f"+1556{int(tenant_id.hex[:7], 16) % 10_000_000:07d}"

    try:
        async with acquire_with_tenant(pool, None) as conn:
            index_ready = await conn.fetchval(
                "SELECT to_regclass('public.uq_inbound_live_canonical_did') IS NOT NULL"
            )
            if not index_ready:
                pytest.skip("database does not have the live-DID uniqueness index")
            await conn.execute(
                "INSERT INTO tenants (id,business_name,subscription_status,status) "
                "VALUES ($1,'Inbound uniqueness test','active','active')",
                tenant_id,
            )
            await conn.execute(
                """
                INSERT INTO campaigns (
                    id,tenant_id,name,status,system_prompt,voice_id,direction
                ) VALUES
                    ($1,$3,'Concurrent A','draft','prompt','voice','inbound'),
                    ($2,$3,'Concurrent B','draft','prompt','voice','inbound')
                """,
                campaign_ids[0],
                campaign_ids[1],
                tenant_id,
            )
            await conn.execute(
                """
                INSERT INTO tenant_phone_numbers (
                    id,tenant_id,e164,provider,status,verification_method,verified_at
                ) VALUES ($1,$2,$3,'manual_admin','verified','manual_admin',NOW())
                """,
                phone_id,
                tenant_id,
                did,
            )
            await conn.execute(
                """
                INSERT INTO tenant_sip_trunks (
                    id,tenant_id,trunk_name,sip_domain,direction,is_active,
                    live_registration_status,live_status_checked_at
                ) VALUES (
                    $1,$2,'concurrent-inbound','concurrent.invalid','inbound',TRUE,
                    'loaded',NOW()
                )
                """,
                trunk_id,
                tenant_id,
            )
            await conn.execute(
                """
                INSERT INTO inbound_campaign_configs (
                    id,tenant_id,campaign_id,name,status,config_checksum
                ) VALUES
                    ($1,$3,$4,'Concurrent A','paused',$6),
                    ($2,$3,$5,'Concurrent B','paused',$6)
                """,
                config_ids[0],
                config_ids[1],
                tenant_id,
                campaign_ids[0],
                campaign_ids[1],
                "b" * 64,
            )

        async def claim(index: int):
            async with acquire_with_tenant(pool, None) as conn:
                return await conn.execute(
                    """
                    INSERT INTO inbound_did_assignments (
                        id,tenant_id,phone_number_id,campaign_id,config_id,
                        sip_trunk_id,canonical_did,status
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,'paused')
                    """,
                    assignment_ids[index],
                    tenant_id,
                    phone_id,
                    campaign_ids[index],
                    config_ids[index],
                    trunk_id,
                    did,
                )

        results = await asyncio.gather(claim(0), claim(1), return_exceptions=True)
        assert sum(result == "INSERT 0 1" for result in results) == 1
        failures = [result for result in results if isinstance(result, Exception)]
        assert len(failures) == 1
        assert isinstance(failures[0], asyncpg.UniqueViolationError)
    finally:
        try:
            async with acquire_with_tenant(pool, None) as conn:
                await conn.execute(
                    "DELETE FROM inbound_did_assignments WHERE id=ANY($1::uuid[])",
                    assignment_ids,
                )
                await conn.execute(
                    "DELETE FROM inbound_campaign_configs WHERE id=ANY($1::uuid[])",
                    config_ids,
                )
                await conn.execute("DELETE FROM tenant_phone_numbers WHERE id=$1", phone_id)
                await conn.execute("DELETE FROM tenant_sip_trunks WHERE id=$1", trunk_id)
                await conn.execute("DELETE FROM campaigns WHERE id=ANY($1::uuid[])", campaign_ids)
        finally:
            await pool.close()

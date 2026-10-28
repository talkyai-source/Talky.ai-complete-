"""Expiry effects use real migrated PostgreSQL, restricted roles, and audit locks.

Only owned disposable fixtures are touched; no provider/notification egress.
"""
# ruff: noqa: F811 -- imported pytest fixtures are requested by parameter name
import asyncio
import json
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.domain.services.suspension_service import SuspensionService, SuspensionType
from tests.integration.test_admin_authority_invariant import authority_db  # noqa: F401
from tests.integration.test_formal_account_holds import account_db  # noqa: F401

pytestmark = pytest.mark.integration


async def temporary(db, target="user"):
    if target == "user":
        result = await db.service.suspend_user(
            db.target, SuspensionType.TEMPORARY, "synthetic", "Synthetic expiry fixture",
            suspended_by=db.actor, duration_hours=1, notify_user=False,
        )
    else:
        method = db.service.suspend_tenant if target == "tenant" else db.service.suspend_partner
        result = await method(
            db.tenant_id if target == "tenant" else db.partner,
            SuspensionType.TEMPORARY, "synthetic", "Synthetic expiry fixture",
            suspended_by=db.actor, duration_hours=1,
        )
    await db.owner.execute(
        "UPDATE suspension_events SET suspended_until=clock_timestamp()-interval '1 minute' WHERE suspension_id=$1",
        result.suspension_id,
    )
    return result


async def restored_count(db, target_id):
    return await db.owner.fetchval(
        "SELECT count(*) FROM audit_logs WHERE resource_id=$1 AND action LIKE '%_restored'", target_id,
    )


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
async def test_actual_expiry_pass_restores_exact_event_with_system_audit(account_db, target):
    db = account_db
    saved = await temporary(db, target)
    results = await db.service.process_auto_restores()
    receipt = next(r for r in results if r["suspension_id"] == str(saved.suspension_id))
    assert receipt["restored"] is True
    event = await db.owner.fetchrow("SELECT is_active,restored_by FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
    assert event["is_active"] is False and event["restored_by"] is None
    audit = await db.owner.fetchrow("SELECT actor_id,actor_type FROM audit_logs WHERE event_id=$1", UUID(receipt["audit_event_id"]))
    assert audit["actor_id"] is None and audit["actor_type"] == "system"
    assert await db.service.process_auto_restores() == []
    assert await restored_count(db, saved.target_id) == 1


@pytest.mark.parametrize("change", ["future", "manual", "no_expiry"])
async def test_selected_user_hold_rechecks_current_eligibility_after_real_lock_wait(account_db, monkeypatch, change):
    db = account_db
    saved = await temporary(db)
    attempting = asyncio.Event()
    real_lock = db.audit.lock_transaction
    async def observed_lock(conn):
        attempting.set()
        await real_lock(conn)
    monkeypatch.setattr(db.audit, "lock_transaction", observed_lock)
    pending = None
    try:
        async with db.owner.transaction():
            await real_lock(db.owner)
            pending = asyncio.create_task(db.service.process_auto_restores())
            await asyncio.wait_for(attempting.wait(), 1)
            alteration = {
                "future": "suspended_until=clock_timestamp()+interval '1 hour'",
                "manual": "suspension_type='ADMIN'",
                "no_expiry": "suspended_until=NULL",
            }[change]
            await db.owner.execute(f"UPDATE suspension_events SET {alteration} WHERE suspension_id=$1", saved.suspension_id)
        results = await pending
        assert next(r for r in results if r["suspension_id"] == str(saved.suspension_id))["restored"] is False
        assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
        assert not await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.target)
        assert await restored_count(db, db.target) == 0
    finally:
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


async def test_system_cannot_restore_user_without_exact_event_or_restore_replacement(account_db):
    db = account_db
    saved = await temporary(db)
    with pytest.raises(HTTPException) as denied:
        await db.service.restore_user(db.target, None, "Synthetic missing event")
    assert denied.value.status_code == 400
    await db.service.restore_user(db.target, db.actor, "Reviewed original", expected_suspension_id=saved.suspension_id)
    replacement = await temporary(db)
    with pytest.raises(HTTPException) as changed:
        await db.service.restore_user(db.target, None, "Stale scheduled hold", expected_suspension_id=saved.suspension_id)
    assert changed.value.status_code == 409
    assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", replacement.suspension_id)
    assert await restored_count(db, db.target) == 1


async def test_two_actual_workers_race_one_expired_user_event_once(account_db, monkeypatch):
    db = account_db
    saved = await temporary(db)
    second = SuspensionService(db.pool, audit_logger=db.audit)
    real_lock = db.audit.lock_transaction
    both = asyncio.Event()
    attempts = []
    async def observed_lock(conn):
        attempts.append(conn.get_server_pid())
        if len(attempts) == 2:
            both.set()
        await real_lock(conn)
    monkeypatch.setattr(db.audit, "lock_transaction", observed_lock)
    tasks = []
    try:
        async with db.owner.transaction():
            await real_lock(db.owner)
            tasks = [asyncio.create_task(service.process_auto_restores()) for service in (db.service, second)]
            await asyncio.wait_for(both.wait(), 1)
        results = [r for batch in await asyncio.gather(*tasks) for r in batch if r["suspension_id"] == str(saved.suspension_id)]
        assert sorted(r["restored"] for r in results) == [False, True]
        assert await restored_count(db, db.target) == 1
        assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.target)
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_real_commit_followed_by_timeout_stays_unknown_without_duplicate_audit(account_db, monkeypatch):
    db = account_db
    saved = await temporary(db)
    committed = asyncio.Event()
    async def lost_delivery(*args, **kwargs):
        committed.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(db.service, "_propagate_committed_user_change", lost_delivery)
    result = await db.service.process_auto_restores(item_timeout_seconds=.2)
    assert committed.is_set()
    assert result[0]["outcome"] == "unknown" and result[0]["restored"] is None
    assert not await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
    assert await restored_count(db, db.target) == 1
    restarted = SuspensionService(db.pool, audit_logger=db.audit)
    assert await restarted.process_auto_restores() == []
    assert await restored_count(db, db.target) == 1


async def test_budget_cancels_real_lock_wait_without_restoration_then_recovers(account_db):
    db = account_db
    saved = await temporary(db)
    async with db.owner.transaction():
        await db.audit.lock_transaction(db.owner)
        result = await db.service.process_auto_restores(time_budget_seconds=.1)
        assert result[0]["outcome"] == "unknown"
        assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
        assert await restored_count(db, db.target) == 0
    assert (await db.service.process_auto_restores())[0]["restored"] is True
    assert await restored_count(db, db.target) == 1


async def test_read_only_historical_failure_cannot_starve_valid_expiry(account_db):
    db = account_db
    saved = await temporary(db)
    historical = UUID(int=1)
    await db.owner.execute(
        """INSERT INTO suspension_events(suspension_id,target_type,target_id,suspension_type,
           reason_category,reason_description,is_active,suspended_until)
           VALUES($1,'tenant',$2,'TEMPORARY','synthetic','Historical no ownership',TRUE,clock_timestamp()-interval '1 minute')""",
        historical, db.tenant_id,
    )
    first = await db.service.process_auto_restores(batch_limit=1)
    second = await db.service.process_auto_restores(batch_limit=1)
    assert first[0]["suspension_id"] == str(historical) and first[0]["restored"] is False
    assert second[0]["suspension_id"] == str(saved.suspension_id) and second[0]["restored"] is True
    assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", historical)


async def test_audit_failure_rolls_back_then_next_pass_recovers(account_db):
    db = account_db
    saved = await temporary(db)
    await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{db.role}"')
    result = await db.service.process_auto_restores()
    assert result[0]["outcome"] == "unknown"
    assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
    assert await restored_count(db, db.target) == 0
    await db.owner.execute(f'GRANT INSERT ON audit_logs TO "{db.role}"')
    assert (await db.service.process_auto_restores())[0]["restored"] is True
    assert await restored_count(db, db.target) == 1


async def test_expired_non_immediate_tenant_hold_preserves_independent_partner_hold(account_db):
    db = account_db
    tenant = await db.service.suspend_tenant(
        db.tenant_id, SuspensionType.BILLING, "synthetic", "Non-immediate expiry fixture",
        suspended_by=db.actor, duration_hours=1, immediate=False,
    )
    partner = await db.service.suspend_partner(
        db.partner, SuspensionType.ADMIN, "synthetic", "Independent partner hold", suspended_by=db.actor,
    )
    await db.owner.execute(
        "UPDATE suspension_events SET suspended_until=clock_timestamp()-interval '1 minute' WHERE suspension_id=$1",
        tenant.suspension_id,
    )
    result = await db.service.process_auto_restores()
    assert next(r for r in result if r["suspension_id"] == str(tenant.suspension_id))["restored"] is True
    saved = await db.owner.fetchrow("SELECT * FROM suspension_events WHERE suspension_id=$1", tenant.suspension_id)
    assert not saved["is_active"]
    assert json.loads(saved["evidence"])["_formal_account_hold"]["immediate"] is False
    assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", partner.suspension_id)
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1", db.tenant_id) == "suspended"

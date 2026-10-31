"""Formal call authority through migrated restricted-role PostgreSQL and real writers."""

from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.domain.services.call_guard import CallGuard
from app.domain.services.suspension_service import SuspensionType
from app.domain.services.telephony.call_authority import (
    CallAuthorityUnavailable,
    evaluate_call_authority,
    read_call_authority,
    read_call_authority_rows,
    read_formal_account_authority,
)
from tests.integration.test_admin_authority_invariant import authority_db  # noqa: F401
from tests.integration.test_formal_account_holds import account_db, hold, restore  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def child_db(account_db):  # noqa: F811
    db = account_db
    db.child = uuid4()
    await db.owner.execute(
        "INSERT INTO tenants(id,business_name,status,subscription_status,white_label_partner_id) "
        "VALUES($1,'Synthetic TP4 child','active','active',$2)",
        db.child,
        db.partner,
    )
    try:
        yield db
    finally:
        await db.owner.execute("DELETE FROM suspension_events WHERE target_id=$1", db.child)
        await db.owner.execute("DELETE FROM audit_logs WHERE tenant_id=$1", db.child)
        await db.owner.execute("DELETE FROM tenants WHERE id=$1", db.child)


async def verdict(db, **kwargs):
    async with acquire_with_tenant(db.pool, None) as conn:
        return await read_call_authority(conn, db.child, **kwargs)


async def child_hold(db, **kwargs):
    return await db.service.suspend_tenant(
        db.child,
        SuspensionType.ADMIN,
        "synthetic",
        "TP4 synthetic formal hold",
        suspended_by=db.actor,
        **kwargs,
    )


async def test_real_parent_rls_visibility_and_service_read_do_not_leak(child_db):
    db = child_db
    async with acquire_with_tenant(db.pool, str(db.child)) as conn:
        assert (
            await conn.fetchval("SELECT id FROM white_label_partners WHERE id=$1", db.partner)
            is None
        )
        with pytest.raises(CallAuthorityUnavailable):
            await read_call_authority(conn, db.child)
    assert (await CallGuard(db.pool)._check_tenant_active(str(db.child))).passed is True
    async with db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT current_setting('app.bypass_rls',TRUE)") in (
            None,
            "",
            "off",
        )
        assert await conn.fetchval("SELECT current_setting('app.current_tenant_id',TRUE)") in (
            None,
            "",
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM white_label_partners WHERE id=$1", db.partner)
            == 0
        )


async def test_active_child_suspended_formal_parent_denied_without_legacy_parent_id(child_db):
    db = child_db
    await db.owner.execute(
        "UPDATE white_label_partners SET status='suspended' WHERE id=$1", db.partner
    )
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1", db.child) == "active"
    assert (await verdict(db)).reason == "formal_partner_inactive"
    assert (await verdict(db, purpose="existing_call")).reason == "formal_partner_inactive"


async def test_new_child_linked_after_parent_hold_cannot_evade_canonical_event(child_db):
    db = child_db
    await db.owner.execute("UPDATE tenants SET white_label_partner_id=NULL WHERE id=$1", db.child)
    await hold(db, "partner")
    await db.owner.execute(
        "UPDATE tenants SET white_label_partner_id=$2 WHERE id=$1", db.child, db.partner
    )
    # Reproduce an independently reset status: the active event still denies.
    await db.owner.execute(
        "UPDATE white_label_partners SET status='active' WHERE id=$1", db.partner
    )
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1", db.child) == "active"
    assert (await verdict(db)).reason == "formal_account_hold"


async def test_audited_partner_snapshot_remains_applicable_after_reparenting(child_db):
    db = child_db
    await hold(db, "partner")
    await db.owner.execute(
        "UPDATE tenants SET white_label_partner_id=NULL,status='active' WHERE id=$1", db.child
    )
    for purpose in ("admission", "principal", "existing_call"):
        assert (await verdict(db, purpose=purpose)).reason == "formal_account_hold"


async def test_untrusted_legacy_snapshot_cannot_claim_an_unrelated_tenant(child_db):
    db = child_db
    saved = await hold(db, "partner")
    await db.owner.execute(
        "UPDATE suspension_events SET audit_log_id=NULL WHERE suspension_id=$1", saved.suspension_id
    )
    await db.owner.execute(
        "UPDATE tenants SET white_label_partner_id=NULL,status='active' WHERE id=$1", db.child
    )
    assert (await verdict(db)).allowed is True
    # Canonical target scope remains authoritative even without trusted policy.
    await db.owner.execute(
        "UPDATE tenants SET white_label_partner_id=$2 WHERE id=$1", db.child, db.partner
    )
    assert (await verdict(db)).allowed is False


async def test_real_non_immediate_policy_exempts_only_proven_existing_call(child_db):
    db = child_db
    saved = await child_hold(db, immediate=False)
    cutoff = await db.owner.fetchval(
        "SELECT suspended_at FROM suspension_events WHERE suspension_id=$1", saved.suspension_id
    )
    assert (await verdict(db)).allowed is False
    async with acquire_with_tenant(db.pool, None) as conn:
        assert (await read_formal_account_authority(conn, db.child)).allowed is False
    assert (
        await verdict(
            db, purpose="existing_call", existing_call_started_at=cutoff - timedelta(seconds=1)
        )
    ).allowed is True
    assert (await verdict(db, purpose="existing_call")).allowed is False
    assert (
        await verdict(db, purpose="existing_call", existing_call_started_at=cutoff)
    ).allowed is False
    await db.owner.execute(
        "UPDATE tenants SET subscription_status='suspended' WHERE id=$1", db.child
    )
    assert (
        await verdict(
            db, purpose="existing_call", existing_call_started_at=cutoff - timedelta(seconds=1)
        )
    ).allowed is True


@pytest.mark.parametrize("proof_gap", ["missing_audit", "missing_marker"])
async def test_legacy_or_unproven_false_policy_cannot_exempt_existing_call(child_db, proof_gap):
    db = child_db
    saved = await child_hold(db, immediate=False)
    cutoff = await db.owner.fetchval(
        "SELECT suspended_at FROM suspension_events WHERE suspension_id=$1", saved.suspension_id
    )
    if proof_gap == "missing_audit":
        await db.owner.execute(
            "UPDATE suspension_events SET audit_log_id=NULL WHERE suspension_id=$1",
            saved.suspension_id,
        )
    else:
        await db.owner.execute(
            "UPDATE audit_logs SET metadata=metadata-'formal_account_hold_version' WHERE event_id=$1",
            saved.audit_event_id,
        )
    assert (
        await verdict(
            db, purpose="existing_call", existing_call_started_at=cutoff - timedelta(seconds=1)
        )
    ).allowed is False


async def test_parent_read_permission_failure_never_allows_or_leaks_bypass(child_db):
    db = child_db
    await db.owner.execute(f'REVOKE SELECT ON white_label_partners FROM "{db.role}"')
    result = await CallGuard(db.pool)._check_tenant_active(str(db.child))
    assert result.passed is False and result.reason.startswith("check_error_blocked")
    async with db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT current_setting('app.bypass_rls',TRUE)") in (
            None,
            "",
            "off",
        )


async def test_real_batch_binds_exact_requested_tenants_and_missing_row_denies(child_db):
    db = child_db
    missing = uuid4()
    async with acquire_with_tenant(db.pool, None) as conn:
        rows = await read_call_authority_rows(conn, [db.child, db.child, missing])
    assert set(rows) == {str(db.child)}
    assert evaluate_call_authority(rows[str(db.child)]).allowed is True
    assert evaluate_call_authority(rows.get(str(missing))).allowed is False


async def test_remaining_non_immediate_hold_retains_older_exact_marker(child_db):
    db = child_db
    parent_hold = await hold(db, "partner")
    saved = await child_hold(db, immediate=False)
    cutoff = await db.owner.fetchval(
        "SELECT suspended_at FROM suspension_events WHERE suspension_id=$1", saved.suspension_id
    )
    await restore(db, "partner", expected_suspension_id=parent_hold.suspension_id)
    marker = await db.owner.fetchval("SELECT suspended_at FROM tenants WHERE id=$1", db.child)
    assert marker < cutoff
    assert (
        await verdict(
            db, purpose="existing_call", existing_call_started_at=marker - timedelta(seconds=1)
        )
    ).allowed is True

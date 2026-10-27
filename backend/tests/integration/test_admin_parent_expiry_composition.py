"""Normal-auth parent/expiry composition on exact owned PostgreSQL records.

The expiry deadline is fixture-only; the real service pass is invoked directly.
No deployed scheduler cadence, browser retirement or delivered-byte recall claim.
"""
import asyncio
import re
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import (
    API_ORIGIN,
    ORIGIN,
    journey_db,  # noqa: F401
)
from tests.integration.test_admin_users_tenants_crud import call, signed_in

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@pytest_asyncio.fixture
async def composition_db(journey_db):  # noqa: F811
    db = journey_db
    db.client_ip = "127.0.1." + str(1 + uuid4().int % 250)
    db.composition_partner = uuid4()
    db.composition_cleanup = {"complete": False}
    db.report["composition_cleanup"] = db.composition_cleanup
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
        flags = dict(await conn.fetchrow(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ))
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    assert flags == {"rolsuper": False, "rolbypassrls": False}
    try:
        await db.owner.execute(f'GRANT SELECT,UPDATE ON white_label_partners TO "{role}"')
        # The partner's owning tenant is an unrelated control, not implicitly
        # its child. Admin A remains outside the explicit B child relationship.
        await db.owner.execute(
            """INSERT INTO white_label_partners(id,tenant_id,company_name,status)
               VALUES($1,$2,'Synthetic parent expiry composition','active')""",
            db.composition_partner, db.tenants[2],
        )
        yield db
    finally:
        # System restoration audits use this owned partner's owning tenant;
        # journey_db subsequently removes them, auth rows and the exact role.
        async with db.owner.transaction():
            await db.owner.execute("SET LOCAL app.bypass_rls='on'")
            await db.owner.execute(
                "DELETE FROM suspension_events WHERE target_type='partner' AND target_id=$1",
                db.composition_partner,
            )
            await db.owner.execute(
                "UPDATE tenants SET white_label_partner_id=NULL "
                "WHERE id=ANY($1::uuid[]) AND white_label_partner_id=$2",
                db.tenants, db.composition_partner,
            )
            await db.owner.execute(
                "DELETE FROM white_label_partners WHERE id=$1 AND tenant_id=$2",
                db.composition_partner, db.tenants[2],
            )
            assert not await db.owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM white_label_partners WHERE id=$1)", db.composition_partner,
            )
            assert not await db.owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM suspension_events WHERE target_id=$1)", db.composition_partner,
            )
        db.composition_cleanup["complete"] = True


async def status(db, admin, target_type, target_id):
    response = await call(db, admin, "GET", f"/admin/suspensions/{target_type}s/{target_id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["target_type"] == target_type and data["target_id"] == str(target_id)
    assert data["history_complete"] is True
    return data


async def hold_partner(db, admin, *, temporary=False):
    body = {
        "suspension_type": "TEMPORARY" if temporary else "ADMIN",
        "reason_category": "synthetic_acceptance",
        "reason_description": "Synthetic owned parent expiry composition",
        "notify_user": False,
        "evidence": {"operator_request_id": str(uuid4())},
    }
    if temporary:
        body["duration_hours"] = 1  # Exercise the normal production request contract.
    response = await call(db, admin, "POST",
                          f"/admin/suspensions/partners/{db.composition_partner}/suspend", json=body)
    assert response.status_code == 200
    saved = response.json()
    assert saved["target_id"] == str(db.composition_partner) and saved["target_type"] == "partner"
    assert saved["audit_event_id"] and saved["changed"] is True
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE event_id=$1",
                                   UUID(saved["audit_event_id"])) == 1
    return saved


async def link_child(db):
    assert await db.owner.execute(
        "UPDATE tenants SET white_label_partner_id=$2 WHERE id=$1 AND white_label_partner_id IS NULL",
        db.tenants[1], db.composition_partner,
    ) == "UPDATE 1"


def recorded_writes(db):
    return [step for step in db.report["steps"]
            if step["path"].startswith("/admin/") and step["method"] != "GET"]


async def bearer_probe(db, authorization):
    transport = httpx.ASGITransport(app=db.app, client=(db.client_ip, 59106), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=API_ORIGIN, headers={
        "Origin": ORIGIN, "User-Agent": "Synthetic-Admin-CRUD", "Authorization": authorization,
    }) as client:
        assert len(client.cookies) == 0
        return (await call(db, client, "GET", "/auth/me")).status_code


async def test_relinked_parent_changes_current_authority_after_retained_status_read(composition_db):
    db = composition_db
    async with signed_in(db) as admin, signed_in(db, 1) as member, signed_in(db, 2) as unrelated:
        retained = await status(db, admin, "tenant", db.tenants[1])
        assert retained["effective_status"] == "active" and not retained["is_suspended"]
        saved = await hold_partner(db, admin)
        # B was not a child at hold time: its actual rows/session remain active.
        profile_before = dict(await db.owner.fetchrow("SELECT * FROM user_profiles WHERE id=$1", db.users[1]))
        membership_before = dict(await db.owner.fetchrow("SELECT * FROM tenant_users WHERE user_id=$1", db.users[1]))
        assert (await call(db, member, "GET", "/auth/me")).status_code == 200
        await link_child(db)
        denied = await call(db, member, "GET", "/auth/me")
        assert denied.status_code == 401
        observed = await status(db, admin, "tenant", db.tenants[1])
        assert observed["effective_status"] == "suspended" and observed["is_suspended"]
        assert observed["active_suspension"] is None and observed["suspension_history"] == []
        assert observed["remaining_hold_ids"] == [saved["suspension_id"]]
        assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1", db.tenants[1]) == "active"
        assert dict(await db.owner.fetchrow("SELECT * FROM user_profiles WHERE id=$1", db.users[1])) == profile_before
        assert dict(await db.owner.fetchrow("SELECT * FROM tenant_users WHERE user_id=$1", db.users[1])) == membership_before
        assert (await call(db, unrelated, "GET", "/auth/me")).status_code == 200
        assert (await call(db, admin, "GET", "/auth/me")).status_code == 200
        current_parent = await status(db, admin, "partner", db.composition_partner)
        assert current_parent["active_suspension"]["suspension_id"] == saved["suspension_id"]
    assert len(recorded_writes(db)) == 1
    RUN_EVIDENCE.append({
        "case": "current_parent_relink_after_retained_read", "normal_auth": True,
        "retained_observation": retained["effective_status"],
        "fresh_observation": observed["effective_status"], "existing_member_request_status": 401,
        "unrelated_and_platform_admin_request_statuses": [200, 200],
        "direct_child_history_fabricated": False, "profile_and_membership_unchanged": True,
        "business_http_writes": 1, "ui_retained_callback_execution_claimed": False,
    })


async def only_owned_expiry_candidate(db, event_id, *, due):
    # Do not let a direct global expiry pass discover and mutate another event.
    rows = await db.owner.fetch(
        """SELECT suspension_id FROM suspension_events WHERE is_active=TRUE
           AND suspended_until IS NOT NULL AND suspension_type IN ('TEMPORARY','BILLING')"""
    )
    assert {row["suspension_id"] for row in rows} == ({event_id} if event_id else set())
    if due:
        assert await db.owner.fetchval(
            "SELECT suspended_until<=clock_timestamp() FROM suspension_events WHERE suspension_id=$1", event_id,
        ) is True


async def test_elapsed_owned_partner_hold_restores_exact_event_but_not_preserved_bearer(composition_db):
    from app.api.v1 import dependencies

    db = composition_db
    await link_child(db)
    async with signed_in(db) as admin, signed_in(db, 1) as member:
        # Keep this only in memory. Do not submit it between hold and restore,
        # and do not record it in request reports or artifacts.
        old_authorization = member.headers["Authorization"]
        assert await bearer_probe(db, old_authorization) == 200
        old_session = await db.owner.fetchval(
            "SELECT id FROM security_sessions WHERE user_id=$1 AND revoked_at IS NULL", db.users[1],
        )
        assert old_session is not None
        saved = await hold_partner(db, admin, temporary=True)
        assert saved["revoked_sessions"] == 1
        event_id = UUID(saved["suspension_id"])
        retained = await status(db, admin, "partner", db.composition_partner)
        assert retained["active_suspension"]["suspension_id"] == str(event_id)
        original_deadline = await db.owner.fetchval(
            "SELECT suspended_until FROM suspension_events WHERE suspension_id=$1", event_id,
        )
        # Shorten ONLY this owned fixture deadline. The application duration
        # contract, service logic, wall clock and deployed scheduler stay real.
        deadline_row = await db.owner.fetchrow(
            """WITH tick AS MATERIALIZED (SELECT clock_timestamp() AS setup_time)
               UPDATE suspension_events SET suspended_until=tick.setup_time+interval '3 seconds'
               FROM tick WHERE suspension_id=$1 AND target_id=$2 AND is_active=TRUE
               RETURNING tick.setup_time,suspended_until""",
            event_id, db.composition_partner,
        )
        assert deadline_row is not None
        deadline = deadline_row["suspended_until"]
        assert original_deadline > deadline > deadline_row["setup_time"]
        await only_owned_expiry_candidate(db, event_id, due=False)
        async with asyncio.timeout(30):
            while await db.owner.fetchval("SELECT clock_timestamp()") < deadline:
                await asyncio.sleep(0.05)
        observed_at = await db.owner.fetchval("SELECT clock_timestamp()")
        assert observed_at >= deadline
        await only_owned_expiry_candidate(db, event_id, due=True)
        # Use the same real service/audit dependency instantiated by normal HTTP.
        service = await dependencies.get_suspension_service(db.pool)
        results = await service.process_auto_restores()
        assert len(results) == 1
        restored = results[0]
        assert restored["suspension_id"] == str(event_id) and restored["restored"] is True
        assert restored["target_id"] == str(db.composition_partner) and restored["audit_event_id"]
        audit = dict(await db.owner.fetchrow(
            "SELECT actor_id,actor_type,resource_id,action FROM audit_logs WHERE event_id=$1",
            UUID(restored["audit_event_id"]),
        ))
        assert audit == {"actor_id": None, "actor_type": "system",
                         "resource_id": db.composition_partner, "action": "partner_restored"}
        current = await status(db, admin, "partner", db.composition_partner)
        assert current["effective_status"] == "active" and current["active_suspension"] is None
        assert len(current["suspension_history"]) == 1
        history = current["suspension_history"][0]
        assert history["suspension_id"] == str(event_id) and history["is_active"] is False
        assert history["restored_by"] is None and history["restored_at"] is not None
        await only_owned_expiry_candidate(db, None, due=False)
        assert await service.process_auto_restores() == []
        assert await db.owner.fetchval(
            "SELECT count(*) FROM audit_logs WHERE resource_id=$1 AND action='partner_restored'",
            db.composition_partner,
        ) == 1
        assert await db.owner.fetchval(
            "SELECT count(*) FROM audit_logs WHERE event_id=$1", UUID(saved["audit_event_id"]),
        ) == 1
        async with signed_in(db, 1) as fresh:
            assert (await call(db, fresh, "GET", "/auth/me")).status_code == 200
        # Independent normal-auth observation; it does not rewrite the earlier
        # partner native run's historically unsent final old-access attempt.
        assert await bearer_probe(db, old_authorization) == 401
        assert await db.owner.fetchval(
            "SELECT revoked_at IS NOT NULL FROM security_sessions WHERE id=$1", old_session,
        ) is True
    assert len(recorded_writes(db)) == 1
    RUN_EVIDENCE.append({
        "case": "elapsed_fixture_deadline_exact_event_and_preserved_bearer", "normal_auth": True,
        "expiry_source": "real direct service pass after actual future fixture-only DB deadline",
        "fixture_setup_db_time": deadline_row["setup_time"].isoformat(),
        "fixture_deadline": deadline.isoformat(), "observed_db_time": observed_at.isoformat(),
        "original_event_restored": True, "system_restoration_audits": 1,
        "second_expiry_pass_results": 0, "fresh_login_request_status": 200,
        "preserved_bearer_first_posthold_submission_status": 401,
        "business_http_writes": 1, "deployed_worker_scheduling_claimed": False,
    })

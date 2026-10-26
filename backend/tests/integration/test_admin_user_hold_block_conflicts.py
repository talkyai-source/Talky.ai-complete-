"""Normal-auth prospective ordinary-write conflicts with an active USER hold.

The two regression cases finish the actual restore/credential checks before
asserting the proposed 409, so an unchanged implementation records the complete
admitted ordinary decision -> reactivation failure. No auth or handler doubles.
The FK fallback is made deterministic by one exact owned tenant reference.
"""
import json
from uuid import UUID, uuid4

import httpx
import pytest

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call, cancel_prepared, prepare_write, signed_in,
)
from tests.integration.test_admin_users_tenants_crud import crud_db  # noqa: F401

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


def safe(value):
    return json.loads(json.dumps(value, default=str))


async def target_state(db):
    profile = await db.owner.fetchrow("""SELECT id,tenant_id,name,role,is_active,
        admin_revision,updated_at FROM user_profiles WHERE id=$1""", db.users[1])
    members = await db.owner.fetch("""SELECT id,user_id,tenant_id,role_id,status,
        is_primary,admin_revision,updated_at FROM tenant_users
        WHERE user_id=$1 ORDER BY id""", db.users[1])
    return safe({"profile": dict(profile) if profile else None,
                 "memberships": [dict(row) for row in members]})


async def ordinary_audits(db):
    rows = await db.owner.fetch("""SELECT event_id,action,actor_id,resource_id,metadata
        FROM audit_logs WHERE actor_id=$1 AND resource_id=$2
        AND action=ANY($3::text[]) ORDER BY event_time,event_id""",
        db.users[0], db.users[1], ["user_updated", "user_deleted", "user_deactivated"])
    return safe([dict(row) for row in rows])


async def reviewed_body(db, admin, method, **changes):
    path = f"/admin/users/{db.users[1]}"
    read = await call(db, admin, "GET", path)
    assert read.status_code == 200
    return await prepare_write(db, admin, method, path, {
        "expected_review_token": read.json()["review_token"], **changes})


async def hold(db, admin):
    result = await call(db, admin, "POST", f"/admin/suspensions/users/{db.users[1]}/suspend", json={
        "suspension_type": "ADMIN", "reason_category": "synthetic_acceptance",
        "reason_description": "Synthetic mixed ordinary/formal decision regression",
        "notify_user": False, "evidence": {"operator_request_id": str(uuid4())}})
    assert result.status_code == 200
    saved = result.json()
    assert saved["target_id"] == str(db.users[1]) and saved["audit_event_id"]
    return saved


async def restore(db, admin, held):
    result = await call(db, admin, "POST", f"/admin/suspensions/users/{db.users[1]}/restore", json={
        "expected_suspension_id": held["suspension_id"],
        "reason": "Synthetic exact-event restore after ordinary-write observation"})
    assert result.status_code == 200
    assert result.json()["suspension_id"] == held["suspension_id"]
    assert result.json()["restored"] is True and result.json()["audit_event_id"]
    return result.json()


async def pin_fallback(db):
    # Supported FK behavior, exact fixture setup only. It does not change the
    # target profile/review token or fabricate the DELETE's SQL outcome.
    changed = await db.owner.execute(
        "UPDATE tenants SET suspended_by=$1 WHERE id=$2", db.users[1], db.tenants[1])
    assert changed == "UPDATE 1"


@pytest.mark.parametrize("method", ["PATCH", "DELETE"], ids=["fresh-block", "linked-delete"])
async def test_new_ordinary_inactivation_is_rejected_while_formally_held(crud_db, method):  # noqa: F811
    db = crud_db
    observation = {"case": "prospective_hold_conflict", "method": method}
    RUN_EVIDENCE.append(observation)
    if method == "DELETE":
        await pin_fallback(db)
    async with signed_in(db) as admin, signed_in(db, 1) as member:
        old_cookies = httpx.Cookies(member.cookies)
        held = await hold(db, admin)
        body = await reviewed_body(db, admin, method, **({"is_active": False} if method == "PATCH" else {}))
        before = await target_state(db)
        before_audits = await ordinary_audits(db)
        changed = await call(db, admin, method, f"/admin/users/{db.users[1]}", json=body)
        after = await target_state(db)
        after_audits = await ordinary_audits(db)
        operation = await call(db, admin, "GET", f"/admin/users/operations/{body['operation_id']}")
        assert operation.status_code == 200
        observation.update(hold_id=held["suspension_id"], operation_id=body["operation_id"],
            ordinary_status=changed.status_code, ordinary_outcome=changed.json().get("outcome"),
            ordinary_audit_event_id=changed.json().get("audit_event_id"),
            before=before, after_ordinary=after, ordinary_audits_before=before_audits,
            ordinary_audits_after=after_audits, operation_state=operation.json()["state"])
        assert changed.status_code in {200, 409}, "Unexpected failure cannot demonstrate the mixed-state defect"
        if changed.status_code == 200:
            assert operation.json()["state"] == "committed"
            assert len(after_audits) == len(before_audits) + 1
            event = next(row for row in after_audits if row["event_id"] == changed.json()["audit_event_id"])
            assert event["action"] == ("user_updated" if method == "PATCH" else "user_deactivated")
            assert after["profile"]["is_active"] is False
            if method == "DELETE":
                assert changed.json()["outcome"] == "deactivated", "Must exercise real FK fallback, not hard deletion"
        if changed.status_code == 409:
            assert changed.json()["error"]["code"] == "conflict"
            assert "formal" in changed.json()["error"]["message"].lower()
            assert operation.json()["state"] == "prepared"
            assert operation.json()["terminal_audit_event_id"] is None
            assert after == before and after_audits == before_audits
            await cancel_prepared(db, admin, body)
        restored = await restore(db, admin, held)
        final = await target_state(db)
        member.cookies = httpx.Cookies(old_cookies)
        old_me = await call(db, member, "GET", "/auth/me")
        member.cookies = httpx.Cookies(old_cookies)
        old_refresh = await call(db, member, "POST", "/auth/refresh")
        async with signed_in(db, 1) as fresh_member:
            fresh = await call(db, fresh_member, "GET", "/auth/me")
        observation.update(restoration_audit_event_id=restored["audit_event_id"],
            after_restore=final, old_session_status=old_me.status_code,
            old_refresh_status=old_refresh.status_code, fresh_login_status=fresh.status_code,
            ordinary_decision_reactivated=changed.status_code == 200 and final["profile"]["is_active"] is True)
        assert old_me.status_code == old_refresh.status_code == 401
        assert fresh.status_code == 200 and final["profile"]["is_active"] is True
        assert changed.status_code == 409, "A new ordinary inactivation must not be admitted under a direct formal hold"


@pytest.mark.parametrize("edit", ["name", "role"])
async def test_held_user_non_status_edit_remains_available(crud_db, edit):  # noqa: F811
    db = crud_db
    async with signed_in(db) as admin:
        held = await hold(db, admin)
        changes = {"name": "Synthetic held account rename"} if edit == "name" else {
            "role": "readonly", "expected_role": "tenant_admin"}
        body = await reviewed_body(db, admin, "PATCH", **changes)
        result = await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=body)
        before_restore = await target_state(db)
        assert result.status_code == 200 and result.json()["audit_event_id"]
        assert before_restore["profile"]["is_active"] is False
        assert await db.owner.fetchval(
            "SELECT is_active FROM suspension_events WHERE suspension_id=$1", UUID(held["suspension_id"]))
        await restore(db, admin, held)
        after = await target_state(db)
        RUN_EVIDENCE.append({"case": "non_status_edit", "edit": edit,
                             "status": result.status_code, "after_restore": after})
        assert after["profile"][edit] == changes[edit]
        assert after["profile"]["is_active"] is True


@pytest.mark.parametrize("method", ["PATCH", "DELETE"], ids=["block-receipt", "deactivation-receipt"])
async def test_historical_ordinary_receipt_replay_during_later_hold_has_no_effect(crud_db, method):  # noqa: F811
    db = crud_db
    if method == "DELETE":
        await pin_fallback(db)
    async with signed_in(db) as admin:
        body = await reviewed_body(db, admin, method, **({"is_active": False} if method == "PATCH" else {}))
        original = await call(db, admin, method, f"/admin/users/{db.users[1]}", json=body)
        assert original.status_code == 200
        if method == "DELETE":
            assert original.json()["outcome"] == "deactivated"
        unblocking = await reviewed_body(db, admin, "PATCH", is_active=True)
        assert (await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=unblocking)).status_code == 200
        held = await hold(db, admin)
        before, audits_before = await target_state(db), await ordinary_audits(db)
        replay = await call(db, admin, method, f"/admin/users/{db.users[1]}", json=body)
        after, audits_after = await target_state(db), await ordinary_audits(db)
        RUN_EVIDENCE.append({"case": "historical_replay", "method": method,
            "status": replay.status_code, "original_audit_event_id": original.json()["audit_event_id"],
            "same_receipt": replay.json() == original.json(), "no_new_write": before == after,
            "no_new_business_audit": audits_before == audits_after})
        assert replay.status_code == 200 and replay.json() == original.json()
        assert before == after and audits_before == audits_after
        assert await db.owner.fetchval(
            "SELECT is_active FROM suspension_events WHERE suspension_id=$1", UUID(held["suspension_id"]))

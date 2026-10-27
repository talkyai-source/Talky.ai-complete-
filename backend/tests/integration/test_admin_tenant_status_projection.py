"""Normal MFA/current-principal HTTP projection proof on owned disposable PG."""
import json
from uuid import UUID, uuid4

import pytest

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import call, crud_db, signed_in  # noqa: F401

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


async def test_null_subscription_list_detail_and_quota_receipts_agree_without_data_rewrite(crud_db):  # noqa: F811
    db = crud_db
    target = db.tenants[1]
    await db.owner.execute("UPDATE tenants SET subscription_status=NULL,minutes_allocated=30 WHERE id=$1", target)
    before = await db.owner.fetchrow("SELECT * FROM tenants WHERE id=$1", target)
    plan = await db.owner.fetchval("SELECT id FROM plans ORDER BY id LIMIT 1")
    path = f"/admin/tenants/{target}"
    async with signed_in(db) as admin:
        listed = await call(db, admin, "GET", "/admin/tenants", params={"search": str(target)})
        detail = await call(db, admin, "GET", path)
        existing = await call(db, admin, "POST", "/admin/tenants", json={"business_name": before["business_name"], "plan_id": plan})
        saved = await call(db, admin, "PATCH", path + "/quota", json={"expected_updated_at": before["updated_at"].isoformat(), "minutes_allocated": 41})
        current = await db.owner.fetchrow("SELECT * FROM tenants WHERE id=$1", target)
        same = await call(db, admin, "PATCH", path + "/quota", json={"expected_updated_at": current["updated_at"].isoformat(), "minutes_allocated": 41})
        conflict = await call(db, admin, "PATCH", path + "/quota", json={"expected_updated_at": before["updated_at"].isoformat(), "minutes_allocated": 42})
    assert all(r.status_code == 200 for r in (listed, detail, existing, saved, same))
    assert conflict.status_code == 409
    observed = {"list": listed.json()[0]["status"], "detail": detail.json()["status"],
                "existing_create": existing.json()["status"], "quota_receipt": saved.json()["status"],
                "no_op_receipt": same.json()["status"], "conflict": conflict.json()["error"]["details"]["status"]}
    final = await db.owner.fetchrow("SELECT * FROM tenants WHERE id=$1", target)
    event = await db.owner.fetchrow("SELECT action,before_state,after_state FROM audit_logs WHERE event_id=$1", UUID(saved.json()["audit_event_id"]))
    RUN_EVIDENCE.append({"case": "null_projection", "observed": observed, "stored_subscription_remains_null": final["subscription_status"] is None,
                         "saved_quota": final["minutes_allocated"], "audit_action": event["action"]})
    assert observed == dict.fromkeys(observed, "active")
    assert final["subscription_status"] is None and final["minutes_allocated"] == 41
    assert final["status"] == before["status"]
    assert existing.json()["created"] is False and existing.json()["audit_event_id"] is None
    assert same.json()["changed"] is False and same.json()["audit_event_id"] is None
    assert event["action"] == "tenant_quota_updated"
    for key in ("before_state", "after_state"):
        state = event[key] if isinstance(event[key], dict) else json.loads(event[key])
        assert state["subscription_status"] is None


async def test_active_filter_includes_null_with_consistent_total_and_pagination(crud_db):  # noqa: F811
    db = crud_db
    prefix = "Synthetic status filter " + uuid4().hex
    for index, status in enumerate(("active", None, "cancelled")):
        await db.owner.execute("UPDATE tenants SET business_name=$2,subscription_status=$3 WHERE id=$1", db.tenants[index], f"{prefix} {index}", status)
    async with signed_in(db) as admin:
        pages = [await call(db, admin, "GET", "/admin/tenants", params={"search": prefix, "status": "active", "limit": 1, "offset": offset}) for offset in range(3)]
        archived = await call(db, admin, "GET", "/admin/tenants", params={"search": prefix, "status": "cancelled"})
    observed = {"totals": [r.headers.get("x-total-count") for r in pages], "page_ids": [[row["id"] for row in r.json()] for r in pages]}
    RUN_EVIDENCE.append({"case": "null_active_filter", **observed})
    assert all(r.status_code == 200 for r in [*pages, archived])
    assert observed["totals"] == ["2", "2", "2"]
    assert observed["page_ids"] == [[str(db.tenants[0])], [str(db.tenants[1])], []]
    assert all(row["status"] == "active" for response in pages for row in response.json())
    assert [row["id"] for row in archived.json()] == [str(db.tenants[2])]
    assert archived.json()[0]["status"] == "cancelled"
    assert await db.owner.fetchval("SELECT subscription_status FROM tenants WHERE id=$1", db.tenants[1]) is None


async def test_explicit_subscription_values_are_not_reinterpreted_by_projection(crud_db):  # noqa: F811
    db = crud_db
    target = db.tenants[1]
    observations = []
    async with signed_in(db) as admin:
        for status in ("active", "inactive", "past_due", "suspended", "cancelled", "trialing", "canceled"):
            await db.owner.execute("UPDATE tenants SET subscription_status=$2 WHERE id=$1", target, status)
            row = await db.owner.fetchrow("SELECT * FROM tenants WHERE id=$1", target)
            listed = await call(db, admin, "GET", "/admin/tenants", params={"search": str(target), "include_archived": "true"})
            detail = await call(db, admin, "GET", f"/admin/tenants/{target}")
            receipt = await call(db, admin, "PATCH", f"/admin/tenants/{target}/quota", json={"expected_updated_at": row["updated_at"].isoformat(), "minutes_allocated": row["minutes_allocated"]})
            assert all(r.status_code == 200 for r in (listed, detail, receipt))
            assert listed.json()[0]["status"] == detail.json()["status"] == receipt.json()["status"] == status
            assert receipt.json()["changed"] is False
            assert await db.owner.fetchval("SELECT subscription_status FROM tenants WHERE id=$1", target) == status
            observations.append(status)
    RUN_EVIDENCE.append({"case": "explicit_status_preserved", "statuses": observations})

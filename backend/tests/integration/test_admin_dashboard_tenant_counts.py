"""Dashboard/directory count agreement through normal MFA and real owned SQL.

The directory's subscription projection does not establish account admission.
Dashboard counts retain their separate active-account requirement.
"""
from uuid import uuid4

import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import call, signed_in

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@pytest_asyncio.fixture
async def dashboard_db(journey_db):  # noqa: F811
    journey_db.client_ip = "127.0.1." + str(1 + uuid4().int % 250)
    yield journey_db


async def dashboard_count(db, admin):
    response = await call(db, admin, "GET", "/admin/dashboard/stats")
    assert response.status_code == 200
    value = response.json()["active_tenants"]
    assert isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return value


async def directory_counts(db, admin, prefix):
    result = {}
    for status in ("active", "trialing"):
        response = await call(db, admin, "GET", "/admin/tenants", params={
            "search": prefix, "status": status, "limit": 10,
        })
        assert response.status_code == 200
        rows = response.json()
        total = int(response.headers["x-total-count"])
        assert total == len(rows)
        assert all(row["status"] == status for row in rows)
        assert all(row["business_name"] == prefix for row in rows)
        result[status] = {"total": total, "ids": [row["id"] for row in rows]}
    return result


@pytest.mark.parametrize("account_status,subscription,projected,count_delta", [
    pytest.param("active", None, "active", 1, id="active-null"),
    pytest.param("active", "", "active", 1, id="active-empty"),
    pytest.param("active", "active", "active", 1, id="active-active"),
    pytest.param("active", "trialing", "trialing", 1, id="active-trialing"),
    pytest.param("active", "inactive", "inactive", 0, id="active-inactive"),
    pytest.param("active", "cancelled", "cancelled", 0, id="active-cancelled"),
    pytest.param("active", "suspended", "suspended", 0, id="active-suspended"),
    pytest.param("suspended", None, "active", 0, id="suspended-null"),
    pytest.param("suspended", "", "active", 0, id="suspended-empty"),
    pytest.param("suspended", "active", "active", 0, id="suspended-active"),
    pytest.param("suspended", "trialing", "trialing", 0, id="suspended-trialing"),
])
async def test_active_tenant_count_matches_projection_without_reactivating_account(
    dashboard_db, account_status, subscription, projected, count_delta,
):
    db = dashboard_db
    target = db.tenants[1]  # Never change the signed-in administrator's tenant.
    prefix = "Synthetic dashboard count " + uuid4().hex
    await db.owner.execute(
        "UPDATE tenants SET business_name=$2,status='active',subscription_status='cancelled' WHERE id=$1",
        target, prefix,
    )
    async with signed_in(db) as admin:
        before = await dashboard_count(db, admin)
        directory_before = await directory_counts(db, admin, prefix)
        assert all(item["total"] == 0 for item in directory_before.values())
        await db.owner.execute(
            "UPDATE tenants SET status=$2,subscription_status=$3 WHERE id=$1",
            target, account_status, subscription,
        )
        stored_before_reads = dict(await db.owner.fetchrow("SELECT * FROM tenants WHERE id=$1", target))
        after = await dashboard_count(db, admin)
        directory_after = await directory_counts(db, admin, prefix)
        stored_after_reads = dict(await db.owner.fetchrow("SELECT * FROM tenants WHERE id=$1", target))

    observed = {
        "case": "owned_dashboard_directory_delta",
        "account_status": account_status,
        "stored_subscription": subscription,
        "projected_subscription": projected,
        "dashboard_before": before,
        "dashboard_after": after,
        "dashboard_delta": after - before,
        "expected_dashboard_delta": count_delta,
        "directory_deltas": {key: directory_after[key]["total"] - directory_before[key]["total"]
                             for key in directory_before},
        "stored_row_unchanged_by_reads": stored_before_reads == stored_after_reads,
        "admin_route_methods": sorted({step["method"] for step in db.report["steps"]
                                       if step["path"].startswith("/admin/")}),
    }
    RUN_EVIDENCE.append(observed)
    assert stored_before_reads == stored_after_reads
    assert stored_after_reads["status"] == account_status
    assert stored_after_reads["subscription_status"] == subscription
    assert observed["admin_route_methods"] == ["GET"]
    for status in ("active", "trialing"):
        expected_ids = [str(target)] if projected == status else []
        assert directory_after[status]["ids"] == expected_ids
        assert observed["directory_deltas"][status] == len(expected_ids)
    assert after - before == count_delta, observed

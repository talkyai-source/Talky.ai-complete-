"""Real tenant HTTP handlers with synthetic DB ports, not normal-auth/PG proof."""
from copy import deepcopy

import pytest

from tests.unit.test_admin_tenant_lifecycle import make_client, tenant_row


def projection_client(status):
    row = tenant_row(1, status=status)

    def respond(method, sql, args):
        query = " ".join(sql.lower().split())
        if "from plans" in query:
            return {"id": "free", "name": "Free", "minutes": 30, "concurrent_calls": 10}
        if "pg_advisory_xact_lock" in query:
            return None
        if method == "fetchval" and "count(*)" in query:
            return 1
        if method == "fetch":
            return [deepcopy(row)]
        if method == "fetchrow" and "from tenants" in query:
            return deepcopy(row)
        if query.startswith("update tenants set minutes_allocated"):
            row["minutes_allocated"] = args[1]
            row["updated_at"] = "2026-08-29T00:00:01+00:00"
            return deepcopy(row)
        raise AssertionError(f"Unexpected synthetic DB operation: {method} {query}")

    client, conn, audit = make_client(respond)
    return client, conn, audit, row


@pytest.mark.parametrize("stored", [None, "", "active", "inactive", "past_due", "suspended", "cancelled", "trialing", "canceled", "provider_future"])
def test_all_tenant_responses_project_same_subscription_without_rewriting_it(stored):
    client, conn, audit, row = projection_client(stored)
    expected = stored or "active"
    path = f"/api/v1/admin/tenants/{row['id']}"
    with client:
        listed = client.get("/api/v1/admin/tenants", params={"include_archived": True})
        detail = client.get(path)
        existing = client.post("/api/v1/admin/tenants", json={"business_name": row["business_name"]})
        unchanged = client.patch(path + "/quota", json={"expected_updated_at": row["updated_at"], "minutes_allocated": row["minutes_allocated"]})
        saved = client.patch(path + "/quota", json={"expected_updated_at": row["updated_at"], "minutes_allocated": 41})
    assert all(response.status_code == 200 for response in (listed, detail, existing, unchanged, saved))
    observed = {"list": listed.json()[0]["status"], "detail": detail.json()["status"],
                "existing_create": existing.json()["status"], "no_op_receipt": unchanged.json()["status"],
                "quota_receipt": saved.json()["status"]}
    assert observed == dict.fromkeys(observed, expected)
    assert existing.json()["created"] is False
    assert unchanged.json()["changed"] is False and unchanged.json()["audit_event_id"] is None
    assert saved.json()["changed"] is True and saved.json()["audit_event_id"]
    assert row["subscription_status"] == stored
    assert len(audit.events) == 1
    assert audit.events[0]["before_state"]["subscription_status"] == stored
    assert audit.events[0]["after_state"]["subscription_status"] == stored
    assert len(conn.statements_matching("UPDATE tenants")) == 1
    assert not conn.statements_matching("INSERT INTO tenants")


@pytest.mark.parametrize("stored", [None, "active", "inactive", "cancelled"])
def test_stale_version_conflict_uses_same_status_and_makes_no_write(stored):
    client, conn, audit, row = projection_client(stored)
    with client:
        response = client.patch(f"/api/v1/admin/tenants/{row['id']}/quota", json={
            "expected_updated_at": "2026-08-28T00:00:00+00:00", "minutes_allocated": 41})
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == (stored or "active")
    assert not conn.statements_matching("UPDATE tenants") and not audit.events


@pytest.mark.parametrize("failure", ["missing", "unavailable"])
def test_detail_does_not_invent_active_tenant_for_missing_or_failed_read(failure):
    def respond(method, sql, args):
        if failure == "unavailable":
            raise RuntimeError("Synthetic DB read unavailable")
        return None

    client, _, _ = make_client(respond)
    with client:
        response = client.get("/api/v1/admin/tenants/00000000-0000-0000-0000-000000000001")
    assert response.status_code == (404 if failure == "missing" else 503)
    assert "status" not in response.json()

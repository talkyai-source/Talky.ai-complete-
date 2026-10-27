"""Owned Usage reads through normal MFA, the real adapter and PostgreSQL.

No provider, supplier spend, browser or deployed-reporting claim. Synthetic
stored legacy zero/missing values are deliberately not authoritative money.
"""
import os
import re
from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import call, signed_in

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []
DAY = "2026-02-28"


@pytest_asyncio.fixture
async def usage_db(journey_db, monkeypatch):  # noqa: F811
    from app.core import postgres_adapter

    db = journey_db
    db.client_ip = "127.0.1." + str(1 + uuid4().int % 250)
    async with db.pool.acquire() as conn:
        db.usage_role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", db.usage_role)
    db.usage_ids = {"calls": [], "assistant_actions": [], "campaigns": []}
    db.usage_cleanup = {"complete": False}
    db.report["usage_cleanup"] = db.usage_cleanup
    # QueryBuilder opens fresh connections independently of Client(pool).
    # Preserve its implementation and bind its startup role to this fixture.
    monkeypatch.setattr(postgres_adapter, "_DATABASE_URL",
                        os.environ["TEST_ADMIN_DATABASE_URL"] + "?role=" + db.usage_role)
    original_execute = postgres_adapter.QueryBuilder._execute_with_conn
    db.usage_adapter_reads = []
    db.report["usage_adapter_reads"] = db.usage_adapter_reads

    async def observed_execute(query, conn):
        flags = dict(await conn.fetchrow("""SELECT current_user AS role,rolsuper,rolbypassrls,
            has_table_privilege(current_user,$1,'SELECT') AS select_allowed
            FROM pg_roles WHERE rolname=current_user""", query.table_name))
        assert flags["role"] == db.usage_role and not flags["rolsuper"] and not flags["rolbypassrls"]
        entry = {"table": query.table_name, **flags}
        db.usage_adapter_reads.append(entry)
        try:
            result = await original_execute(query, conn)
        except Exception as exc:
            entry.update(result="error", sqlstate=getattr(exc, "sqlstate", None))
            raise
        entry["result"] = "success"
        return result

    monkeypatch.setattr(postgres_adapter.QueryBuilder, "_execute_with_conn", observed_execute)
    try:
        await db.owner.execute(f'GRANT SELECT ON assistant_actions TO "{db.usage_role}"')
        campaigns = {}
        for index in (1, 2):
            identity = uuid4()
            db.usage_ids["campaigns"].append(identity)
            campaigns[index] = identity
            await db.owner.execute(
                "INSERT INTO campaigns(id,tenant_id,name,direction) "
                "VALUES($1,$2,'Synthetic owned usage','outbound')",
                identity, db.tenants[index],
            )
        # All fixture writes use the disposable owner, including the campaign
        # guard's FOR SHARE. The authenticated role needs no campaign UPDATE.
        calls = [
            (1, "2026-02-27T23:59:59.999999+00:00", 999, None),
            (1, "2026-02-28T00:00:00+00:00", 60, 0),
            (1, "2026-02-28T12:00:00+00:00", 59, None),
            # Same last UTC instant, expressed with a non-UTC input offset.
            (1, "2026-03-01T04:59:59.999999+05:00", 61, None),
            (1, "2026-03-01T00:00:00+00:00", 120, None),
            (2, "2026-02-28T12:00:00+00:00", 600, None),
        ]
        for index, timestamp, seconds, cost in calls:
            identity = uuid4()
            db.usage_ids["calls"].append(identity)
            await db.owner.execute(
                """INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status,
                       direction,is_test,duration_seconds,cost,created_at)
                   VALUES($1,$2,$3,'+15555550100','completed','outbound',FALSE,$4,$5,$6)""",
                identity, db.tenants[index], campaigns[index], seconds, cost,
                datetime.fromisoformat(timestamp),
            )
        actions = [
            (1, "2026-02-27T23:59:59.999999+00:00", "make_call"),
            (1, "2026-02-28T00:00:00+00:00", "send_email"),
            (1, "2026-03-01T04:59:59.999999+05:00", "create_reminder"),
            (1, "2026-03-01T00:00:00+00:00", "book_meeting"),
            (2, "2026-02-28T12:00:00+00:00", "send_sms"),
        ]
        for index, timestamp, kind in actions:
            identity = uuid4()
            db.usage_ids["assistant_actions"].append(identity)
            await db.owner.execute(
                """INSERT INTO assistant_actions(id,tenant_id,user_id,type,status,
                       triggered_by,created_at,input_data,output_data)
                   VALUES($1,$2,$3,$4,'completed','assistant',$5,'{}'::jsonb,'{}'::jsonb)""",
                identity, db.tenants[index], db.users[index], kind,
                datetime.fromisoformat(timestamp),
            )
        yield db
    finally:
        # Lists are populated before each INSERT, so partial setup also has an
        # exact cleanup boundary. Do not disable triggers or clear namespaces.
        async with db.owner.transaction():
            await db.owner.execute("SET LOCAL app.bypass_rls='on'")
            for table in ("crm_deliveries", "call_events", "call_legs"):
                await db.owner.execute(
                    f"DELETE FROM {table} WHERE call_id=ANY($1::uuid[]) "
                    "AND call_id IN (SELECT id FROM calls WHERE tenant_id=ANY($2::uuid[]))",
                    db.usage_ids["calls"], db.tenants,
                )
            for table in ("assistant_actions", "calls", "campaigns"):
                await db.owner.execute(
                    f"DELETE FROM {table} WHERE id=ANY($1::uuid[]) AND tenant_id=ANY($2::uuid[])",
                    db.usage_ids[table], db.tenants,
                )
                assert await db.owner.fetchval(
                    f"SELECT count(*) FROM {table} WHERE id=ANY($1::uuid[])", db.usage_ids[table],
                ) == 0
        db.usage_cleanup.update(complete=True, removed_seed_counts={
            table: len(ids) for table, ids in db.usage_ids.items()
        })


async def owned_snapshot(db):
    result = {}
    for table, ids in db.usage_ids.items():
        result[table] = [dict(row) for row in await db.owner.fetch(
            f"SELECT * FROM {table} WHERE id=ANY($1::uuid[]) ORDER BY id", ids,
        )]
        assert len(result[table]) == len(ids)
    for table in ("crm_deliveries", "call_events", "call_legs"):
        assert await db.owner.fetchval(
            f"SELECT count(*) FROM {table} WHERE call_id=ANY($1::uuid[])", db.usage_ids["calls"],
        ) == 0
    return result


async def read_usage(db, admin, *, index=1, day=DAY, group=None, expected=200):
    params = {"tenant_id": str(db.tenants[index]), "from_date": day, "to_date": day}
    path = "/admin/usage/summary"
    if group is not None:
        path = "/admin/usage/breakdown"
        params["group_by"] = group
    start = len(db.usage_adapter_reads)
    response = await call(db, admin, "GET", path, params=params)
    assert response.status_code == expected
    actual_reads = db.usage_adapter_reads[start:]
    expected_tables = ["calls"] if group == "tenant" else ["calls", "assistant_actions"]
    assert [item["table"] for item in actual_reads] == expected_tables
    for item in actual_reads:
        denied = expected == 503 and item["table"] == "assistant_actions"
        assert item["select_allowed"] is (not denied)
        assert item["result"] == ("error" if denied else "success")
        if denied:
            assert item["sqlstate"] == "42501"
    data = response.json()
    if expected == 200:
        assert data["period_start"] == data["period_end"] == day
        assert data["supplier_cost_status"] == "unavailable"
        assert data["authoritative_inbound_monetary_totals_included"] is False
        assert data["legacy_calls_cost_scope"] == "outbound_only"
        assert data["cost_currency"] == "USD"
    else:
        assert data["error"]["message"] == "Usage data is unavailable. Please retry."
        assert data["error"]["details"] is None
        assert "breakdown" not in data and "total_call_seconds" not in data
    return data


def assert_summary(data, seconds, actions, *, covered=0, missing=0):
    assert data["total_call_seconds"] == seconds
    assert data["total_call_minutes"] == seconds // 60
    assert data["total_action_records"] == data["total_api_calls"] == actions
    assert data["total_cost"] is None and data["providers"] == []
    assert data["legacy_outbound_estimate"] == {
        "recorded_total": 0.0 if covered else None,
        "covered_call_count": covered, "missing_call_count": missing, "currency": "USD",
        "coverage": "partial" if covered and missing else "unavailable",
    }


def record(db, case, **observations):
    methods = sorted({step["method"] for step in db.report["steps"]
                      if step["path"].startswith("/admin/")})
    assert methods == ["GET"]
    RUN_EVIDENCE.append({"case": case, "admin_route_methods": methods,
                         "actual_adapter_role_verified": True,
                         "owned_rows_unchanged_by_reads": True, **observations})


async def test_owned_utc_day_and_tenant_filters_execute_in_postgresql(usage_db):
    db = usage_db
    before = await owned_snapshot(db)
    async with signed_in(db) as admin:
        summary = await read_usage(db, admin)
        assert_summary(summary, 180, 2, covered=1, missing=2)
        tenants = await read_usage(db, admin, group="tenant")
        assert tenants["breakdown"] == [{
            "tenant_id": str(db.tenants[1]),
            "tenant_name": f"Synthetic Admin HTTP {db.tenants[1]}",
            "call_count": 3, "total_seconds": 180, "total_minutes": 3, "total_cost": None,
            "legacy_outbound_estimate": summary["legacy_outbound_estimate"],
        }]
        types = {row["type"]: row for row in (await read_usage(db, admin, group="type"))["breakdown"]}
        assert types == {
            "voice_calls": {"type": "voice_calls", "total_units": 3, "total_seconds": 180,
                            "total_cost": None, "count": 3,
                            "legacy_outbound_estimate": summary["legacy_outbound_estimate"]},
            **{kind: {"type": kind, "total_units": 1, "total_cost": None,
                      "count": 1, "unit": "action_records"}
               for kind in ("send_email", "create_reminder")},
        }
        assert_summary(await read_usage(db, admin, day="2026-03-01"), 120, 1, missing=1)
        next_types = await read_usage(db, admin, day="2026-03-01", group="type")
        assert {row["type"]: row["count"] for row in next_types["breakdown"]} == {
            "voice_calls": 1, "book_meeting": 1,
        }
        assert_summary(await read_usage(db, admin, index=2), 600, 1, missing=1)
        assert_summary(await read_usage(db, admin, index=0), 0, 0)
        assert (await read_usage(db, admin, index=0, group="tenant"))["breakdown"] == []
    assert await owned_snapshot(db) == before
    record(db, "real_sql_utc_and_tenant_filter", selected_seconds=180, selected_actions=2,
           selected_calls=3, next_utc_day_seconds=120, other_tenant_seconds=600,
           empty_owned_tenant_seconds=0, query_scope="every read has an exact owned tenant_id",
           seed_counts={table: len(ids) for table, ids in db.usage_ids.items()})


async def test_real_query_denial_is_unavailable_then_read_recovers(usage_db):
    db = usage_db
    before = await owned_snapshot(db)
    async with signed_in(db) as admin:
        assert_summary(await read_usage(db, admin), 180, 2, covered=1, missing=2)
        try:
            await db.owner.execute(f'REVOKE SELECT ON assistant_actions FROM "{db.usage_role}"')
            async with db.pool.acquire() as conn:
                assert not await conn.fetchval("SELECT has_table_privilege(current_user,'assistant_actions','SELECT')")
            await read_usage(db, admin, expected=503)
            await read_usage(db, admin, group="type", expected=503)
        finally:
            await db.owner.execute(f'GRANT SELECT ON assistant_actions TO "{db.usage_role}"')
        assert_summary(await read_usage(db, admin), 180, 2, covered=1, missing=2)
        recovered = await read_usage(db, admin, group="type")
        assert {row["type"]: row["count"] for row in recovered["breakdown"]} == {
            "voice_calls": 3, "send_email": 1, "create_reminder": 1,
        }
    assert await owned_snapshot(db) == before
    record(db, "real_sql_read_denial_and_recovery", failure_statuses=[503, 503],
           recovery_statuses=[200, 200], failure_source="SELECT revoked only from disposable fixture role")

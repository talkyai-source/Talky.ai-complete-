"""Audit-log lookup by id and platform-admin scoping, against a real Postgres.

Bug-hunt findings tenancy-rbac-admin-7 and tenancy-rbac-admin-8.

* -7: GET /admin/audit/logs/{event_id} asked AuditLogger.query for the single
  newest row (``limit=1``, no id filter, ``datetime.min``) and looked for the id
  in that one row, so it 404'd for every id but the newest.
* -8: a platform_admin whose profile carries a home tenant was silently pinned
  to that tenant on every audit-log and security-event route.

Runs only with an explicit TEST_DATABASE_URL; creates and drops its own
database. ``audit_logs`` and ``security_events`` DDL is cut from
``database/schema/baseline_2026-06-02.sql`` (a pg_dump of production).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import audit_logs as al
from app.api.v1.endpoints import security_events as se
from app.domain.services.audit_logger import AuditLogger

pytestmark = pytest.mark.asyncio

_BASELINE = (
    Path(__file__).resolve().parents[2] / "database" / "schema" / "baseline_2026-06-02.sql"
)

TENANT_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TENANT_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _create_table(sql_text: str, table: str) -> str:
    match = re.search(rf"CREATE TABLE public\.{table} \(.*?\n\);", sql_text, re.S)
    assert match, f"{table} DDL not found"
    return match.group(0)


def _admin_dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL is required")
    return dsn


async def _jsonb_codec(conn):
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


_DB_PREFIX = "bh_audit"


async def _apply_schema(conn):
    baseline = _BASELINE.read_text(encoding="utf-8")
    await conn.execute(
        "CREATE FUNCTION public.uuid_generate_v4() RETURNS uuid "
        "LANGUAGE sql AS $$ SELECT gen_random_uuid() $$"
    )
    await conn.execute(_create_table(baseline, "audit_logs"))
    await conn.execute(_create_table(baseline, "security_events"))


@pytest.fixture(scope="module")
def database_dsn():
    """One throwaway database per module (CREATE DATABASE is slow on CI boxes)."""
    admin_dsn = _admin_dsn_or_skip()
    dbname = f"{_DB_PREFIX}_{uuid.uuid4().hex[:10]}"
    base, _, _ = admin_dsn.rpartition("/")

    async def create():
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'CREATE DATABASE "{dbname}"')
        await admin.close()
        conn = await asyncpg.connect(f"{base}/{dbname}")
        try:
            await _apply_schema(conn)
        finally:
            await conn.close()

    async def drop():
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()

    asyncio.run(create())
    try:
        yield f"{base}/{dbname}"
    finally:
        asyncio.run(drop())


@pytest_asyncio.fixture
async def pool(database_dsn):
    p = await asyncpg.create_pool(database_dsn, min_size=1, max_size=5, init=_jsonb_codec)
    try:
        async with p.acquire() as conn:
            await conn.execute("TRUNCATE audit_logs, security_events CASCADE")
        yield p
    finally:
        await p.close()


def _user(role, tenant_id=None):
    return CurrentUser(
        id=str(uuid.uuid4()),
        email="u@example.test",
        tenant_id=str(tenant_id) if tenant_id else None,
        role=role,
    )


def _logger(pool) -> AuditLogger:
    audit = AuditLogger(pool, signing_key="test-key")
    audit.log = AsyncMock()  # the routes' own access log is not under test
    return audit


async def _seed_audit(pool, tenant_id, *, age_minutes, action):
    event_id = uuid.uuid4()
    when = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO audit_logs (event_id, event_time, event_type, event_category,
                                    tenant_id, action, entry_hash, retention_until,
                                    compliance_tags)
            VALUES ($1, $2, 'record_viewed', 'DATA_ACCESS', $3, $4, $5, CURRENT_DATE + 30, '{}')
            """,
            event_id, when, tenant_id, action, uuid.uuid4().hex + uuid.uuid4().hex,
        )
    return event_id


async def _seed_security_event(pool, tenant_id, *, severity="HIGH", status="open"):
    event_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO security_events (event_id, event_type, severity, status, tenant_id,
                                         detection_source, title, description)
            VALUES ($1, 'suspicious_login', $2, $3, $4, 'manual', 't', 'd')
            """,
            event_id, severity, status, tenant_id,
        )
    return event_id


# ---------------------------------------------------------------------------
# tenancy-rbac-admin-7: lookup by id
# ---------------------------------------------------------------------------

async def test_get_audit_log_finds_an_entry_that_is_not_the_newest(pool):
    older = await _seed_audit(pool, TENANT_A, age_minutes=30, action="older")
    await _seed_audit(pool, TENANT_A, age_minutes=1, action="newest")

    entry = await al.get_audit_log(
        event_id=older, current_user=_user("platform_admin"), audit_logger=_logger(pool),
    )

    assert entry.event_id == older
    assert entry.action == "older"


async def test_get_audit_log_unknown_id_is_404(pool):
    await _seed_audit(pool, TENANT_A, age_minutes=1, action="only")

    with pytest.raises(HTTPException) as exc:
        await al.get_audit_log(
            event_id=uuid.uuid4(), current_user=_user("platform_admin"),
            audit_logger=_logger(pool),
        )

    assert exc.value.status_code == 404


async def test_tenant_caller_gets_404_not_403_for_another_tenants_entry(pool):
    foreign = await _seed_audit(pool, TENANT_B, age_minutes=5, action="b-row")
    await _seed_audit(pool, TENANT_A, age_minutes=1, action="a-row")

    with pytest.raises(HTTPException) as exc:
        await al.get_audit_log(
            event_id=foreign, current_user=_user("tenant_admin", TENANT_A),
            audit_logger=_logger(pool),
        )

    # 404, not 403: a 403 would confirm the id exists in another tenant.
    assert exc.value.status_code == 404


async def test_tenant_caller_reads_their_own_older_entry(pool):
    mine = await _seed_audit(pool, TENANT_A, age_minutes=20, action="a-old")
    await _seed_audit(pool, TENANT_B, age_minutes=1, action="b-new")

    entry = await al.get_audit_log(
        event_id=mine, current_user=_user("tenant_admin", TENANT_A),
        audit_logger=_logger(pool),
    )

    assert entry.action == "a-old"


async def test_platform_admin_with_home_tenant_reads_another_tenants_entry(pool):
    foreign = await _seed_audit(pool, TENANT_B, age_minutes=5, action="b-row")

    entry = await al.get_audit_log(
        event_id=foreign, current_user=_user("platform_admin", TENANT_A),
        audit_logger=_logger(pool),
    )

    assert entry.tenant_id == TENANT_B


# ---------------------------------------------------------------------------
# tenancy-rbac-admin-8: audit-log routes
# ---------------------------------------------------------------------------

async def _query_logs(pool, user, tenant_id=None):
    return await al.query_audit_logs(
        request=None, start_date=None, end_date=None, event_type=None, actor_id=None,
        tenant_id=tenant_id, resource_type=None, severity=None, limit=100, offset=0,
        current_user=user, audit_logger=_logger(pool),
    )


async def test_platform_admin_with_home_tenant_sees_every_tenant(pool):
    await _seed_audit(pool, TENANT_A, age_minutes=3, action="a")
    await _seed_audit(pool, TENANT_B, age_minutes=2, action="b")
    await _seed_audit(pool, None, age_minutes=1, action="platform")

    rows = await _query_logs(pool, _user("platform_admin", TENANT_A))

    assert {r.action for r in rows} == {"a", "b", "platform"}


async def test_platform_admin_with_home_tenant_may_filter_to_another_tenant(pool):
    await _seed_audit(pool, TENANT_A, age_minutes=3, action="a")
    await _seed_audit(pool, TENANT_B, age_minutes=2, action="b")

    rows = await _query_logs(pool, _user("platform_admin", TENANT_A), tenant_id=TENANT_B)

    assert [r.action for r in rows] == ["b"]


async def test_tenant_admin_stays_pinned_to_their_tenant(pool):
    await _seed_audit(pool, TENANT_A, age_minutes=3, action="a")
    await _seed_audit(pool, TENANT_B, age_minutes=2, action="b")
    user = _user("tenant_admin", TENANT_A)

    rows = await _query_logs(pool, user)
    assert [r.action for r in rows] == ["a"]

    with pytest.raises(HTTPException) as exc:
        await _query_logs(pool, user, tenant_id=TENANT_B)
    assert exc.value.status_code == 403


async def test_tenant_less_non_platform_caller_is_refused_not_unscoped(pool):
    await _seed_audit(pool, TENANT_A, age_minutes=3, action="a")

    with pytest.raises(HTTPException) as exc:
        await _query_logs(pool, _user("tenant_admin", None))

    assert exc.value.status_code == 403


async def test_export_for_platform_admin_with_home_tenant_covers_all_tenants(pool):
    await _seed_audit(pool, TENANT_A, age_minutes=3, action="a")
    await _seed_audit(pool, TENANT_B, age_minutes=2, action="b")
    now = datetime.now(timezone.utc)

    out = await al.export_audit_logs(
        request=None, start_date=now - timedelta(days=1), end_date=now + timedelta(days=1),
        tenant_id=None, format="json", current_user=_user("platform_admin", TENANT_A),
        audit_logger=_logger(pool),
    )

    assert out["record_count"] == 2


async def test_export_for_tenant_admin_cannot_name_another_tenant(pool):
    now = datetime.now(timezone.utc)

    with pytest.raises(HTTPException) as exc:
        await al.export_audit_logs(
            request=None, start_date=now - timedelta(days=1), end_date=now + timedelta(days=1),
            tenant_id=TENANT_B, format="json", current_user=_user("tenant_admin", TENANT_A),
            audit_logger=_logger(pool),
        )

    assert exc.value.status_code == 403


async def test_event_stats_for_platform_admin_with_home_tenant_cover_all_tenants(pool):
    await _seed_audit(pool, TENANT_A, age_minutes=3, action="a")
    await _seed_audit(pool, TENANT_B, age_minutes=2, action="b")

    out = await al.get_event_stats(
        days=30, tenant_id=None, current_user=_user("platform_admin", TENANT_A),
        db_pool=pool,
    )

    assert out["events_by_type"] == {"record_viewed": 2}


async def test_event_stats_for_tenant_admin_stay_pinned(pool):
    await _seed_audit(pool, TENANT_A, age_minutes=3, action="a")
    await _seed_audit(pool, TENANT_B, age_minutes=2, action="b")

    out = await al.get_event_stats(
        days=30, tenant_id=None, current_user=_user("tenant_admin", TENANT_A),
        db_pool=pool,
    )

    assert out["events_by_type"] == {"record_viewed": 1}


# ---------------------------------------------------------------------------
# tenancy-rbac-admin-8: security-event routes
# ---------------------------------------------------------------------------

async def _list_events(pool, user, tenant_id=None):
    return await se.list_security_events(
        status=None, severity=None, tenant_id=tenant_id, assigned_to=None,
        detection_source=None, limit=100, offset=0, current_user=user, db_pool=pool,
    )


async def test_security_list_platform_admin_with_home_tenant_sees_all(pool):
    a = await _seed_security_event(pool, TENANT_A)
    b = await _seed_security_event(pool, TENANT_B)
    platform = await _seed_security_event(pool, None)

    rows = await _list_events(pool, _user("platform_admin", TENANT_A))

    assert {r.event_id for r in rows} == {a, b, platform}


async def test_security_list_platform_admin_may_filter_to_another_tenant(pool):
    await _seed_security_event(pool, TENANT_A)
    b = await _seed_security_event(pool, TENANT_B)

    rows = await _list_events(pool, _user("platform_admin", TENANT_A), tenant_id=TENANT_B)

    assert [r.event_id for r in rows] == [b]


async def test_security_list_tenant_admin_pinned_and_cross_tenant_403(pool):
    a = await _seed_security_event(pool, TENANT_A)
    await _seed_security_event(pool, TENANT_B)
    user = _user("tenant_admin", TENANT_A)

    assert [r.event_id for r in await _list_events(pool, user)] == [a]
    with pytest.raises(HTTPException) as exc:
        await _list_events(pool, user, tenant_id=TENANT_B)
    assert exc.value.status_code == 403


async def test_security_get_platform_admin_reads_another_tenants_event(pool):
    b = await _seed_security_event(pool, TENANT_B)

    row = await se.get_security_event(
        event_id=b, current_user=_user("platform_admin", TENANT_A), db_pool=pool,
    )

    assert row.event_id == b


async def test_security_get_tenant_admin_cross_tenant_still_403(pool):
    b = await _seed_security_event(pool, TENANT_B)

    with pytest.raises(HTTPException) as exc:
        await se.get_security_event(
            event_id=b, current_user=_user("tenant_admin", TENANT_A), db_pool=pool,
        )

    assert exc.value.status_code == 403


async def test_security_update_resolve_escalate_reach_another_tenant_for_platform_admin(pool):
    admin = _user("platform_admin", TENANT_A)
    audit = AsyncMock()

    upd = await _seed_security_event(pool, TENANT_B)
    out = await se.update_security_event(
        event_id=upd, data=se.SecurityEventUpdate(status="investigating"),
        current_user=admin, audit_logger=audit, db_pool=pool,
    )
    assert out.status == "investigating"

    res = await _seed_security_event(pool, TENANT_B)
    out = await se.resolve_security_event(
        event_id=res, resolution_notes="done", current_user=admin,
        audit_logger=audit, db_pool=pool,
    )
    assert out.status == "resolved"

    esc = await _seed_security_event(pool, TENANT_B)
    out = await se.escalate_event(
        event_id=esc, reason="urgent", current_user=admin, audit_logger=audit,
        db_pool=pool,
    )
    assert out.status == "escalated"


async def test_security_update_tenant_admin_cross_tenant_is_404(pool):
    b = await _seed_security_event(pool, TENANT_B)

    with pytest.raises(HTTPException) as exc:
        await se.update_security_event(
            event_id=b, data=se.SecurityEventUpdate(status="investigating"),
            current_user=_user("tenant_admin", TENANT_A), audit_logger=AsyncMock(),
            db_pool=pool,
        )

    assert exc.value.status_code == 404


async def test_security_open_alerts_platform_admin_sees_all_tenants(pool):
    await _seed_security_event(pool, TENANT_A)
    await _seed_security_event(pool, TENANT_B)

    out = await se.get_open_alerts(
        current_user=_user("platform_admin", TENANT_A), db_pool=pool,
    )
    assert len(out["alerts"]) == 2

    out = await se.get_open_alerts(
        current_user=_user("tenant_admin", TENANT_A), db_pool=pool,
    )
    assert len(out["alerts"]) == 1


async def test_security_create_platform_admin_may_attribute_to_another_tenant(pool):
    row = await se.create_security_event(
        data=se.SecurityEventCreate(
            event_type="manual_note", severity="LOW", title="t", description="d",
            tenant_id=TENANT_B,
        ),
        current_user=_user("platform_admin", TENANT_A), audit_logger=AsyncMock(),
        db_pool=pool,
    )

    assert row.tenant_id == TENANT_B


async def test_security_create_tenant_admin_cannot_attribute_to_another_tenant(pool):
    with pytest.raises(HTTPException) as exc:
        await se.create_security_event(
            data=se.SecurityEventCreate(
                event_type="manual_note", severity="LOW", title="t", description="d",
                tenant_id=TENANT_B,
            ),
            current_user=_user("tenant_admin", TENANT_A), audit_logger=AsyncMock(),
            db_pool=pool,
        )

    assert exc.value.status_code == 403

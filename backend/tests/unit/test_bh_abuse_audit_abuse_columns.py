"""Abuse-monitoring console against a real Postgres built from the repo schema.

Bug-hunt findings tenancy-rbac-admin-6 and tenancy-rbac-admin-11.

* tenancy-rbac-admin-6: every query that rendered a tenant name selected
  ``tenants.name``. The tenants table has ``business_name`` and no ``name``, so
  /events, /events/{id}, /events/{id}/resolve, /alerts and
  ``AbuseDetectionService.get_recent_events`` raised UndefinedColumnError (500).
  A mocked pool cannot see that, so the ``tenants`` DDL here is cut out of
  ``database/schema/baseline_2026-06-02.sql`` (a pg_dump of production).
* tenancy-rbac-admin-11: the resolver id came from a client query parameter and
  no audit row was written.

Runs only with an explicit TEST_DATABASE_URL (an admin DSN on a throwaway
server). It creates and drops its own database.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import abuse_monitoring as am
from app.api.v1.endpoints import call_limits
from app.domain.services.audit_logger import AuditEvent

pytestmark = pytest.mark.asyncio

_BACKEND = Path(__file__).resolve().parents[2]
_BASELINE = _BACKEND / "database" / "schema" / "baseline_2026-06-02.sql"
_DAY7 = _BACKEND / "database" / "migrations" / "_archive" / "day7_voice_security.sql"


def _create_table(sql_text: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE (?:public\.)?{table} \(.*?\n\);", sql_text, re.S
    )
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


_DB_PREFIX = "bh_abuse"


async def _apply_schema(conn):
    baseline = _BASELINE.read_text(encoding="utf-8")
    day7 = _DAY7.read_text(encoding="utf-8")
    await conn.execute(
        "CREATE FUNCTION public.uuid_generate_v4() RETURNS uuid "
        "LANGUAGE sql AS $$ SELECT gen_random_uuid() $$"
    )
    await conn.execute(_create_table(baseline, "tenants"))
    await conn.execute(_create_table(baseline, "abuse_detection_rules"))
    await conn.execute(_create_table(baseline, "partner_limits"))
    # The baseline adds its primary keys with separate ALTER TABLEs.
    await conn.execute("ALTER TABLE tenants ADD PRIMARY KEY (id)")
    await conn.execute("ALTER TABLE abuse_detection_rules ADD PRIMARY KEY (id)")
    # day7 is the only DDL that defines the columns the abuse code reads and
    # writes (partner_id, action_taken, resolution_notes ...).
    await conn.execute(_create_table(day7, "abuse_events"))


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
            await conn.execute("TRUNCATE tenants, abuse_events, abuse_detection_rules, partner_limits CASCADE")
        yield p
    finally:
        await p.close()


async def _seed_event(pool, *, business_name="Acme Dialers", severity="high"):
    tenant_id = uuid.uuid4()
    event_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, business_name) VALUES ($1, $2)",
            tenant_id,
            business_name,
        )
        await conn.execute(
            """
            INSERT INTO abuse_events
                (id, tenant_id, event_type, severity, trigger_value, threshold_value,
                 phone_number_called, action_taken, action_details)
            VALUES ($1, $2, 'velocity_spike', $3, 150, 100, '+15550100', 'flagged', '{}'::jsonb)
            """,
            event_id,
            tenant_id,
            severity,
        )
    return tenant_id, event_id


def _admin(user_id=None):
    return CurrentUser(
        id=str(user_id or uuid.uuid4()),
        email="ops@example.test",
        tenant_id=None,
        role="platform_admin",
    )


async def test_list_events_renders_tenant_business_name(pool):
    tenant_id, event_id = await _seed_event(pool)

    events = await am.list_abuse_events(
        tenant_id=None, partner_id=None, event_type=None, severity=None,
        unresolved_only=False, hours=24, limit=100, db_pool=pool,
    )

    assert [e.id for e in events] == [event_id]
    assert events[0].tenant_name == "Acme Dialers"


async def test_get_event_renders_tenant_business_name(pool):
    _, event_id = await _seed_event(pool)

    event = await am.get_abuse_event(event_id=event_id, db_pool=pool)

    assert event.tenant_name == "Acme Dialers"


async def test_alerts_render_tenant_business_name(pool):
    _, event_id = await _seed_event(pool, severity="critical")

    alerts = await am.get_abuse_alerts(
        tenant_id=None, min_severity="high", limit=20, db_pool=pool,
    )

    assert [a["id"] for a in alerts] == [str(event_id)]
    assert alerts[0]["tenant_name"] == "Acme Dialers"


async def test_detection_service_recent_events_render_tenant_name(pool):
    from app.domain.services.abuse_detection import AbuseDetectionService

    tenant_id, event_id = await _seed_event(pool)
    service = AbuseDetectionService(pool)

    rows = await service.get_recent_events(tenant_id=str(tenant_id))

    assert [r["id"] for r in rows] == [event_id]
    assert rows[0]["tenant_name"] == "Acme Dialers"


async def test_resolve_attributes_to_the_authenticated_admin_and_audits(pool):
    tenant_id, event_id = await _seed_event(pool)
    me = _admin()
    audit = AsyncMock()

    resolved = await am.resolve_abuse_event(
        event_id=event_id,
        request=am.ResolveEventRequest(notes="customer confirmed", false_positive=True),
        current_user=me,
        audit_logger=audit,
        db_pool=pool,
    )

    assert str(resolved.resolved_by) == me.id
    assert resolved.false_positive is True
    assert resolved.tenant_name == "Acme Dialers"
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT resolved_by FROM abuse_events WHERE id = $1", event_id
        )
    assert str(stored) == me.id

    audit.log.assert_awaited_once()
    kwargs = audit.log.await_args.kwargs
    assert kwargs["event_type"] == AuditEvent.SUSPICIOUS_ACTIVITY
    assert kwargs["action"] == "abuse_event_marked_false_positive"
    assert kwargs["actor_id"] == me.id
    assert str(kwargs["tenant_id"]) == str(tenant_id)
    assert str(kwargs["resource_id"]) == str(event_id)


async def test_resolve_takes_no_client_supplied_resolver(pool):
    params = inspect.signature(am.resolve_abuse_event).parameters
    assert "resolved_by" not in params


async def test_resolving_twice_is_404_and_writes_no_second_audit_row(pool):
    _, event_id = await _seed_event(pool)
    audit = AsyncMock()
    kwargs = dict(
        event_id=event_id,
        request=am.ResolveEventRequest(notes=None, false_positive=False),
        current_user=_admin(),
        audit_logger=audit,
        db_pool=pool,
    )
    await am.resolve_abuse_event(**kwargs)

    with pytest.raises(HTTPException) as exc:
        await am.resolve_abuse_event(**kwargs)

    assert exc.value.status_code == 404
    assert audit.log.await_count == 1


async def test_partner_limits_render_the_partner_business_name(pool):
    partner_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, business_name) VALUES ($1, 'Reseller Inc')", partner_id
        )
        await conn.execute("INSERT INTO partner_limits (partner_id) VALUES ($1)", partner_id)

    out = await call_limits.get_partner_limits(
        partner_id=partner_id, admin_user=_admin(), db_client=SimpleNamespace(pool=pool),
    )

    assert out["partner_name"] == "Reseller Inc"


async def test_partner_without_limits_row_gets_defaults_with_its_business_name(pool):
    partner_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, business_name) VALUES ($1, 'Reseller Inc')", partner_id
        )

    out = await call_limits.get_partner_limits(
        partner_id=partner_id, admin_user=_admin(), db_client=SimpleNamespace(pool=pool),
    )

    assert out["is_default"] is True
    assert out["partner_name"] == "Reseller Inc"

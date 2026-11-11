"""0093 on disposable loopback PostgreSQL: abuse_events gets the code's columns (QA F14-02).

LINEAGE is production's narrow abuse_events, read from the production catalog
on 11 Nov 2026. CLEAN is complete_schema.sql's definition, the shape the abuse
code was written for.
"""
import re
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError

from app.api.v1.endpoints.abuse_monitoring import AbuseEventResponse
from tests.integration.test_touch_trigger_repair_pg import database, load  # noqa: F401

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
LINEAGE = (
    "CREATE TABLE tenants (id uuid PRIMARY KEY, business_name text)",
    """CREATE TABLE abuse_events (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
       tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
       event_type varchar NOT NULL DEFAULT 'velocity_anomaly', severity varchar NOT NULL DEFAULT 'medium'
           CHECK (severity IN ('low','medium','high','critical')),
       source varchar NOT NULL DEFAULT 'system', description text, details jsonb NOT NULL DEFAULT '{}'::jsonb,
       resolved_at timestamptz, resolved_by uuid, resolution_note text,
       created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())""",
)
CLEAN = (
    "CREATE TABLE tenants (id uuid PRIMARY KEY, business_name text)",
    """CREATE TABLE abuse_events (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
       tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, partner_id uuid REFERENCES tenants(id),
       event_type text NOT NULL, severity text NOT NULL CHECK (severity IN ('low','medium','high','critical')),
       trigger_value numeric, threshold_value numeric, phone_number_called varchar(50), call_id uuid,
       destination_country text,
       action_taken text NOT NULL CHECK (action_taken IN ('flagged','warned','throttled','blocked','suspended')),
       action_details jsonb DEFAULT '{}', resolved_at timestamptz, resolved_by uuid, resolution_notes text,
       false_positive boolean, created_at timestamptz DEFAULT now())""",
)
NARROW = {"source", "description", "details", "resolution_note"}


def migration():
    module = load("0093_abuse_event_columns")
    assert (module.revision, module.down_revision) == ("0093_abuse_event_columns", "0092_drop_broken_touch_triggers")
    return module


def app_columns():
    """The columns the abuse code really inserts and resolves with, read from its SQL."""
    detection = (BACKEND / "app/domain/services/abuse_detection.py").read_text(encoding="utf-8")
    monitoring = (BACKEND / "app/api/v1/endpoints/abuse_monitoring.py").read_text(encoding="utf-8")
    inserted = re.search(r"INSERT INTO abuse_events \(([^)]*)\)", detection).group(1)
    insert = [name.strip() for name in inserted.split(",")]
    resolved = set()
    for source in (detection, monitoring):
        for block in re.findall(r"UPDATE abuse_events\s+SET(.*?)WHERE", source, re.S):
            resolved |= set(re.findall(r"(\w+)\s*=", block))
    return insert, sorted(resolved)


async def columns(conn):
    return {row["attname"]: row["owned"] for row in await conn.fetch(
        """SELECT attname, col_description(attrelid, attnum) = 'created by 0093_abuse_event_columns' AS owned
           FROM pg_attribute WHERE attrelid='abuse_events'::regclass AND attnum > 0 AND NOT attisdropped""")}


async def record_and_resolve(conn, insert, resolved):
    """Run the application's own column lists against the table."""
    tenant = uuid4()
    await conn.execute("INSERT INTO tenants (id, business_name) VALUES ($1, 'Abuse fixture')", tenant)
    sample = {"tenant_id": tenant, "partner_id": None, "event_type": "velocity_spike", "severity": "high",
              "trigger_value": 150, "threshold_value": 100, "phone_number_called": "+14155550107",
              "call_id": uuid4(), "action_taken": "throttled", "action_details": '{"limit": 100}',
              "destination_country": "US"}
    placeholders = ", ".join(f"${index}" for index in range(1, len(insert) + 1))
    event = await conn.fetchval(f"INSERT INTO abuse_events ({', '.join(insert)}) VALUES ({placeholders}) RETURNING id",
                                *[sample[name] for name in insert])
    values = {"resolved_by": uuid4(), "resolution_notes": "Reviewed", "false_positive": True}
    names = [name for name in resolved if name != "resolved_at"]
    assignments = ", ".join(["resolved_at = NOW()"] + [f"{name} = ${index}" for index, name in enumerate(names, start=2)])
    result = await conn.execute(f"UPDATE abuse_events SET {assignments} WHERE id = $1", event, *[values[name] for name in names])
    assert result == "UPDATE 1"
    return event


async def test_production_lineage_can_record_and_resolve_abuse_events_after_the_repair(database):  # noqa: F811
    insert, resolved = app_columns()
    assert {"tenant_id", "action_taken", "partner_id"} <= set(insert)
    assert {"resolved_at", "resolution_notes", "false_positive"} <= set(resolved)
    await database.build(LINEAGE)
    async with database.pool.acquire() as conn:
        with pytest.raises(asyncpg.UndefinedColumnError):
            await record_and_resolve(conn, insert, resolved)

    await database.apply(migration())
    await database.apply(migration())  # idempotent

    async with database.pool.acquire() as conn:
        event = await record_and_resolve(conn, insert, resolved)
        saved = await conn.fetchrow("SELECT * FROM abuse_events WHERE id=$1", event)
        model = set(AbuseEventResponse.model_fields) - {"tenant_name"}
        assert model <= set(saved.keys()), model - set(saved.keys())
        assert saved["action_taken"] == "throttled" and saved["false_positive"] is True
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("UPDATE abuse_events SET action_taken='deleted' WHERE id=$1", event)
        owned = await columns(conn)
        assert {name for name, mine in owned.items() if mine} == {name for name, _, _ in migration().ABUSE_COLUMNS}
        assert NARROW <= set(owned)  # production's own columns stay


async def test_fresh_install_shape_is_adopted_without_claiming_or_dropping_anything(database):  # noqa: F811
    await database.build(CLEAN)
    await database.apply(migration())
    async with database.pool.acquire() as conn:
        assert not any((await columns(conn)).values())
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE conrelid='abuse_events'::regclass AND conname='abuse_events_action_taken_check'") == 1
    await database.apply(migration(), "downgrade")
    async with database.pool.acquire() as conn:
        assert {"partner_id", "action_taken", "false_positive"} <= set(await columns(conn))


async def test_incompatible_existing_column_is_refused_without_change(database):  # noqa: F811
    await database.build(LINEAGE)
    async with database.pool.acquire() as conn:
        await conn.execute("ALTER TABLE abuse_events ADD COLUMN call_id text")
    with pytest.raises(DBAPIError, match="incompatible abuse_events.call_id"):
        await database.apply(migration())
    async with database.pool.acquire() as conn:
        assert "partner_id" not in await columns(conn)


async def test_downgrade_removes_only_owned_columns_and_refuses_with_rows(database):  # noqa: F811
    await database.build(LINEAGE)
    await database.apply(migration())
    insert, resolved = app_columns()
    async with database.pool.acquire() as conn:
        event = await record_and_resolve(conn, insert, resolved)
    with pytest.raises(DBAPIError, match="Refusing to remove abuse event columns"):
        await database.apply(migration(), "downgrade")
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM abuse_events WHERE id=$1", event) == 1
        await conn.execute("DELETE FROM abuse_events")
    await database.apply(migration(), "downgrade")
    async with database.pool.acquire() as conn:
        remaining = set(await columns(conn))
        assert not remaining & {name for name, _, _ in migration().ABUSE_COLUMNS}
        assert NARROW <= remaining
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE conrelid='abuse_events'::regclass AND conname='abuse_events_action_taken_check'") == 0

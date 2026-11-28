"""0109 on disposable loopback PostgreSQL: the Basic plan's recording retention line says 90 days."""
import json
import os
import re
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from tests.integration.test_touch_trigger_repair_pg import database, load  # noqa: F401

pytestmark = pytest.mark.integration
OLD = "Recording retention: 30 days"
NEW = "Recording retention: 90 days"
LINEAGE = ("CREATE TABLE plans (id text PRIMARY KEY, features jsonb NOT NULL DEFAULT '[]'::jsonb)",)


def migration():
    module = load("0109_plan_retention_line")
    assert (module.revision, module.down_revision) == ("0109_plan_retention_line", "0108_telephony_number_requests")
    return module


async def test_after_bootstrap_and_upgrade_to_head_basic_holds_one_retention_line_saying_90_days():
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("explicit loopback TEST_DATABASE_URL required; real PostgreSQL not run")
    parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
    if (parsed.scheme != "postgresql" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.query or parsed.fragment
            or not re.search(r"(?:^|[_-])(?:test|ci|tmp)(?:[_-]|$)", parsed.path.lstrip("/"), re.I)):
        pytest.fail("this test requires a loopback test DSN without routing overrides", pytrace=False)
    conn = await asyncpg.connect(urlunsplit(parsed), timeout=5, command_timeout=30)
    try:
        # The prepared database was built by the schema bootstrap and `alembic upgrade head` (CI and pg_local_up.sh).
        features = json.loads(await conn.fetchval("SELECT features::text FROM plans WHERE id = 'basic'"))
        lines = [item for item in features if item.startswith("Recording retention")]
        assert lines == [NEW]
    finally:
        await conn.close()


async def _plan(conn, plan_id):
    return json.loads(await conn.fetchval("SELECT features::text FROM plans WHERE id = $1", plan_id))


async def test_upgrade_rewrites_the_old_line_and_leaves_a_staff_edited_line_alone(database):  # noqa: F811
    await database.build(LINEAGE)
    other = ["Concurrent calls: 3", OLD, "Campaigns: 2"]
    async with database.pool.acquire() as conn:
        await conn.execute("INSERT INTO plans VALUES ('basic', $1::jsonb), ('business', $2::jsonb)",
                           json.dumps(["Concurrent calls: 3", OLD, "Campaigns: 2"]),
                           json.dumps(["Recording retention: 90 days"]))
    await database.apply(migration())
    async with database.pool.acquire() as conn:
        assert await _plan(conn, "basic") == ["Concurrent calls: 3", NEW, "Campaigns: 2"]
        assert await _plan(conn, "business") == [NEW]
        await database.apply(migration())  # a second run changes nothing
        assert await _plan(conn, "basic") == ["Concurrent calls: 3", NEW, "Campaigns: 2"]
        await database.apply(migration(), "downgrade")
        assert await _plan(conn, "basic") == other
        assert await _plan(conn, "business") == [NEW]
        edited = ["Concurrent calls: 3", "Recording retention: 45 days", "Campaigns: 2"]
        await conn.execute("UPDATE plans SET features = $1::jsonb WHERE id = 'basic'", json.dumps(edited))
    await database.apply(migration())
    async with database.pool.acquire() as conn:
        assert await _plan(conn, "basic") == edited
    await database.apply(migration(), "downgrade")
    async with database.pool.acquire() as conn:
        assert await _plan(conn, "basic") == edited

"""Migration 0083 (pay as you go) runs down and up, and refuses while an account uses it."""
from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from tests.helpers.support_db import require_test_dsn
from tests.integration.test_support_migration import _alembic

pytestmark = pytest.mark.integration


async def _state(dsn: str) -> tuple:
    conn = await asyncpg.connect(dsn, timeout=5)
    try:
        await conn.execute("SET app.bypass_rls = 'on'")
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        has_model = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'plans' AND column_name = 'billing_model')")
        has_plan = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM plans WHERE id = 'payg')")
        return version, has_model, has_plan
    finally:
        await conn.close()


async def test_downgrade_then_upgrade_round_trips_and_refuses_while_an_account_is_on_it():
    dsn = require_test_dsn()
    assert await _state(dsn) == ("0083_pay_as_you_go", True, True)

    down = _alembic(dsn, "downgrade", "0082_support_system")
    assert down.returncode == 0, down.stderr[-2000:]
    assert await _state(dsn) == ("0082_support_system", False, False)
    up = _alembic(dsn, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    assert await _state(dsn) == ("0083_pay_as_you_go", True, True)

    conn = await asyncpg.connect(dsn, timeout=5)
    tenant = uuid4()
    try:
        await conn.execute("SET app.bypass_rls = 'on'")
        await conn.execute("INSERT INTO tenants (id, business_name, plan_id, minutes_allocated) "
                           "VALUES ($1, 'Migration refusal tenant', 'payg', 0)", tenant)
        refused = _alembic(dsn, "downgrade", "0082_support_system")
        assert refused.returncode != 0
        assert "Refusing to remove pay as you go" in refused.stderr
        assert await _state(dsn) == ("0083_pay_as_you_go", True, True)
    finally:
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant)
        await conn.close()

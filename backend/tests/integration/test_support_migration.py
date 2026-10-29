"""Migration 0082 runs down and up on migrated PostgreSQL and refuses to drop records."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from tests.helpers.support_db import require_test_dsn

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
SUPPORT_TABLES = ("support_tickets", "support_conversations", "support_conversation_messages",
                  "support_ticket_messages", "support_ticket_attachments", "support_ticket_events",
                  "support_ticket_reads", "support_kb_articles", "support_request_receipts")


def _alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, capture_output=True, text=True,
                          env={**os.environ, "DATABASE_URL": dsn}, timeout=300)


async def _tables(dsn: str) -> set[str]:
    conn = await asyncpg.connect(dsn, timeout=5)
    try:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'support_%'")
        return {row["tablename"] for row in rows}
    finally:
        await conn.close()


async def test_downgrade_then_upgrade_round_trips_and_refuses_with_records():
    dsn = require_test_dsn()
    conn = await asyncpg.connect(dsn, timeout=5)
    try:
        await conn.execute("SET app.bypass_rls = 'on'")
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM support_tickets) OR "
                               "EXISTS (SELECT 1 FROM support_conversations)"):
            pytest.skip("Support records exist in this test database; the round trip needs empty support tables")
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0082_support_system"
    finally:
        await conn.close()

    down = _alembic(dsn, "downgrade", "0081_admin_payment_merge")
    assert down.returncode == 0, down.stderr[-2000:]
    assert await _tables(dsn) == set()
    up = _alembic(dsn, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    assert await _tables(dsn) == set(SUPPORT_TABLES)

    # With a ticket present, rollback refuses instead of dropping customer records.
    conn = await asyncpg.connect(dsn, timeout=5)
    tenant, ticket = uuid4(), uuid4()
    try:
        await conn.execute("SET app.bypass_rls = 'on'")
        await conn.execute("INSERT INTO tenants (id, business_name) VALUES ($1, 'Migration refusal tenant')", tenant)
        await conn.execute(
            """INSERT INTO support_tickets (id, tenant_id, subject, category, priority, first_response_due_at,
                   resolution_due_at, request_id) VALUES ($1, $2, 'Keep me', 'other', 'normal', now(), now(), $3)""",
            ticket, tenant, uuid4())
        refused = _alembic(dsn, "downgrade", "0081_admin_payment_merge")
        assert refused.returncode != 0
        assert "Refusing to drop support records" in refused.stderr
        assert await conn.fetchval("SELECT count(*) FROM support_tickets WHERE id = $1", ticket) == 1
    finally:
        await conn.execute("DELETE FROM tenants WHERE id = $1", tenant)
        await conn.close()


def test_repository_has_exactly_one_head():
    result = subprocess.run([sys.executable, "-m", "alembic", "heads"], cwd=BACKEND, capture_output=True, text=True,
                            timeout=120, env={**os.environ, "DATABASE_URL": os.getenv("TEST_DATABASE_URL") or
                                              "postgresql://unused@127.0.0.1:1/unused_test"})
    heads = [line for line in result.stdout.splitlines() if line.strip()]
    assert heads == ["0082_support_system (head)"], result.stdout + result.stderr

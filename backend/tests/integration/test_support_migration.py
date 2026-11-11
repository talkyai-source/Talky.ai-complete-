"""Migration 0082 runs down and up on migrated PostgreSQL and refuses to drop records.

0087 (bootstrap parity) is forward-only: a database that has passed it can never be
taken back to 0082 again. The round trip therefore runs on a disposable database built
by the CI lineage only up to the revision below 0087, and the shared head database
proves that a downgrade past 0087 is refused without changing anything.
"""
from __future__ import annotations

import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
from alembic.script import ScriptDirectory
import pytest

from tests.helpers.support_db import require_test_dsn

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
SUPPORT_TABLES = ("support_tickets", "support_conversations", "support_conversation_messages",
                  "support_ticket_messages", "support_ticket_attachments", "support_ticket_events",
                  "support_ticket_reads", "support_kb_articles", "support_request_receipts")
FORWARD_ONLY = "0087_bootstrap_manual_parity"
BELOW_FORWARD_ONLY = "0086_call_workflows"


def _repository_head() -> str:
    scripts = ScriptDirectory(str(BACKEND / "Alembic"))
    heads = scripts.get_heads()
    assert len(heads) == 1, f"Expected exactly one migration head, got {heads}"
    head = heads[0]
    ancestry = {revision.revision for revision in scripts.walk_revisions(base="base", head=head)}
    assert "0084_call_failure_lead_signals" in ancestry, "Current head lost the released 0084 chain"
    return head


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


async def _version(dsn: str) -> str:
    conn = await asyncpg.connect(dsn, timeout=5)
    try:
        return await conn.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await conn.close()


@asynccontextmanager
async def database_below_forward_only(dsn: str):
    """A disposable database built like CI's talky_alembic_test, stopped just below 0087."""
    revision = ScriptDirectory(str(BACKEND / "Alembic")).get_revision(FORWARD_ONLY)
    assert revision.down_revision == BELOW_FORWARD_ONLY, revision.down_revision
    name = f"talky_rt_{uuid4().hex[:12]}_test"
    target = urlunparse(urlparse(dsn)._replace(path=f"/{name}"))
    admin = await asyncpg.connect(dsn, timeout=5)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        try:
            conn = await asyncpg.connect(target, timeout=5)
            try:
                await conn.execute((BACKEND / "database" / "complete_schema.sql").read_text(encoding="utf-8"))
            finally:
                await conn.close()
            for step in (("stamp", "0021_billing_topup"), ("upgrade", BELOW_FORWARD_ONLY)):
                result = _alembic(target, *step)
                assert result.returncode == 0, result.stderr[-2000:]
            yield target
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def test_downgrade_then_upgrade_round_trips_and_refuses_with_records():
    async with database_below_forward_only(require_test_dsn()) as dsn:
        assert await _version(dsn) == BELOW_FORWARD_ONLY
        assert await _tables(dsn) == set(SUPPORT_TABLES)

        down = _alembic(dsn, "downgrade", "0081_admin_payment_merge")
        assert down.returncode == 0, down.stderr[-2000:]
        assert await _tables(dsn) == set()
        up = _alembic(dsn, "upgrade", BELOW_FORWARD_ONLY)
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
            await conn.close()


async def test_head_database_refuses_a_downgrade_past_the_forward_only_0087():
    dsn = require_test_dsn()
    head = _repository_head()
    assert await _version(dsn) == head
    before = await _tables(dsn)
    assert before == set(SUPPORT_TABLES)

    refused = _alembic(dsn, "downgrade", "0081_admin_payment_merge")
    assert refused.returncode != 0, refused.stdout[-2000:]
    # The whole downgrade is one transaction: nothing above 0087 stays undone either.
    assert await _version(dsn) == head, refused.stderr[-2000:]
    assert await _tables(dsn) == before


def test_repository_has_exactly_one_head():
    expected_head = _repository_head()
    result = subprocess.run([sys.executable, "-m", "alembic", "heads"], cwd=BACKEND, capture_output=True, text=True,
                            timeout=120, env={**os.environ, "DATABASE_URL": os.getenv("TEST_DATABASE_URL") or
                                              "postgresql://unused@127.0.0.1:1/unused_test"})
    assert result.returncode == 0, result.stdout + result.stderr
    heads = [line for line in result.stdout.splitlines() if line.strip()]
    assert heads == [f"{expected_head} (head)"], result.stdout + result.stderr

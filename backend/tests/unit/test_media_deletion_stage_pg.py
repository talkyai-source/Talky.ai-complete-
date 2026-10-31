"""The media-erasure stage update runs on real Postgres.

Bug hunt 2026-10-10 (db-schema-core-5): _mark_deletion_stage_on_connection
bound $2 both as ``SET status = $2`` (varchar(24), migration 0023) and in
``CASE WHEN $2 = 'object_deleted'`` (text). Postgres refuses to prepare it
(AmbiguousParameterError: inconsistent types deduced for parameter $2), and
it runs AFTER the irreversible object delete, so every admin erasure removed
the bytes and then failed to record object_deleted, and the 'failed' path hit
the same error. Every existing test monkeypatches this function, so none
executed the SQL.

Runs only with an explicit TEST_DATABASE_URL (an admin DSN on a throwaway
server). It creates and drops its own database.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from app.api.v1.endpoints.admin.media import _mark_deletion_stage_on_connection

# Column types as created by alembic 0023_admin_media_deletion_safety.
_DDL = """
CREATE TABLE admin_media_deletion_intents (
    id uuid PRIMARY KEY,
    status character varying(24) NOT NULL,
    last_error text,
    object_deleted_at timestamptz,
    updated_at timestamptz
)"""


def _admin_dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL is required")
    return dsn


async def _run(admin_dsn: str):
    dbname = f"bh_media_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    await admin.close()
    conn = await asyncpg.connect(f"{admin_dsn.rpartition('/')[0]}/{dbname}")
    try:
        await conn.execute(_DDL)
        results = {}
        for stage in ("object_deleted", "failed"):
            intent = uuid.uuid4()
            await conn.execute(
                "INSERT INTO admin_media_deletion_intents (id, status) VALUES ($1, 'intent_committed')", intent)
            await _mark_deletion_stage_on_connection(
                conn, intent, stage, error="synthetic storage error" if stage == "failed" else None)
            results[stage] = await conn.fetchrow(
                "SELECT status, object_deleted_at IS NOT NULL AS stamped, last_error "
                "FROM admin_media_deletion_intents WHERE id = $1", intent)
        return results
    finally:
        await conn.close()
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()


def test_both_erasure_stages_are_recorded():
    results = asyncio.run(_run(_admin_dsn_or_skip()))
    assert (results["object_deleted"]["status"], results["object_deleted"]["stamped"]) == ("object_deleted", True)
    assert (results["failed"]["status"], results["failed"]["stamped"]) == ("failed", False)
    assert results["failed"]["last_error"] == "synthetic storage error"

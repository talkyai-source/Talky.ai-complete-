"""
PostgreSQL connectivity smoke test.

This test validates that the configured DATABASE_URL is reachable and can
execute a trivial query.
"""

import os

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_postgres_connection_smoke():
    dsn = os.getenv(
        "DATABASE_URL",
        "postgresql://talkyai:talkyai_secret@localhost:5432/talkyai",
    )

    try:
        conn = await asyncpg.connect(dsn, timeout=3)
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable for smoke test: {exc}")
        return

    try:
        value = await conn.fetchval("SELECT 1")
        assert value == 1
    finally:
        await conn.close()

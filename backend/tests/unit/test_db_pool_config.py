"""Tests for the Phase 2.3 db pool config.

We don't spin up a real Postgres here; we patch asyncpg.create_pool so
the test verifies _what_ would be created and how the read-pool falls
back to the primary."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def reset_db_module():
    from app.core import db
    db._pool = None
    db._read_pool = None
    yield db
    db._pool = None
    db._read_pool = None


@pytest.mark.asyncio
async def test_init_db_pool_uses_env_for_sizes(monkeypatch, reset_db_module):
    db = reset_db_module
    monkeypatch.setenv("PG_POOL_MIN_SIZE", "7")
    monkeypatch.setenv("PG_POOL_MAX_SIZE", "33")
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/db")
    monkeypatch.delenv("READ_DATABASE_URL", raising=False)

    fake_pool = SimpleNamespace(close=AsyncMock())
    create_mock = AsyncMock(return_value=fake_pool)

    with patch("app.core.db.asyncpg.create_pool", create_mock):
        await db.init_db_pool()

    # Verify the primary pool was built with the env-supplied sizes.
    assert create_mock.await_args.kwargs["min_size"] == 7
    assert create_mock.await_args.kwargs["max_size"] == 33

    # No replica configured → read pool aliases the primary.
    assert db.get_read_pool() is db.get_pool()


@pytest.mark.asyncio
async def test_init_db_pool_creates_replica_when_configured(
    monkeypatch, reset_db_module
):
    db = reset_db_module
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/primary")
    monkeypatch.setenv("READ_DATABASE_URL", "postgresql://stub/replica")
    monkeypatch.setenv("PG_READ_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("PG_READ_POOL_MAX_SIZE", "9")

    primary = SimpleNamespace(close=AsyncMock(), id="primary")
    replica = SimpleNamespace(close=AsyncMock(), id="replica")
    create_mock = AsyncMock(side_effect=[primary, replica])

    with patch("app.core.db.asyncpg.create_pool", create_mock):
        await db.init_db_pool()

    # Two distinct pools were created.
    assert create_mock.await_count == 2
    assert db.get_pool() is primary
    assert db.get_read_pool() is replica
    # Read-pool sizing came from the dedicated env vars.
    assert create_mock.await_args_list[1].kwargs["min_size"] == 1
    assert create_mock.await_args_list[1].kwargs["max_size"] == 9


@pytest.mark.asyncio
async def test_pgbouncer_compat_env_disables_statement_cache(
    monkeypatch, reset_db_module
):
    db = reset_db_module
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/db")
    monkeypatch.setenv("PG_STATEMENT_CACHE_SIZE", "0")
    monkeypatch.delenv("READ_DATABASE_URL", raising=False)

    create_mock = AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))
    with patch("app.core.db.asyncpg.create_pool", create_mock):
        await db.init_db_pool()

    assert create_mock.await_args.kwargs["statement_cache_size"] == 0

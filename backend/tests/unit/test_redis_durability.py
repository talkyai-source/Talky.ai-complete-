"""T2.4 — Redis durability probe tests.

Exercises the four states:
  - No Redis client → probed=False, raw_error.
  - CONFIG GET raises → probed=False, raw_error.
  - AOF on, RDB off → durable.
  - AOF off, RDB on → durable.
  - Both off → not durable, warning in production only.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.core.redis_durability import DurabilityStatus, probe_redis_durability


class _FakeRedis:
    def __init__(self, *, appendonly: str, save: str, raise_on: str | None = None):
        self._appendonly = {"appendonly": appendonly}
        self._save = {"save": save}
        self._raise = raise_on

    async def config_get(self, key: str):
        if self._raise == key:
            raise RuntimeError("redis config_get failed")
        if key == "appendonly":
            return self._appendonly
        if key == "save":
            return self._save
        return {}


@pytest.mark.asyncio
async def test_probe_none_client():
    status = await probe_redis_durability(None)
    assert status.probed is False
    assert status.raw_error == "redis_client_is_none"


@pytest.mark.asyncio
async def test_probe_config_raises():
    r = _FakeRedis(appendonly="yes", save="3600 1", raise_on="appendonly")
    status = await probe_redis_durability(r)
    assert status.probed is False
    assert "config_get" in (status.raw_error or "")


@pytest.mark.asyncio
async def test_aof_on_is_durable():
    r = _FakeRedis(appendonly="yes", save="")
    status = await probe_redis_durability(r)
    assert status.probed is True
    assert status.aof_enabled is True
    assert status.rdb_snapshots_enabled is False
    assert status.is_durable() is True
    assert status.warning is None


@pytest.mark.asyncio
async def test_rdb_save_rule_is_durable():
    r = _FakeRedis(appendonly="no", save="3600 1 300 100")
    status = await probe_redis_durability(r)
    assert status.aof_enabled is False
    assert status.rdb_snapshots_enabled is True
    assert status.rdb_save_rules == "3600 1 300 100"
    assert status.is_durable() is True


@pytest.mark.asyncio
async def test_both_off_dev_no_warning(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    r = _FakeRedis(appendonly="no", save="")
    status = await probe_redis_durability(r)
    assert status.is_durable() is False
    assert status.warning is None  # dev: log only


@pytest.mark.asyncio
async def test_both_off_prod_emits_warning(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    r = _FakeRedis(appendonly="no", save="")
    status = await probe_redis_durability(r)
    assert status.is_durable() is False
    assert status.warning is not None
    assert "dialer jobs will be lost" in status.warning.lower()


@pytest.mark.asyncio
async def test_empty_quoted_save_rule_is_not_durable(monkeypatch: pytest.MonkeyPatch):
    """Redis returns `""` literally when the operator explicitly
    disabled snapshotting via `save ""`. Must not be treated as a
    real save rule."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    r = _FakeRedis(appendonly="no", save='""')
    status = await probe_redis_durability(r)
    assert status.rdb_snapshots_enabled is False
    assert status.warning is not None


@pytest.mark.asyncio
async def test_status_serializes_to_dict():
    r = _FakeRedis(appendonly="yes", save="3600 1")
    status = await probe_redis_durability(r)
    d = status.to_dict()
    assert d["probed"] is True
    assert d["aof_enabled"] is True
    assert d["rdb_save_rules"] == "3600 1"

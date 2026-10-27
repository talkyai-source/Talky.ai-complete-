"""Deliverable 4: verified TLS off-host and a restricted runtime role."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.core import db
from app.core.database_policy import (
    DatabasePolicyError,
    database_url_problems,
    enforce_database_url_policy,
    enforce_runtime_posture,
    unsafe_reasons,
    url_problems,
)

SECRET = "pa55-not-to-be-logged"
RESTRICTED = {"role": "talklee_app", "superuser": False, "bypass_rls": False, "create_role": False,
              "create_database": False, "owned_relations": 0, "tls": True}


class _Conn:
    def __init__(self, row=None, error=None):
        self.row, self.error = row, error

    async def fetchrow(self, sql):
        if self.error:
            raise self.error
        return self.row


class _Pool:
    def __init__(self, row=None, error=None):
        self.conn = _Conn(row, error)
        self.close = AsyncMock()

    @asynccontextmanager
    async def _lease(self):
        yield self.conn

    def acquire(self):
        return self._lease()


@pytest.mark.parametrize("url", [
    f"postgresql://talkyai:{SECRET}@127.0.0.1:5432/talkyai",
    f"postgresql://talkyai:{SECRET}@localhost/talkyai",
    f"postgresql+asyncpg://talkyai:{SECRET}@[::1]:5432/talkyai",
    "postgresql:///talkyai?host=/var/run/postgresql",
    f"postgresql://talkyai:{SECRET}@db.internal:5432/talkyai?sslmode=verify-full&sslrootcert=/etc/ca.pem",
    f"postgresql://talkyai:{SECRET}@10.0.0.5/talkyai?sslmode=verify-ca&sslrootcert=/etc/ca.pem",
])
def test_loopback_socket_or_verified_tls_is_allowed(url):
    assert url_problems("DATABASE_URL", url, {}) == []


@pytest.mark.parametrize("url,mode", [
    (f"postgresql://talkyai:{SECRET}@db.internal:5432/talkyai", "prefer"),
    (f"postgresql://talkyai:{SECRET}@10.0.0.5:5432/talkyai?sslmode=require", "require"),
    (f"postgresql+asyncpg://talkyai:{SECRET}@db.internal/talkyai?sslmode=disable", "disable"),
    (f"postgresql://talkyai:{SECRET}@127.0.0.1:5432,db.internal:5432/talkyai", "prefer"),
])
def test_remote_host_without_verified_tls_is_a_problem_and_never_leaks_the_password(url, mode):
    problems = url_problems("DATABASE_URL", url, {})
    assert len(problems) == 1
    assert "db.internal" in problems[0] or "10.0.0.5" in problems[0]
    assert f"sslmode={mode}" in problems[0]
    assert SECRET not in problems[0] and "talkyai:" not in problems[0]


def test_pgsslmode_environment_counts_when_the_url_has_no_sslmode():
    url = f"postgresql://talkyai:{SECRET}@db.internal/talkyai"
    assert url_problems("DATABASE_URL", url, {"PGSSLMODE": "verify-full"}) == []
    assert url_problems("DATABASE_URL", url, {"PGSSLMODE": "require"})


def test_production_requires_database_url_and_checks_the_read_url():
    assert database_url_problems({"ENVIRONMENT": "production"}) == [
        "DATABASE_URL is not set; the pool would fall back to the development default on localhost"]
    assert database_url_problems({"ENVIRONMENT": "development"}) == []
    problems = database_url_problems({
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql://a:b@127.0.0.1/talkyai",
        "READ_DATABASE_URL": f"postgresql://a:{SECRET}@replica.internal/talkyai",
    })
    assert len(problems) == 1 and problems[0].startswith("READ_DATABASE_URL connects to replica.internal")


def test_url_policy_refuses_in_production_and_only_warns_elsewhere(caplog):
    remote = f"postgresql://a:{SECRET}@db.internal/talkyai"
    with pytest.raises(DatabasePolicyError) as refused:
        enforce_database_url_policy({"ENVIRONMENT": "production", "DATABASE_URL": remote})
    assert SECRET not in str(refused.value)
    with caplog.at_level(logging.WARNING, logger="app.core.database_policy"):
        enforce_database_url_policy({"ENVIRONMENT": "development", "DATABASE_URL": remote})
    assert "db_url_policy_warning" in caplog.text and SECRET not in caplog.text


def test_unsafe_reasons_cover_every_privilege_and_table_ownership():
    assert unsafe_reasons(RESTRICTED) == []
    owner = dict(RESTRICTED, owned_relations=88)
    assert unsafe_reasons(owner) == ["owns 88 relations"]
    legacy = dict(RESTRICTED, superuser=True, bypass_rls=True, create_role=True, create_database=True)
    assert unsafe_reasons(legacy) == ["superuser", "bypass_rls", "create_role", "create_database"]


async def test_restricted_role_passes_and_is_logged(caplog):
    with caplog.at_level(logging.INFO, logger="app.core.database_policy"):
        posture = await enforce_runtime_posture(_Pool(RESTRICTED), pool_name="primary", environ={})
    assert posture == RESTRICTED
    assert "db_runtime_posture pool=primary role=talklee_app" in caplog.text
    assert "restricted=True" in caplog.text


async def test_table_owner_only_warns_until_the_restricted_role_is_required(caplog):
    owner = dict(RESTRICTED, role="talkyai", owned_relations=88)
    with caplog.at_level(logging.WARNING, logger="app.core.database_policy"):
        assert await enforce_runtime_posture(_Pool(owner), pool_name="primary", environ={}) == owner
    assert "db_runtime_role_not_restricted" in caplog.text
    with pytest.raises(DatabasePolicyError, match="owns 88 relations"):
        await enforce_runtime_posture(_Pool(owner), pool_name="primary",
                                      environ={"DATABASE_REQUIRE_RESTRICTED_ROLE": "true"})


async def test_probe_failure_never_breaks_boot_unless_the_role_is_required():
    broken = _Pool(error=OSError("connection reset"))
    assert await enforce_runtime_posture(broken, pool_name="read", environ={}) is None
    with pytest.raises(DatabasePolicyError, match="could not verify the read database role"):
        await enforce_runtime_posture(broken, pool_name="read",
                                      environ={"DATABASE_REQUIRE_RESTRICTED_ROLE": "1"})


async def test_init_db_pool_closes_the_pool_and_refuses_an_unrestricted_required_role(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@127.0.0.1/talkyai")
    monkeypatch.setenv("DATABASE_REQUIRE_RESTRICTED_ROLE", "true")
    monkeypatch.delenv("READ_DATABASE_URL", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    pool = _Pool(dict(RESTRICTED, superuser=True))
    with patch.object(db.asyncpg, "create_pool", AsyncMock(return_value=pool)):
        with pytest.raises(DatabasePolicyError, match="superuser"):
            await db.init_db_pool()
    pool.close.assert_awaited_once()
    assert db._pool is None


async def test_init_db_pool_refuses_unverified_remote_tls_before_connecting(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", f"postgresql://a:{SECRET}@db.internal/talkyai?sslmode=require")
    monkeypatch.delenv("READ_DATABASE_URL", raising=False)
    create_pool = AsyncMock()
    with patch.object(db.asyncpg, "create_pool", create_pool):
        with pytest.raises(DatabasePolicyError, match="db.internal"):
            await db.init_db_pool()
    create_pool.assert_not_called()


async def test_init_db_pool_keeps_todays_loopback_owner_role_running(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://talkyai:x@127.0.0.1:5432/talkyai")
    monkeypatch.delenv("DATABASE_REQUIRE_RESTRICTED_ROLE", raising=False)
    monkeypatch.delenv("READ_DATABASE_URL", raising=False)
    pool = _Pool(dict(RESTRICTED, role="talkyai", owned_relations=88))
    try:
        with patch.object(db.asyncpg, "create_pool", AsyncMock(return_value=pool)):
            assert await db.init_db_pool() is pool
        pool.close.assert_not_awaited()
    finally:
        db._pool = db._read_pool = None


def test_production_gate_reports_the_database_policy(monkeypatch):
    from app.core import prod_gate

    monkeypatch.setenv("DATABASE_URL", f"postgresql://a:{SECRET}@db.internal/talkyai")
    violations = prod_gate._check_database_policy()
    assert [v.rule for v in violations] == ["database_policy"]
    assert SECRET not in violations[0].detail

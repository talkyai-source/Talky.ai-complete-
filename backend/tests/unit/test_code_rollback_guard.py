"""F8-08: exercise the rollback gate using synthetic state and disposable Git history."""
from __future__ import annotations

from contextlib import asynccontextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_code_rollback.py"
SPEC = importlib.util.spec_from_file_location("qa_code_rollback_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)
SYNTHETIC_DSN = "postgresql://qa_user:synthetic-password@db.invalid/qa?sslmode=verify-full&sslrootcert=/qa/root.pem"


class _Connection:
    def __init__(self, *, head=guard.PAYG_HEAD, billing=True, audience=True):
        self.heads = [{"version_num": head}]
        self.columns = {"billing": billing, "audience": audience}
        self.hidden_columns = False
        self.relations = dict.fromkeys(("tenants", "plans", "packages", "version"), True)
        self.recovery = False
        self.tenants = False
        self.bundles = False
        self.observation_error = None
        self.data_error = None
        self.close_error = None
        self.queries = []
        self.closed = []
        self.transaction_options = None
        self.transaction_active = False

    @asynccontextmanager
    async def transaction(self, **kwargs):
        self.transaction_options = kwargs
        self.transaction_active = True
        try:
            yield
        finally:
            self.transaction_active = False

    async def execute(self, query):
        assert self.transaction_active
        self.queries.append(query)

    async def fetchrow(self, query):
        assert self.transaction_active
        self.queries.append(query)
        if " AS tenants" in query:
            return self.relations
        # information_schema exposes only columns the current role can access.
        # pg_attribute describes physical columns independently of those grants.
        if self.hidden_columns and "information_schema.columns" in query:
            return {"billing": False, "audience": False}
        return self.columns

    async def fetch(self, query):
        self.queries.append(query)
        return self.heads

    async def fetchval(self, query):
        assert self.transaction_active
        self.queries.append(query)
        if self.observation_error:
            raise self.observation_error
        if "pg_is_in_recovery" in query:
            return self.recovery
        if "public.tenants" in query:
            if self.data_error:
                raise self.data_error
            return self.tenants
        if "public.topup_packages" in query:
            if self.data_error:
                raise self.data_error
            return self.bundles
        raise AssertionError(query)

    async def close(self, *, timeout):
        self.closed.append(timeout)
        if self.close_error:
            raise self.close_error


def _connect(monkeypatch, conn):
    calls = []

    async def connect(dsn, **kwargs):
        calls.append((dsn, kwargs))
        return conn

    monkeypatch.setattr(guard.asyncpg, "connect", connect)
    return calls


@pytest.mark.asyncio
async def test_primary_probe_preserves_tls_and_uses_bounded_read_only_unfiltered_queries(monkeypatch):
    conn = _Connection()
    calls = _connect(monkeypatch, conn)

    state = await guard.read_payg_state(SYNTHETIC_DSN)

    assert state == guard.PaygState("payg", None, None)
    assert calls == [(SYNTHETIC_DSN, {"timeout": 10, "command_timeout": 10})]
    assert conn.transaction_options == {"readonly": True}
    assert conn.queries[:3] == [
        "SET LOCAL statement_timeout = '5000ms'",
        "SET LOCAL lock_timeout = '1000ms'",
        "SET LOCAL row_security = off",
    ]
    assert conn.closed == [5]
    assert not conn.transaction_active


@pytest.mark.asyncio
async def test_known_payg_schema_requires_no_privileged_tenant_reads(monkeypatch):
    conn = _Connection()
    conn.data_error = PermissionError("synthetic restricted runtime role")
    _connect(monkeypatch, conn)

    assert await guard.read_payg_state(SYNTHETIC_DSN) == guard.PaygState("payg", None, None)
    assert not any("FROM public.tenants" in query or "FROM public.topup_packages" in query for query in conn.queries)


@pytest.mark.asyncio
async def test_pre_payg_schema_still_requires_unfiltered_data_visibility(monkeypatch):
    conn = _Connection(head=guard.PRE_PAYG_HEAD, billing=False, audience=False)
    conn.data_error = PermissionError("synthetic restricted runtime role")
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="Database observation failed"):
        await guard.read_payg_state(SYNTHETIC_DSN)


@pytest.mark.asyncio
async def test_clean_pre_payg_schema_records_proved_absence(monkeypatch):
    conn = _Connection(head=guard.PRE_PAYG_HEAD, billing=False, audience=False)
    _connect(monkeypatch, conn)
    assert await guard.read_payg_state(SYNTHETIC_DSN) == guard.PaygState("pre_payg", False, False)


@pytest.mark.asyncio
async def test_asyncpg_scheme_normalization_keeps_tls_options(monkeypatch):
    calls = _connect(monkeypatch, _Connection())
    await guard.read_payg_state(SYNTHETIC_DSN.replace("postgresql://", "postgresql+asyncpg://"))
    assert calls[0][0] == SYNTHETIC_DSN


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", [True, None, 0])
async def test_standby_or_unknown_primary_identity_refuses(monkeypatch, recovery):
    conn = _Connection()
    conn.recovery = recovery
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="Authoritative primary"):
        await guard.read_payg_state(SYNTHETIC_DSN)
    assert conn.closed == [5]


@pytest.mark.asyncio
@pytest.mark.parametrize("head,billing,audience", [
    (guard.PRE_PAYG_HEAD, True, False),
    (guard.PRE_PAYG_HEAD, False, True),
    (guard.PRE_PAYG_HEAD, True, True),
    (guard.PAYG_HEAD, False, True),
    (guard.PAYG_HEAD, True, False),
    (guard.PAYG_HEAD, False, False),
    ("unreviewed-head", True, True),
])
async def test_unknown_or_mixed_schema_is_refused(monkeypatch, head, billing, audience):
    conn = _Connection(head=head, billing=billing, audience=audience)
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="Unknown or mixed"):
        await guard.read_payg_state(SYNTHETIC_DSN)


@pytest.mark.asyncio
async def test_privilege_hidden_partial_column_cannot_authorize_legacy_rollback(monkeypatch):
    conn = _Connection(head=guard.PRE_PAYG_HEAD, billing=True, audience=False)
    conn.hidden_columns = True
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="Unknown or mixed"):
        await guard.read_payg_state(SYNTHETIC_DSN)


@pytest.mark.asyncio
@pytest.mark.parametrize("heads", [[], [{"version_num": guard.PRE_PAYG_HEAD}, {"version_num": guard.PAYG_HEAD}]])
async def test_absent_or_multiple_schema_heads_refuse(monkeypatch, heads):
    conn = _Connection()
    conn.heads = heads
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="Exactly one"):
        await guard.read_payg_state(SYNTHETIC_DSN)


@pytest.mark.asyncio
@pytest.mark.parametrize("relation", ["tenants", "plans", "packages", "version"])
async def test_missing_required_relation_refuses(monkeypatch, relation):
    conn = _Connection()
    conn.relations[relation] = False
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="Required database schema"):
        await guard.read_payg_state(SYNTHETIC_DSN)


@pytest.mark.asyncio
@pytest.mark.parametrize("tenants,bundles", [(True, False), (False, True), (True, True)])
async def test_pre_payg_revision_with_payg_records_refuses(monkeypatch, tenants, bundles):
    conn = _Connection(head=guard.PRE_PAYG_HEAD, billing=False, audience=False)
    conn.tenants, conn.bundles = tenants, bundles
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="contradict"):
        await guard.read_payg_state(SYNTHETIC_DSN)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenants", "bundles"])
async def test_unknown_data_visibility_refuses(monkeypatch, field):
    conn = _Connection(head=guard.PRE_PAYG_HEAD, billing=False, audience=False)
    setattr(conn, field, None)
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="visibility"):
        await guard.read_payg_state(SYNTHETIC_DSN)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(SYNTHETIC_DSN), RuntimeError(SYNTHETIC_DSN)])
async def test_database_or_rls_failure_is_sanitized_and_connection_closed(monkeypatch, error):
    conn = _Connection()
    conn.observation_error = error
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="Database observation failed") as raised:
        await guard.read_payg_state(SYNTHETIC_DSN)
    assert "synthetic-password" not in str(raised.value)
    assert conn.closed == [5]


@pytest.mark.asyncio
async def test_connection_cleanup_failure_is_sanitized(monkeypatch):
    conn = _Connection()
    conn.close_error = RuntimeError(SYNTHETIC_DSN)
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused) as raised:
        await guard.read_payg_state(SYNTHETIC_DSN)
    assert "synthetic-password" not in str(raised.value)


@pytest.mark.asyncio
async def test_connection_cleanup_failure_preserves_primary_refusal(monkeypatch):
    conn = _Connection()
    conn.recovery = True
    conn.close_error = RuntimeError(SYNTHETIC_DSN)
    _connect(monkeypatch, conn)
    with pytest.raises(guard.RollbackRefused, match="Authoritative primary"):
        await guard.read_payg_state(SYNTHETIC_DSN)


def _git(repo, *args):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=QA", "-c", "user.email=qa@example.invalid", *args],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    repo = tmp_path_factory.mktemp("rollback-git")
    _git(repo, "init", "--quiet")
    commits = []
    for label in ("legacy", "payg-introduction", "compatible"):
        _git(repo, "commit", "--quiet", "--allow-empty", "--no-gpg-sign", "-m", label)
        commits.append(_git(repo, "rev-parse", "HEAD"))
    return repo, *commits


@pytest.fixture
def trusted_history(history, monkeypatch):
    monkeypatch.setattr(guard, "PAYG_INTRODUCTION", history[2])
    return history


@pytest.mark.parametrize("tenants,bundles", [(False, False), (True, False), (False, True), (True, True), (None, None)])
def test_all_legacy_targets_refused_on_payg_schema_even_when_no_payg_rows(trusted_history, tenants, bundles):
    repo, legacy, _, _ = trusted_history
    with pytest.raises(guard.RollbackRefused, match="Legacy target is incompatible"):
        guard.verify_target(repo, legacy, guard.PaygState("payg", tenants, bundles))


@pytest.mark.parametrize("target_index", [2, 3])
def test_introduction_and_its_descendant_accept_payg_schema(trusted_history, target_index):
    guard.verify_target(trusted_history[0], trusted_history[target_index], guard.PaygState("payg", True, True))


def test_legacy_target_accepts_only_clean_pre_payg_state(trusted_history):
    repo, legacy, _, _ = trusted_history
    guard.verify_target(repo, legacy, guard.PaygState("pre_payg", False, False))
    for state in (guard.PaygState("unknown", False, False), guard.PaygState("pre_payg", True, False)):
        with pytest.raises(guard.RollbackRefused):
            guard.verify_target(repo, legacy, state)


@pytest.mark.parametrize("tenants,bundles", [(None, None), (False, None), (None, False)])
def test_unobserved_pre_payg_data_cannot_authorize_legacy_target(trusted_history, tenants, bundles):
    repo, legacy, _, _ = trusted_history
    with pytest.raises(guard.RollbackRefused):
        guard.verify_target(repo, legacy, guard.PaygState("pre_payg", tenants, bundles))


def test_missing_trusted_history_refuses(trusted_history, monkeypatch):
    repo, _, _, compatible = trusted_history
    monkeypatch.setattr(guard, "PAYG_INTRODUCTION", "e" * 40)
    with pytest.raises(guard.RollbackRefused, match="introduction commit is unavailable"):
        guard.verify_target(repo, compatible, guard.PaygState("payg", False, False))


@pytest.mark.parametrize("target", ["HEAD", "--help", "abc123", "f" * 40])
def test_invalid_or_missing_target_never_opens_database(trusted_history, tmp_path, monkeypatch, capsys, target):
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(f"DATABASE_URL={SYNTHETIC_DSN}\n", encoding="utf-8")
    observed = []

    async def observe(dsn):
        observed.append(dsn)
        return guard.PaygState("payg", False, False)

    monkeypatch.setattr(guard, "read_payg_state", observe)
    assert guard.main(["--repo", str(trusted_history[0]), f"--target-sha={target}", "--env-file", str(env_file)]) == 1
    assert observed == []
    assert "synthetic-password" not in capsys.readouterr().err


def test_cli_uses_explicit_primary_env_file_without_inherited_or_replica_dsn(trusted_history, tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(
        f"MIGRATION_DATABASE_URL={SYNTHETIC_DSN}\nDATABASE_URL=postgresql://fallback.invalid/qa\nREAD_DATABASE_URL=postgresql://replica.invalid/qa\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://inherited.invalid/qa")
    observed = []

    async def observe(dsn):
        observed.append(dsn)
        return guard.PaygState("payg", False, False)

    monkeypatch.setattr(guard, "read_payg_state", observe)
    assert guard.main(["--repo", str(trusted_history[0]), "--target-sha", trusted_history[3], "--env-file", str(env_file)]) == 0
    assert observed == [SYNTHETIC_DSN]
    assert "synthetic-password" not in capsys.readouterr().out


def test_git_timeout_is_a_sanitized_refusal(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(SYNTHETIC_DSN, 10)

    monkeypatch.setattr(guard.subprocess, "run", timeout)
    with pytest.raises(guard.RollbackRefused, match="Repository compatibility") as raised:
        guard.verify_target(tmp_path, "f" * 40, guard.PaygState("payg", False, False))
    assert "synthetic-password" not in str(raised.value)

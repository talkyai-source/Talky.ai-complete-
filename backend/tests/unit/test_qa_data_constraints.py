"""F2-04 dry-run behavior and migration guards; PostgreSQL proof is separate."""
from __future__ import annotations

from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import sys
from uuid import UUID

import pytest


BACKEND = Path(__file__).resolve().parents[2]
REVISION = BACKEND / "Alembic/versions/0088_qa_data_constraints.py"
SCRIPT = BACKEND / "scripts/check_data_constraints.py"
TENANT_A = UUID("00000000-0000-0000-0000-000000000031")
TENANT_B = UUID("00000000-0000-0000-0000-000000000032")
CAMPAIGN_STATES = {"draft", "scheduled", "running", "paused", "completed", "cancelled", "stopped", "deleted"}
LEAD_STATES = {"pending", "calling", "called", "contacted", "completed", "failed", "dnc", "deleted", "skipped"}
MINUTE_COLUMNS = {
    ("plans", "minutes"), ("tenants", "minutes_allocated"), ("tenants", "minutes_used"),
    ("tenant_call_limits", "monthly_minutes_allocated"),
    ("tenant_call_limits", "monthly_minutes_used"),
}


def load(path, name):
    assert path.is_file(), f"F2-04 capability missing: {path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration():
    return load(REVISION, "qa_data_constraints_migration")


@pytest.fixture
def checker():
    return load(SCRIPT, "qa_data_constraints_checker")


def test_revision_is_linear_and_registry_excludes_signed_financial_values(migration):
    assert migration.revision == "0088_qa_data_constraints"
    assert migration.down_revision == "0087_bootstrap_manual_parity"
    assert len(migration.revision) <= 32
    checks = migration.CHECKS
    assert {(c[0], c[1]) for c in checks} == MINUTE_COLUMNS | {("campaigns", "status"), ("leads", "status")}
    assert len({c[2] for c in checks}) == 7
    assert all("billing_ledger" not in str(c) and "balance" not in str(c) for c in checks)
    assert set(migration.CAMPAIGN_STATES) == CAMPAIGN_STATES
    assert set(migration.LEAD_STATES) == LEAD_STATES


def test_migration_uses_not_valid_and_does_not_clean_or_validate_existing_rows(migration, monkeypatch):
    issued = []
    monkeypatch.setattr(migration.op, "execute", lambda sql: issued.append(str(sql)))
    migration.upgrade()
    sql = "\n".join(issued).upper()
    assert "NOT VALID" in sql
    assert "::REGCLASS" not in sql, "DO-block catalog lookups must use to_regclass"
    assert "VALIDATE CONSTRAINT" not in sql
    assert "DELETE FROM" not in sql and "UPDATE PUBLIC." not in sql
    assert "LOCK_TIMEOUT" in sql
    assert "PG_INDEX" in sql and "INDISVALID" in sql and "INDISREADY" in sql
    assert "INDPRED" in sql and "INDEXPRS" in sql and "INDNKEYATTS" in sql
    assert "CREATE INDEX IF NOT EXISTS" in sql


class CountConnection:
    def __init__(self, *, denied=False):
        self.denied = denied
        self.queries = []
        self.transactions = []
        self.active = False

    @asynccontextmanager
    async def transaction(self, **options):
        self.transactions.append(options)
        assert options == {"readonly": True, "isolation": "repeatable_read"}
        self.active = True
        try:
            yield self
        finally:
            self.active = False

    async def execute(self, query, *args):
        assert self.active
        normalized = " ".join(query.lower().split())
        assert normalized.startswith("set local "), f"Dry-run attempted mutation: {query}"
        self.queries.append((normalized, args))
        return "SET"

    async def fetch(self, query, *args):
        assert self.active
        normalized = " ".join(query.lower().split())
        self.queries.append((normalized, args))
        if "pg_constraint" in normalized:
            return []
        assert "from public.tenants" in normalized and "order by id" in normalized and "limit" in normalized
        # Platform directory enumeration is the sole global customer lookup.
        assert "where" in normalized and "id >" in normalized
        assert len(args) == 2 and 1 <= args[1] <= 500
        return [{"id": value} for value in (TENANT_A, TENANT_B) if args[0] is None or value > args[0]]

    async def fetchval(self, query, *args):
        assert self.active
        normalized = " ".join(query.lower().split())
        self.queries.append((normalized, args))
        if self.denied:
            raise RuntimeError("synthetic unfiltered row-security refusal")
        assert "count(*)" in normalized and " is false" in normalized
        if 'from public."plans"' in normalized:
            assert not args
            return 2
        assert len(args) == 1 and args[0] in {TENANT_A, TENANT_B}
        expected = '"id" = $1' if 'from public."tenants"' in normalized else '"tenant_id" = $1'
        assert expected in normalized, "Every customer count needs an explicit tenant predicate"
        return int(args[0] == TENANT_B)


async def test_tenant_dry_run_counts_only_requested_scope_without_data_mutation(checker):
    conn = CountConnection()
    report = await checker.collect_report(conn, tenant_id=TENANT_A)
    assert report["mode"] == "dry_run"
    assert report["tenant_count"] == 1
    assert len(report["constraints"]) == 7
    assert sum(row["violations"] for row in report["constraints"]) == 2
    assert report["missing_fk_indexes"] == []
    assert not any("order by id" in q for q, _ in conn.queries)
    assert any("row_security" in q and "off" in q for q, _ in conn.queries)
    assert not conn.active


async def test_all_tenants_counts_add_each_scope_once_and_global_plan_once(checker):
    conn = CountConnection()
    report = await checker.collect_report(conn, tenant_id=None)
    assert report["tenant_count"] == 2
    assert sorted(row["violations"] for row in report["constraints"]) == [1, 1, 1, 1, 1, 1, 2]
    assert sum('from public."plans"' in q for q, _ in conn.queries) == 1
    assert all("sample" not in row for row in report["constraints"])
    serialized = json.dumps(report)
    assert str(TENANT_A) not in serialized and str(TENANT_B) not in serialized


async def test_count_failure_is_not_reported_as_zero_or_partial_success(checker):
    conn = CountConnection(denied=True)
    with pytest.raises(RuntimeError, match="row-security refusal"):
        await checker.collect_report(conn, tenant_id=TENANT_A)
    assert not conn.active


@pytest.mark.parametrize("argv", [[], ["--tenant-id", "not-a-uuid"], ["--all-tenants", "--apply"], ["--all-tenants", "--tenant-id", str(TENANT_A)]])
def test_cli_requires_explicit_scope_and_has_no_apply_mode(checker, argv):
    with pytest.raises(SystemExit) as exc:
        checker.parse_args(argv)
    assert exc.value.code == 2


async def test_cli_connects_only_explicit_env_dsn_and_sanitizes_failures(checker, monkeypatch, capsys):
    dsn = "postgresql://qa:synthetic-password-never-print@127.0.0.1:15439/qa_constraints_test?sslmode=require"
    monkeypatch.setenv("QA_CONSTRAINT_TEST_DSN", dsn)
    calls = []

    async def failing_connect(value, **kwargs):
        calls.append((value, kwargs))
        raise RuntimeError(f"connection refused {dsn}")

    monkeypatch.setattr(checker.asyncpg, "connect", failing_connect)
    code = await checker.run(["--all-tenants", "--dsn-env", "QA_CONSTRAINT_TEST_DSN"])
    assert code != 0
    assert calls == [(dsn, {"timeout": 5, "command_timeout": 60})]
    output = capsys.readouterr()
    assert "synthetic-password" not in output.out + output.err
    assert dsn not in output.out + output.err
    assert "failed" in (output.out + output.err).lower()

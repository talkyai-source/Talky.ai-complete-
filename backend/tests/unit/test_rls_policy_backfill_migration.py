from __future__ import annotations

import importlib
import re
from pathlib import Path
from textwrap import dedent

import pytest

from scripts import rls_acquire_inventory as inventory

EXPECTED_POLICY_TABLES = {
    "abuse_detection_rules",
    "abuse_events",
    "action_plans",
    "ai_config_migrations",
    "assistant_actions",
    "assistant_conversations",
    "audit_logs",
    "call_guard_decisions",
    "call_velocity_snapshots",
    "clients",
    "cloned_voices",
    "connector_accounts",
    "dialer_jobs",
    "dnc_entries",
    "invoices",
    "meetings",
    "recordings",
    "refresh_tokens",
    "reminders",
    "secret_access_log",
    "security_events",
    "subscriptions",
    "tenant_ai_configs",
    "tenant_call_limits",
    "tenant_quota_usage",
    "tenant_quotas",
    "tenant_secrets",
    "tenant_settings",
    "tenant_users",
    "transcripts",
    "usage_records",
    "user_permissions",
    "user_profiles",
    "webhook_configs",
    "white_label_partners",
}

ALREADY_CANONICAL_TABLES = {
    "campaign_knowledge_nodes",
    "campaign_knowledge_sources",
}

BOOTSTRAP_REPAIR_TABLES = {
    "cloned_voices",
    "refresh_tokens",
    "secret_access_log",
    "tenant_secrets",
    "webhook_configs",
}

LEGACY_BOOTSTRAP_POLICY_TABLES = {
    "calls",
    "campaigns",
    "connectors",
    "conversations",
    "leads",
}

EXPECTED_REFRESH_REASONS = {
    "admin",
    "expired",
    "expired_with_subsequent_use",
    "logout",
    "mfa_disabled",
    "password_change",
    "password_reset",
    "reuse_detected",
    "rotated",
}


class _ScalarResult:
    def __init__(self, value: str) -> None:
        self._value = value

    def scalar(self) -> str:
        return self._value


class _Connection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict | None]] = []

    def execute(self, value, parameters=None):
        sql = " ".join(str(value).split())
        self.statements.append((sql, parameters))
        if "current_setting('app.bypass_rls'" in sql:
            return _ScalarResult("off")
        if "current_setting('lock_timeout'" in sql:
            return _ScalarResult("0")
        return _ScalarResult("")


def _load_migration():
    return importlib.import_module("Alembic.versions.0038_tenant_table_rls_backfill")


def _bootstrap_table_ddl(schema: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\((.*?)\n\);",
        schema,
        re.DOTALL,
    )
    assert match is not None, f"complete_schema.sql does not define {table}"
    return " ".join(match.group(1).split())


def test_upgrade_installs_one_strict_forced_policy_on_every_unprotected_table(
    monkeypatch,
):
    migration = _load_migration()
    connection = _Connection()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.upgrade()

    assert migration.down_revision == "0037_inbound_duration_quarantine"
    assert set(migration.POLICY_TABLES) == EXPECTED_POLICY_TABLES
    assert set(migration.ALREADY_CANONICAL_TABLES) == ALREADY_CANONICAL_TABLES
    assert len(migration.POLICY_TABLES) == 35

    for table in EXPECTED_POLICY_TABLES | ALREADY_CANONICAL_TABLES:
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in statements

    policy_statements = [sql for sql in statements if sql.startswith("CREATE POLICY")]
    assert len(policy_statements) == len(EXPECTED_POLICY_TABLES)
    for table in EXPECTED_POLICY_TABLES:
        policy = next(
            sql
            for sql in policy_statements
            if sql.startswith(f"CREATE POLICY {table}_tenant_isolation ")
        )
        assert f"ON public.{table} FOR ALL" in policy
        assert "USING (" in policy
        assert "WITH CHECK (" in policy
        assert "current_setting('app.bypass_rls', TRUE)" in policy
        assert "current_setting('app.current_tenant_id', TRUE)" in policy
        assert "tenant_id IS NULL" not in policy

    # Existing policies on the 35 expected-empty tables are unknown semantics,
    # not something a tenant-boundary migration may silently overwrite.
    ownership_guard = next(sql for sql in statements if "unexpected existing RLS policies" in sql)
    assert "unexpected table/tenant_id catalog shape" in ownership_guard
    assert "format_type(a.atttypid, a.atttypmod)" in ownership_guard
    assert "tenant_id_type IS DISTINCT FROM 'uuid'" in ownership_guard
    for table in EXPECTED_POLICY_TABLES:
        assert table in ownership_guard
    assert statements.index(ownership_guard) < next(
        index for index, sql in enumerate(statements) if sql.startswith("ALTER TABLE public.")
    )
    postcondition = next(sql for sql in statements if "tenant RLS postcondition failed" in sql)
    assert statements.index(postcondition) > max(
        index for index, sql in enumerate(statements) if sql.startswith("CREATE POLICY")
    )
    assert "set_config('lock_timeout', :prior, TRUE)" in connection.statements[-2][0]
    assert connection.statements[-2][1] == {"prior": "0"}
    assert "set_config('app.bypass_rls', :prior, TRUE)" in connection.statements[-1][0]
    assert connection.statements[-1][1] == {"prior": "off"}


def test_supported_bootstrap_defines_every_pre_0008_policy_target():
    migration = _load_migration()
    backend_root = Path(__file__).resolve().parents[2]
    schema = (backend_root / "database" / "complete_schema.sql").read_text(encoding="utf-8")
    normalized = " ".join(schema.split())

    # ai_config_migrations is intentionally introduced after the stamp floor by
    # 0033. Every other 0038 target must exist in complete_schema.sql itself.
    post_stamp_tables = {"ai_config_migrations"}
    for table in set(migration.POLICY_TABLES) - post_stamp_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in schema
    repair_tables = set(migration.POLICY_TABLES) - {
        match.group(1)
        for match in inventory._CREATE_TABLE_SQL_RE.finditer(
            schema.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE")
        )
    }
    assert repair_tables == post_stamp_tables
    migration_0033 = (
        backend_root / "Alembic" / "versions" / "0033_bootstrap_contract_repair.py"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS ai_config_migrations" in migration_0033

    preserved = (backend_root / "database" / "schema" / "baseline_2026-06-02.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE TABLE IF NOT EXISTS cloned_voices (" in preserved
    assert "CONSTRAINT uq_cloned_voices_voice_id UNIQUE (voice_id)" in preserved
    assert "CREATE INDEX IF NOT EXISTS idx_cloned_voices_tenant" in preserved

    for table in BOOTSTRAP_REPAIR_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in schema

    refresh = _bootstrap_table_ddl(schema, "refresh_tokens")
    assert "REFERENCES user_profiles(id) ON DELETE CASCADE" in refresh
    assert "CONSTRAINT chk_rt_expires_after_issued" in refresh
    reason_constraint = normalized[
        normalized.index("ADD CONSTRAINT refresh_tokens_revoked_reason_check") : normalized.index(
            "VALIDATE CONSTRAINT refresh_tokens_revoked_reason_check"
        )
    ]
    assert set(re.findall(r"'([^']+)'::text", reason_constraint)) == EXPECTED_REFRESH_REASONS
    assert "NOT VALID" in reason_constraint
    assert "idx_rt_user_active" in schema

    clones = _bootstrap_table_ddl(schema, "cloned_voices")
    assert "consent_at TIMESTAMPTZ NOT NULL" in clones
    assert "CONSTRAINT uq_cloned_voices_voice_id UNIQUE (voice_id)" in clones
    trigger_loop = normalized[normalized.index("FOR t IN SELECT t.table_name") :]
    assert "'cloned_voices'" in trigger_loop.split("END $$;", 1)[0]

    secrets = _bootstrap_table_ddl(schema, "tenant_secrets")
    assert "encrypted_value BYTEA NOT NULL" in secrets
    assert "encrypted_dek BYTEA NOT NULL" in secrets
    assert "CONSTRAINT tenant_secrets_tenant_id_secret_name_is_active_key" in secrets
    assert "idx_tenant_secrets_expires" in schema

    access_log = _bootstrap_table_ddl(schema, "secret_access_log")
    assert "REFERENCES tenant_secrets(secret_id)" in access_log
    assert "presented_permission VARCHAR(50)" in access_log

    webhooks = _bootstrap_table_ddl(schema, "webhook_configs")
    assert "CONSTRAINT webhook_configs_tenant_id_webhook_name_key" in webhooks
    assert "REFERENCES tenants(id) ON DELETE CASCADE" in webhooks

    for table in LEGACY_BOOTSTRAP_POLICY_TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in schema
        assert f"CREATE POLICY {table}_tenant_isolation ON {table}" in schema


def test_upgrade_does_not_mask_policy_ddl_failure_with_restore_sql(monkeypatch):
    migration = _load_migration()
    connection = _Connection()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    def fail_on_first_ddl(_value):
        raise RuntimeError("simulated policy DDL failure")

    monkeypatch.setattr(migration.op, "execute", fail_on_first_ddl)

    with pytest.raises(RuntimeError, match="simulated policy DDL failure"):
        migration.upgrade()

    sql = [statement for statement, _parameters in connection.statements]
    assert "set_config('app.bypass_rls', 'on', TRUE)" in sql[2]
    assert "set_config('lock_timeout', '5s', TRUE)" in sql[3]
    assert all(":prior" not in statement for statement in sql)


def test_static_inventory_has_no_tenant_table_without_a_policy_source():
    backend_root = Path(__file__).resolve().parents[2]

    rls_tables, tenant_scoped_tables = inventory.discover_rls_tables(backend_root)

    assert tenant_scoped_tables - rls_tables == set()


def test_static_inventory_depends_on_the_runtime_migration(monkeypatch):
    backend_root = Path(__file__).resolve().parents[2]
    original_read = inventory._read

    def read_without_0038(path: Path) -> str:
        if path.name == "0038_tenant_table_rls_backfill.py":
            return ""
        return original_read(path)

    monkeypatch.setattr(inventory, "_read", read_without_0038)

    rls_tables, tenant_scoped_tables = inventory.discover_rls_tables(backend_root)

    assert tenant_scoped_tables - rls_tables == EXPECTED_POLICY_TABLES


def test_schema_discovery_ignores_non_runtime_evidence(tmp_path):
    (tmp_path / "database" / "tests").mkdir(parents=True)
    (tmp_path / "database" / "docs").mkdir()
    (tmp_path / "database" / "generated").mkdir()
    (tmp_path / "Alembic" / "versions").mkdir(parents=True)
    (tmp_path / "database" / "schema.sql").write_text(
        """
        CREATE TABLE tenant_rows (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL
        );
        """,
        encoding="utf-8",
    )
    evidence = "ALTER TABLE public.tenant_rows ENABLE ROW LEVEL SECURITY;"
    (tmp_path / "database" / "tests" / "claim.sql").write_text(
        evidence,
        encoding="utf-8",
    )
    (tmp_path / "database" / "docs" / "claim.sql").write_text(
        evidence,
        encoding="utf-8",
    )
    (tmp_path / "database" / "generated" / "claim.sql").write_text(
        evidence,
        encoding="utf-8",
    )

    rls_tables, tenant_scoped_tables = inventory.discover_rls_tables(tmp_path)

    assert tenant_scoped_tables == {"tenant_rows"}
    assert rls_tables == set()

    (tmp_path / "Alembic" / "versions" / "0001_runtime.py").write_text(
        dedent(
            """
            _RLS_TABLES = ("model_rows",)

            def _canonical_rls(table):
                op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table}")

            def upgrade():
                op.create_table(
                    "model_rows",
                    sa.Column("id", sa.UUID(), primary_key=True),
                    sa.Column("tenant_id", sa.UUID(), nullable=False),
                )
                for table in _RLS_TABLES:
                    _canonical_rls(table)
                op.execute("ALTER TABLE public.tenant_rows ENABLE ROW LEVEL SECURITY")
            """
        ),
        encoding="utf-8",
    )

    rls_tables, tenant_scoped_tables = inventory.discover_rls_tables(tmp_path)

    assert tenant_scoped_tables == {"model_rows", "tenant_rows"}
    assert rls_tables == {"model_rows", "tenant_rows"}


def test_downgrade_refuses_to_reopen_the_tenant_boundary():
    migration = _load_migration()

    with pytest.raises(RuntimeError, match="forward-only tenant-isolation boundary"):
        migration.downgrade()

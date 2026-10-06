"""Migration 0048 shape: next head after 0047, keeps the audit-function
contract 0033 verifies, and downgrades to the exact pre-0048 body.

Behaviour against a real Postgres is in
tests/integration/test_audit_heartbeat_skip.py.
"""
from __future__ import annotations

import importlib

MIGRATION = importlib.import_module("Alembic.versions.0048_audit_skip_heartbeat_updates")


def test_0048_follows_0047():
    assert MIGRATION.revision == "0048_audit_skip_heartbeat_updates"
    assert MIGRATION.down_revision == "0047_protect_ai_config_backup"


def test_the_function_contract_0033_checks_is_unchanged():
    for sql in (MIGRATION.FUNCTION_SQL, MIGRATION.PREVIOUS_FUNCTION_SQL):
        assert "FUNCTION public.log_tenant_policy_mutation()" in sql
        assert "RETURNS trigger" in sql


def test_only_the_heartbeat_columns_are_skipped_and_only_for_updates():
    sql = MIGRATION.FUNCTION_SQL
    assert MIGRATION.HEARTBEAT_COLUMNS == ("live_status_checked_at", "updated_at")
    assert "IF TG_OP = 'UPDATE' AND changed_cols" in sql
    assert "<@ ARRAY['live_status_checked_at', 'updated_at']::TEXT[]" in sql
    # The skip happens before the INSERT, and the INSERT is otherwise unchanged.
    assert sql.index("<@ ARRAY[") < sql.index("INSERT INTO public.tenant_policy_audit_log")
    assert "'db_trigger'" in sql


def test_downgrade_restores_the_pre_0048_body():
    assert "<@ ARRAY[" not in MIGRATION.PREVIOUS_FUNCTION_SQL
    assert "INSERT INTO public.tenant_policy_audit_log" in MIGRATION.PREVIOUS_FUNCTION_SQL

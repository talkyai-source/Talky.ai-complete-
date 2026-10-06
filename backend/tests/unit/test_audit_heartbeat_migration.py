"""Migration 0048 shape: next head after 0047, keeps the audit-function
contract 0033 verifies, and downgrades to the exact pre-0048 body.

Behaviour against a real Postgres is in
tests/integration/test_audit_heartbeat_skip.py.
"""
from __future__ import annotations

import importlib

MIGRATION = importlib.import_module("Alembic.versions.0048_audit_skip_heartbeat")


def test_0048_follows_0047():
    assert MIGRATION.revision == "0048_audit_skip_heartbeat"
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


def test_every_revision_id_fits_the_alembic_version_column():
    """alembic_version.version_num is VARCHAR(32) on production. The first
    0048 id was 33 characters: `alembic upgrade` failed on the version
    UPDATE and rolled the migration back (2026-10-07). Three older ids are
    exactly 32, so the next long name would have hit the same wall."""
    import re
    from pathlib import Path

    versions = Path(__file__).resolve().parents[2] / "Alembic" / "versions"
    too_long = []
    for path in versions.glob("*.py"):
        match = re.search(r'^revision[^=]*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
        if match and len(match.group(1)) > 32:
            too_long.append(match.group(1))
    assert too_long == []

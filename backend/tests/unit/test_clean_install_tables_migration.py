"""0099 creates the two tables a clean install lacked, and never touches existing ones.

No database here: these check the revision chain, the statements the migration
sends and the shape of its two branches. The real behaviour on an empty
database is exercised by tests/integration/test_clean_install.py in CI.
"""
import importlib
import re
from pathlib import Path

from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0099_clean_install_tables"
OWNER = "created by 0099_clean_install_tables"
TABLES = ("tenant_recording_policy", "tenant_provider_cost_events")


def _module():
    return importlib.import_module("Alembic.versions.0099_clean_install_tables")


def _sql(monkeypatch, operation):
    migration = _module()
    statements = []
    monkeypatch.setattr(migration.op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(migration, operation)()
    return statements


def _branches(block):
    """(exists branch, missing branch) of one upgrade DO block."""
    exists, missing = block.split(" ELSE ", 1)
    return exists, missing


def test_0099_sits_on_top_of_0098_under_one_head():
    assert SCRIPTS.get_heads() == ["0104_paypal_review_dismissed"]
    assert SCRIPTS.get_revision("0100_force_legal_acceptance_rls").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0098_calls_changed_since_index"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_upgrade_bounds_its_locks_and_handles_each_table_in_one_block(monkeypatch):
    statements = _sql(monkeypatch, "upgrade")
    assert statements[0] == "SET LOCAL lock_timeout='5s'"
    assert statements[1] == "SET LOCAL statement_timeout='120s'"
    assert len(statements) == 4
    for table, block in zip(TABLES, statements[2:]):
        assert f"target := to_regclass('public.{table}');" in block


def test_missing_branch_creates_enables_forces_and_applies_the_canonical_policy(monkeypatch):
    for table, block in zip(TABLES, _sql(monkeypatch, "upgrade")[2:]):
        _, missing = _branches(block)
        assert f"CREATE TABLE public.{table} (" in missing
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;" in missing
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;" in missing
        assert f"CREATE POLICY {table}_tenant_isolation ON public.{table} FOR ALL USING (" in missing
        # The canonical expression of 0013 / 0087 / 0090: bypass OR tenant, NULLIF before the cast.
        assert "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)" in missing
        assert "tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid" in missing
        assert missing.count("WITH CHECK (") == 1
        assert f"COMMENT ON TABLE public.{table} IS '{OWNER}';" in missing


def test_missing_branch_matches_the_columns_the_application_uses(monkeypatch):
    policy, cost = (_branches(b)[1] for b in _sql(monkeypatch, "upgrade")[2:])
    for column in ("tenant_id UUID PRIMARY KEY", "default_consent_mode", "announcement_text",
                   "opt_out_dtmf_digit", "two_party_country_codes", "retention_days",
                   "created_at", "updated_at"):
        assert column in policy
    assert "retention_days > 0 AND retention_days <= 3650" in policy
    # provider_cost_ledger.py inserts exactly these 13 columns.
    columns = ("tenant_id", "call_id", "provider", "provider_role", "api_key_fp", "unit", "quantity",
               "unit_price_usd", "cost_usd", "model", "voice_id", "latency_ms", "status")
    for column in columns:
        assert re.search(rf"\b{column}\b\s+[A-Z]", cost), column
    from app.domain.services import provider_cost_ledger
    statement = " ".join(provider_cost_ledger._INSERT_EVENT_SQL.split())
    listed = statement[statement.index("(") + 1:statement.index(")")]
    assert tuple(name.strip() for name in listed.split(",")) == columns


def test_exists_branch_only_reports_and_changes_nothing(monkeypatch):
    for table, block in zip(TABLES, _sql(monkeypatch, "upgrade")[2:]):
        exists, _ = _branches(block)
        assert f"RAISE NOTICE '0099 {table} exists, left unchanged: row_security=% forced=% policies=% columns=%'" in exists
        for word in ("ALTER", "CREATE", "GRANT", "DROP", "COMMENT", "INSERT", "UPDATE", "DELETE"):
            assert not re.search(rf"\b{word}\b", exists), word
        assert "REVOKE" not in exists and "OWNER" not in exists


def test_policy_table_requires_tenants_and_cost_table_has_no_foreign_key(monkeypatch):
    policy, cost = _sql(monkeypatch, "upgrade")[2:]
    assert "to_regclass('public.tenants') IS NULL" in policy
    assert "REFERENCES public.tenants(id) ON DELETE CASCADE" in policy
    assert "REFERENCES" not in cost


def test_no_grant_is_issued_by_the_migration(monkeypatch):
    assert not any("GRANT" in s for s in _sql(monkeypatch, "upgrade"))


def test_downgrade_drops_only_tables_this_migration_created_and_refuses_with_rows(monkeypatch):
    statements = _sql(monkeypatch, "downgrade")
    assert statements[0] == "SET LOCAL lock_timeout='5s'"
    assert "SET LOCAL row_security=off" in statements
    blocks = statements[-2:]
    assert len(blocks) == 2
    for table, block in zip(TABLES, blocks):
        assert f"obj_description(present, 'pg_class') IS NOT DISTINCT FROM '{OWNER}'" in block
        assert block.index("IS NOT DISTINCT FROM") < block.index(f"DROP TABLE public.{table};")
        assert "RAISE EXCEPTION" in block and "while it holds rows" in block
        assert block.index("RAISE EXCEPTION") < block.index("DROP TABLE")
    assert "DROP FUNCTION IF EXISTS public.tenant_recording_policy_touch_updated_at();" in blocks[0]

"""0096 adds the per-import permission confirmation columns to contact_lists."""
import importlib
from pathlib import Path

from alembic.script import ScriptDirectory

SCRIPTS = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "Alembic"))
REVISION = "0096_contact_list_permission"
COLUMNS = ("consent_confirmed_at", "consent_confirmed_by", "consent_basis",
           "consent_source", "consent_permission_date", "consent_statement_version")


def _module():
    return importlib.import_module("Alembic.versions.0096_contact_list_permission")


def _sql(monkeypatch, operation):
    migration = _module()
    statements = []
    monkeypatch.setattr(migration.op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(migration, operation)()
    return " ".join(statements)


def test_alembic_finds_0096_on_top_of_0095_and_one_head_above_it():
    assert SCRIPTS.get_heads() == ["0099_clean_install_tables"]
    assert SCRIPTS.get_revision("0097_remove_wizard_plan_line").down_revision == REVISION
    revision = SCRIPTS.get_revision(REVISION)
    assert revision.down_revision == "0095_webhook_receipt_dismissed"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_upgrade_is_idempotent_in_form_and_adds_all_six_columns(monkeypatch):
    sql = _sql(monkeypatch, "upgrade")
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 6
    for column in COLUMNS:
        assert f"ADD COLUMN IF NOT EXISTS {column} " in sql
    assert "REFERENCES user_profiles(id) ON DELETE SET NULL" in sql
    assert "SET LOCAL lock_timeout" in sql and "SET LOCAL statement_timeout" in sql
    # constraint: guarded by a catalog lookup, added NOT VALID, then validated
    assert "contact_lists_consent_complete" in sql
    assert "FROM pg_catalog.pg_constraint" in sql and "IF NOT EXISTS" in sql
    assert "NOT VALID" in sql and "VALIDATE CONSTRAINT contact_lists_consent_complete" in sql
    for basis in ("express_consent", "existing_customer", "made_an_enquiry",
                  "other_documented", "pre_existing_unverified"):
        assert f"'{basis}'" in sql
    assert "UPDATE" not in sql  # no backfill


def test_downgrade_refuses_when_a_confirmation_exists_then_drops(monkeypatch):
    sql = _sql(monkeypatch, "downgrade")
    assert "consent_confirmed_at IS NOT NULL" in sql and "RAISE EXCEPTION" in sql
    assert sql.index("RAISE EXCEPTION") < sql.index("DROP CONSTRAINT IF EXISTS contact_lists_consent_complete")
    assert sql.count("DROP COLUMN IF EXISTS") == 6

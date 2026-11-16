"""0098 adds the index the call list's "changed since" read is served from (D5-20).

No database here: these check the revision chain, the statements the migration
sends, and that the index order is the order the endpoint asks for. The index
itself is exercised by CI's Postgres job when the chain is upgraded to head.
"""
import importlib
from pathlib import Path

from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0098_calls_changed_since_index"
INDEX_ORDER = "updated_at DESC NULLS LAST, id DESC"


def _module():
    return importlib.import_module("Alembic.versions.0098_calls_changed_since_index")


def _sql(monkeypatch, operation):
    migration = _module()
    statements = []
    monkeypatch.setattr(migration.op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(migration, operation)()
    return statements


def test_single_head_is_0098_on_top_of_0097():
    assert SCRIPTS.get_heads() == [REVISION]
    assert SCRIPTS.get_revision(REVISION).down_revision == "0097_remove_wizard_plan_line"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_upgrade_bounds_its_locks_and_creates_the_index_only_when_missing(monkeypatch):
    statements = _sql(monkeypatch, "upgrade")
    assert statements[0] == "SET LOCAL lock_timeout='5s'"
    assert statements[1] == "SET LOCAL statement_timeout='120s'"
    body = statements[2]
    assert "IF to_regclass('public.idx_calls_tenant_updated_id') IS NULL THEN" in body
    assert f"ON public.calls (tenant_id, {INDEX_ORDER});" in body
    assert "COMMENT ON INDEX public.idx_calls_tenant_updated_id IS 'created by 0098_calls_changed_since_index'" in body
    assert "CONCURRENTLY" not in body  # it runs inside the migration's transaction
    assert len(statements) == 3


def test_the_index_is_in_the_order_the_endpoint_asks_for():
    """The index only removes the sort while its order equals the ORDER BY."""
    source = (BACKEND / "app" / "api" / "v1" / "endpoints" / "calls.py").read_text(encoding="utf-8")
    assert f"ORDER BY c.updated_at DESC NULLS LAST, c.id DESC" in source
    assert INDEX_ORDER == "updated_at DESC NULLS LAST, id DESC"


def test_downgrade_drops_only_the_index_this_migration_created(monkeypatch):
    statements = _sql(monkeypatch, "downgrade")
    assert statements[0] == "SET LOCAL lock_timeout='5s'"
    body = statements[1]
    assert ("IF obj_description(to_regclass('public.idx_calls_tenant_updated_id'),'pg_class')"
            "='created by 0098_calls_changed_since_index' THEN") in body
    assert "DROP INDEX public.idx_calls_tenant_updated_id;" in body
    assert len(statements) == 2

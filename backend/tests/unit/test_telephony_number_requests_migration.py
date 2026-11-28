"""0108 creates the two telephony number request tables with forced row security.

No database here: the chain and the statements. Table behaviour needs PostgreSQL (CI).
"""
import importlib
import runpy
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0108_telephony_number_requests"
TABLES = ("telephony_number_requests", "telephony_number_request_documents")
BYPASS = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"


def _module():
    return importlib.import_module("Alembic.versions.0108_telephony_number_requests")


def _sql(monkeypatch):
    migration = _module()
    statements = []
    monkeypatch.setattr(migration.op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    migration.upgrade()
    return statements


def _create(statements, table):
    found = [s for s in statements if s.startswith(f"CREATE TABLE IF NOT EXISTS public.{table} (")]
    assert len(found) == 1
    return found[0]


def test_0108_sits_on_0107_and_0109_sits_on_it():
    assert SCRIPTS.get_heads() == ["0109_plan_retention_line"]
    assert SCRIPTS.get_revision("0109_plan_retention_line").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0107_staff_suspension"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_lock_timeout_comes_first_and_exactly_two_tables_are_created(monkeypatch):
    statements = _sql(monkeypatch)
    assert statements[0] == "SET LOCAL lock_timeout='5s'"
    assert len([s for s in statements if s.startswith("CREATE TABLE")]) == 2
    for table in TABLES:
        _create(statements, table)


def test_requests_table_constraints(monkeypatch):
    create = _create(_sql(monkeypatch), "telephony_number_requests")
    assert "status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved', 'rejected'))" in create
    assert "CONSTRAINT telephony_number_requests_tenant_key UNIQUE (tenant_id, idempotency_key)" in create
    assert "CONSTRAINT telephony_number_requests_tenant_id_id UNIQUE (tenant_id, id)" in create
    assert "tenant_id UUID NOT NULL REFERENCES tenants(id)" in create
    assert "CHECK ((status = 'submitted') = (reviewed_at IS NULL))" in create
    assert "CHECK (number_released_at IS NULL OR status = 'approved')" in create


def test_documents_table_constraints(monkeypatch):
    create = _create(_sql(monkeypatch), "telephony_number_request_documents")
    assert "FOREIGN KEY (tenant_id, request_id) REFERENCES telephony_number_requests (tenant_id, id)" in create
    assert "UNIQUE (tenant_id, request_id, kind)" in create
    assert "scan_status TEXT NOT NULL DEFAULT 'not_scanned'" in create
    assert "CHECK ((files_deleted_at IS NULL) = (wrapped_key IS NOT NULL))" in create
    assert "storage_key TEXT NOT NULL UNIQUE" in create


def test_no_contact_columns(monkeypatch):
    statements = _sql(monkeypatch)
    for table in TABLES:
        create = _create(statements, table).lower()
        for forbidden in ("email", "address", "phone"):
            assert forbidden not in create


def test_row_security_is_enabled_and_forced_before_each_policy(monkeypatch):
    statements = _sql(monkeypatch)
    for table in TABLES:
        enable = f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"
        force = f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"
        assert enable in statements and force in statements
        policy = [s for s in statements if s.startswith("CREATE POLICY") and f"ON public.{table} " in s]
        assert len(policy) == 1
        assert statements.index(enable) < statements.index(force) < statements.index(policy[0])
    assert [s for s in statements if s.startswith("CREATE POLICY")] == [
        f"CREATE POLICY tenant_isolation ON public.{t} "
        f"FOR ALL USING ({BYPASS} OR {TENANT}) WITH CHECK ({BYPASS} OR {TENANT})"
        for t in TABLES
    ]


def test_no_grant_and_no_data_writes(monkeypatch):
    for statement in _sql(monkeypatch):
        for forbidden in ("GRANT", "INSERT ", "UPDATE ", "DELETE "):
            assert forbidden not in statement


def test_downgrade_refuses_to_drop_customer_documents():
    with pytest.raises(RuntimeError, match="must be retained"):
        _module().downgrade()


def test_the_rls_inventory_finds_both_tables():
    inventory = runpy.run_path(str(BACKEND / "scripts" / "rls_acquire_inventory.py"))
    result = inventory["discover_rls_tables"](BACKEND)
    assert len(result) == 2
    for found in result:
        assert set(TABLES) <= set(found)

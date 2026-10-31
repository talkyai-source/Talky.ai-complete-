"""Policy helper arguments are not necessarily table names."""
import runpy
from pathlib import Path


def test_policy_scope_argument_is_not_a_table_even_when_identifier_shaped():
    inventory = runpy.run_path(str(Path(__file__).parents[2] / "scripts/rls_acquire_inventory.py"))
    source = '''
TABLES = ("webhook_endpoints", "webhook_deliveries")
SCOPE = "FALSE"
def apply(scope):
    for table in TABLES:
        op.execute(f"CREATE POLICY isolation ON public.{table} USING ({scope})")
apply(SCOPE)
'''
    protected, _ = inventory["_python_schema_evidence"](source)
    assert protected == {"webhook_endpoints", "webhook_deliveries"}


def test_policy_helper_resolves_actual_table_parameter_not_first_argument():
    inventory = runpy.run_path(str(Path(__file__).parents[2] / "scripts/rls_acquire_inventory.py"))
    source = '''
def apply(scope, table):
    op.execute(f"CREATE POLICY isolation ON public.{table} USING ({scope})")
apply("FALSE", "calls")
apply(table="leads", scope="TRUE")
'''
    protected, _ = inventory["_python_schema_evidence"](source)
    assert protected == {"calls", "leads"}


def test_temporary_policy_reference_does_not_invent_persistent_surface(tmp_path):
    inventory = runpy.run_path(str(Path(__file__).parents[2] / "scripts/rls_acquire_inventory.py"))
    schema = tmp_path / "database"
    schema.mkdir()
    (schema / "repair.sql").write_text('''
CREATE TEMPORARY TABLE policy_reference (tenant_id uuid) ON COMMIT DROP;
CREATE POLICY reference ON policy_reference USING (TRUE);
CREATE POLICY "public.decoy" ON policy_reference USING (TRUE);
ALTER TABLE policy_reference ENABLE ROW LEVEL SECURITY;
CREATE TEMP TABLE second_reference (tenant_id uuid);
CREATE POLICY second ON pg_temp.second_reference USING (TRUE);
ALTER TABLE pg_temp.second_reference ENABLE ROW LEVEL SECURITY;
CREATE POLICY permanent ON public.actual_tenant_rows USING (TRUE);
CREATE POLICY "pg_temp.decoy" ON public.permanent_named_like_schema USING (TRUE);
''', encoding="utf-8")
    protected, _ = inventory["discover_rls_tables"](tmp_path)
    assert protected == {"actual_tenant_rows", "permanent_named_like_schema"}


def test_temporary_name_cannot_hide_a_permanent_table(tmp_path):
    inventory = runpy.run_path(str(Path(__file__).parents[2] / "scripts/rls_acquire_inventory.py"))
    schema = tmp_path / "database"
    schema.mkdir()
    (schema / "repair.sql").write_text('''
CREATE TEMP TABLE same_name (tenant_id uuid);
CREATE POLICY reference ON same_name USING (TRUE);
CREATE POLICY permanent ON public.same_name USING (TRUE);
CREATE TEMP TABLE another_name (tenant_id uuid);
CREATE POLICY reference_two ON another_name USING (TRUE);
''', encoding="utf-8")
    (schema / "permanent.sql").write_text('''
CREATE POLICY permanent_other_source ON another_name USING (TRUE);
''', encoding="utf-8")
    protected, _ = inventory["discover_rls_tables"](tmp_path)
    assert protected == {"same_name", "another_name"}

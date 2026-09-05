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

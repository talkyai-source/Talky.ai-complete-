"""Null tenant is platform-only, never implicitly shared with every tenant."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock


def load_migration():
    path = Path(__file__).parents[2] / "Alembic/versions/0044_webhook_null_tenant_rls.py"
    spec = importlib.util.spec_from_file_location("webhook_rls_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_webhook_policy_excludes_unowned_rows_without_removing_platform_access():
    migration = load_migration()
    migration.op = MagicMock()
    migration.upgrade()
    statements = [str(call.args[0]) for call in migration.op.execute.call_args_list]
    assert migration.down_revision == "0043_campaign_direction_lock"
    for table in ("webhook_endpoints", "webhook_deliveries"):
        policy = next(sql for sql in statements if f"ALTER POLICY {table}_tenant_isolation" in sql)
        assert "tenant_id IS NULL" not in policy
        assert "USING" in policy and "WITH CHECK" in policy
        assert "app.bypass_rls" in policy and "app.current_tenant_id" in policy
        assert any(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in sql for sql in statements)
    assert not any("DELETE" in sql or "UPDATE public." in sql for sql in statements)

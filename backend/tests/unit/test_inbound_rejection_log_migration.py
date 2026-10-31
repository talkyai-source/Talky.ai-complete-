from __future__ import annotations

import importlib


def test_rejection_log_is_append_only_tenant_isolated_and_privacy_safe(monkeypatch):
    migration = importlib.import_module("Alembic.versions.0036_inbound_rejection_log")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda value: statements.append(str(value)))

    migration.upgrade()
    sql = "\n".join(statements)

    assert migration.down_revision == "0035_user_profiles_role_widen"
    assert "CREATE TABLE public.inbound_rejections" in sql
    assert "UNIQUE (provider, provider_call_id)" in sql
    assert "inbound_rejections_unowned_ani_private" in sql
    assert "caller_ani IS NULL AND caller_ani_private = TRUE" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY inbound_rejections_select" in sql
    assert "CREATE POLICY inbound_rejections_insert" in sql
    assert "CREATE POLICY inbound_rejections_update" in sql
    assert "FOR UPDATE USING (FALSE)" in sql
    assert "retention_until" in sql


def test_rejection_log_downgrade_drops_only_its_table(monkeypatch):
    migration = importlib.import_module("Alembic.versions.0036_inbound_rejection_log")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda value: statements.append(str(value)))

    migration.downgrade()

    assert statements == ["DROP TABLE public.inbound_rejections"]

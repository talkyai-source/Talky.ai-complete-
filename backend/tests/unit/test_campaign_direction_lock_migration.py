"""Contract tests for the campaign-direction advisory-lock backstop."""

from __future__ import annotations

import importlib

from app.domain.services.campaign_direction_guard import CAMPAIGN_DIRECTION_LOCK_SQL


def _migration():
    return importlib.import_module("Alembic.versions.0043_campaign_direction_lock")


def _statements(monkeypatch, operation: str) -> list[str]:
    migration = _migration()
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )
    getattr(migration, operation)()
    return statements


def test_0043_installs_same_campaign_direction_lock_as_application(monkeypatch):
    migration = _migration()
    statements = _statements(monkeypatch, "upgrade")
    sql = " ".join(statements)

    assert migration.revision == "0043_campaign_direction_lock"
    assert migration.down_revision == "0042_dialer_origination_guard"
    assert len(migration.revision) <= 32
    assert "CREATE OR REPLACE FUNCTION public.talky_lock_campaign_direction_update()" in sql
    assert "RETURNS trigger" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "hashtextextended('talky:campaign-direction:' || NEW.id::text, 0)" in sql

    # Guard against the application and database silently choosing different
    # lock namespaces or hash seeds.
    app_lock_sql = " ".join(CAMPAIGN_DIRECTION_LOCK_SQL.split())
    # The UUID cast is part of the lock identity.  Without it, an uppercase
    # spelling of the same UUID hashes differently from NEW.id::text in the
    # trigger and silently bypasses serialization.
    assert "'talky:campaign-direction:' || $1::uuid::text" in app_lock_sql
    assert "hashtextextended" in app_lock_sql
    assert ", 0)" in app_lock_sql


def test_0043_trigger_is_direction_only_idempotent_safety_net(monkeypatch):
    migration = _migration()
    statements = _statements(monkeypatch, "upgrade")
    sql = " ".join(statements)

    drop_at = sql.index("DROP TRIGGER IF EXISTS campaigns_direction_advisory_lock")
    create_at = sql.index("CREATE TRIGGER campaigns_direction_advisory_lock")
    assert drop_at < create_at
    assert "BEFORE UPDATE OF direction ON public.campaigns" in sql
    assert "FOR EACH ROW" in sql
    assert "WHEN (OLD.direction IS DISTINCT FROM NEW.direction)" in sql
    assert "EXECUTE FUNCTION public.talky_lock_campaign_direction_update()" in sql
    assert "before any campaign row lock" in sql
    assert "deadlock victim" in (migration.__doc__ or "")


def test_0043_downgrade_removes_only_revision_owned_objects(monkeypatch):
    statements = _statements(monkeypatch, "downgrade")
    sql = " ".join(statements)

    assert (
        "DROP TRIGGER IF EXISTS campaigns_direction_advisory_lock "
        "ON public.campaigns" in sql
    )
    assert (
        "DROP FUNCTION IF EXISTS public.talky_lock_campaign_direction_update()" in sql
    )
    assert "DROP TABLE" not in sql
    assert "DELETE FROM" not in sql

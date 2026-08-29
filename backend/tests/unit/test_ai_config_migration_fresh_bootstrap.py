"""Fresh-bootstrap guard for migration 0019's historical tenant data move."""

from __future__ import annotations

import importlib

import pytest


class _Result:
    def __init__(self, *, scalar_value=None, rows=None):
        self._scalar_value = scalar_value
        self._rows = [] if rows is None else rows

    def scalar(self):
        return self._scalar_value

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, tenant_count: int):
        self.tenant_count = tenant_count
        self.statements: list[str] = []

    def execute(self, statement, _parameters=None):
        sql = str(statement)
        self.statements.append(sql)
        if "current_setting('app.bypass_rls'" in sql:
            return _Result(scalar_value="off")
        if "SELECT count(*) FROM tenant_ai_configs" in sql:
            return _Result(scalar_value=self.tenant_count)
        if "WHERE tenant_id::text LIKE" in sql:
            return _Result(rows=[])
        return _Result()


def test_0019_skips_only_the_data_move_on_an_empty_schema_snapshot(
    monkeypatch,
) -> None:
    migration = importlib.import_module("Alembic.versions.0019_ai_config_migration_records")
    connection = _Connection(tenant_count=0)
    ddl: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration.op, "execute", lambda statement: ddl.append(str(statement)))

    migration.upgrade()

    assert any("CREATE TABLE IF NOT EXISTS ai_config_migrations" in sql for sql in ddl)
    assert any("idx_ai_config_migrations_batch" in sql for sql in ddl)
    count_at = connection.statements.index("SELECT count(*) FROM tenant_ai_configs")
    bypass_at = next(
        index
        for index, sql in enumerate(connection.statements)
        if "set_config('app.bypass_rls', 'on'" in sql
    )
    assert bypass_at < count_at
    assert "set_config('app.bypass_rls', :prior" in connection.statements[-1]


def test_0019_keeps_exact_target_assertions_on_any_nonempty_database(
    monkeypatch,
) -> None:
    migration = importlib.import_module("Alembic.versions.0019_ai_config_migration_records")
    connection = _Connection(tenant_count=1)
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration.op, "execute", lambda _statement: None)

    with pytest.raises(RuntimeError, match="matched 0 rows, expected 1"):
        migration.upgrade()

    assert any("WHERE tenant_id::text LIKE" in sql for sql in connection.statements)

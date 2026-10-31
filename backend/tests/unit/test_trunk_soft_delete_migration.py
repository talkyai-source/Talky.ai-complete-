"""0070: a tenant can always delete a PBX line, and the name can be reused.

Owner report 2026-10-14: DELETE /telephony/sip/trunks/{id} answered 409 "Trunk In
Use" for every line that ever had an inbound number. ``inbound_did_assignments``
keeps its rows (and a DEFERRABLE foreign key to the trunk) as call history, so
the row cannot be hard-deleted. Migration 0070 adds ``deleted_at`` so such a line
is retired in place, and makes the per-tenant name index partial so the retired
name is free again. The real-PostgreSQL behaviour is proven in
tests/integration/test_op04_trunk_lifecycle.py; this file pins the chain and the
shape of the DDL that cannot be exercised without a database.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

MODULE = "Alembic.versions.0070_trunk_soft_delete"


def _load():
    return importlib.import_module(MODULE)


def _statements(monkeypatch, direction: str) -> list[str]:
    migration = _load()
    captured: list[str] = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=lambda sql: captured.append(str(sql))))
    getattr(migration, direction)()
    return captured


def test_0070_chains_from_assemblyai_settings_and_fits_version_column():
    migration = _load()
    assert migration.revision == "0070_trunk_soft_delete"
    assert migration.down_revision == "0069_assemblyai_settings"
    assert len(migration.revision) <= 32  # alembic_version.version_num is VARCHAR(32)


def test_upgrade_adds_the_column_then_swaps_the_name_index_for_a_partial_one(monkeypatch):
    statements = _statements(monkeypatch, "upgrade")
    joined = "\n".join(statements)
    # One statement per op.execute: asyncpg prepared statements refuse several commands.
    assert statements[0].startswith("SET LOCAL lock_timeout")
    column = next(i for i, s in enumerate(statements) if "ADD COLUMN IF NOT EXISTS deleted_at" in s)
    index = next(i for i, s in enumerate(statements) if "idx_tenant_sip_trunks_tenant_name_unique" in s)
    assert column < index, "the partial index references deleted_at, so the column comes first"
    assert "timestamptz" in statements[column].lower()
    assert "ALTER TABLE IF EXISTS public.tenant_sip_trunks" in statements[column]
    # Same name, same expression as the original index, restricted to live rows.
    assert "CREATE UNIQUE INDEX idx_tenant_sip_trunks_tenant_name_unique" in joined
    assert "(tenant_id, lower(trunk_name))" in joined
    assert "WHERE deleted_at IS NULL" in joined
    # Databases without the table (partial lineages) are a no-op, and a re-run
    # does not rebuild an index that is already partial.
    assert "to_regclass('public.tenant_sip_trunks')" in statements[index]
    assert "pg_get_indexdef" in statements[index]


def test_downgrade_refuses_duplicate_names_then_restores_the_full_index_and_drops_the_column(monkeypatch):
    statements = _statements(monkeypatch, "downgrade")
    refuse = next(i for i, s in enumerate(statements) if "RAISE EXCEPTION" in s)
    restore = next(i for i, s in enumerate(statements) if "CREATE UNIQUE INDEX idx_tenant_sip_trunks_tenant_name_unique" in s)
    drop = next(i for i, s in enumerate(statements) if "DROP COLUMN IF EXISTS deleted_at" in s)
    assert refuse < restore < drop
    # Soft-deleted rows come back as ordinary rows once the column is gone, so
    # the duplicate check must count every row, not only the live ones.
    assert "GROUP BY tenant_id, lower(trunk_name)" in statements[refuse]
    assert "deleted_at IS NULL" not in statements[refuse]
    assert "WHERE deleted_at" not in statements[restore]


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_every_statement_is_a_single_command_guarded_for_missing_tables(monkeypatch, direction):
    for statement in _statements(monkeypatch, direction):
        if statement.startswith("SET LOCAL"):
            continue
        # Either a plain IF EXISTS DDL or a DO block that checks to_regclass first.
        assert "IF EXISTS" in statement or "to_regclass" in statement, statement

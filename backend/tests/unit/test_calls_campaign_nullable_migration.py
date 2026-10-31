"""Contract tests for the campaign-less calls schema repair."""

from __future__ import annotations

import importlib

import pytest


def test_0040_drops_only_campaign_not_null_and_keeps_a_postcondition(monkeypatch):
    migration = importlib.import_module(
        "Alembic.versions.0040_calls_campaign_nullable"
    )
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.upgrade()

    sql = " ".join(statements)
    assert migration.down_revision == "0039_contact_capture_audit"
    assert "ALTER TABLE public.calls ALTER COLUMN campaign_id DROP NOT NULL" in sql
    assert "AND attnotnull" in sql
    assert "ALTER COLUMN lead_id" not in sql
    assert "DROP CONSTRAINT" not in sql
    assert "IF NOT EXISTS" in sql
    assert "is_nullable = 'YES'" in sql
    assert "0040 calls.campaign_id nullable postcondition failed" in sql


def test_0040_refuses_a_destructive_downgrade():
    migration = importlib.import_module(
        "Alembic.versions.0040_calls_campaign_nullable"
    )

    with pytest.raises(RuntimeError, match="campaign-less calls"):
        migration.downgrade()

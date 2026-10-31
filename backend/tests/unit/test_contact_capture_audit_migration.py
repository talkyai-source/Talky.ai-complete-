from __future__ import annotations

import importlib


def test_0039_adds_contact_capture_audit_columns_and_exact_status_constraint(monkeypatch):
    migration = importlib.import_module(
        "Alembic.versions.0039_contact_capture_audit"
    )
    statements: list[str] = []

    class _Result:
        @staticmethod
        def scalar():
            return "off"

    class _Bind:
        def execute(self, value, *_args, **_kwargs):
            statements.append(" ".join(str(value).split()))
            return _Result()

    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Bind())

    migration.upgrade()

    sql = " ".join(statements)
    assert migration.down_revision == "0038_tenant_table_rls_backfill"
    for column in (
        "raw_value TEXT",
        "normalized_value TEXT",
        "validation_status VARCHAR(32)",
        "confirmed_at TIMESTAMPTZ",
    ):
        assert column in sql
    for status in (
        "needs_clarification",
        "invalid",
        "awaiting_confirmation",
        "confirmed",
        "cancelled",
    ):
        assert f"'{status}'" in sql
    assert "NOT VALID" in sql
    assert "VALIDATE CONSTRAINT call_lead_details_validation_status_valid" in sql
    assert "set_config('app.bypass_rls', 'on', TRUE)" in sql
    assert "set_config('app.bypass_rls', :prior, TRUE)" in sql
    assert "WHEN value IS NULL THEN 'cancelled'" in sql
    assert "0039 contact audit backfill postcondition failed" in sql


def test_0039_is_forward_only():
    migration = importlib.import_module(
        "Alembic.versions.0039_contact_capture_audit"
    )
    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("contact audit columns must not be destructively removed")

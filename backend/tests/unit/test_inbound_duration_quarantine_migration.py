from __future__ import annotations

import importlib


def test_unanswered_inbound_duration_is_audited_then_quarantined(monkeypatch):
    migration = importlib.import_module(
        "Alembic.versions.0037_inbound_duration_quarantine"
    )
    statements: list[str] = []
    connection_statements: list[str] = []

    class Result:
        def scalar(self):
            return "off"

    class Connection:
        def execute(self, value, _parameters=None):
            connection_statements.append(" ".join(str(value).split()))
            return Result()

    monkeypatch.setattr(migration.op, "get_bind", lambda: Connection())
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.upgrade()

    assert migration.down_revision == "0036_inbound_rejection_log"
    assert any("ADD COLUMN provider_terminated_at TIMESTAMPTZ" in sql for sql in statements)
    audit = next(sql for sql in statements if "INSERT INTO inbound_audit_events" in sql)
    correction = next(sql for sql in statements if "UPDATE calls" in sql)
    assert "INSERT INTO inbound_audit_events" in audit
    assert "historical_missing_answer_proof_duration_quarantined" in audit
    assert "duration_seconds" in audit
    assert "direction='inbound'" in audit
    assert "answered_at IS NULL" in audit
    assert "billing_status IN ('reserved','held')" in audit
    assert "COALESCE(duration_seconds,0) <> 0" in audit
    assert "UPDATE calls" in correction
    assert "SET duration_seconds=0" in correction
    assert "direction='inbound'" in correction
    assert "answered_at IS NULL" in correction
    assert "billing_status IN ('reserved','held')" in correction
    assert "COALESCE(duration_seconds,0) <> 0" in correction
    assert "current_setting('app.bypass_rls'" in connection_statements[0]
    assert "set_config('app.bypass_rls', 'on', TRUE)" in connection_statements[1]
    assert "set_config('app.bypass_rls', :prior, TRUE)" in connection_statements[-1]


def test_duration_quarantine_downgrade_never_reinvents_usage(monkeypatch):
    migration = importlib.import_module(
        "Alembic.versions.0037_inbound_duration_quarantine"
    )
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda value: statements.append(str(value)))

    migration.downgrade()

    assert statements == []

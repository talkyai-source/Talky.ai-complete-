"""0101 creates paypal_webhook_events (ids and a hash only, platform-only row security).

No database here: the chain and the statements. Table behaviour needs PostgreSQL (CI).
"""
import importlib
from pathlib import Path

from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0101_paypal_webhook_events"


def _module():
    return importlib.import_module("Alembic.versions.0101_paypal_webhook_events")


def _sql(monkeypatch, operation):
    migration = _module()
    statements = []
    monkeypatch.setattr(migration.op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(migration, operation)()
    return statements


def test_0101_sits_on_top_of_0100_and_under_0102():
    assert SCRIPTS.get_heads() == ["0102_paypal_subscriptions"]
    assert SCRIPTS.get_revision("0102_paypal_subscriptions").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0100_force_legal_acceptance_rls"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_upgrade_creates_the_table_without_tenant_or_body(monkeypatch):
    statements = _sql(monkeypatch, "upgrade")
    assert statements[0] == "SET LOCAL lock_timeout='5s'"
    create = statements[1]
    assert create.startswith("CREATE TABLE IF NOT EXISTS public.paypal_webhook_events (")
    assert "event_id TEXT PRIMARY KEY" in create
    assert "provider_mode TEXT NOT NULL CHECK (provider_mode IN ('test', 'live'))" in create
    assert "state IN ('received', 'completed', 'ignored', 'needs_review', 'failed')" in create
    assert "payload_hash TEXT NOT NULL" in create
    assert "attempt_count INTEGER NOT NULL DEFAULT 1" in create
    for forbidden in ("tenant_id", "payload JSON", "body", "email"):
        assert forbidden not in create
    assert any(
        s.startswith("CREATE INDEX IF NOT EXISTS idx_paypal_webhook_events_open")
        and "WHERE state IN ('needs_review', 'failed', 'received')" in s
        for s in statements
    )


def test_upgrade_enables_and_forces_row_security_with_the_platform_only_policy(monkeypatch):
    statements = _sql(monkeypatch, "upgrade")
    enable = "ALTER TABLE public.paypal_webhook_events ENABLE ROW LEVEL SECURITY"
    force = "ALTER TABLE public.paypal_webhook_events FORCE ROW LEVEL SECURITY"
    assert enable in statements and force in statements
    policy = [s for s in statements if s.startswith("CREATE POLICY")]
    bypass = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    assert policy == [
        "CREATE POLICY paypal_webhook_events_platform_only ON public.paypal_webhook_events "
        f"FOR ALL USING ({bypass}) WITH CHECK ({bypass})"
    ]
    assert statements.index(force) < statements.index(policy[0])
    assert not any("GRANT" in s for s in statements)


def test_policy_matches_the_one_0054_gives_processed_webhook_events():
    source = (BACKEND / "Alembic" / "versions" / "0054_billing_webhook_receipts.py").read_text(encoding="utf-8")
    assert "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)" in source
    assert "FOR ALL USING ({bypass}) WITH CHECK ({bypass})" in source


def test_downgrade_drops_policy_index_and_table(monkeypatch):
    statements = _sql(monkeypatch, "downgrade")
    assert statements[1:] == [
        "DROP POLICY IF EXISTS paypal_webhook_events_platform_only ON public.paypal_webhook_events",
        "DROP INDEX IF EXISTS public.idx_paypal_webhook_events_open",
        "DROP TABLE IF EXISTS public.paypal_webhook_events",
    ]

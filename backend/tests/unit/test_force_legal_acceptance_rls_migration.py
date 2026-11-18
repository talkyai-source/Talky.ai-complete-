"""0100 forces row security on legal_acceptances (0085 enabled it but never forced it).

No database here: the chain and the statements. The behaviour is proven in CI by
tests/integration/test_clean_install.py, which requires every table with a
tenant_id column to be enabled and forced on a database built from empty.
"""
import importlib
from pathlib import Path

from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0100_force_legal_acceptance_rls"


def _module():
    return importlib.import_module("Alembic.versions.0100_force_legal_acceptance_rls")


def _sql(monkeypatch, operation):
    migration = _module()
    statements = []
    monkeypatch.setattr(migration.op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(migration, operation)()
    return statements


def test_0100_sits_on_top_of_0099_and_0101_is_the_head():
    assert SCRIPTS.get_heads() == ["0103_recording_notice_state"]
    assert SCRIPTS.get_revision("0101_paypal_webhook_events").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0099_clean_install_tables"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_upgrade_forces_row_security_and_never_without_a_policy(monkeypatch):
    statements = _sql(monkeypatch, "upgrade")
    assert statements[0] == "SET LOCAL lock_timeout='5s'"
    assert statements[1] == "SET LOCAL statement_timeout='60s'"
    body = statements[2]
    refuse = body.index("refuses to force row security on legal_acceptances without a policy")
    force = body.index("ALTER TABLE public.legal_acceptances FORCE ROW LEVEL SECURITY;")
    assert refuse < force, "the policy check must come before the FORCE"
    assert "ALTER TABLE public.legal_acceptances ENABLE ROW LEVEL SECURITY;" in body
    # It changes the posture only: no policy, grant, column or data statement.
    for word in ("CREATE POLICY", "DROP POLICY", "GRANT", "ADD COLUMN", "DELETE", "UPDATE ", "INSERT"):
        assert word not in body
    assert len(statements) == 3


def test_the_policy_0085_created_allows_what_the_application_does():
    """Forcing is only safe because the policy admits the tenant's own rows and the internal bypass."""
    source = (BACKEND / "Alembic" / "versions" / "0085_legal_acceptance.py").read_text(encoding="utf-8")
    assert "CREATE POLICY legal_acceptances_tenant ON legal_acceptances" in source
    assert source.count("tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid") == 2
    assert source.count("current_setting('app.bypass_rls', TRUE)") == 2


def test_downgrade_removes_only_the_force(monkeypatch):
    statements = _sql(monkeypatch, "downgrade")
    assert statements[0] == "SET LOCAL lock_timeout='5s'"
    body = statements[1]
    assert "ALTER TABLE public.legal_acceptances NO FORCE ROW LEVEL SECURITY;" in body
    assert "DISABLE ROW LEVEL SECURITY" not in body and "DROP" not in body
    assert len(statements) == 2

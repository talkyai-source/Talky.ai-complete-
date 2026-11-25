"""0102 creates the three PayPal plan tables with forced row security.

No database here: the chain and the statements. Table behaviour needs PostgreSQL (CI).
"""
import importlib
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0102_paypal_subscriptions"
TABLES = ("paypal_plan_options", "paypal_subscriptions", "paypal_subscription_payments")
BYPASS = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"


def _module():
    return importlib.import_module("Alembic.versions.0102_paypal_subscriptions")


def _sql(monkeypatch):
    migration = _module()
    statements = []
    monkeypatch.setattr(migration.op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    migration.upgrade()
    return statements


def _create(statements, table):
    found = [s for s in statements if s.startswith(f"CREATE TABLE IF NOT EXISTS public.{table} (")]
    assert len(found) == 1
    return found[0]


def test_0102_sits_on_top_of_0101_under_0103():
    assert SCRIPTS.get_heads() == ["0108_telephony_number_requests"]
    assert SCRIPTS.get_revision("0103_recording_notice_state").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0101_paypal_webhook_events"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_stripe_tables_are_not_touched(monkeypatch):
    statements = _sql(monkeypatch)
    assert statements[0] == "SET LOCAL lock_timeout='5s'"
    for statement in statements[1:]:
        assert "ALTER TABLE public.paypal_" in statement or " public.paypal_" in statement
        for stripe_table in (" subscriptions ", " invoices ", "billing_checkout_attempts", "ALTER TABLE public.tenants"):
            assert stripe_table not in statement
    assert not any("GRANT" in s for s in statements)


def test_plan_options_bind_one_paypal_plan_to_one_price(monkeypatch):
    create = _create(_sql(monkeypatch), "paypal_plan_options")
    assert "price_option_id UUID NOT NULL UNIQUE REFERENCES plan_price_options(id)" in create
    assert "paypal_plan_id TEXT NOT NULL UNIQUE" in create
    assert "provider_mode TEXT NOT NULL CHECK (provider_mode IN ('test', 'live'))" in create
    assert "amount_minor BIGINT NOT NULL CHECK (amount_minor > 0)" in create
    assert "CHECK (NOT active OR verified_at IS NOT NULL)" in create


def test_subscriptions_keep_one_live_row_per_tenant(monkeypatch):
    statements = _sql(monkeypatch)
    create = _create(statements, "paypal_subscriptions")
    assert "id UUID PRIMARY KEY," in create
    assert "tenant_id UUID NOT NULL REFERENCES tenants(id)" in create
    assert "paypal_subscription_id TEXT UNIQUE" in create
    assert (
        "status IN ('creating', 'unknown', 'approval_pending', 'active', 'past_due', "
        "'suspended', 'cancelled', 'expired', 'failed')" in create
    )
    assert "cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE" in create
    assert "paypal_status TEXT," in create
    assert "approved_at TIMESTAMPTZ," in create
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS paypal_subscriptions_one_live_per_tenant "
        "ON public.paypal_subscriptions (tenant_id) WHERE status IN "
        "('creating', 'unknown', 'approval_pending', 'active', 'past_due', 'suspended')"
    ) in statements


def test_payments_are_unique_per_paypal_transaction_and_hold_no_payer_details(monkeypatch):
    create = _create(_sql(monkeypatch), "paypal_subscription_payments")
    assert "paypal_transaction_id TEXT NOT NULL UNIQUE" in create
    assert "subscription_id UUID NOT NULL REFERENCES paypal_subscriptions(id)" in create
    assert (
        "status IN ('completed', 'pending', 'declined', 'failed', 'partially_refunded', "
        "'refunded', 'reversed')" in create
    )
    for forbidden in ("email", "payer", "name ", "address"):
        assert forbidden not in create


def test_row_security_is_forced_before_each_policy(monkeypatch):
    statements = _sql(monkeypatch)
    for table in TABLES:
        enable = f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"
        force = f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"
        assert enable in statements and force in statements
        policy = [s for s in statements if s.startswith("CREATE POLICY") and f"ON public.{table} " in s]
        assert len(policy) == 1
        assert statements.index(force) < statements.index(policy[0])
    policies = [s for s in statements if s.startswith("CREATE POLICY")]
    assert policies == [
        "CREATE POLICY paypal_plan_options_platform_only ON public.paypal_plan_options "
        f"FOR ALL USING ({BYPASS}) WITH CHECK ({BYPASS})",
        "CREATE POLICY tenant_isolation ON public.paypal_subscriptions "
        f"FOR ALL USING ({BYPASS} OR {TENANT}) WITH CHECK ({BYPASS} OR {TENANT})",
        "CREATE POLICY tenant_isolation ON public.paypal_subscription_payments "
        f"FOR ALL USING ({BYPASS} OR {TENANT}) WITH CHECK ({BYPASS} OR {TENANT})",
    ]


def test_tenant_policy_matches_the_one_0053_gives_checkout_attempts():
    source = (BACKEND / "Alembic" / "versions" / "0053_billing_price_options.py").read_text(encoding="utf-8")
    assert "tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid" in source
    assert "FOR ALL USING ({bypass} OR {tenant}) WITH CHECK ({bypass} OR {tenant})" in source


def test_downgrade_refuses_to_drop_billing_records():
    with pytest.raises(RuntimeError, match="must be retained"):
        _module().downgrade()

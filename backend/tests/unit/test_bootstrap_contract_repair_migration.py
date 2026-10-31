"""Static contracts for the conservative bootstrap and forward repair."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
MIGRATION = BACKEND / "Alembic" / "versions" / "0033_bootstrap_contract_repair.py"
FOUR_EYE_MIGRATION = BACKEND / "Alembic" / "versions" / "0034_inbound_billing_four_eye.py"
BOOTSTRAP_INTEGRATION = BACKEND / "tests" / "integration" / "test_bootstrap_contract_repair.py"


def _load_bootstrap_integration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "bootstrap_contract_repair_integration_guard",
        BOOTSTRAP_INTEGRATION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalized(value: str) -> str:
    return " ".join(value.lower().split())


def test_0033_is_the_comprehensive_additive_forward_repair() -> None:
    migration = importlib.import_module("Alembic.versions.0033_bootstrap_contract_repair")
    source = _normalized(MIGRATION.read_text(encoding="utf-8"))

    assert migration.revision == "0033_bootstrap_contract_repair"
    assert migration.down_revision == "0032_inbound_billing_hold"
    assert len(migration.revision) <= 32
    for invariant in (
        "failure_category text",
        "knowledge_mode text not null default 'none'",
        "knowledge_model text",
        "tts_provider text",
        "prompt_version_pin varchar(64)",
        "alter table calls alter column lead_id drop not null",
        "create table if not exists campaign_knowledge_sources",
        "create table if not exists campaign_knowledge_nodes",
        "create table if not exists call_feedback",
        "create table if not exists conversation_reviews",
        "create table if not exists review_reward_ledger",
        "create table if not exists prompt_template_versions",
        "add column if not exists is_test boolean not null default false",
        "create table if not exists ai_config_migrations",
        "create table if not exists campaign_lead_fields",
        "create table if not exists call_lead_details",
        "create table if not exists topup_packages",
        "create table if not exists topup_orders",
        "create table if not exists billing_ledger",
        "create or replace function public.prevent_billing_ledger_mutation()",
        "create trigger billing_ledger_immutable",
        "alter table billing_ledger enable always trigger billing_ledger_immutable",
        "create table if not exists tenant_policy_audit_log",
        "create or replace view billable_calls",
    ):
        assert invariant in source

    # 0019's tenant-specific model updates are historical data, not a generic
    # schema repair.  The forward repair creates only its empty audit table.
    assert "update tenant_ai_configs" not in source
    assert "_validate_contract(" in source
    assert "malformed partial schema" in source


def test_0033_targets_only_the_audited_legacy_and_new_rls_tables() -> None:
    migration = importlib.import_module("Alembic.versions.0033_bootstrap_contract_repair")
    expected = {
        "campaign_knowledge_sources",
        "campaign_knowledge_nodes",
        "call_feedback",
        "conversation_reviews",
        "review_reward_ledger",
        "campaign_lead_fields",
        "call_lead_details",
        "topup_orders",
        "billing_ledger",
        "tenant_sip_trunks",
        "tenant_codec_policies",
        "tenant_route_policies",
        "tenant_telephony_idempotency",
        "tenant_runtime_policy_versions",
        "tenant_runtime_policy_events",
        "tenant_sip_trust_policies",
        "tenant_telephony_threshold_policies",
        "tenant_telephony_quota_events",
        "tenant_policy_audit_log",
    }

    assert set(migration._RLS_TABLES) == expected
    assert migration._APPEND_ONLY_RLS_TABLES == {
        "billing_ledger",
        "tenant_policy_audit_log",
    }
    assert migration._POLICY_AUDIT_TRIGGER_TARGETS == {
        "tenant_sip_trunks": "trg_audit_tenant_sip_trunks",
        "tenant_codec_policies": "trg_audit_tenant_codec_policies",
        "tenant_route_policies": "trg_audit_tenant_route_policies",
        "tenant_sip_trust_policies": "trg_audit_tenant_sip_trust_policies",
        "tenant_runtime_policy_versions": "trg_audit_tenant_runtime_policy_versions",
        "tenant_telephony_threshold_policies": ("trg_audit_tenant_telephony_threshold_policies"),
    }
    source = MIGRATION.read_text(encoding="utf-8")
    assert "app.bypass_rls" in source
    assert "FOR existing_policy IN" in source
    # Do not replay 0013's dynamic every-RLS-table sweep at current head: later
    # migrations own specialized policies that must not be flattened.
    assert "FOR tbl IN" not in source
    assert "c.relkind" not in source
    assert "CREATE OR REPLACE FUNCTION public.log_tenant_policy_mutation" not in source
    assert "CREATE OR REPLACE FUNCTION public.prune_tenant_policy_audit_log" not in source


def test_0033_enforces_billing_ledger_append_only_at_rls_and_trigger_boundaries() -> None:
    source = _normalized(MIGRATION.read_text(encoding="utf-8"))

    assert "if table in _append_only_rls_tables" in source
    assert "for update using (false)" in source
    assert "for delete using (false)" in source
    assert "prevent_billing_ledger_mutation()" in source
    assert "before update or delete on billing_ledger" in source
    assert "enable always trigger billing_ledger_immutable" in source
    assert "billing_ledger_immutable has incompatible trigger binding" in source


def test_0034_forward_repairs_preexisting_mutable_billing_ledgers() -> None:
    migration = importlib.import_module("Alembic.versions.0034_inbound_billing_four_eye")
    source = _normalized(FOUR_EYE_MIGRATION.read_text(encoding="utf-8"))

    assert migration.down_revision == "0033_bootstrap_contract_repair"
    assert "def _repair_billing_ledger_immutability" in source
    assert "for existing_policy in" in source
    assert "create policy billing_ledger_select" in source
    assert "create policy billing_ledger_insert" in source
    assert "create policy billing_ledger_update" in source
    assert "for update using (false)" in source
    assert "create policy billing_ledger_delete" in source
    assert "for delete using (false)" in source
    assert "before update or delete on billing_ledger" in source
    assert "enable always trigger billing_ledger_immutable" in source
    assert "_validate_billing_ledger_immutability()" in source

    downgrade = source[source.index("def downgrade()") :]
    assert "drop trigger billing_ledger_immutable" not in downgrade
    assert "drop function public.prevent_billing_ledger_mutation" not in downgrade


def test_0033_downgrade_is_a_hard_forward_only_boundary() -> None:
    source = _normalized(MIGRATION.read_text(encoding="utf-8"))
    downgrade = source[source.index("def downgrade()") :]

    assert "raise runtimeerror" in downgrade
    assert "refusing to downgrade 0033" in downgrade
    assert "drop table" not in downgrade
    assert "drop column" not in downgrade
    assert "drop view" not in downgrade
    assert "drop index" not in downgrade


def test_docs_and_ci_run_the_full_chain_from_the_conservative_floor() -> None:
    docs = (BACKEND / "database" / "MIGRATIONS.md").read_text(encoding="utf-8")
    baseline = (BACKEND / "Alembic" / "versions" / "0001_baseline.py").read_text(encoding="utf-8")
    preserved_snapshot = (BACKEND / "database" / "schema" / "baseline_2026-06-02.sql").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert docs.count("alembic stamp 0008_tenant_voice_tuning") >= 2
    assert "alembic stamp 0021_billing_topup" not in docs
    assert "old 0021 stamp shortcut" in docs
    assert "0008_tenant_voice_tuning" in baseline
    assert "0021_billing_topup`` followed" not in baseline
    assert "alembic stamp 0008_tenant_voice_tuning" in workflow
    # The sole 0021 stamp is an intentional legacy false-stamp regression
    # fixture. Production docs must never prescribe it again.
    assert workflow.count("alembic stamp 0021_billing_topup") == 1
    assert "Legacy false-stamp repair" in workflow
    assert "Preserved baseline bootstrap contract" in workflow
    assert "database/schema/baseline_2026-06-02.sql" in workflow
    assert "postgres:16.14-alpine" in workflow
    assert "docker run --rm --network host" in workflow
    assert "-f /work/database/schema/baseline_2026-06-02.sql" in workflow
    assert "pytest tests/integration/test_bootstrap_contract_repair.py -q" in workflow
    assert "CREATE ROLE authenticated NOLOGIN NOSUPERUSER NOBYPASSRLS" in workflow
    assert "tests/integration/test_inbound_hold_resolution.py" in workflow
    assert "tests/integration/test_bootstrap_contract_repair.py" in workflow

    # pg_dump clears search_path. The snapshot's hand-appended, unqualified DDL
    # must restore public before its first CREATE TABLE or a fresh load aborts.
    appended_snapshot = preserved_snapshot[preserved_snapshot.index("\\unrestrict") :]
    assert appended_snapshot.index(
        "SELECT pg_catalog.set_config('search_path', 'public', false);"
    ) < appended_snapshot.index("CREATE TABLE IF NOT EXISTS tenant_ai_credentials")


def test_0013_does_not_overwrite_objects_owned_by_later_migrations() -> None:
    migration = (BACKEND / "Alembic" / "versions" / "0013_canonical_rls_policies.py").read_text(
        encoding="utf-8"
    )
    upgrade = migration[migration.index("def upgrade()") : migration.index("def downgrade()")]

    assert "c.relname NOT IN ('admin_media_deletion_intents')" in upgrade
    assert "0023" in upgrade


def test_destructive_bootstrap_integration_ignores_ordinary_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_bootstrap_integration()
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:secret@127.0.0.1:5432/talky_test",
    )

    with pytest.raises(pytest.skip.Exception, match="explicit TEST_DATABASE_URL"):
        module._dsn_or_skip()


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://user:secret@db.prod.example:5432/talky_test",
        "postgresql://user:secret@127.0.0.1:5432/talky",
        "mysql://user:secret@127.0.0.1:3306/talky_test",
    ],
    ids=["remote-host", "non-test-database", "wrong-engine"],
)
def test_destructive_bootstrap_integration_refuses_unsafe_target(
    monkeypatch: pytest.MonkeyPatch,
    dsn: str,
) -> None:
    module = _load_bootstrap_integration()
    monkeypatch.setenv("TEST_DATABASE_URL", dsn)

    with pytest.raises(pytest.fail.Exception):
        module._dsn_or_skip()


def test_destructive_bootstrap_integration_accepts_explicit_loopback_test_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_bootstrap_integration()
    dsn = "postgresql://user:secret@127.0.0.1:55432/talky_alembic_test"
    monkeypatch.setenv("TEST_DATABASE_URL", dsn)

    assert module._dsn_or_skip() == dsn


def test_configured_test_database_connection_failure_is_not_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_bootstrap_integration()
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql://user:secret@127.0.0.1:55432/talky_alembic_test",
    )

    class BrokenEngine:
        disposed = False

        def connect(self):
            raise module.SQLAlchemyError("unreachable")

        def dispose(self) -> None:
            self.disposed = True

    engine = BrokenEngine()
    monkeypatch.setattr(module, "create_engine", lambda *_args, **_kwargs: engine)

    with pytest.raises(pytest.fail.Exception, match="not reachable"):
        module._engine_or_fail()
    assert engine.disposed is True

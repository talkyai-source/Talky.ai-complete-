"""Both released histories remain addressable behind one deployment head."""
from importlib import import_module
from pathlib import Path

from alembic.script import ScriptDirectory
import pytest

SCRIPTS = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "Alembic"))
HEARTBEAT = "0048_audit_skip_heartbeat"
RECEIPTS = "0062_saved_acknowledgement"
MERGE = "0063_release_history_merge"
ADMIN = "0077_legacy_tenant_rls_repair"
PAID = "0071_paid_only_billing"
PREVIOUS_HEAD = "0078_admin_billing_merge"
SUPPLIER = "0079_supplier_evidence"
ALLOWANCE = "0072_allowance_anchor"
SUPPLIER_ALLOWANCE = "0080_supplier_allowance_merge"
PAYMENT = "0073_payment_test_plan"
PAYMENT_MERGE = "0081_admin_payment_merge"
SUPPORT = "0082_support_system"
HEAD = "0083_pay_as_you_go"


def test_release_exposes_one_head_containing_both_existing_histories():
    # 0064 (DNC phone_number compatibility trigger) sits on top of the merge;
    # 0065/0066 (bug hunt 2026-10-10 schema repairs), 0067/0068 (credential
    # history, API-key lookup hash), 0069 (AssemblyAI settings) and 0070 (PBX
    # line soft delete) remain common ancestors. Published paid billing and
    # connector/Admin histories now join through a metadata-only merge.
    assert SCRIPTS.get_heads() == [HEAD]
    # 0083 (pay as you go) sits on 0082 (support system), which sits on the
    # payment merge that joins both histories.
    assert SCRIPTS.get_revision(HEAD).down_revision == SUPPORT
    assert SCRIPTS.get_revision(SUPPORT).down_revision == PAYMENT_MERGE
    assert set(SCRIPTS.get_revision(PAYMENT_MERGE).down_revision) == {SUPPLIER_ALLOWANCE, PAYMENT}
    assert set(SCRIPTS.get_revision(SUPPLIER_ALLOWANCE).down_revision) == {SUPPLIER, ALLOWANCE}
    assert SCRIPTS.get_revision(PAYMENT).down_revision == ALLOWANCE
    assert SCRIPTS.get_revision(SUPPLIER).down_revision == PREVIOUS_HEAD
    assert SCRIPTS.get_revision(ALLOWANCE).down_revision == PAID
    assert set(SCRIPTS.get_revision(PREVIOUS_HEAD).down_revision) == {ADMIN, PAID}
    assert SCRIPTS.get_revision(PAID).down_revision == "0070_trunk_soft_delete"
    assert SCRIPTS.get_revision(ADMIN).down_revision == "0076_admin_user_operations"
    assert SCRIPTS.get_revision("0076_admin_user_operations").down_revision == "0075_admin_user_review"
    assert SCRIPTS.get_revision("0075_admin_user_review").down_revision == "0074_contact_enquiry_revision"
    assert SCRIPTS.get_revision("0074_contact_enquiry_revision").down_revision == "0073_alertmanager_incidents"
    assert SCRIPTS.get_revision("0073_alertmanager_incidents").down_revision == "0072_admin_incidents"
    assert SCRIPTS.get_revision("0072_admin_incidents").down_revision == "0071_connector_lifecycle"
    assert SCRIPTS.get_revision("0071_connector_lifecycle").down_revision == "0070_trunk_soft_delete"
    assert SCRIPTS.get_revision("0070_trunk_soft_delete").down_revision == "0069_assemblyai_settings"
    assert SCRIPTS.get_revision("0069_assemblyai_settings").down_revision == "0068_tenant_secret_lookup"
    assert SCRIPTS.get_revision("0068_tenant_secret_lookup").down_revision == "0067_ai_credentials_history"
    assert SCRIPTS.get_revision("0067_ai_credentials_history").down_revision == "0066_crm_skip_test_calls"
    assert SCRIPTS.get_revision("0066_crm_skip_test_calls").down_revision == "0065_stream_events_call_category"
    assert SCRIPTS.get_revision("0065_stream_events_call_category").down_revision == "0064_dnc_phone_number_default"
    assert SCRIPTS.get_revision("0064_dnc_phone_number_default").down_revision == MERGE
    merge = SCRIPTS.get_revision(MERGE)
    assert set(merge.down_revision) == {HEARTBEAT, RECEIPTS}
    assert SCRIPTS.get_revision(HEARTBEAT).down_revision == "0047_protect_ai_config_backup"
    assert SCRIPTS.get_revision("0048_crm_deliveries").down_revision == "0047_protect_ai_config_backup"
    assert SCRIPTS.get_revision(RECEIPTS).down_revision == "0061_dnc_runtime_contract"


@pytest.mark.parametrize("start,required,already_present", [
    (HEARTBEAT, {"0048_crm_deliveries", RECEIPTS, MERGE}, HEARTBEAT),
    (RECEIPTS, {HEARTBEAT, MERGE}, RECEIPTS),
])
def test_upgrade_plan_runs_only_the_missing_branch(start, required, already_present):
    # Match Alembic command.upgrade's cross-branch traversal.
    revisions = {r.revision for r in SCRIPTS.iterate_revisions(MERGE, start, implicit_base=True)}
    assert required <= revisions
    assert already_present not in revisions
    # The current deployment must also retain both branches behind its new head.
    head_revisions = {r.revision for r in SCRIPTS.iterate_revisions(HEAD, start, implicit_base=True)}
    assert required | {
        "0070_trunk_soft_delete", "0071_connector_lifecycle",
        "0072_admin_incidents", "0073_alertmanager_incidents", "0074_contact_enquiry_revision",
        "0075_admin_user_review", "0076_admin_user_operations", ADMIN, PAID,
        PREVIOUS_HEAD, SUPPLIER, ALLOWANCE, SUPPLIER_ALLOWANCE, PAYMENT, HEAD,
    } <= head_revisions
    assert already_present not in head_revisions


def test_merge_has_no_schema_or_evidence_mutation_and_refuses_history_split():
    migration = import_module("Alembic.versions.0063_release_history_merge")
    assert migration.upgrade() is None
    with pytest.raises(RuntimeError, match="forward migration"):
        migration.downgrade()


@pytest.mark.parametrize("start,expected", [
    (PAID, {"0071_connector_lifecycle", "0072_admin_incidents", "0073_alertmanager_incidents",
            "0074_contact_enquiry_revision", "0075_admin_user_review", "0076_admin_user_operations", ADMIN, PREVIOUS_HEAD}),
    (ADMIN, {PAID, PREVIOUS_HEAD}),
    ((ADMIN, PAID), {PREVIOUS_HEAD}),
    (PREVIOUS_HEAD, set()),
])
def test_current_release_upgrade_plan_never_replays_an_applied_branch(start, expected):
    # This is the planner used by command.upgrade, including a database with
    # two existing version rows. A paid deployment must not re-run catalog SQL.
    steps = SCRIPTS._upgrade_revs(PREVIOUS_HEAD, start)
    revisions = [step.revision.revision for step in steps]
    assert set(revisions) == expected
    assert len(revisions) == len(expected)
    if revisions:
        assert revisions[-1] == PREVIOUS_HEAD
    applied = {start} if isinstance(start, str) else set(start)
    assert not applied.intersection(revisions)


def test_admin_billing_merge_has_no_business_sql_and_refuses_history_split():
    migration = import_module("Alembic.versions.0078_admin_billing_merge")
    assert migration.upgrade() is None
    with pytest.raises(RuntimeError, match="forward migration"):
        migration.downgrade()


@pytest.mark.parametrize("start,expected", [
    (ALLOWANCE, {"0071_connector_lifecycle", "0072_admin_incidents", "0073_alertmanager_incidents",
                 "0074_contact_enquiry_revision", "0075_admin_user_review", "0076_admin_user_operations",
                 ADMIN, PREVIOUS_HEAD, SUPPLIER, SUPPLIER_ALLOWANCE}),
    (SUPPLIER, {ALLOWANCE, SUPPLIER_ALLOWANCE}),
    ((SUPPLIER, ALLOWANCE), {SUPPLIER_ALLOWANCE}),
    (PREVIOUS_HEAD, {SUPPLIER, ALLOWANCE, SUPPLIER_ALLOWANCE}),
    ((PREVIOUS_HEAD, ALLOWANCE), {SUPPLIER, SUPPLIER_ALLOWANCE}),
    (SUPPLIER_ALLOWANCE, set()),
])
def test_supplier_allowance_upgrade_plan_never_replays_paid_terms(start, expected):
    revisions = [step.revision.revision for step in SCRIPTS._upgrade_revs(SUPPLIER_ALLOWANCE, start)]
    assert set(revisions) == expected and len(revisions) == len(expected)
    assert PAID not in revisions
    if revisions:
        assert revisions[-1] == SUPPLIER_ALLOWANCE


def test_supplier_allowance_merge_has_no_business_sql_and_refuses_history_split():
    migration = import_module("Alembic.versions.0080_supplier_allowance_merge")
    assert migration.upgrade() is None
    with pytest.raises(RuntimeError, match="forward migration"):
        migration.downgrade()


@pytest.mark.parametrize("start,expected", [
    (SUPPLIER_ALLOWANCE, {PAYMENT, PAYMENT_MERGE}),
    (PAYMENT, {"0071_connector_lifecycle", "0072_admin_incidents", "0073_alertmanager_incidents",
               "0074_contact_enquiry_revision", "0075_admin_user_review", "0076_admin_user_operations",
               ADMIN, PREVIOUS_HEAD, SUPPLIER, SUPPLIER_ALLOWANCE, PAYMENT_MERGE}),
    ((SUPPLIER_ALLOWANCE, PAYMENT), {PAYMENT_MERGE}),
    ((SUPPLIER, PAYMENT), {SUPPLIER_ALLOWANCE, PAYMENT_MERGE}),
    (PAYMENT_MERGE, set()),
])
def test_payment_merge_runs_only_missing_revisions(start, expected):
    revisions = [step.revision.revision for step in SCRIPTS._upgrade_revs(PAYMENT_MERGE, start)]
    assert set(revisions) == expected and len(revisions) == len(expected)
    assert PAID not in revisions and ALLOWANCE not in revisions
    applied = {start} if isinstance(start, str) else set(start)
    assert not applied.intersection(revisions)
    if revisions:
        assert revisions[-1] == PAYMENT_MERGE


def test_payment_merge_has_no_business_sql_and_refuses_history_split():
    migration = import_module("Alembic.versions.0081_admin_payment_merge")
    assert migration.upgrade() is None
    with pytest.raises(RuntimeError, match="forward migration"):
        migration.downgrade()


def test_upgrade_after_the_payment_merge_runs_only_support_then_pay_as_you_go():
    revisions = [step.revision.revision for step in SCRIPTS._upgrade_revs("head", PAYMENT_MERGE)]
    assert revisions == [SUPPORT, HEAD]

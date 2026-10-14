"""Both released histories remain addressable behind one deployment head."""
from importlib import import_module
from pathlib import Path

from alembic.script import ScriptDirectory
import pytest

SCRIPTS = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "Alembic"))
HEARTBEAT = "0048_audit_skip_heartbeat"
RECEIPTS = "0062_saved_acknowledgement"
MERGE = "0063_release_history_merge"


def test_release_exposes_one_head_containing_both_existing_histories():
    # 0064 (DNC phone_number compatibility trigger) sits on top of the merge;
    # 0065/0066 (bug hunt 2026-10-10 schema repairs), 0067/0068 (credential
    # history, API-key lookup hash), 0069 (AssemblyAI settings) and 0070 (PBX
    # line soft delete) are linear above it, behind this single deployment head.
    assert SCRIPTS.get_heads() == ["0070_trunk_soft_delete"]
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


def test_merge_has_no_schema_or_evidence_mutation_and_refuses_history_split():
    migration = import_module("Alembic.versions.0063_release_history_merge")
    assert migration.upgrade() is None
    with pytest.raises(RuntimeError, match="forward migration"):
        migration.downgrade()

"""Static contract for the four-eye inbound billing migration."""

from __future__ import annotations

import importlib
import inspect


def test_0034_is_single_forward_revision_with_durable_four_eye_state() -> None:
    migration = importlib.import_module("Alembic.versions.0034_inbound_billing_four_eye")
    source = inspect.getsource(migration)

    assert migration.revision == "0034_inbound_billing_four_eye"
    assert migration.down_revision == "0033_bootstrap_contract_repair"
    assert "CREATE TABLE inbound_billing_hold_finalize_approvals" in source
    assert "FOREIGN KEY (call_id, tenant_id)" in source
    assert "UNIQUE (tenant_id, call_id)" in source
    assert "approved_by <> requested_by" in source
    assert "resolution_hash CHAR(64) NOT NULL" in source
    assert "inbound_hold_finalize_request_key_unique" in source
    assert "inbound_hold_finalize_approval_key_unique" in source


def test_0034_database_enforces_immutable_distinct_approval() -> None:
    migration = importlib.import_module("Alembic.versions.0034_inbound_billing_four_eye")
    source = inspect.getsource(migration)

    assert "OLD.requested_by IS DISTINCT FROM NEW.requested_by" in source
    assert "OLD.resolution_hash IS DISTINCT FROM NEW.resolution_hash" in source
    assert "NEW.approved_by = OLD.requested_by" in source
    assert "OLD.status <> 'pending'" in source
    assert "NEW.status <> 'approved'" in source
    assert "ENABLE ALWAYS TRIGGER inbound_hold_finalize_approval_transition" in source
    assert 'trigger_contract["tgtype"] != 27' in source
    assert 'trigger_contract["tgenabled"] != "A"' in source
    assert 'not trigger_contract["canonical_function"]' in source


def test_0034_downgrade_refuses_to_destroy_approval_evidence() -> None:
    migration = importlib.import_module("Alembic.versions.0034_inbound_billing_four_eye")
    source = inspect.getsource(migration.downgrade)

    assert "LOCK TABLE inbound_billing_hold_finalize_approvals" in source
    assert "SELECT count(*)" in source
    assert "Refusing to downgrade 0034" in source
    assert source.index("if retained") < source.index("DROP TABLE")

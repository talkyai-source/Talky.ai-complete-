from __future__ import annotations

import importlib
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "Alembic" / "versions" / "0032_inbound_billing_hold.py"


def test_0032_persists_only_reasoned_inbound_billing_holds() -> None:
    module = importlib.import_module("Alembic.versions.0032_inbound_billing_hold")
    source = MIGRATION.read_text(encoding="utf-8")

    assert module.revision == "0032_inbound_billing_hold"
    assert module.down_revision == "0031_inbound_lease_safety"
    assert len(module.revision) <= 32
    assert "ADD COLUMN IF NOT EXISTS" in source
    assert "billing_hold_reason VARCHAR(64)" in source
    assert "'settlement_switch_disabled'" in source
    assert "'usage_exceeded_reservation'" in source
    assert "'provider_answer_ambiguous'" in source
    assert "lease.release_reason" in source
    assert "VALIDATE CONSTRAINT" in source
    assert "CREATE INDEX IF NOT EXISTS" in source


def test_0032_downgrade_refuses_to_delete_hold_evidence() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade()") :]

    assert downgrade.index("IN ACCESS EXCLUSIVE MODE") < downgrade.index(
        "billing_hold_reason IS NOT NULL"
    )
    assert "Refusing to downgrade 0032" in downgrade
    assert downgrade.index("billing_hold_reason IS NOT NULL") < downgrade.index(
        "DROP COLUMN IF EXISTS billing_hold_reason"
    )

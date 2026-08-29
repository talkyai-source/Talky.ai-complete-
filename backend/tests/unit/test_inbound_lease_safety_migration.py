from __future__ import annotations

import importlib
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "Alembic" / "versions" / "0031_inbound_lease_safety.py"
FOUNDATION = BACKEND / "Alembic" / "versions" / "0022_inbound_calling_foundation.py"


def test_0031_normalizes_and_enforces_the_heartbeat_window() -> None:
    module = importlib.import_module("Alembic.versions.0031_inbound_lease_safety")
    source = MIGRATION.read_text(encoding="utf-8")

    assert module.revision == "0031_inbound_lease_safety"
    assert module.down_revision == "0030_inbound_transfer_leg_usage"
    assert len(module.revision) <= 32
    assert "IN ACCESS EXCLUSIVE MODE" in source
    assert "GREATEST(" in source
    assert "lease_ttl_seconds + heartbeat_grace_seconds" in source
    assert "VALIDATE CONSTRAINT" in source
    assert "def downgrade() -> None" in source
    assert "DROP CONSTRAINT" not in source


def test_fresh_inbound_foundation_has_the_same_minimum_window() -> None:
    source = FOUNDATION.read_text(encoding="utf-8")

    assert "telephony_policy_heartbeat_window_safe" in source
    assert "lease_ttl_seconds + heartbeat_grace_seconds >= 90" in source

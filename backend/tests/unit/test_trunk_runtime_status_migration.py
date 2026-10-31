from __future__ import annotations

import importlib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "Alembic" / "versions" / "0029_trunk_runtime_status.py"


def test_0029_chains_from_terminal_settlement_and_fits_version_column():
    module = importlib.import_module("Alembic.versions.0029_trunk_runtime_status")
    assert module.revision == "0029_trunk_runtime_status"
    assert module.down_revision == "0028_call_terminal_settle_cas"
    assert len(module.revision) <= 32


def test_live_status_columns_are_on_the_single_alembic_deployment_path():
    source = MIGRATION.read_text(encoding="utf-8")
    for column in (
        "live_registration_status",
        "live_status_detail",
        "live_status_checked_at",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in source
        assert f"DROP COLUMN IF EXISTS {column}" not in source
    assert "idx_tenant_sip_trunks_active_live_status" in source
    assert "pre-existing calls.dialer_job_id" in source


def test_runtime_consumers_use_the_migrated_columns():
    service = (BACKEND / "app" / "domain" / "services" / "inbound_campaign_service.py").read_text(
        encoding="utf-8"
    )
    admission = (
        BACKEND / "app" / "domain" / "services" / "telephony" / "inbound_admission.py"
    ).read_text(encoding="utf-8")
    updater = (BACKEND / "scripts" / "trunk_live_status_updater.py").read_text(encoding="utf-8")
    for source in (service, admission, updater):
        assert "live_registration_status" in source
        assert "live_status_checked_at" in source

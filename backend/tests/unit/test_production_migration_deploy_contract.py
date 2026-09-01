"""Static guards for production migration, release, and deploy contracts."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

BACKEND = Path(__file__).parents[2]
ROOT = BACKEND.parent


def _load_alembic_head_verifier():
    script = BACKEND / "scripts" / "verify_alembic_current_heads.py"
    spec = importlib.util.spec_from_file_location("verify_alembic_current_heads", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_docs_never_stamp_historical_snapshot_at_head():
    docs = (BACKEND / "database" / "MIGRATIONS.md").read_text(encoding="utf-8")

    assert "alembic stamp 0021_billing_topup" not in docs
    assert "alembic stamp 0008_tenant_voice_tuning" in docs
    assert "old 0021 stamp shortcut" in docs
    assert "Never stamp an old snapshot at `head`" in docs
    assert "alembic stamp head      # or:" not in docs


def test_deploy_runs_migrations_before_application_restart():
    deploy = (ROOT / "deploy_to_server.sh").read_text(encoding="utf-8")

    install_at = deploy.index("sudo bash backend/systemd/install-services.sh")
    migrate_at = deploy.index("sudo systemctl start talky-migrate.service")
    verify_at = deploy.index("backend/scripts/verify_alembic_current_heads.py")
    restart_at = deploy.index("sudo systemctl restart talky-api talky-dialer-worker")
    assert install_at < migrate_at < verify_at < restart_at
    assert "sudo systemctl start talky-trunk-status.service" in deploy


def test_alembic_current_heads_verifier_accepts_exact_match_and_rejects_drift():
    verifier = _load_alembic_head_verifier()

    class RepositoryScripts:
        @staticmethod
        def get_heads():
            return ["0036_inbound_rejection_log"]

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO alembic_version (version_num) VALUES " "('0036_inbound_rejection_log')"
            )

        with engine.connect() as connection:
            assert verifier.verify_current_heads(connection, RepositoryScripts()) == (
                {"0036_inbound_rejection_log"},
                {"0036_inbound_rejection_log"},
            )

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE alembic_version SET version_num = '0035_inbound_hardening'"
            )

        with engine.connect() as connection:
            with pytest.raises(RuntimeError, match="database heads do not match repository heads"):
                verifier.verify_current_heads(connection, RepositoryScripts())

        class MultipleRepositoryHeads:
            @staticmethod
            def get_heads():
                return ["0035_inbound_hardening", "0036_inbound_rejection_log"]

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO alembic_version (version_num) VALUES "
                "('0036_inbound_rejection_log')"
            )

        with engine.connect() as connection:
            with pytest.raises(RuntimeError, match="exactly one Alembic head"):
                verifier.verify_current_heads(connection, MultipleRepositoryHeads())
    finally:
        engine.dispose()


def test_deploy_fails_closed_on_inactive_services_or_unhealthy_api():
    deploy = (ROOT / "deploy_to_server.sh").read_text(encoding="utf-8")

    assert "if [ \\\"\\$state\\\" != 'active' ]" in deploy
    assert 'if [ \\"\\$service_failure\\" -ne 0 ]' in deploy
    assert "curl -fsS --max-time 10 http://127.0.0.1:8000/health" in deploy
    assert ("curl -fsS --max-time 10 " "http://127.0.0.1:8000/api/v1/healthz/ready") in deploy


def test_loadtest_cli_is_executable_not_a_silent_noop():
    script = BACKEND / "scripts" / "loadtest_calls.py"

    source = script.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "exit_code = _result_exit_code(" in source
    assert "if exit_code:" in source
    assert "minimum_originated=args.minimum_originated" in source
    assert "required_peak_live=args.required_peak_live" in source
    assert '"X-Internal-Service-Token": internal_token' in source
    assert "json=payload" in source
    assert "params=params" not in source
    assert "raise SystemExit(exit_code)" in source
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--request-workers" in result.stdout
    assert "--concurrent" not in result.stdout
    assert "--minimum-originated" in result.stdout
    assert "--required-peak-live" in result.stdout
    assert "--duration" in result.stdout


def test_cluster_soak_runner_cleans_up_and_never_claims_release_acceptance():
    source = (BACKEND / "scripts" / "soak_runner.sh").read_text(encoding="utf-8")

    assert 'PYTHON_BIN="${SOAK_PYTHON_BIN:-./backend/venv/bin/python}"' in source
    assert "trap cleanup EXIT" in source
    assert "trap 'exit 130' INT" in source
    assert "trap 'exit 143' TERM" in source
    assert "CHAOS_CLEANUP_REQUIRED=1" in source
    assert "--ignore-not-found" in source
    assert 'if wait "$LOAD_PID"; then' in source
    assert "SNAPSHOT_FAILURES" in source
    assert 'PROM_METRIC="talky_telephony_calls_setup_attempts"' in source
    assert "Prometheus result is empty" in source
    assert "driver alone is not a release verdict" in source
    assert "./venv/bin/python backend/scripts/loadtest_calls.py" not in source


def test_restore_runbook_uses_new_isolated_database_and_validates_evidence():
    runbook = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")

    assert "createdb -U talky -O talky --template=template0" in runbook
    assert 'pg_restore -U talky -d "${restore_db}" --exit-on-error' in runbook
    assert "sha256sum --check" in runbook
    assert "SELECT version_num FROM alembic_version" in runbook
    assert "application compatibility smoke test" in runbook
    assert "never use the production `talky`" in runbook


def test_inbound_release_soak_disables_transfer_scenarios_and_is_not_overclaimed():
    release_gate = (ROOT / "docs" / "INBOUND_CALLING_RELEASE_GATE.md").read_text(encoding="utf-8")

    assert "DAY10_PROFILE_TRANSFER_PERCENT=0" in release_gate
    assert "DAY10_PROFILE_BASELINE_PERCENT=100" in release_gate
    assert "DAY10_PROFILE_BARGEIN_PERCENT=0" in release_gate
    assert "DAY10_REQUIRE_TRANSFER=0" in release_gate
    assert "DAY10_REQUIRE_BARGEIN_REACTION=0" in release_gate
    assert "DAY10_REQUIRE_EXTERNAL_LIVE_MEDIA_EVIDENCE=1" in release_gate
    assert "DAY10_EXTERNAL_LIVE_MEDIA_EVIDENCE_JSON=" in release_gate
    assert "does not bind that port, send RTP, receive RTP" in release_gate
    assert "`status: not_measured` with `pass: null`" in release_gate
    assert "does **not** by itself prove the required 300" in release_gate
    assert "any `transfer_*` trace event" in release_gate
    assert "canary_stage_controller.sh set 100" in release_gate
    assert "do not add\n`--skip-gates`" in release_gate
    assert "canary_set_stage.sh 100" not in release_gate


def test_transfer_proof_runbook_opens_and_closes_both_staging_gates():
    release_gate = (ROOT / "docs" / "INBOUND_CALLING_RELEASE_GATE.md").read_text(encoding="utf-8")

    assert "Controlled transfer proof window (staging only)" in release_gate
    assert "INBOUND_TRANSFER_STAGING_PROOF_ENABLED=true" in release_gate
    assert "INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID=" in release_gate
    assert "INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID=" in release_gate
    assert "scan **all tenants**" in release_gate
    assert "capabilities?config_id=${TRANSFER_PROOF_CONFIG_ID}" in release_gate
    assert '"${STAGING_API}/inbound-campaigns/capabilities"' in release_gate
    assert "config_id=${OTHER_TEST_CONFIG_ID}" in release_gate
    assert "Authorization: Bearer ${OTHER_TENANT_TOKEN}" in release_gate
    assert "config_id=${OTHER_TENANT_CONFIG_ID}" in release_gate
    assert "transfer-proof-open-${TRANSFER_PROOF_CHANGE_ID}" in release_gate
    assert "transfer_configuration_available == true" in release_gate
    assert "transfer-proof-close-${TRANSFER_PROOF_CHANGE_ID}" in release_gate
    assert "INBOUND_TRANSFER_STAGING_PROOF_ENABLED=false" in release_gate
    assert "reset-to-false step is mandatory" in release_gate
    assert "unset both" in release_gate
    assert "transfer_configuration_available == false" in release_gate
    assert "transfer_policy @>" in release_gate
    assert "must return zero rows across every tenant" in release_gate


def test_migration_unit_uses_backend_environment_and_current_head():
    unit = (BACKEND / "systemd" / "talky-migrate.service").read_text(encoding="utf-8")

    assert "Type=oneshot" in unit
    assert "EnvironmentFile=/opt/talky/backend/.env" in unit
    assert "alembic.ini upgrade head" in unit
    assert "[Install]" not in unit


def test_installer_reconciles_and_enables_trunk_status_timer():
    installer = (BACKEND / "systemd" / "install-services.sh").read_text(encoding="utf-8")

    # The units were consolidated into backend/systemd/ on 2026-08-27; the
    # installer's glob must therefore cover .timer units in its own directory
    # and the timer must be enabled so trunk evidence keeps refreshing.
    assert '"$SCRIPT_DIR"/*.timer' in installer
    assert "systemctl enable talky-trunk-status.timer" in installer


def test_inbound_foundation_downgrade_locks_every_guarded_writer():
    migration = (BACKEND / "Alembic" / "versions" / "0022_inbound_calling_foundation.py").read_text(
        encoding="utf-8"
    )
    downgrade = migration[migration.index("def downgrade()") :]
    lock_at = downgrade.index("LOCK TABLE")
    guard_at = downgrade.index("retained_rows =")

    assert lock_at < guard_at
    for table in (
        "inbound_campaign_configs",
        "inbound_did_assignments",
        "tenant_inbound_controls",
        "inbound_usage_transactions",
        "inbound_reassignment_requests",
        "inbound_operation_idempotency",
        "inbound_audit_events",
        "campaigns",
        "calls",
        "platform_runtime_controls",
    ):
        assert table in downgrade[lock_at:guard_at]
    assert "IN ACCESS EXCLUSIVE MODE" in downgrade[lock_at:guard_at]

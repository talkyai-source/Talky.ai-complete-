"""Merged A1 + backup guard: unsafe recovery cannot restart stopped writers."""

from tests.unit import test_bh_ops_scripts_deploy as ops
from tests.unit import test_deploy_recovery as recovery
from tests.unit import test_deploy_service_recovery as service


captured_remote_script = ops.captured_remote_script
recovery_shims = recovery.recovery_shims
service_shims = service.service_shims
pytestmark = ops.pytestmark

# Inactive one-shots must not enter the recorded stop set or recovery command.
INACTIVE_UNITS = "talky-cleanup talky-support-jobs"
STOPPED_UNITS = [unit for unit in service.UNITS if unit not in INACTIVE_UNITS.split()]


def _assert_failed_recovery_keeps_writers_stopped(tmp_path, result, log):
    assert result.returncode != 0
    assert service._stopped(log) == STOPPED_UNITS
    assert service._started(log) == []
    assert not any(line.startswith("sudo systemctl start ") for line in log)
    assert not any("systemctl restart talky-api" in line for line in log)
    assert not any("cp -a --remove-destination" in line for line in log)
    assert len([line for line in log if "install-services.sh" in line]) == 1
    assert (tmp_path / "units/talky-api-web.service").read_text() == "repository unit\n"
    for unit in STOPPED_UNITS:
        assert (tmp_path / "state" / f"stopped-{unit}").exists()

    diagnostics = result.deploy_log
    assert "Keep ingress frozen." in diagnostics
    assert "After verifying code/schema compatibility, recover the stopped units with:" in diagnostics
    command = "sudo systemctl start " + " ".join(STOPPED_UNITS)
    commands = [line for line in diagnostics.splitlines() if line.startswith("sudo systemctl start")]
    assert commands == [command]
    assert diagnostics.count("DEPLOY_RESULT ") == 1
    assert "DEPLOY_RESULT status=failed" in diagnostics


def test_unsafe_fallback_after_writer_stop_never_starts_or_replaces_units(
    tmp_path, captured_remote_script, recovery_shims, service_shims,
):
    result, log, head = ops._run_remote(
        tmp_path,
        captured_remote_script,
        INACTIVE_UNITS=INACTIVE_UNITS,
        FAKE_GATEWAY_READY_JSON='{"ready": false}',
        UNSAFE_FALLBACK="1",
    )
    _assert_failed_recovery_keeps_writers_stopped(tmp_path, result, log)
    assert head == ops.remote_sha(captured_remote_script)
    checkouts = [line for line in log if line.startswith("git checkout --detach")]
    assert len(checkouts) == 1
    assert "ROLLBACK BLOCKED" in result.deploy_log
    assert "rollback=blocked" in result.deploy_log


def test_failed_fallback_checkout_after_writer_stop_never_starts_or_copies_old_unit(
    tmp_path, captured_remote_script, recovery_shims, service_shims,
):
    result, log, head = ops._run_remote(
        tmp_path,
        captured_remote_script,
        INACTIVE_UNITS=INACTIVE_UNITS,
        FAKE_GATEWAY_READY_JSON='{"ready": false}',
        FAIL_FALLBACK_CHECKOUT="1",
    )
    _assert_failed_recovery_keeps_writers_stopped(tmp_path, result, log)
    assert head == ops.remote_sha(captured_remote_script)
    checkouts = [line for line in log if line.startswith("git checkout --detach")]
    assert len(checkouts) == 2
    assert checkouts[-1].endswith(ops.PREV_SHA)
    assert "Fallback checkout failed; retaining candidate unit definitions." in result.deploy_log
    assert "rollback=failed" in result.deploy_log

"""A1: real deploy shell with local command stubs; no SSH or host mutation."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.unit import test_bh_ops_scripts_deploy as ops

captured_remote_script = ops.captured_remote_script
pytestmark = ops.pytestmark
UNITS = (
    "talky-cleanup.timer", "talky-support-jobs.timer", "talky-api", "talky-api-web",
    "talky-dialer-worker", "talky-voice-worker", "talky-reminder-worker",
    "talky-trunk-status", "talky-cleanup", "talky-support-jobs",
)


@pytest.fixture
def service_shims(monkeypatch):
    controls = '''
case "$*" in
  *prepare-service-user.sh*--check)
    [ "${FAIL_SERVICE_CHECK:-0}" != 1 ] || exit 1
    exit 0 ;;
  "systemctl stop "*)
    for unit in "${@:3}"; do
      echo "unit-stop $unit" >> "$LOG"
      touch "$STATE/stopped-$unit"
      if [ "${FAIL_PARTIAL_STOP:-0}" = 1 ]; then exit 1; fi
    done ;;
  "systemctl start "*)
    for unit in "${@:3}"; do
      echo "unit-start $unit" >> "$LOG"
      rm -f "$STATE/stopped-$unit"
    done ;;
  "bash backend/systemd/install-services.sh")
    if [ "${FAIL_FALLBACK_INSTALL:-0}" = 1 ] && [ "$(cat "$STATE/head")" != "$FAKE_NEW_SHA" ]; then exit 1; fi ;;
esac
'''
    monkeypatch.setitem(ops.STUBS, "sudo", ops.STUBS["sudo"].replace(
        'echo "sudo $*" >> "$LOG"', 'echo "sudo $*" >> "$LOG"\n' + controls, 1,
    ))
    monkeypatch.setitem(ops.STUBS, "systemctl", ops.STUBS["systemctl"].replace(
        "is-active) echo active ;;", '''is-active)
    unit="${@: -1}"
    if [[ " ${INACTIVE_UNITS:-} " == *" $unit "* ]] || [ -f "$STATE/stopped-$unit" ]; then exit 3; fi
    echo active ;;''', 1,
    ))


def _started(log):
    return [line.split()[1] for line in log if line.startswith("unit-start ") and line.split()[1] in UNITS]


def _stopped(log):
    return [line.split()[1] for line in log if line.startswith("unit-stop ")]


def test_service_check_failure_precedes_any_stop(tmp_path, captured_remote_script, service_shims):
    result, log, head = ops._run_remote(tmp_path, captured_remote_script, FAIL_SERVICE_CHECK="1")
    assert result.returncode != 0
    assert head == ops.PREV_SHA
    assert any("prepare-service-user.sh" in line and line.endswith(" --check") for line in log)
    assert not _stopped(log)
    assert not _started(log)
    assert not any(line.startswith("git checkout") for line in log)


def test_failed_identity_apply_restores_only_previously_active_units(tmp_path, captured_remote_script, service_shims):
    inactive = "talky-cleanup talky-support-jobs"
    result, log, head = ops._run_remote(
        tmp_path, captured_remote_script, FAIL_SERVICE_USER="1", INACTIVE_UNITS=inactive,
    )
    assert result.returncode != 0 and head == ops.PREV_SHA
    expected = [unit for unit in UNITS if unit not in inactive.split()]
    assert _stopped(log) == expected
    assert sorted(_started(log)) == sorted(expected)
    assert len(_started(log)) == len(set(_started(log)))
    restore = ops._index(log, "sudo cp -a --remove-destination")
    assert restore < ops._index(log, "unit-start ")
    assert not any("systemctl start talky-migrate" in line for line in log)


def test_partial_stop_failure_restores_exact_stopped_set(tmp_path, captured_remote_script, service_shims):
    result, log, head = ops._run_remote(tmp_path, captured_remote_script, FAIL_PARTIAL_STOP="1")
    assert result.returncode != 0 and head == ops.PREV_SHA
    assert _stopped(log) == ["talky-cleanup.timer"]
    assert _started(log) == _stopped(log)


def test_failed_fallback_never_starts_apps(tmp_path, captured_remote_script, service_shims):
    result, log, _ = ops._run_remote(
        tmp_path, captured_remote_script, FAIL_SERVICE_USER="1", FAIL_FALLBACK_INSTALL="1",
    )
    assert result.returncode != 0 and _stopped(log)
    assert not _started(log)
    diagnostics = result.stderr + getattr(result, "deploy_log", "")
    assert "ROLLBACK FAILED" in diagnostics


@pytest.mark.parametrize("failure", ["FAIL_MIGRATE", "FAIL_RECONCILE"])
def test_after_migration_start_prints_exact_recovery_command_without_restart(
    tmp_path, captured_remote_script, service_shims, failure,
):
    result, log, head = ops._run_remote(tmp_path, captured_remote_script, **{failure: "1"})
    assert result.returncode != 0 and head == ops.remote_sha(captured_remote_script)
    assert not _started(log)
    assert len([line for line in log if line.startswith("git checkout")]) == 1
    diagnostics = result.stderr + getattr(result, "deploy_log", "")
    assert "After verifying code/schema compatibility, recover the stopped units with:" in diagnostics
    assert "sudo systemctl start " + " ".join(UNITS) in diagnostics


def _run_prepare(tmp_path: Path, *, missing_user=False, active=True, args="--check"):
    """Redirect all filesystem paths and every mutator to a disposable fixture."""
    root, shim = tmp_path / "fixture", tmp_path / "shim"
    backend = root / "opt/talky/backend"
    backend.mkdir(parents=True)
    (backend / ".env").write_text("", encoding="utf-8")  # synthetic empty fixture, never read
    log = tmp_path / "commands.log"
    log.write_text("", encoding="utf-8")
    script = tmp_path / "prepare.sh"
    source = (ops.ROOT / "backend/deploy/infra/prepare-service-user.sh").read_text(encoding="utf-8")
    source = source.replace('[[ ${EUID} -eq 0 ]]', '[[ 0 -eq 0 ]]')
    for prefix in ("/opt/", "/etc/"):
        source = source.replace(prefix, ops._no_spaces(root) + prefix)
    ops._write(script, source)
    for name in ("useradd", "chgrp", "chmod", "chown", "install", "find"):
        ops._write(shim / name, f'#!/usr/bin/env bash\necho "MUTATE {name} $*" >> "$LOG"\n')
    ops._write(shim / "getent", '#!/usr/bin/env bash\nexit 0\n')
    ops._write(shim / "systemctl", '#!/usr/bin/env bash\nexit "$ACTIVE_CODE"\n')
    ops._write(shim / "id", '''#!/usr/bin/env bash
if [ "$MISSING_USER" = 1 ]; then exit 1; fi
case "${1:-}" in -u) echo 1001 ;; -gn|-nG) echo talky ;; esac
''')
    env = dict(os.environ, LOG=ops._no_spaces(log), MISSING_USER=str(int(missing_user)), ACTIVE_CODE="0" if active else "3")
    result = ops._launch(f'exec bash "{ops._no_spaces(script)}" {args}', env, tmp_path, shim)
    return result, log.read_text(encoding="utf-8")


@pytest.mark.parametrize("missing_user", [False, True])
def test_prepare_check_is_read_only_while_services_run(tmp_path, missing_user):
    result, log = _run_prepare(tmp_path, missing_user=missing_user)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not log


def test_prepare_rejects_unknown_mode_without_mutation(tmp_path):
    result, log = _run_prepare(tmp_path, active=False, args="--typo")
    assert result.returncode != 0
    assert not log

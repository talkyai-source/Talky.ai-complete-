"""F2-15: actual read-only timer helper and existing all-stub deploy harness."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil

import pytest

_fixture_path = Path(__file__).with_name("test_bh_ops_scripts_deploy.py")
_fixture_spec = importlib.util.spec_from_file_location("round3_timer_deploy_fixture", _fixture_path)
ops = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(ops)
captured_remote_script = ops.captured_remote_script
pytestmark = ops.pytestmark
ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "backend/deploy/verify-service-timers.sh"
RETIRED = "talky-trunk-status.timer"
TIMERS = sorted(path.name for path in (ROOT / "backend/systemd").glob("*.timer"))
ACTIVE_TIMERS = [name for name in TIMERS if name != RETIRED]


def _run_helper(tmp_path, *, bad_unit="", bad_property="", bad_value="", extra=False, empty=False, masked_retired=False):
    assert HELPER.is_file(), "F2-15 is missing the read-only complete timer verifier"
    fake = tmp_path / "backend"
    units = fake / "systemd"
    units.mkdir(parents=True)
    if not empty:
        for name in TIMERS:
            shutil.copy(ROOT / "backend/systemd" / name, units / name)
    if extra:
        (units / "talky-future-check.timer").write_text("[Timer]\nOnBootSec=1min\n", encoding="utf-8")
    helper = fake / "deploy/verify-service-timers.sh"
    ops._write(helper, HELPER.read_text(encoding="utf-8"))
    shim = tmp_path / "shim"
    log = tmp_path / "commands.log"
    log.write_text("", encoding="utf-8")
    ops._write(shim / "systemctl", '''#!/usr/bin/env bash
echo "$*" >> "$TIMER_TEST_LOG"
case "$1" in
  show) property=load; value=loaded ;;
  is-enabled) property=enabled; value=enabled ;;
  is-active) property=active; value=active ;;
  *) echo unexpected-mutation >&2; exit 97 ;;
esac
unit="$2"
if [ "$unit" = talky-trunk-status.timer ]; then
  case "$property" in enabled) value=disabled ;; active) value=inactive ;; esac
  if [ "${MASKED_RETIRED:-0}" = 1 ]; then
    case "$property" in load|enabled) value=masked ;; esac
  fi
fi
if [ "$unit" = "${BAD_UNIT:-}" ] && [ "$property" = "${BAD_PROPERTY:-}" ]; then value="$BAD_VALUE"; fi
printf '%s\n' "$value"
case "$value" in disabled|masked|not-found) exit 1 ;; inactive|failed) exit 3 ;; esac
''')
    env = dict(os.environ)
    env.update(TIMER_TEST_LOG=ops._no_spaces(log), BAD_UNIT=bad_unit,
               BAD_PROPERTY=bad_property, BAD_VALUE=bad_value,
               MASKED_RETIRED="1" if masked_retired else "0",
               TALKY_PROD_HOST="nobody@example.invalid", TALKY_PROD_KEY="synthetic-unused-key")
    result = ops._launch(f'exec bash "{ops._no_spaces(helper)}"', env, tmp_path, shim)
    return result, log.read_text(encoding="utf-8").splitlines()


def test_every_current_timer_is_checked_and_retired_timer_is_not_required_active(tmp_path):
    result, log = _run_helper(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "talky-model-catalog.timer" in TIMERS and "talky-recording-discrepancy.timer" in TIMERS
    for name in TIMERS:
        for command in ("show", "is-enabled", "is-active"):
            assert any(line.startswith(f"{command} {name}") for line in log), (name, command, log)
    assert all(line.split()[0] in {"show", "is-enabled", "is-active"} for line in log)


@pytest.mark.parametrize("timer", ACTIVE_TIMERS)
@pytest.mark.parametrize(("property_name", "value"), [("enabled", "disabled"), ("active", "inactive")])
def test_any_disabled_or_dead_current_timer_fails_verification(tmp_path, timer, property_name, value):
    result, _ = _run_helper(tmp_path, bad_unit=timer, bad_property=property_name, bad_value=value)
    assert result.returncode != 0
    assert timer in result.stdout + result.stderr


@pytest.mark.parametrize(("property_name", "value"), [("load", "not-found"), ("active", "failed"), ("enabled", "enabled-runtime"), ("enabled", "")])
def test_missing_failed_runtime_only_or_unreadable_timer_is_not_certified(tmp_path, property_name, value):
    result, _ = _run_helper(tmp_path, bad_unit="talky-db-backup.timer", bad_property=property_name, bad_value=value)
    assert result.returncode != 0
    assert "talky-db-backup.timer" in result.stdout + result.stderr


@pytest.mark.parametrize(("property_name", "value"), [("enabled", "enabled"), ("active", "active")])
def test_retired_trunk_timer_must_remain_disabled_and_inactive(tmp_path, property_name, value):
    result, _ = _run_helper(tmp_path, bad_unit=RETIRED, bad_property=property_name, bad_value=value)
    assert result.returncode != 0
    assert RETIRED in result.stdout + result.stderr


def test_retired_masked_timer_is_accepted_when_inactive(tmp_path):
    result, _ = _run_helper(tmp_path, masked_retired=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "talky-trunk-status.timer load=masked enabled=masked active=inactive" in result.stdout


def test_new_timer_file_is_automatically_verified_without_allowlist_update(tmp_path):
    result, log = _run_helper(tmp_path, extra=True, bad_unit="talky-future-check.timer", bad_property="active", bad_value="inactive")
    assert result.returncode != 0
    assert "talky-future-check.timer" in result.stdout + result.stderr
    assert any(line.startswith("is-active talky-future-check.timer") for line in log)


def test_empty_timer_inventory_fails_closed(tmp_path):
    result, _ = _run_helper(tmp_path, empty=True)
    assert result.returncode != 0


@pytest.fixture
def timer_deploy_shim(monkeypatch):
    fragment = '''
if [ "$*" = "bash backend/deploy/verify-service-timers.sh" ]; then
  echo timer-verification >> "$LOG"
  [ "${FAIL_TIMER_VERIFY:-0}" != 1 ] || exit 1
  exit 0
fi
'''
    monkeypatch.setitem(ops.STUBS, "sudo", ops.STUBS["sudo"].replace(
        'echo "sudo $*" >> "$LOG"', 'echo "sudo $*" >> "$LOG"\n' + fragment, 1,
    ))


def test_deploy_runs_timer_verification_after_service_starts_before_success(tmp_path, captured_remote_script, timer_deploy_shim):
    result, log, _ = ops._run_remote(tmp_path, captured_remote_script,
                                   TALKY_PROD_HOST="nobody@example.invalid", TALKY_PROD_KEY="synthetic-unused-key")
    assert result.returncode == 0, result.stdout + result.stderr + result.deploy_log
    verified = ops._index(log, "timer-verification")
    assert ops._index(log, "sudo systemctl start talky-cleanup.timer talky-support-jobs.timer") < verified
    assert verified < ops._index(log, "curl -fsS --max-time 10 http://127.0.0.1:8000/health")


def test_dead_timer_blocks_deploy_success_without_unsafe_postmigration_rollback(tmp_path, captured_remote_script, timer_deploy_shim):
    result, log, head = ops._run_remote(tmp_path, captured_remote_script, FAIL_TIMER_VERIFY="1",
                                      TALKY_PROD_HOST="nobody@example.invalid", TALKY_PROD_KEY="synthetic-unused-key")
    assert result.returncode != 0
    assert "timer-verification" in log
    assert head == ops.remote_sha(captured_remote_script)
    assert len([line for line in log if line.startswith("git checkout --detach")]) == 1
    assert "DEPLOY_RESULT status=failed" in result.deploy_log
    assert not any(":8000/health" in line for line in log)

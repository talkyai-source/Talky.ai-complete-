"""Report 08: execute the canonical deploy shell against disposable local shims."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from tests.unit import test_bh_ops_scripts_deploy as ops

captured_remote_script = ops.captured_remote_script


def test_controller_guard_is_bootstrapped_independent_of_older_target(tmp_path):
    repo, origin, shim = (tmp_path / name for name in ("repo", "origin.git", "shim"))
    repo.mkdir()
    shim.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=repo, text=True, stderr=subprocess.PIPE,
        ).strip()

    (repo / "README").write_text("old target without the guard\n")
    git("add", "README")
    git("commit", "-q", "-m", "legacy target")
    target = git("rev-parse", "HEAD")
    shutil.copy(ops.DEPLOY, repo / "deploy_to_server.sh")
    ops._write(repo / "backend/scripts/verify_deploy_drain_manifest.py", "import sys\nsys.exit(0)\n")
    shutil.copy(ops.ROOT / "backend/scripts/verify_code_rollback.py", repo / "backend/scripts/verify_code_rollback.py")
    git("add", "-A")
    git("commit", "-q", "-m", "trusted controller")
    controller = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(origin))
    git("push", "-q", "origin", "main")
    capture = tmp_path / "captured.sh"
    ops._write(shim / "ssh", '#!/usr/bin/env bash\nprintf "%s" "${@: -1}" > "$CAPTURE_FILE"\n')
    env = dict(os.environ, CAPTURE_FILE=str(capture), TALKY_DEPLOY_SHA=target,
               TALKY_DEPLOY_DRAIN_MANIFEST="synthetic", TALKY_DEPLOY_DRAIN_MANIFEST_SHA256="synthetic",
               TALKY_LOCAL_PYTHON=sys.executable, TALKY_PROD_KEY="synthetic", TALKY_PROD_HOST="nobody@example.invalid")
    result = ops._launch("exec bash deploy_to_server.sh", env, repo, shim)
    assert result.returncode == 0, result.stdout + result.stderr
    remote = capture.read_text()
    assert controller != target
    assert f"git show '{controller}:backend/scripts/verify_code_rollback.py'" in remote
    assert f"git checkout --detach '{target}'" in remote
    assert f"git show '{target}:backend/scripts/verify_code_rollback.py'" not in remote


@pytest.fixture
def recovery_shims(monkeypatch):
    controls = '''
noninteractive=0
if [ "${1:-}" = -n ]; then noninteractive=1; shift; fi
if [ "${1:-}" != -v ]; then echo "sudo-mode $noninteractive" >> "$LOG"; fi
case "$*" in
  *verify_code_rollback.py*)
    if [[ "$*" == *"--target-sha $FAKE_NEW_SHA"* ]]; then
      n=$(cat "$STATE/guard_candidate_calls" 2>/dev/null || echo 0)
      echo $((n + 1)) > "$STATE/guard_candidate_calls"
      [ "${UNSAFE_CANDIDATE:-0}" != 1 ] || exit 1
      if [ "${UNSAFE_LATE_CANDIDATE:-0}" = 1 ] && [ "$n" -ge 1 ]; then exit 1; fi
    else
      [ "${UNSAFE_FALLBACK:-0}" != 1 ] || exit 1
      if [ -n "${EXIT_SIGNAL:-}" ]; then kill -"$EXIT_SIGNAL" "$PPID"; fi
    fi ;;
  "systemctl restart talky-voice-gateway")
    if [ -n "${PRE_MIGRATION_SIGNAL:-}" ]; then kill -"$PRE_MIGRATION_SIGNAL" "$PPID"; fi ;;
  "systemctl start talky-migrate.service")
    [ "${SUDO_EXPIRED:-0}" != 1 ] || exit 1
    if [ -n "${MIGRATION_SIGNAL:-}" ]; then
      kill -"$MIGRATION_SIGNAL" "$PPID"
    fi ;;
esac
'''
    monkeypatch.setitem(ops.STUBS, "sudo", ops.STUBS["sudo"].replace(
        "#!/usr/bin/env bash\n", "#!/usr/bin/env bash\n" + controls, 1,
    ))
    monkeypatch.setitem(ops.STUBS, "git", ops.STUBS["git"].replace(
        'echo "git $*" >> "$LOG"', '''echo "git $*" >> "$LOG"
if [ "${FAIL_FALLBACK_CHECKOUT:-0}" = 1 ] && [ "$1 $2" = "checkout --detach" ] && [ "$3" != "$FAKE_NEW_SHA" ]; then exit 1; fi''', 1,
    ))


def test_unsafe_candidate_never_moves_checkout(tmp_path, captured_remote_script, recovery_shims):
    result, log, head = ops._run_remote(tmp_path, captured_remote_script, UNSAFE_CANDIDATE="1")
    assert result.returncode != 0
    assert head == ops.PREV_SHA
    assert not any(line.startswith("git checkout") for line in log)


def test_unsafe_abort_fallback_keeps_candidate_and_its_units(tmp_path, captured_remote_script, recovery_shims):
    result, log, head = ops._run_remote(
        tmp_path, captured_remote_script, UNSAFE_FALLBACK="1", FAKE_GATEWAY_READY_JSON='{"ready": false}',
    )
    assert result.returncode != 0
    assert head == ops.remote_sha(captured_remote_script)
    assert len([line for line in log if line.startswith("git checkout")]) == 1
    assert not any("cp -a --remove-destination" in line for line in log)


def test_hangup_during_migration_does_not_abort_deploy(tmp_path, captured_remote_script, recovery_shims):
    result, _, head = ops._run_remote(tmp_path, captured_remote_script, MIGRATION_SIGNAL="HUP")
    assert result.returncode == 0
    assert head == ops.remote_sha(captured_remote_script)


@pytest.mark.parametrize("signal,exit_code", [("TERM", 143), ("INT", 130)])
def test_interruption_after_migration_start_keeps_candidate(tmp_path, captured_remote_script, recovery_shims, signal, exit_code):
    result, log, head = ops._run_remote(tmp_path, captured_remote_script, MIGRATION_SIGNAL=signal)
    assert result.returncode == exit_code
    assert head == ops.remote_sha(captured_remote_script)
    assert len([line for line in log if line.startswith("git checkout")]) == 1
    assert not any("restart talky-api" in line for line in log)


def test_private_log_records_final_status_and_later_sudo_never_prompts(tmp_path, captured_remote_script, recovery_shims):
    result, log, _ = ops._run_remote(tmp_path, captured_remote_script)
    assert result.returncode == 0
    assert "DEPLOY_RESULT status=success exit_code=0 phase=verified" in getattr(result, "deploy_log", "")
    controller = ops.remote_sha(captured_remote_script)
    assert f"DEPLOY_START candidate={controller} controller={controller}" in result.deploy_log
    assert f"candidate={controller} controller={controller} rollback=not_needed" in result.deploy_log
    assert any(line == "sudo-mode 1" for line in log)
    assert not any(line == "sudo-mode 0" for line in log)


def test_candidate_compatibility_is_rechecked_after_long_preflight(tmp_path, captured_remote_script, recovery_shims):
    result, log, head = ops._run_remote(tmp_path, captured_remote_script, UNSAFE_LATE_CANDIDATE="1")
    assert result.returncode != 0 and head == ops.PREV_SHA
    assert not any(line.startswith("git checkout") for line in log)
    assert (tmp_path / "state/guard_candidate_calls").read_text().strip() == "2"


def test_failed_fallback_checkout_never_restores_old_unit(tmp_path, captured_remote_script, recovery_shims):
    result, log, head = ops._run_remote(
        tmp_path, captured_remote_script, FAIL_FALLBACK_CHECKOUT="1", FAKE_GATEWAY_READY_JSON='{"ready": false}',
    )
    assert result.returncode != 0 and head == ops.remote_sha(captured_remote_script)
    assert not any("cp -a --remove-destination" in line for line in log)
    assert (tmp_path / "units/talky-api-web.service").read_text() == "repository unit\n"
    assert "rollback=failed" in result.deploy_log


def test_early_termination_and_repeated_signal_restore_once(tmp_path, captured_remote_script, recovery_shims):
    result, log, head = ops._run_remote(
        tmp_path, captured_remote_script, PRE_MIGRATION_SIGNAL="TERM", EXIT_SIGNAL="TERM",
    )
    assert result.returncode == 143 and head == ops.PREV_SHA
    assert len([line for line in log if line.startswith("git checkout")]) == 2
    assert len([line for line in log if "cp -a --remove-destination" in line]) == 1
    assert result.deploy_log.count("DEPLOY_RESULT ") == 1


def test_closed_ssh_output_reader_does_not_stop_remote_work(tmp_path, captured_remote_script, recovery_shims):
    result, _, head = ops._run_remote(tmp_path, captured_remote_script, CLOSE_REMOTE_OUTPUT="1")
    assert result.returncode == 0 and head == ops.remote_sha(captured_remote_script)
    assert "DEPLOY_RESULT status=success" in result.deploy_log


def test_expired_sudo_is_logged_without_prompt_or_old_code_fallback(tmp_path, captured_remote_script, recovery_shims):
    result, log, head = ops._run_remote(tmp_path, captured_remote_script, SUDO_EXPIRED="1")
    assert result.returncode != 0 and head == ops.remote_sha(captured_remote_script)
    assert "phase=migration_started" in result.deploy_log
    assert not any(line == "sudo-mode 0" for line in log)
    assert not any("restart talky-api" in line for line in log)

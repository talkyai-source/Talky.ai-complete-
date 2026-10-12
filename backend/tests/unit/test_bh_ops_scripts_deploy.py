"""Behavioural tests for deploy_to_server.sh (workers-jobs-ops-4 and -8).

The local half of the script is run for real against a throwaway git repo with
a fake ``ssh`` that captures the remote script exactly as the production shell
would receive it.  That remote script is then executed under bash with every
system command (git, sudo, systemctl, curl, sleep) replaced by a logging stub,
so ordering and abort behaviour are observed, not grepped.  Nothing here ever
touches a server.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
DEPLOY = Path(os.environ.get("BH_OPS_DEPLOY_SCRIPT", ROOT / "deploy_to_server.sh"))

PREV_SHA = "1" * 40


def _real_bash() -> str | None:
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.exists():
        return str(git_bash)
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    return None


pytestmark = pytest.mark.skipif(_real_bash() is None, reason="no POSIX bash available")


def _no_spaces(path: Path) -> str:
    """The remote script uses an unquoted ``cd /opt/talky``; give it a space-free root."""
    text = str(path)
    if os.name == "nt" and " " in text:
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(text, buf, 1024):
            text = buf.value
    return text.replace("\\", "/")


def _launch(script_cmd: str, env: dict, cwd: Path, shim: Path):
    # Git Bash re-prepends /usr/bin to PATH at start-up; put the shims first from
    # inside the shell so they win over real binaries (sleep, install, ...).
    env = dict(env)
    env["SHIM_DIR"] = str(shim)
    launcher = (
        'if command -v cygpath >/dev/null 2>&1; then SHIM_DIR="$(cygpath -u "$SHIM_DIR")"; fi; '
        'PATH="$SHIM_DIR:$PATH"; ' + script_cmd
    )
    return subprocess.run(
        [_real_bash(), "-c", launcher], env=env, cwd=cwd, capture_output=True, text=True, timeout=180
    )


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")


@pytest.fixture(scope="module")
def captured_remote_script(tmp_path_factory) -> str:
    """Run the local half of deploy_to_server.sh and return the script ssh would send."""
    work = tmp_path_factory.mktemp("deploy-local")
    repo = work / "repo"
    origin = work / "origin.git"
    shim = work / "shim"
    shim.mkdir()
    _write(shim / "ssh", '#!/usr/bin/env bash\nprintf "%s" "${@: -1}" > "$CAPTURE_FILE"\n')

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=repo, check=True, capture_output=True, text=True,
        )

    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    shutil.copy(DEPLOY, repo / "deploy_to_server.sh")
    _write(repo / "backend/scripts/verify_deploy_drain_manifest.py", "import sys\nsys.exit(0)\n")
    git("add", "-A")
    git("commit", "-q", "-m", "x")
    git("remote", "add", "origin", str(origin))
    git("push", "-q", "origin", "main")

    capture = work / "remote.sh"
    env = dict(os.environ)
    env.update(
        CAPTURE_FILE=str(capture),
        TALKY_DEPLOY_DRAIN_MANIFEST="m",
        TALKY_DEPLOY_DRAIN_MANIFEST_SHA256="s",
        TALKY_LOCAL_PYTHON=sys.executable,
        TALKY_PROD_KEY="k",
        TALKY_PROD_HOST="nobody@example.invalid",
        HOME=str(work),
    )
    env.pop("TALKY_DEPLOY_SHA", None)
    result = _launch("exec bash deploy_to_server.sh", env, repo, shim)
    assert result.returncode == 0, result.stdout + result.stderr
    return capture.read_text(encoding="utf-8")


STUBS = {
    "git": """#!/usr/bin/env bash
echo "git $*" >> "$LOG"
args=("$@")
if [ "${args[0]:-}" = "-C" ]; then args=("${args[@]:2}"); fi
case "${args[0]:-} ${args[1]:-}" in
  "rev-parse HEAD") cat "$STATE/head" ;;
  "checkout --detach") echo "${args[2]}" > "$STATE/head" ;;
  "worktree add")
    mkdir -p "${args[3]}/backend/scripts" "${args[3]}/backend/deploy"
    for f in scripts/build_voice_gateway_release.sh scripts/reconcile_asterisk_release.sh deploy/inbound-synthetic-call.sh; do
      echo "exit 0" > "${args[3]}/backend/$f"
    done ;;
  "worktree remove") rm -rf "${args[3]}" ;;
esac
exit 0
""",
    "sudo": """#!/usr/bin/env bash
echo "sudo $*" >> "$LOG"
if [ "$1 $2" = "systemctl start" ] && [ "${FAIL_MIGRATE:-0}" = 1 ]; then exit 1; fi
case "$*" in
  *"reconcile_asterisk_release.sh") if [ "${FAIL_RECONCILE:-0}" = 1 ]; then exit 1; fi ;;
esac
exit 0
""",
    "systemctl": """#!/usr/bin/env bash
echo "systemctl $*" >> "$LOG"
case "$1" in
  show) echo loaded ;;
  is-active) echo active ;;
esac
exit 0
""",
    "sleep": '#!/usr/bin/env bash\necho "sleep $*" >> "$LOG"\n',
    "curl": """#!/usr/bin/env bash
echo "curl $*" >> "$LOG"
url="${@: -1}"
case "$url" in
  *:18080/stats) echo "{\\"active_sessions\\": ${FAKE_SESSIONS:-0}}" ;;
  *:18080/ready) echo "{\\"ready\\": true, \\"protocol_version\\": 2, \\"codecs\\": [\\"pcmu\\"], \\"build_sha\\": \\"$FAKE_NEW_SHA\\"}" ;;
  *:8000/*|*:8001/*)
    n=$(cat "$STATE/probe_calls" 2>/dev/null || echo 0)
    echo $((n + 1)) > "$STATE/probe_calls"
    if [ "$n" -lt "${FAKE_FAIL_PROBES:-0}" ]; then exit 7; fi ;;
esac
exit 0
""",
}
for _tool in ("asterisk", "cmake", "ctest", "c++", "make"):
    STUBS[_tool] = "#!/usr/bin/env bash\nexit 0\n"


def remote_sha(remote: str) -> str:
    # the frozen SHA is the real commit of the throwaway repo, baked into the script
    marker = "git cat-file -e '"
    start = remote.index(marker) + len(marker)
    return remote[start : start + 40]


def _run_remote(tmp_path: Path, remote: str, **flags: str):
    fake_root = tmp_path / "optroot"
    shim = tmp_path / "shim"
    state = tmp_path / "state"
    state.mkdir()
    (state / "head").write_text(PREV_SHA, encoding="utf-8")
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    for name, body in STUBS.items():
        _write(shim / name, body)
    real_python = _no_spaces(Path(sys.executable))
    venv_python = (
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "-c" ] && [[ "$2" == *"import app.main"* ]]; then exit 0; fi\n'
        f'exec "{real_python}" "$@"\n'
    )
    _write(fake_root / "backend/venv/bin/python", venv_python)
    _write(fake_root / "backend/venv/bin/alembic", "#!/usr/bin/env bash\nexit 0\n")
    _write(fake_root / "backend/scripts/verify_alembic_current_heads.py", "import sys\nsys.exit(0)\n")
    script = tmp_path / "remote.sh"
    _write(script, remote.replace("/opt/talky", _no_spaces(fake_root)))
    env = dict(os.environ)
    env.update(LOG=_no_spaces(log), STATE=_no_spaces(state), FAKE_NEW_SHA=remote_sha(remote))
    env.update(flags)
    result = _launch(f'exec bash "{_no_spaces(script)}"', env, tmp_path, shim)
    head = (state / "head").read_text(encoding="utf-8").strip()
    return result, log.read_text(encoding="utf-8").splitlines(), head


def _index(log: list, prefix: str, start: int = 0) -> int:
    for i in range(start, len(log)):
        if log[i].startswith(prefix):
            return i
    raise AssertionError(f"{prefix!r} never ran; log:\n" + "\n".join(log))


def test_an_active_call_blocks_the_deploy_before_the_checkout_moves(tmp_path, captured_remote_script):
    result, log, head = _run_remote(tmp_path, captured_remote_script, FAKE_SESSIONS="2")

    assert result.returncode != 0
    assert "Gateway still owns 2 active session(s); deploy is blocked." in result.stderr
    assert not [line for line in log if line.startswith("git checkout")], "checkout must not move"
    assert not [line for line in log if "install-services.sh" in line], "unit files must not be rewritten"
    assert head == PREV_SHA


def test_a_clean_deploy_checks_out_after_the_drain_proof_and_does_not_roll_back(tmp_path, captured_remote_script):
    result, log, head = _run_remote(tmp_path, captured_remote_script)

    assert result.returncode == 0, result.stdout + result.stderr
    stats_at = _index(log, "curl -fsS --max-time 10 http://127.0.0.1:18080/stats")
    checkout_at = _index(log, "git checkout --detach")
    assert stats_at < checkout_at
    assert head == remote_sha(captured_remote_script), "no rollback after a good deploy"
    assert len([line for line in log if line.startswith("git checkout")]) == 1


def test_a_failed_migration_puts_the_previous_checkout_and_units_back(tmp_path, captured_remote_script):
    result, log, head = _run_remote(tmp_path, captured_remote_script, FAIL_MIGRATE="1")

    assert result.returncode != 0
    assert head == PREV_SHA, "the old commit must be restored when migration fails"
    checkouts = [i for i, line in enumerate(log) if line.startswith("git checkout --detach")]
    assert len(checkouts) == 2
    assert log[checkouts[1]].endswith(PREV_SHA)
    reinstall = _index(log, "sudo bash backend/systemd/install-services.sh", checkouts[1])
    assert reinstall > checkouts[1]
    assert "restoring checkout" in result.stderr
    assert not [line for line in log if "systemctl restart talky-api" in line], "no restart after a failed migration"


def test_a_failure_after_the_migration_succeeded_is_not_rolled_back(tmp_path, captured_remote_script):
    result, log, head = _run_remote(tmp_path, captured_remote_script, FAIL_RECONCILE="1")

    assert result.returncode != 0
    assert head == remote_sha(captured_remote_script), "code must not be rolled back under a migrated schema"
    assert len([line for line in log if line.startswith("git checkout")]) == 1


def test_post_restart_probes_wait_for_a_slow_api_instead_of_one_shot_curl(tmp_path, captured_remote_script):
    # connection refused for the first 7 probe attempts, then healthy
    result, log, _ = _run_remote(tmp_path, captured_remote_script, FAKE_FAIL_PROBES="7")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "liveness OK" in result.stdout
    assert "dependency readiness OK" in result.stdout
    assert "dashboard API liveness OK" in result.stdout
    assert len([line for line in log if line.startswith("sleep 2")]) >= 7


def test_post_restart_probes_still_fail_the_deploy_at_the_deadline(tmp_path, captured_remote_script):
    result, log, _ = _run_remote(tmp_path, captured_remote_script, FAKE_FAIL_PROBES="100000")

    assert result.returncode != 0
    assert "did not become healthy" in result.stderr
    health_calls = [line for line in log if line.endswith(":8000/health")]
    assert 2 <= len(health_calls) <= 100, "the wait must be bounded"

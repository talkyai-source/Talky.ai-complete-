"""Behavioural tests for backend/deploy/healthwatch.sh (workers-jobs-ops-5).

Routine restarts used to page ``HEALTHWATCH CRITICAL`` because the probe was a
single curl.  These run the real script under bash with a stub ``curl`` and a
stub ``sleep``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "deploy" / "healthwatch.sh"

# FAKE_BAD_ATTEMPTS bad answers first (FAKE_MODE=refused -> curl exit 7, =503 -> HTTP 503), then 200.
FAKE_CURL = """#!/usr/bin/env bash
n=$(cat "$STATE/calls" 2>/dev/null || echo 0)
echo $((n + 1)) > "$STATE/calls"
if [ "$n" -lt "${FAKE_BAD_ATTEMPTS}" ]; then
  if [ "${FAKE_MODE}" = refused ]; then
    echo "curl: (7) Failed to connect to localhost port 8000: Connection refused" >&2
    exit 7
  fi
  printf '{"status":"unhealthy"}\\n503\\n'
  exit 0
fi
printf '{"status":"ok"}\\n200\\n'
"""
FAKE_SLEEP = '#!/usr/bin/env bash\necho "$1" >> "$STATE/sleeps"\n'


def _real_bash() -> str | None:
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.exists():
        return str(git_bash)
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    return None


pytestmark = pytest.mark.skipif(_real_bash() is None, reason="no POSIX bash available")


def _run(tmp_path: Path, bad_attempts: int, mode: str = "refused"):
    shim = tmp_path / "shim"
    state = tmp_path / "state"
    shim.mkdir()
    state.mkdir()
    for name, body in (("curl", FAKE_CURL), ("sleep", FAKE_SLEEP)):
        (shim / name).write_text(body, encoding="utf-8", newline="\n")
        # Linux PATH lookup skips non-executable files and would run the real tool.
        (shim / name).chmod(0o755)
    env = dict(os.environ)
    env.update(
        SHIM_DIR=str(shim),
        STATE=str(state),
        SCRIPT_UNDER_TEST=str(SCRIPT),
        FAKE_BAD_ATTEMPTS=str(bad_attempts),
        FAKE_MODE=mode,
    )
    launcher = (
        'if command -v cygpath >/dev/null 2>&1; then SHIM_DIR="$(cygpath -u "$SHIM_DIR")"; '
        'STATE="$(cygpath -u "$STATE")"; SCRIPT_UNDER_TEST="$(cygpath -u "$SCRIPT_UNDER_TEST")"; fi; '
        'export STATE; PATH="$SHIM_DIR:$PATH"; exec bash "$SCRIPT_UNDER_TEST"'
    )
    result = subprocess.run(
        [_real_bash(), "-c", launcher], env=env, capture_output=True, text=True, timeout=60
    )
    calls = int((state / "calls").read_text().strip()) if (state / "calls").exists() else 0
    sleeps = (state / "sleeps").read_text().split() if (state / "sleeps").exists() else []
    return result, calls, sleeps


def test_a_healthy_probe_passes_immediately_without_sleeping(tmp_path):
    result, calls, sleeps = _run(tmp_path, bad_attempts=0)

    assert result.returncode == 0
    assert "healthwatch: all workers healthy" in result.stdout
    assert calls == 1 and sleeps == []


def test_an_api_restart_window_does_not_page_critical(tmp_path):
    # connection refused for the first 3 probes (API still starting), then healthy
    result, calls, sleeps = _run(tmp_path, bad_attempts=3, mode="refused")

    assert result.returncode == 0, result.stderr
    assert "HEALTHWATCH CRITICAL" not in result.stderr
    assert calls == 4 and len(sleeps) == 3


def test_workers_still_refilling_heartbeats_do_not_page_critical(tmp_path):
    result, calls, _ = _run(tmp_path, bad_attempts=2, mode="503")

    assert result.returncode == 0, result.stderr
    assert "HEALTHWATCH CRITICAL" not in result.stderr
    assert calls == 3


def test_a_persistent_connection_failure_still_pages_critical_once_and_exits_nonzero(tmp_path):
    result, calls, sleeps = _run(tmp_path, bad_attempts=10_000, mode="refused")

    assert result.returncode == 1
    assert result.stderr.count("HEALTHWATCH CRITICAL") == 1
    assert "could not reach" in result.stderr and "Connection refused" in result.stderr
    assert 2 <= calls <= 12, "the grace must be bounded"
    assert len(sleeps) == calls - 1


def test_persistently_unhealthy_workers_still_page_critical(tmp_path):
    result, calls, _ = _run(tmp_path, bad_attempts=10_000, mode="503")

    assert result.returncode == 1
    assert result.stderr.count("HEALTHWATCH CRITICAL") == 1
    assert "workers unhealthy (http 503)" in result.stderr
    assert 2 <= calls <= 12

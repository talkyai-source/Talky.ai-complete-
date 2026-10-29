"""deploy/asterisk-reconcile-on-drift.sh: the every-minute Asterisk sync.

Production had no timer for the reconciler (2026-10-15): the live inbound call
plan still routed accounts whose lines were switched off weeks earlier, and
lines added on the website never got a route. The timer must apply only on
drift (the reconciler's apply always reloads Asterisk), retry quietly when calls
are in progress or another run holds the lock, and fail loudly otherwise.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "asterisk-reconcile-on-drift.sh"
DRIFT = {"mode": "check", "drifted": True, "changed": ["extensions.d/talky-inbound.conf"],
         "missing": [], "unexpected": [], "route_count": 1, "digest": "a" * 64}


def _find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (Path(r"C:\Program Files\Git\bin\bash.exe"), Path(r"C:\Program Files\Git\usr\bin\bash.exe")):
            if candidate.exists():
                return str(candidate)
    from shutil import which

    return which("bash")


def _shell_path(path: Path | str) -> str:
    # Preserve the venv interpreter symlink; resolving it selects system Python
    # and drops the installed dotenv parser from this real subprocess fixture.
    resolved = os.path.abspath(path).replace("\\", "/")
    if os.name == "nt" and len(resolved) >= 3 and resolved[1:3] == ":/":
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


def _blocked(error: str) -> str:
    return json.dumps({"mode": "check", "status": "blocked", "error": error})


def run(tmp_path: Path, *, check_status: int, check_out: str, apply_status: int = 0, apply_out: str = "{}",
        profile: str = "TALKY_STUB=1\n", expected_url: str = "", expected_literal: str = ""):
    bash = _find_bash()
    if not bash:
        pytest.skip("Bash is required for the reconcile timer script")
    (tmp_path / ".env").write_text(profile, encoding="utf-8")
    log = tmp_path / "calls.log"
    check = tmp_path / "check.py"
    check.write_text(
        "import os, sys\n"
        "if os.environ.get('EXPECTED_URL'):\n"
        " assert os.environ.get('DATABASE_URL') == os.environ['EXPECTED_URL']\n"
        " assert os.environ.get('LITERAL_VALUE') == os.environ['EXPECTED_LITERAL']\n"
        "open(os.environ['STUB_LOG'], 'a').write('check\\n')\n"
        "sys.stdout.write(os.environ['STUB_CHECK_OUT'])\n"
        "sys.exit(int(os.environ['STUB_CHECK_STATUS']))\n",
        encoding="utf-8",
    )
    apply = tmp_path / "apply.sh"
    apply.write_text(
        '#!/bin/bash\nif [ -n "$EXPECTED_URL" ]; then\n'
        '  [ "$DATABASE_URL" = "$EXPECTED_URL" ] && [ "$LITERAL_VALUE" = "$EXPECTED_LITERAL" ] || exit 97\nfi\n'
        'echo apply >> "$STUB_LOG"\nprintf \'%s\' "$STUB_APPLY_OUT" >&2\nexit "$STUB_APPLY_STATUS"\n',
        encoding="utf-8", newline="\n",
    )
    env = {
        **os.environ,
        "TALKY_BACKEND_DIR": _shell_path(tmp_path),
        "TALKY_BACKEND_ENV_FILE": _shell_path(tmp_path / ".env"),
        "TALKY_BACKEND_PYTHON": _shell_path(sys.executable),
        "TALKY_RECONCILER": _shell_path(check),
        "TALKY_RECONCILE_APPLY": _shell_path(apply),
        "STUB_LOG": str(log),
        "STUB_CHECK_OUT": check_out,
        "STUB_CHECK_STATUS": str(check_status),
        "STUB_APPLY_OUT": apply_out,
        "STUB_APPLY_STATUS": str(apply_status),
        "EXPECTED_URL": expected_url,
        "EXPECTED_LITERAL": expected_literal,
    }
    result = subprocess.run([bash, _shell_path(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    calls = log.read_text(encoding="utf-8").split() if log.exists() else []
    return result, calls


def test_in_sync_applies_nothing_and_logs_nothing(tmp_path):
    result, calls = run(tmp_path, check_status=0, check_out=json.dumps({"mode": "check", "drifted": False}))
    assert (result.returncode, calls, result.stdout, result.stderr) == (0, ["check"], "", "")


def test_drift_is_applied_and_named(tmp_path):
    result, calls = run(tmp_path, check_status=1, check_out=json.dumps(DRIFT), apply_out=json.dumps({"mode": "apply"}))
    assert result.returncode == 0 and calls == ["check", "apply"]
    assert "extensions.d/talky-inbound.conf" in result.stdout and "routes=1" in result.stdout
    assert "applied" in result.stdout


@pytest.mark.parametrize("refusal", [
    "refusing Asterisk apply with 2 active channels",
    "another Asterisk reconciliation is active",
])
def test_an_apply_refused_for_live_calls_or_a_held_lock_waits_for_the_next_tick(tmp_path, refusal):
    result, calls = run(tmp_path, check_status=1, check_out=json.dumps(DRIFT),
                        apply_status=2, apply_out=json.dumps({"mode": "apply", "status": "blocked", "error": refusal}))
    assert result.returncode == 0 and calls == ["check", "apply"]
    assert "apply deferred" in result.stdout and refusal in result.stdout


def test_a_check_that_finds_the_lock_held_waits_without_applying(tmp_path):
    result, calls = run(tmp_path, check_status=2, check_out=_blocked("another Asterisk reconciliation is active"))
    assert (result.returncode, calls) == (0, ["check"])
    assert "check deferred" in result.stdout


def test_a_blocked_check_fails_loudly_and_never_applies(tmp_path):
    result, calls = run(tmp_path, check_status=2, check_out=_blocked("two verified routes claim one carrier account"))
    assert result.returncode == 2 and calls == ["check"]
    assert "check blocked" in result.stderr and "two verified routes" in result.stderr


def test_a_failed_apply_fails_loudly(tmp_path):
    result, calls = run(tmp_path, check_status=1, check_out=json.dumps(DRIFT), apply_status=2,
                        apply_out=json.dumps({"mode": "apply", "status": "blocked", "error": "Asterisk did not accept the candidate"}))
    assert result.returncode == 2 and calls == ["check", "apply"]
    assert "apply FAILED" in result.stderr and "did not accept" in result.stderr


def test_tls_url_and_shell_metacharacters_are_literal_in_check_and_apply(tmp_path):
    marker = tmp_path / 'must-not-execute'
    literal = "$(touch '" + _shell_path(marker) + "')"
    url = 'postgresql://fixture:private@db.invalid/db?sslmode=verify-full&sslrootcert=/protected/ca.pem'
    result, calls = run(tmp_path, check_status=1, check_out=json.dumps(DRIFT),
                        profile=f'DATABASE_URL={url}\nLITERAL_VALUE={literal}\n',
                        expected_url=url, expected_literal=literal)
    assert result.returncode == 0 and calls == ['check', 'apply']
    assert not marker.exists()
    assert url not in result.stdout + result.stderr


def test_the_timer_runs_the_script_every_minute_and_install_enables_it():
    unit = (ROOT / "systemd" / "talky-asterisk-reconcile.service").read_text(encoding="utf-8")
    timer = (ROOT / "systemd" / "talky-asterisk-reconcile.timer").read_text(encoding="utf-8")
    install = (ROOT / "systemd" / "install-services.sh").read_text(encoding="utf-8")
    assert "ExecStart=/bin/bash /opt/talky/backend/deploy/asterisk-reconcile-on-drift.sh" in unit
    assert "User=root" in unit and "Type=oneshot" in unit
    assert "OnUnitActiveSec=60s" in timer
    assert "systemctl enable --now talky-asterisk-reconcile.timer" in install

"""Behavioural tests for scripts/firewall-setup.sh (workers-jobs-ops-10).

The script is run under bash with a stub ``ufw`` that records every call.  Two
things are rewritten in a scratch copy only: the root check (``$EUID`` cannot be
overridden) and, for the "with carriers" cases, the empty CARRIER_IPS array.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "firewall-setup.sh"

FAKE_UFW = """#!/usr/bin/env bash
echo "ufw $*" >> "$LOG"
if [ "$1 $2" = "status numbered" ]; then
  echo "[ 1] 5060/udp ALLOW IN Anywhere  # hand-added rule"
fi
exit 0
"""
NOOP = "#!/usr/bin/env bash\nexit 0\n"


def _real_bash() -> str | None:
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.exists():
        return str(git_bash)
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    return None


pytestmark = pytest.mark.skipif(_real_bash() is None, reason="no POSIX bash available")


def _run(tmp_path: Path, carrier_ips: tuple = ()):
    shim = tmp_path / "shim"
    shim.mkdir()
    log = tmp_path / "ufw.log"
    log.write_text("", encoding="utf-8")
    for name, body in (("ufw", FAKE_UFW), ("apt-get", NOOP), ("hostname", "#!/usr/bin/env bash\necho 10.0.0.9\n")):
        (shim / name).write_text(body, encoding="utf-8", newline="\n")
        # Linux PATH lookup skips non-executable files and would run the real tool.
        (shim / name).chmod(0o755)

    text = SCRIPT.read_text(encoding="utf-8")
    assert "[[ $EUID -ne 0 ]]" in text
    text = text.replace("[[ $EUID -ne 0 ]]", "[[ 1 -ne 1 ]]", 1)
    if carrier_ips:
        injected = "CARRIER_IPS=(\n" + "".join(f'  "{ip}"\n' for ip in carrier_ips)
        text = text.replace("CARRIER_IPS=(\n", injected, 1)
    script = tmp_path / "firewall-under-test.sh"
    script.write_text(text, encoding="utf-8", newline="\n")

    env = dict(os.environ)
    env.update(
        SHIM_DIR=str(shim),
        LOG=str(log),
        SCRIPT_UNDER_TEST=str(script),
        UFW_BACKUP_DIR=str(tmp_path),
    )
    launcher = (
        'if command -v cygpath >/dev/null 2>&1; then SHIM_DIR="$(cygpath -u "$SHIM_DIR")"; '
        'LOG="$(cygpath -u "$LOG")"; UFW_BACKUP_DIR="$(cygpath -u "$UFW_BACKUP_DIR")"; '
        'SCRIPT_UNDER_TEST="$(cygpath -u "$SCRIPT_UNDER_TEST")"; fi; '
        'export LOG UFW_BACKUP_DIR; PATH="$SHIM_DIR:$PATH"; exec bash "$SCRIPT_UNDER_TEST"'
    )
    result = subprocess.run([_real_bash(), "-c", launcher], env=env, capture_output=True, text=True, timeout=60)
    return result, log.read_text(encoding="utf-8").splitlines()


def test_without_carrier_ips_it_aborts_before_touching_ufw(tmp_path):
    result, log = _run(tmp_path)

    assert result.returncode != 0
    assert "CARRIER_IPS" in result.stdout + result.stderr
    assert "will remain open" not in result.stdout + result.stderr, "the old text claimed the opposite of what ufw does"
    assert log == [], f"ufw must not be reset/enabled without carriers, got {log}"


def test_with_carrier_ips_rules_are_port_scoped_never_all_udp(tmp_path):
    result, log = _run(tmp_path, carrier_ips=("203.0.113.7/32",))

    assert result.returncode == 0, result.stdout + result.stderr
    carrier_rules = [line for line in log if "203.0.113.7/32" in line]
    assert carrier_rules, "carrier rules must be installed"
    for line in carrier_rules:
        assert " port " in line, f"carrier rule without a port scope: {line}"
    assert any("port 16384:32768 proto udp" in line for line in carrier_rules), "RTP range rule must remain"
    assert any("port 5060 proto udp" in line for line in carrier_rules)


def test_existing_rules_are_snapshotted_before_the_reset(tmp_path):
    result, log = _run(tmp_path, carrier_ips=("203.0.113.7/32",))

    assert result.returncode == 0, result.stdout + result.stderr
    snapshot_at = log.index("ufw status numbered")
    reset_at = log.index("ufw --force reset")
    assert snapshot_at < reset_at
    backups = list(tmp_path.glob("ufw-rules-before-*.txt"))
    assert len(backups) == 1
    assert "hand-added rule" in backups[0].read_text(encoding="utf-8")

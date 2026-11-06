"""Behavioural tests for scripts/firewall-setup.sh (workers-jobs-ops-10).

The script is run under bash with a stub ``ufw`` that records every call.  Two
things are rewritten in a scratch copy only: the root check (``$EUID`` cannot be
overridden) and, for the "with carriers" cases, the empty CARRIER_IPS array.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
SYSTEMD = "#!/usr/bin/env bash\necho \"$(basename \"$0\") $*\" >> \"$LOG\"\n[[ $1 == is-active ]] && exit 3\nexit 0\n"


def _real_bash() -> str | None:
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.exists():
        return str(git_bash)
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    return None


pytestmark = pytest.mark.skipif(_real_bash() is None, reason="no POSIX bash available")


def _run(tmp_path: Path, carrier_ips: tuple = (), identify_matches="203.0.113.7/32"):
    shim = tmp_path / "shim"
    shim.mkdir()
    log = tmp_path / "ufw.log"
    log.write_text("", encoding="utf-8")
    config = tmp_path / "ufw"
    config.mkdir()
    for filename in ("before.rules", "before6.rules"):
        chain = "ufw6-before-input" if filename == "before6.rules" else "ufw-before-input"
        (config / filename).write_text(f"*filter\n:{chain} - [0:0]\n-A {chain} -i lo -j ACCEPT\nCOMMIT\n", encoding="utf-8", newline="\n")
    for name, body in (("ufw", FAKE_UFW), ("apt-get", NOOP), ("systemd-run", SYSTEMD), ("systemctl", SYSTEMD), ("asterisk", '#!/usr/bin/env bash\nprintf "Match: <criteria.....>\\nMatch: %s\\n" "$IDENTIFY_MATCHES"\n'), ("python3", '#!/usr/bin/env bash\nexec "$PYTHON_FOR_FIREWALL" "$@"\n'), ("hostname", "#!/usr/bin/env bash\necho 10.0.0.9\n")):
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
        UFW_CONFIG_DIR=str(config),
        IDENTIFY_MATCHES=identify_matches,
        PYTHON_FOR_FIREWALL=sys.executable,
    )
    launcher = (
        'if command -v cygpath >/dev/null 2>&1; then SHIM_DIR="$(cygpath -u "$SHIM_DIR")"; '
        'LOG="$(cygpath -u "$LOG")"; UFW_BACKUP_DIR="$(cygpath -u "$UFW_BACKUP_DIR")"; '
        'UFW_CONFIG_DIR="$(cygpath -u "$UFW_CONFIG_DIR")"; '
        'SCRIPT_UNDER_TEST="$(cygpath -u "$SCRIPT_UNDER_TEST")"; fi; '
        'export LOG UFW_BACKUP_DIR UFW_CONFIG_DIR; PATH="$SHIM_DIR:$PATH"; exec bash "$SCRIPT_UNDER_TEST"'
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
    # The RTP rule must match Asterisk's live rtp.conf (10000-20000); the old
    # 16384:32768 range left most of the real media ports closed.
    assert any("port 10000:20000 proto udp" in line for line in carrier_rules), "RTP range rule must remain"
    assert not any("16384" in line or "32768" in line for line in carrier_rules)
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


def test_firewall_rtp_range_matches_the_asterisk_rtp_conf():
    """The ufw RTP range is derived from nothing but a comment: pin it to the
    range setup-asterisk.sh actually writes to rtp.conf."""
    import re

    asterisk = (ROOT / "setup-asterisk.sh").read_text(encoding="utf-8")
    start = re.search(r"^rtpstart=(\d+)$", asterisk, re.MULTILINE)
    end = re.search(r"^rtpend=(\d+)$", asterisk, re.MULTILINE)
    assert start and end, "setup-asterisk.sh no longer writes rtpstart/rtpend"
    rtp = f"{start.group(1)}:{end.group(1)}"

    firewall = SCRIPT.read_text(encoding="utf-8")
    assert f"port {rtp} proto udp" in firewall
    assert f"RTP range {start.group(1)}-{end.group(1)}" in firewall
    assert "16384" not in firewall and "32768" not in firewall


def test_rollback_is_armed_before_any_firewall_mutation(tmp_path):
    result, log = _run(tmp_path, carrier_ips=("203.0.113.7/32",))
    assert result.returncode == 0, result.stdout + result.stderr
    timers = [line for line in log if line.startswith("systemd-run ")]
    assert len(timers) == 1
    assert "--on-active=180s" in timers[0]
    assert "ufw --force disable" in timers[0]
    assert log.index(timers[0]) < log.index("ufw --force reset")


def test_unused_iax_is_explicitly_blocked_and_every_carrier_allowed(tmp_path):
    result, log = _run(tmp_path, carrier_ips=("203.0.113.7/32", "198.51.100.0/24"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert any("deny 4569/udp" in line for line in log)
    for carrier in ("203.0.113.7/32", "198.51.100.0/24"):
        assert any(f"from {carrier} to any port 5060 proto udp" in line for line in log)
        assert any(f"from {carrier} to any port 10000:20000 proto udp" in line for line in log)
    assert not any("port 5080" in line for line in log)


def test_ssh_is_allowed_before_default_deny(tmp_path):
    result, log = _run(tmp_path, carrier_ips=("203.0.113.7/32",))
    assert result.returncode == 0, result.stdout + result.stderr
    ssh = next(i for i, line in enumerate(log) if "allow 22/tcp" in line)
    assert ssh < log.index("ufw default deny incoming")


def test_sip_rate_limit_is_persistent_and_precedes_loopback_accept(tmp_path):
    result, log = _run(tmp_path, carrier_ips=("203.0.113.7/32",))
    assert result.returncode == 0, result.stdout + result.stderr
    for filename in ("before.rules", "before6.rules"):
        rules = (tmp_path / "ufw" / filename).read_text(encoding="utf-8")
        assert "--dport 5060 -m hashlimit" in rules
        assert "--hashlimit-above 100/second" in rules
        assert rules.index("--hashlimit-above") < rules.index("-i lo -j ACCEPT")


def test_missing_active_carrier_aborts_before_mutation(tmp_path):
    result, log = _run(tmp_path, carrier_ips=("203.0.113.7/32",), identify_matches="198.51.100.1/32")
    assert result.returncode != 0
    assert "Loaded carrier network missing" in result.stdout + result.stderr
    assert log == []


def test_existing_flows_cannot_bypass_carrier_or_internal_port_policy(tmp_path):
    result, log = _run(tmp_path, carrier_ips=("203.0.113.7/32",))
    assert result.returncode == 0, result.stdout + result.stderr
    for filename, chain in (("before.rules", "ufw-before-input"), ("before6.rules", "ufw6-before-input")):
        rules = (tmp_path / "ufw" / filename).read_text(encoding="utf-8")
        for match in ("-p udp --dport 5060", "-p udp --dport 10000:20000", "-p udp --dport 4569", "-p tcp -m multiport --dports 5432,6379,5050,8000"):
            deny = f"-A {chain} ! -i lo {match} -j DROP"
            assert deny in rules
            assert rules.index(deny) < rules.index("-i lo -j ACCEPT")
        if filename == "before.rules":
            for port in ("5060", "10000:20000"):
                allow = f"--dport {port} -s 203.0.113.7/32 -j ACCEPT"
                assert allow in rules
                assert rules.index(allow) < rules.index(f"--dport {port} -j DROP")

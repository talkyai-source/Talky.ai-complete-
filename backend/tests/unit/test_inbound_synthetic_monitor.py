from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"


def test_synthetic_probe_is_carrier_hairpin_and_shell_injection_bounded():
    script = (BACKEND / "deploy" / "inbound-synthetic-call.sh").read_text(
        encoding="utf-8"
    )

    assert "PJSIP/${did}@${endpoint}" in script
    assert "application Wait" in script
    assert "^\\+?[0-9]{7,15}$" in script
    assert "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$" in script
    assert "eval " not in script
    assert "source " not in script
    assert "--check-config-file" in script


def test_synthetic_timer_is_installed_and_deploy_requires_configuration():
    installer = (BACKEND / "systemd" / "install-services.sh").read_text(
        encoding="utf-8"
    )
    deploy = (ROOT / "deploy_to_server.sh").read_text(encoding="utf-8")
    service = (BACKEND / "systemd" / "talky-inbound-synthetic.service").read_text(
        encoding="utf-8"
    )
    timer = (BACKEND / "systemd" / "talky-inbound-synthetic.timer").read_text(
        encoding="utf-8"
    )

    assert "systemctl enable talky-inbound-synthetic.timer" in installer
    assert "--check-config-file /etc/talky/inbound-synthetic.env" in deploy
    assert "systemctl restart talky-inbound-synthetic.timer" in deploy
    assert "EnvironmentFile=/etc/talky/inbound-synthetic.env" in service
    assert "OnUnitActiveSec=60min" in timer
    assert "Persistent=true" in timer


# --- behaviour: the probe must prove the call came back in ------------------
# From 2026-09-22 the probe's carrier account was rejected and the DID's line
# was off, yet every hourly run printed "originate accepted" and succeeded.

import os
import subprocess

import pytest


def _find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (Path(r"C:\Program Files\Git\bin\bash.exe"), Path(r"C:\Program Files\Git\usr\bin\bash.exe")):
            if candidate.exists():
                return str(candidate)
    from shutil import which

    return which("bash")


def _shell_path(path: Path) -> str:
    resolved = str(Path(path).resolve()).replace("\\", "/")
    if os.name == "nt" and len(resolved) >= 3 and resolved[1:3] == ":/":
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


def _run_probe(tmp_path: Path, *, originate_status: int = 0, inbound_arrives: bool):
    bash = _find_bash()
    if not bash:
        pytest.skip("Bash is required for the inbound probe script")
    fake = tmp_path / "bin"
    fake.mkdir()
    scripts = {
        "asterisk": '#!/bin/bash\necho "$*" >> "$STUB_LOG"\ncase "$*" in\n'
                    '  *"channel originate"*) touch "$STUB_LOG.originated"; exit "$STUB_ORIGINATE_STATUS" ;;\n'
                    '  *"core show channels concise"*) [ -n "$STUB_INBOUND_ARRIVES" ] && [ -e "$STUB_LOG.originated" ] && '
                    'echo "PJSIP/trunk-x-00000001!from-talky-inbound!150001!3!Up!Stasis!talky_ai,inbound"; exit 0 ;;\n'
                    'esac\n',
        "timeout": '#!/bin/bash\nshift\nexec "$@"\n',
        "sleep": "#!/bin/bash\nexit 0\n",
    }
    for name, body in scripts.items():
        target = fake / name
        target.write_text(body, encoding="utf-8", newline="\n")
        target.chmod(0o755)
    log = tmp_path / "asterisk.log"
    env = {
        **os.environ,
        "STUB_LOG": str(log),
        "STUB_ORIGINATE_STATUS": str(originate_status),
        "STUB_INBOUND_ARRIVES": "1" if inbound_arrives else "",
        "INBOUND_SYNTHETIC_DID": "+442046132300",
        "INBOUND_SYNTHETIC_TRUNK_ENDPOINT": "blazedigitel-endpoint",
        "INBOUND_SYNTHETIC_WAIT_SECONDS": "8",
    }
    result = subprocess.run(
        [bash, "-c", 'export PATH="$1:$PATH"; exec bash "$2"', "bash", _shell_path(fake),
         _shell_path(BACKEND / "deploy" / "inbound-synthetic-call.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    return result, (log.read_text(encoding="utf-8").splitlines() if log.exists() else [])


def test_the_probe_succeeds_only_when_the_call_reaches_the_inbound_call_plan(tmp_path):
    result, asterisk_calls = _run_probe(tmp_path, inbound_arrives=True)
    assert result.returncode == 0, result.stderr
    assert "reached the inbound call plan" in result.stdout
    # asterisk_calls[0] is the pre-originate baseline snapshot (ops-8): only a
    # channel NOT present before the probe may count as the probe's own leg.
    assert "core show channels concise" in asterisk_calls[0]
    assert asterisk_calls[1].startswith("-rx channel originate PJSIP/+442046132300@blazedigitel-endpoint")


def test_an_accepted_originate_that_never_comes_back_in_fails_the_unit(tmp_path):
    result, asterisk_calls = _run_probe(tmp_path, inbound_arrives=False)
    assert result.returncode == 1
    assert "never came back in on the inbound call plan within 18s" in result.stderr
    assert sum("core show channels concise" in call for call in asterisk_calls) == 19  # 1 baseline + 18 polls


def test_a_rejected_originate_still_fails_before_waiting(tmp_path):
    result, asterisk_calls = _run_probe(tmp_path, originate_status=1, inbound_arrives=True)
    assert result.returncode == 1 and "rejected or timed out originating" in result.stderr
    originate_at = next(i for i, c in enumerate(asterisk_calls) if "channel originate" in c)
    assert not any("core show channels" in c for c in asterisk_calls[originate_at + 1 :])

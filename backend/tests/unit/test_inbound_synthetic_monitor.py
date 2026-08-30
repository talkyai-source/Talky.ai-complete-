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

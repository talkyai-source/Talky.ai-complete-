"""Release guards; effective UID/filesystem behaviour is also rehearsed in WSL."""
from configparser import ConfigParser
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[2]
APP_UNITS = (
    "talky-api", "talky-api-web", "talky-dialer-worker",
    "talky-voice-worker", "talky-reminder-worker", "talky-trunk-status",
    "talky-cleanup", "talky-support-jobs",
)


def service(name):
    config = ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    config.read(BACKEND / "systemd" / f"{name}.service", encoding="utf-8")
    return config["Service"]


@pytest.mark.parametrize("name", APP_UNITS)
def test_application_identity_cannot_be_root_or_gain_privileges(name):
    unit = service(name)
    assert unit.get("User", "root") == "talky"
    assert unit["Group"] == "talky"
    assert unit["NoNewPrivileges"] == "yes"
    assert unit["CapabilityBoundingSet"] == ""
    assert unit["ProtectSystem"] == "strict"
    assert unit["ProtectHome"] == "yes"
    assert "docker" not in unit.get("SupplementaryGroups", "").split()
    assert "/opt/talky" not in unit.get("ReadWritePaths", "").split()


def test_call_api_is_loopback_only():
    assert "--host 127.0.0.1 --port 8000" in service("talky-api")["ExecStart"]


def test_only_telephony_processes_have_asterisk_group():
    for name in APP_UNITS:
        groups = service(name).get("SupplementaryGroups", "").split()
        manages_trunks = name in {"talky-api", "talky-api-web", "talky-trunk-status"}
        assert ("asterisk" in groups) == manages_trunks
        assert ("/etc/asterisk/pjsip.d" in service(name).get("ReadWritePaths", "").split()) == manages_trunks


def test_docker_readiness_runs_only_as_trusted_privileged_prestart():
    config = ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    config.read(BACKEND / "deploy/infra/systemd/talky-deps.conf")
    assert config["Service"]["ExecStartPre"] == "+/opt/talky-infra/bin/wait-for-deps"

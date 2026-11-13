"""Prepared PITR/monitor artifacts: wiring guards, not a Linux execution claim."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("stem", ["talklee-db-basebackup", "talklee-db-monitor"])
def test_prepared_services_require_explicit_owner_configuration(stem):
    service = ROOT / "systemd" / (stem + ".service")
    timer = ROOT / "systemd" / (stem + ".timer")
    assert service.is_file() and timer.is_file(), "F8-03 prepared PITR/monitor scheduler artifacts are missing"
    text = service.read_text()
    assert "ConditionPathExists=/etc/talky/pitr.enabled" in text
    assert "EnvironmentFile=/etc/talky/pitr.env" in text
    assert "OnFailure=talky-notify@%n.service" in text
    assert "User=talklee-backup" not in text and "User=talklee-monitor" not in text
    assert "roles.py" not in text and "cutover.py" not in text
    assert "Persistent=true" in timer.read_text()


def test_monitor_requires_wal_evidence_and_owner_capacity():
    path = ROOT / "systemd/talklee-db-monitor.service"
    assert path.is_file(), "F8-03 monitor unit is missing"
    text = path.read_text()
    assert "--require-archiving" in text
    assert "--archive-max-age-minutes 15" in text
    assert "${TALKY_PITR_CAPACITY_BYTES}" in text
    assert "214748364800" not in text, "capacity must be measured/configured, not invented"

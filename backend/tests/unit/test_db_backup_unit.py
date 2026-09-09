"""Production had no scheduled database backup (found 2026-09-10).

Every dump on the box was a hand-run pre-migration dump, and the three from
Aug 28–30 were schema-only: pg_dump ran without ``app.bypass_rls`` against a
FORCE-RLS database and silently wrote 0 TABLE DATA entries. These guards pin
the two properties the nightly job must never lose: the bypass GUC on the dump
connection and a verification step that refuses a schema-only result.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "deploy" / "db-backup.sh"
SERVICE = BACKEND / "systemd" / "talky-db-backup.service"
TIMER = BACKEND / "systemd" / "talky-db-backup.timer"
INSTALLER = BACKEND / "systemd" / "install-services.sh"


def test_backup_dumps_with_the_rls_bypass_and_refuses_a_schema_only_result():
    text = SCRIPT.read_text(encoding="utf-8")
    dump_cmd = re.search(r"docker exec (.*?)pg_dump ", text, re.S)
    assert dump_cmd, "the dump must run through docker exec"
    assert "PGOPTIONS='-c app.bypass_rls=true'" in dump_cmd.group(1), "without the bypass the dump is schema-only"
    assert "--format=custom" in text, "custom format is what pg_restore --list can verify"
    assert re.search(r"grep -c 'TABLE DATA'", text), "must count data entries"
    assert "schema-only dump" in text, "must name the failure when data is missing"
    assert "refusing to publish a shrunken backup" in text
    assert re.search(r"find .*?-mtime .*?-delete", text, re.S), "retention must be enforced"


def test_units_are_wired_the_way_the_other_oneshots_are():
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "ExecStart=/bin/bash /opt/talky/backend/deploy/db-backup.sh" in service
    assert not re.search(r"^Restart=", service, re.M), "a failed backup must surface, not loop"
    assert "OnCalendar=*-*-* 02:30:00" in timer and "Persistent=true" in timer
    assert "systemctl enable talky-db-backup.timer" in installer, "symlinked but never enabled = dead on disk"


def _real_bash() -> str | None:
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.exists():
        return str(git_bash)
    found = shutil.which("bash")
    if found and "system32" not in found.lower():  # the Windows WSL stub is not a shell
        return found
    return None


@pytest.mark.skipif(_real_bash() is None, reason="no POSIX bash available")
def test_backup_script_parses():
    result = subprocess.run(
        [_real_bash(), "-n", "-s"],
        input=SCRIPT.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

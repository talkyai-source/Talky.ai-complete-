"""Behavioural tests for backend/deploy/db-backup.sh (workers-jobs-ops-1).

The script runs under a real bash with a fake ``docker`` on PATH, so the
shrink guard is exercised for real instead of being grepped.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "deploy" / "db-backup.sh"

FAKE_DOCKER = """#!/usr/bin/env bash
case "$*" in
  *pg_dump*) head -c "${FAKE_DUMP_BYTES}" /dev/zero ;;
  *pg_restore*)
    cat > /dev/null
    for i in $(seq 1 "${FAKE_TABLE_DATA}"); do echo "1; 0 0 TABLE DATA public t$i owner"; done ;;
esac
"""


# NTFS cannot chmod an existing directory, so ``install -d -m 0700`` fails on a
# pre-seeded backup dir under Windows Git Bash; the mode is not under test.
FAKE_INSTALL = """#!/usr/bin/env bash
mkdir -p "${@: -1}"
"""


def _real_bash() -> str | None:
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.exists():
        return str(git_bash)
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    return None


pytestmark = pytest.mark.skipif(_real_bash() is None, reason="no POSIX bash available")


def _run_backup(tmp_path: Path, dump_bytes: int, table_data: int = 60):
    shim = tmp_path / "shim"
    shim.mkdir(exist_ok=True)
    docker = shim / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
    (shim / "install").write_text(FAKE_INSTALL, encoding="utf-8", newline="\n")
    # Linux PATH lookup skips files without the execute bit and would run the real
    # docker/install instead (Git Bash on Windows does not check it).
    for name in ("docker", "install"):
        (shim / name).chmod(0o755)
    backups = tmp_path / "backups"
    env = dict(os.environ)
    env.update(
        SHIM_DIR=str(shim),
        SCRIPT_UNDER_TEST=str(SCRIPT),
        TALKY_BACKUP_DIR=backups.as_posix(),
        FAKE_DUMP_BYTES=str(dump_bytes),
        FAKE_TABLE_DATA=str(table_data),
    )
    # Git Bash re-prepends /usr/bin to PATH at startup, which would shadow shims
    # for ``install``; put the shim dir first from inside the shell instead.
    launcher = (
        'if command -v cygpath >/dev/null 2>&1; then SHIM_DIR="$(cygpath -u "$SHIM_DIR")"; '
        'SCRIPT_UNDER_TEST="$(cygpath -u "$SCRIPT_UNDER_TEST")"; fi; '
        'PATH="$SHIM_DIR:$PATH"; exec bash "$SCRIPT_UNDER_TEST"'
    )
    return subprocess.run(
        [_real_bash(), "-c", launcher], env=env, capture_output=True, text=True, timeout=120
    )


def _seed_previous(tmp_path: Path, size: int, age_days: float, name: str) -> Path:
    backups = tmp_path / "backups"
    backups.mkdir(exist_ok=True)
    path = backups / name
    path.write_bytes(b"\0" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def _published(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "backups").glob("talky-*.dump"))


def test_a_shrunken_dump_is_kept_and_alerts_once_not_discarded(tmp_path):
    previous = _seed_previous(tmp_path, 10_000, 1, "talky-20261001T000000Z.dump")

    result = _run_backup(tmp_path, dump_bytes=2_000)

    assert result.returncode != 0, "the shrink must still page the operator"
    assert "published anyway" in result.stderr
    assert "DB-BACKUP CRITICAL" in result.stderr
    fresh = [p for p in _published(tmp_path) if p != previous]
    assert len(fresh) == 1, f"the shrunken dump must still be published, got {_published(tmp_path)}"
    assert fresh[0].stat().st_size == 2_000
    assert Path(str(fresh[0]) + ".sha256").is_file()
    assert not list((tmp_path / "backups").glob("*.partial"))
    assert previous.exists(), "the last good dump must never be removed by a shrink"


def test_the_night_after_a_shrink_succeeds_because_the_baseline_moved(tmp_path):
    _seed_previous(tmp_path, 10_000, 1, "talky-20261001T000000Z.dump")
    first = _run_backup(tmp_path, dump_bytes=2_000)
    assert first.returncode != 0
    time.sleep(1.2)  # the artifact name is stamped to the second

    second = _run_backup(tmp_path, dump_bytes=2_000)

    assert second.returncode == 0, second.stderr
    assert len(_published(tmp_path)) == 3


def test_retention_still_runs_when_the_dump_shrank(tmp_path):
    _seed_previous(tmp_path, 10_000, 1, "talky-20261001T000000Z.dump")
    stale = _seed_previous(tmp_path, 10_000, 45, "talky-20250101T000000Z.dump")

    result = _run_backup(tmp_path, dump_bytes=2_000)

    assert result.returncode != 0
    assert not stale.exists(), "a shrink exit must not skip the 30-day retention"


def test_a_schema_only_dump_is_still_refused_and_not_published(tmp_path):
    previous = _seed_previous(tmp_path, 10_000, 1, "talky-20261001T000000Z.dump")

    result = _run_backup(tmp_path, dump_bytes=9_000, table_data=3)

    assert result.returncode != 0
    assert "schema-only dump" in result.stderr
    assert _published(tmp_path) == [previous]
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_a_healthy_dump_publishes_and_exits_zero(tmp_path):
    _seed_previous(tmp_path, 10_000, 1, "talky-20261001T000000Z.dump")

    result = _run_backup(tmp_path, dump_bytes=9_000)

    assert result.returncode == 0, result.stderr
    assert "db-backup: ok" in result.stdout

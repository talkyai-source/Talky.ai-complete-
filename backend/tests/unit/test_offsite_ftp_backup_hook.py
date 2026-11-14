"""Execute the real backup shell: the FTP off-site copy runs after the local
dump is published and checksummed, and a failed copy fails the unit without
losing a local recovery point (F8-01, 12 Nov 2026)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_fixture_path = Path(__file__).with_name("test_bh_ops_scripts_backup.py")
_fixture_spec = importlib.util.spec_from_file_location("qa_offsite_ftp_backup_shell_fixture", _fixture_path)
backup = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(backup)


pytestmark = backup.pytestmark

PYTHON_BOUNDARIES = """#!/usr/bin/env bash
case "$1" in
  *backup_snapshot.py)
    echo snapshot >> "$FTP_TEST_LOG"
    head -c "${FAKE_DUMP_BYTES}" /dev/zero
    exit "${FAKE_HELPER_EXIT:-0}" ;;
  *offsite_ftp.py)
    [[ "$2" = sync-recordings ]] || echo offsite-ftp >> "$FTP_TEST_LOG"
    shift
    if [[ "$1" = sync-recordings ]]; then
      echo recordings >> "$FTP_TEST_LOG"
      exit "${FAKE_REC_EXIT:-0}"
    fi
    [[ "$1" = publish ]] || exit 91
    shift
    published=""
    while (( $# )); do
      case "$1" in --dump) published="$2"; shift 2 ;; *) shift ;; esac
    done
    [[ -s "$published" && -s "$published.sha256" ]] || exit 92
    sha256sum --check --quiet "$published.sha256" || exit 93
    echo verified-local-publication >> "$FTP_TEST_LOG"
    exit "${FAKE_FTP_EXIT:-0}" ;;
  *) echo unexpected-python-boundary >&2; exit 94 ;;
esac
"""


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    log = tmp_path / "boundary-events"
    monkeypatch.setattr(backup, "FAKE_PYTHON", PYTHON_BOUNDARIES)
    monkeypatch.setenv("FTP_TEST_LOG", log.as_posix())
    monkeypatch.delenv("TALKY_OFFHOST_ROOT", raising=False)
    monkeypatch.setenv("TALKY_FTP_BACKUP_URL", "ftp://ftp.example.invalid/talky-db-backups/")
    monkeypatch.setenv("FAKE_FTP_EXIT", "0")
    return log


def test_configured_ftp_copy_runs_only_after_snapshot_and_checksum_publish(tmp_path, boundary):
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode == 0, result.stderr
    assert boundary.read_text().splitlines() == ["snapshot", "offsite-ftp", "verified-local-publication"]
    assert len(backup._published(tmp_path)) == 1


def test_a_failed_ftp_copy_fails_the_unit_and_keeps_every_local_recovery_point(tmp_path, boundary, monkeypatch):
    stale = backup._seed_previous(tmp_path, 9000, 45, "talky-20250101T000000Z.dump")
    monkeypatch.setenv("FAKE_FTP_EXIT", "1")
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode != 0, "a failed off-site copy must fail the backup unit"
    assert stale.exists(), "local retention waits for a verified off-site copy"
    assert len(backup._published(tmp_path)) == 2


def test_a_verified_ftp_copy_lets_local_retention_run(tmp_path, boundary):
    stale = backup._seed_previous(tmp_path, 9000, 45, "talky-20250101T000000Z.dump")
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode == 0, result.stderr
    assert not stale.exists()


def test_without_an_ftp_destination_nothing_is_sent(tmp_path, boundary, monkeypatch):
    monkeypatch.delenv("TALKY_FTP_BACKUP_URL", raising=False)
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode == 0, result.stderr
    assert boundary.read_text().splitlines() == ["snapshot"]


def test_recordings_sync_is_off_unless_the_explicit_switch_is_set(tmp_path, boundary, monkeypatch):
    monkeypatch.delenv("TALKY_OFFSITE_FTP_RECORDINGS", raising=False)
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode == 0, result.stderr
    assert "recordings" not in boundary.read_text().splitlines()


def test_recordings_sync_runs_after_the_database_upload_when_switched_on(tmp_path, boundary, monkeypatch):
    monkeypatch.setenv("TALKY_OFFSITE_FTP_RECORDINGS", "1")
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode == 0, result.stderr
    assert boundary.read_text().splitlines() == ["snapshot", "offsite-ftp", "verified-local-publication", "recordings"]


def test_a_recordings_failure_fails_the_unit_but_the_database_copy_and_retention_stand(tmp_path, boundary, monkeypatch):
    stale = backup._seed_previous(tmp_path, 9000, 45, "talky-20250101T000000Z.dump")
    monkeypatch.setenv("TALKY_OFFSITE_FTP_RECORDINGS", "1")
    monkeypatch.setenv("FAKE_REC_EXIT", "1")
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode != 0, "a failed recordings sync must fail the unit"
    assert "verified-local-publication" in boundary.read_text().splitlines()
    assert not stale.exists(), "the database copy succeeded, so local retention still runs"

"""Execute the actual backup shell with strict snapshot/remote boundary doubles."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_fixture_path = Path(__file__).with_name("test_bh_ops_scripts_backup.py")
_fixture_spec = importlib.util.spec_from_file_location("qa_round3_backup_shell_fixture", _fixture_path)
backup = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(backup)


pytestmark = backup.pytestmark

PYTHON_BOUNDARIES = """#!/usr/bin/env bash
case "$1" in
  *backup_snapshot.py)
    echo snapshot >> "$OFFHOST_TEST_LOG"
    head -c "${FAKE_DUMP_BYTES}" /dev/zero
    exit "${FAKE_HELPER_EXIT:-0}" ;;
  *offhost.py)
    echo offhost >> "$OFFHOST_TEST_LOG"
    shift
    [[ "$1" = publish ]] || exit 91
    shift
    published=""
    while (( $# )); do
      case "$1" in --dump) published="$2"; shift 2 ;; *) shift ;; esac
    done
    [[ -s "$published" && -s "$published.sha256" ]] || exit 92
    sha256sum --check --quiet "$published.sha256" || exit 93
    echo verified-local-publication >> "$OFFHOST_TEST_LOG"
    exit "${FAKE_OFFHOST_EXIT:-0}" ;;
  *) echo unexpected-python-boundary >&2; exit 94 ;;
esac
"""


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    log = tmp_path / "boundary-events"
    monkeypatch.setattr(backup, "FAKE_PYTHON", PYTHON_BOUNDARIES)
    monkeypatch.setenv("OFFHOST_TEST_LOG", log.as_posix())
    monkeypatch.setenv("TALKY_OFFHOST_ROOT", (tmp_path / "synthetic-remote").as_posix())
    monkeypatch.setenv("TALKY_OFFHOST_SOURCE", "backup.example.invalid:/talky")
    monkeypatch.setenv("TALKY_OFFHOST_FSTYPE", "nfs4")
    monkeypatch.setenv("TALKY_OFFHOST_PASSPHRASE_FILE", (tmp_path / "synthetic-private-key").as_posix())
    monkeypatch.setenv("TALKY_RECORDINGS_DIR", (tmp_path / "synthetic-recordings").as_posix())
    monkeypatch.setenv("FAKE_OFFHOST_EXIT", "0")
    return log


def test_configured_offhost_runs_only_after_snapshot_and_checksum_publish(tmp_path, boundary):
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode == 0, result.stderr
    assert boundary.read_text().splitlines() == ["snapshot", "offhost", "verified-local-publication"]
    assert len(backup._published(tmp_path)) == 1


def test_remote_failure_is_nonzero_keeps_local_dump_and_skips_retention(tmp_path, boundary, monkeypatch):
    stale = backup._seed_previous(tmp_path, 9000, 45, "talky-20250101T000000Z.dump")
    monkeypatch.setenv("FAKE_OFFHOST_EXIT", "1")
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode != 0, "remote verification failure must fail the backup unit"
    assert boundary.read_text().splitlines() == ["snapshot", "offhost", "verified-local-publication"]
    assert stale.exists(), "failed off-host copy must not age out the prior local recovery point"
    assert len(backup._published(tmp_path)) == 2, "verified new local dump is preserved for retry"


def test_successful_remote_verification_preserves_existing_retention(tmp_path, boundary):
    stale = backup._seed_previous(tmp_path, 9000, 45, "talky-20250101T000000Z.dump")
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode == 0, result.stderr
    assert "offhost" in boundary.read_text().splitlines()
    assert not stale.exists()


@pytest.mark.parametrize("helper_exit,table_data", [(1, 60), (0, 3)])
def test_failed_snapshot_or_schema_only_dump_never_reaches_offhost(tmp_path, boundary, helper_exit, table_data):
    result = backup._run_backup(tmp_path, 9000, helper_exit=helper_exit, table_data=table_data)
    assert result.returncode != 0
    assert boundary.read_text().splitlines() == ["snapshot"]
    assert backup._published(tmp_path) == []


def test_unconfigured_destination_preserves_local_only_behavior(tmp_path, boundary, monkeypatch):
    monkeypatch.delenv("TALKY_OFFHOST_ROOT", raising=False)
    result = backup._run_backup(tmp_path, 9000)
    assert result.returncode == 0, result.stderr
    assert boundary.read_text().splitlines() == ["snapshot"]
    assert len(backup._published(tmp_path)) == 1

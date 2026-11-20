"""A failed off-site publish must not skip local retention or the shrink check.

Production: the FTP login was refused every night from 14 Nov; ``set -e`` ended
the script at the publish line, so retention, the shrink warning and the ``ok``
line never ran. Same harness as test_bh_ops_scripts_backup.py (real bash, fake
docker/python3), with a python3 stub that can fail ``offsite_ftp.py publish``.
"""
from __future__ import annotations

import pytest

from tests.unit import test_bh_ops_scripts_backup as base
from tests.unit.test_bh_ops_scripts_backup import _published, _seed_previous

pytestmark = pytest.mark.skipif(base._real_bash() is None, reason="no POSIX bash available")

NEW_LINE = (
    "DB-BACKUP CRITICAL: the database copy on this server is safe, "
    "but the off-site copy failed (offsite_ftp.py publish)"
)

STUB = """#!/usr/bin/env bash
case "$1" in
  *offsite_ftp.py)
    echo "$*" >> "${FAKE_CALL_LOG}"
    case "$2" in
      publish) exit "${FAKE_PUBLISH_EXIT:-0}" ;;
      *) exit "${FAKE_SYNC_EXIT:-0}" ;;
    esac ;;
  *offhost.py)
    echo "$*" >> "${FAKE_CALL_LOG}"
    exit "${FAKE_OFFHOST_EXIT:-0}" ;;
esac
head -c "${FAKE_DUMP_BYTES}" /dev/zero
exit "${FAKE_HELPER_EXIT:-0}"
"""


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "FAKE_PYTHON", STUB)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_CALL_LOG", log.as_posix())
    monkeypatch.setenv("TALKY_FTP_BACKUP_URL", "ftps://example.invalid/x")
    monkeypatch.setenv("FAKE_PUBLISH_EXIT", "1")
    return log


def _calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_failed_publish_keeps_every_local_copy_prints_ok_and_ends_with_the_new_line(tmp_path, env):
    _seed_previous(tmp_path, 10_000, 1, "talky-20261001T000000Z.dump")
    stale = _seed_previous(tmp_path, 10_000, 45, "talky-20250101T000000Z.dump")
    result = base._run_backup(tmp_path, dump_bytes=9_000)
    assert result.returncode != 0
    assert stale.exists(), "local retention waits for a verified off-site copy"
    assert "local retention skipped" in result.stdout
    assert "db-backup: ok" in result.stdout
    assert result.stderr.rstrip().splitlines()[-1] == NEW_LINE
    assert len(_published(tmp_path)) == 3, "the stale copy, the previous copy and the new dump are all kept"


def test_failed_publish_and_shrunk_dump_reports_the_shrink_line(tmp_path, env):
    _seed_previous(tmp_path, 10_000, 1, "talky-20261001T000000Z.dump")
    result = base._run_backup(tmp_path, dump_bytes=2_000)
    assert result.returncode != 0
    assert "published anyway" in result.stderr
    assert NEW_LINE not in result.stderr, "the shrink is more serious and decides the exit"


def test_failed_publish_skips_the_recordings_sync(tmp_path, env, monkeypatch):
    monkeypatch.setenv("TALKY_OFFSITE_FTP_RECORDINGS", "1")
    result = base._run_backup(tmp_path, dump_bytes=9_000)
    assert result.returncode != 0
    assert result.stderr.rstrip().splitlines()[-1] == NEW_LINE
    calls = _calls(env)
    assert any("publish" in c for c in calls)
    assert not any("sync-recordings" in c for c in calls)


def test_successful_publish_is_unchanged(tmp_path, env, monkeypatch):
    monkeypatch.setenv("FAKE_PUBLISH_EXIT", "0")
    monkeypatch.setenv("TALKY_OFFSITE_FTP_RECORDINGS", "1")
    _seed_previous(tmp_path, 10_000, 1, "talky-20261001T000000Z.dump")
    result = base._run_backup(tmp_path, dump_bytes=9_000)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("db-backup: ok ")
    assert any("sync-recordings" in c for c in _calls(env))


def test_failed_recordings_only_keeps_its_own_line(tmp_path, env, monkeypatch):
    monkeypatch.setenv("FAKE_PUBLISH_EXIT", "0")
    monkeypatch.setenv("FAKE_SYNC_EXIT", "1")
    monkeypatch.setenv("TALKY_OFFSITE_FTP_RECORDINGS", "1")
    result = base._run_backup(tmp_path, dump_bytes=9_000)
    assert result.returncode != 0
    assert "FTP recordings sync failed" in result.stderr


def test_failed_offhost_publish_also_keeps_every_local_copy(tmp_path, env, monkeypatch):
    monkeypatch.delenv("TALKY_FTP_BACKUP_URL")
    monkeypatch.setenv("TALKY_OFFHOST_ROOT", "/mnt/x")
    monkeypatch.setenv("FAKE_OFFHOST_EXIT", "1")
    stale = _seed_previous(tmp_path, 10_000, 45, "talky-20250101T000000Z.dump")
    result = base._run_backup(tmp_path, dump_bytes=9_000)
    assert result.returncode != 0
    assert stale.exists(), "a failed off-host copy must not age out a local recovery point"
    assert "db-backup: ok" in result.stdout
    assert "off-host copy failed (offhost.py publish)" in result.stderr


def test_dump_failure_stops_at_once_and_deletes_nothing(tmp_path, env):
    previous = _seed_previous(tmp_path, 10_000, 45, "talky-20250101T000000Z.dump")
    result = base._run_backup(tmp_path, dump_bytes=9_000, helper_exit=1)
    assert result.returncode != 0
    assert "db-backup: ok" not in result.stdout
    assert previous.exists()
    assert _published(tmp_path) == [previous]
    assert _calls(env) == [], "the off-site publish must not be reached"

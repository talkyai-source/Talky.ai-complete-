"""Atomic backup status and scheduled wrapper faults; no database/network calls."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))
try:
    import backup_status
    import scheduled_backup
finally:
    sys.path.remove(str(DIRECTORY))


class BackupStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.status = self.root / "monitoring"

    def tearDown(self):
        self.temporary.cleanup()

    def test_success_running_and_failure_keep_exact_success_time(self):
        backup_status.publish(self.status, "succeeded", 100, now=110)
        pending = backup_status.publish(self.status, "running", 120, now=120)
        self.assertEqual(pending["last_success_at"], 110)
        self.assertIsNone(pending["completed_at"])
        failed = backup_status.publish(self.status, "failed", 120, now=130)
        self.assertEqual(failed["last_success_at"], 110)
        self.assertEqual(failed["result"], "failed")
        self.assertEqual(backup_status.read_receipt(self.status), failed)
        self.assertEqual(set(self.status.iterdir()), {self.status / "backup-attempt.json"})

    def test_unpublished_success_cannot_advance_previous_success(self):
        previous = backup_status.publish(self.status, "succeeded", 100, now=110)
        with patch.object(backup_status.os, "replace", side_effect=OSError("private path")):
            with self.assertRaises(OSError):
                backup_status.publish(self.status, "succeeded", 120, now=130)
        self.assertEqual(backup_status.read_receipt(self.status), previous)
        self.assertFalse(list(self.status.glob(".backup-status-*")))

    def test_first_failure_never_invents_success(self):
        result = backup_status.publish(self.status, "failed", 100, now=110)
        self.assertIsNone(result["last_success_at"])

    def test_malformed_prior_receipt_is_not_silently_adopted(self):
        self.status.mkdir()
        path = self.status / "backup-attempt.json"
        path.write_text('{"last_success_at": 100}')
        with self.assertRaises(ValueError):
            backup_status.publish(self.status, "running", 120, now=120)
        self.assertEqual(path.read_text(), '{"last_success_at": 100}')

    @unittest.skipUnless(os.name == "posix", "Linux ownership/mode behavior")
    def test_private_umask_does_not_hide_public_nonsecret_status(self):
        before = os.umask(0o077)
        try:
            backup_status.publish(self.status, "running", 100, now=100)
        finally:
            os.umask(before)
        self.assertEqual(self.status.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.status / "backup-attempt.json").stat().st_mode & 0o777, 0o644)

    def run_scheduled(self, *, failure=None, status=None):
        argv = ["scheduled_backup.py", "--service-file", str(self.root / "private-service.conf"),
                "--backup-root", str(self.root / "archives"),
                "--status-directory", str(status or self.status)]
        lock = Mock(side_effect=BlockingIOError("private concurrent job") if failure == "lock" else None)
        fake_fcntl = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lock)
        port = Mock(side_effect=scheduled_backup.recovery.RecoveryError("private DB credentials")
                    if failure == "backup" else None,
                    return_value={"status": "archive_verified"})
        out, err = io.StringIO(), io.StringIO()
        # Synthetic lock/DB ports retain the actual wrapper and publisher. A
        # separate POSIX test exercises real advisory locking below.
        with patch.object(sys, "argv", argv), patch.dict(sys.modules, {"fcntl": fake_fcntl}), \
             patch.object(scheduled_backup, "os", SimpleNamespace(name="posix", umask=Mock(), getenv=os.getenv)), \
             patch.object(scheduled_backup.recovery, "Service", return_value=object()), \
             patch.object(scheduled_backup.recovery, "Postgres", return_value=object()), \
             patch.object(scheduled_backup.recovery, "backup", port), \
             redirect_stdout(out), redirect_stderr(err):
            code = scheduled_backup.main()
        return code, err.getvalue(), port

    def test_scheduled_success_publishes_after_verified_backup(self):
        code, error, port = self.run_scheduled()
        self.assertEqual(code, 0, error)
        port.assert_called_once()
        receipt = backup_status.read_receipt(self.status)
        self.assertEqual(receipt["result"], "succeeded")
        self.assertEqual(receipt["last_success_at"], receipt["completed_at"])

    def test_scheduled_failed_backup_preserves_prior_success(self):
        backup_status.publish(self.status, "succeeded", 100, now=110)
        code, error, port = self.run_scheduled(failure="backup")
        self.assertEqual(code, 1)
        self.assertNotIn("private DB", error)
        receipt = backup_status.read_receipt(self.status)
        self.assertEqual(receipt["result"], "failed")
        self.assertEqual(receipt["last_success_at"], 110)

    def test_duplicate_run_cannot_overwrite_active_run(self):
        original = backup_status.publish(self.status, "running", 100, now=100)
        code, error, port = self.run_scheduled(failure="lock")
        self.assertEqual(code, 1)
        port.assert_not_called()
        self.assertEqual(backup_status.read_receipt(self.status), original)

    def test_status_cannot_make_private_archive_directory_readable(self):
        archives = self.root / "archives"
        code, error, port = self.run_scheduled(status=archives)
        self.assertEqual(code, 1)
        port.assert_not_called()
        self.assertFalse((archives / "backup-attempt.json").exists())

    def test_unit_prepares_owned_status_directory_without_api_write_grants(self):
        unit = (DIRECTORY / "systemd/talklee-db-backup.service").read_text()
        self.assertIn("User=talklee-backup", unit)
        self.assertIn("StateDirectory=talklee-backup-monitoring", unit)
        self.assertIn("StateDirectoryMode=0755", unit)
        self.assertIn("Environment=TALKY_BACKUP_STATUS_DIR=/var/lib/talklee-backup-monitoring", unit)
        self.assertNotIn("SupplementaryGroups=talky", unit)

    @unittest.skipUnless(os.name == "posix", "Linux advisory locking")
    def test_real_advisory_lock_rejects_second_writer(self):
        import fcntl
        path = self.root / "lock"
        with path.open("a") as first, path.open("a") as second:
            fcntl.flock(first.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == "__main__":
    unittest.main()

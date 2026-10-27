"""Safety tests run with the Python standard library, without DB access."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

MODULE = Path(__file__).resolve().parents[1] / "recovery.py"
spec = importlib.util.spec_from_file_location("talklee_recovery", MODULE)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, extra="", host="127.0.0.1", sslmode="disable"):
        path = self.root / "service.conf"
        path.write_text(f"[test]\nhost={host}\nport=5432\ndbname=talky\nuser=runtime\nsslmode={sslmode}\n" + extra, encoding="utf-8")
        path.chmod(0o600)
        return path

    def bundle(self):
        bundle = self.root / "backup"
        bundle.mkdir()
        archive = bundle / "database.dump"
        archive.write_bytes(b"synthetic dump" * 100)
        checksum = recovery.digest(archive)
        receipt = {"operation": "backup", "status": "archive_verified", "created_at": recovery.utcnow(),
                   "bytes": archive.stat().st_size, "sha256": checksum,
                   "source_database": "talky", "source_endpoint_fingerprint": "a" * 64, "source_cluster_id": "123456789",
                   "inventory": {"heads": ["test_head"], "snapshot_time": recovery.utcnow(), "counts": {t: 2 for t in recovery.TABLES}}}
        (bundle / "receipt.json").write_text(json.dumps(receipt))
        (bundle / "database.dump.sha256").write_text(checksum + "  database.dump\n")
        return bundle

    def test_remote_plaintext_cannot_use_local_exception(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.Service(self.service(host="db.example.invalid"), "test", True)

    def test_loopback_plaintext_requires_explicit_flag(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.Service(self.service(), "test")

    def test_hostaddr_cannot_redirect_local_plaintext(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.Service(self.service("hostaddr=192.0.2.1\n"), "test", True)

    def test_embedded_password_and_options_are_rejected(self):
        for setting in ("password=secret\n", "options=-c default_transaction_read_only=off\n"):
            with self.subTest(setting=setting), self.assertRaises(recovery.RecoveryError):
                recovery.Service(self.service(setting), "test", True)

    def test_connection_override_is_explicit_and_credentials_are_not_in_args(self):
        service = recovery.Service(self.service(), "test", True)
        self.assertEqual(service.connection("talky_restore_one"), "dbname=talky_restore_one")
        self.assertNotIn("password", service.connection())

    def test_parent_connection_environment_cannot_redirect_children(self):
        service = recovery.Service(self.service(), "test", True)
        with patch.dict(os.environ, {"PGHOST": "wrong", "PGPASSWORD": "secret", "PGDATABASE": "prod", "DATABASE_URL": "secret"}):
            env = service.env()
        self.assertEqual(env["PGHOST"], "127.0.0.1")
        self.assertNotIn("PGPASSWORD", env)
        self.assertEqual(env["PGDATABASE"], "talky")
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("PGSERVICE", env)
        self.assertNotIn("PGSERVICEFILE", env)
        self.assertIn("default_transaction_read_only=on", env["PGOPTIONS"])

    def test_service_edits_cannot_redirect_an_in_progress_operation(self):
        path = self.service()
        service = recovery.Service(path, "test", True)
        self.service(host="database.example.invalid", sslmode="verify-full")
        self.assertEqual(service.env()["PGHOST"], "127.0.0.1")
        self.assertNotIn("PGSERVICEFILE", service.env())

    def test_restore_rejects_production_names_and_sql_injection(self):
        for value in ("talky", "postgres", "talky_restore_x;DROP", "talky_restore_", "talky_restore_" + "x" * 60):
            with self.subTest(value=value), self.assertRaises(recovery.RecoveryError):
                recovery.validate_restore_target(value, "talky")
        with self.assertRaises(recovery.RecoveryError):
            recovery.validate_restore_target("talky_restore_same", "talky_restore_same")

    def test_checksum_detects_truncation(self):
        bundle = self.bundle()
        (bundle / "database.dump").write_bytes(b"short")
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_bundle(bundle)

    def test_receipt_and_sidecar_must_agree(self):
        bundle = self.bundle()
        (bundle / "database.dump.sha256").write_text("0" * 64 + "  database.dump\n")
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_bundle(bundle)

    def test_health_fails_for_stale_archive(self):
        bundle = self.bundle()
        receipt = recovery.verify_bundle(bundle)
        receipt["created_at"] = "2000-01-01T00:00:00+00:00"
        receipt["inventory"]["snapshot_time"] = "2000-01-01T00:00:00+00:00"
        (bundle / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaises(recovery.RecoveryError):
            recovery.health(self.root, 26)

    def test_health_uses_snapshot_age_not_late_publication_time(self):
        bundle = self.bundle()
        receipt = recovery.verify_bundle(bundle)
        receipt["inventory"]["snapshot_time"] = "2000-01-01T00:00:00+00:00"
        (bundle / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaises(recovery.RecoveryError):
            recovery.health(self.root, 26)

    def test_health_validates_latest_instead_of_falling_back(self):
        bundle = self.bundle()
        (bundle / "database.dump").write_bytes(b"corrupt")
        with self.assertRaises(recovery.RecoveryError):
            recovery.health(self.root, 26)

    def test_receipts_cannot_overwrite_existing_files(self):
        path = self.root / "receipt.json"
        recovery.write_json_new(path, {"status": "first"})
        with self.assertRaises(FileExistsError):
            recovery.write_json_new(path, {"status": "replacement"})
        self.assertEqual(json.loads(path.read_text())["status"], "first")

    def test_archive_flush_succeeds_without_modifying_bytes(self):
        path = self.root / "database.dump"
        original = b"PGDMP\x00\xffbinary archive bytes\n"
        path.write_bytes(original)
        before = recovery.digest(path)
        recovery.sync_file(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(recovery.digest(path), before)

    def test_restore_checks_head_before_database_contact(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "schema heads"):
            recovery.restore(None, None, self.bundle(), "talky_restore_test", "runtime", ["wrong"], self.root / "result.json")

    def test_cluster_identity_guard_rejects_same_source_before_create(self):
        service = recovery.Service(self.service().with_name("service.conf"), "test", True)
        service.values["dbname"] = "postgres"
        calls = []
        class Pg:
            def sql(self, service, sql, **kwargs):
                calls.append(sql)
                return "123456789"
        with self.assertRaisesRegex(recovery.RecoveryError, "source cluster"):
            recovery.restore(Pg(), service, self.bundle(), "talky_restore_test", "runtime", ["test_head"], self.root / "restore.json")
        self.assertFalse(any("CREATE DATABASE" in query for query in calls))
        self.assertFalse(json.loads((self.root / "restore.json").read_text())["database_retained"])

    def test_empty_tenant_fixture_cannot_pass_rls(self):
        class Pg:
            def json(self, service, sql, **kwargs):
                if "same_as_restorer" in sql:
                    return {"exists": True, "unsafe": False, "same_as_restorer": False}
                if "relrowsecurity" in sql:
                    return {"enabled": True, "tables": 3}
                return []
            def sql(self, *args, **kwargs):
                return ""
        with self.assertRaisesRegex(recovery.RecoveryError, "insufficient RLS coverage"):
            recovery.rls_probe(Pg(), None, "talky_restore_test", "runtime")

    def test_backup_refuses_existing_destination_before_contact(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "existing"):
            recovery.backup(None, None, self.bundle(), 1024, None, .5)

    def test_client_failure_does_not_leak_database_diagnostics(self):
        pg = recovery.Postgres()
        with patch.object(pg, "executable", return_value="psql"), patch.object(recovery.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "password=NEVER_PRINT customer=NEVER_PRINT"
            with self.assertRaises(recovery.RecoveryError) as caught:
                pg.run("psql", [])
            self.assertNotIn("NEVER_PRINT", str(caught.exception))

    def test_snapshot_popen_failure_closes_error_handle(self):
        pg = recovery.Postgres()
        service = recovery.Service(self.service(), "test", True)
        snapshot = recovery.Snapshot(pg, service)
        with patch.object(pg, "executable", return_value="psql"), patch.object(recovery.subprocess, "Popen", side_effect=OSError("startup failed")):
            with self.assertRaises(OSError):
                snapshot.__enter__()
        self.assertTrue(snapshot.errors.closed)
        self.assertIsNone(snapshot.process)

    def test_snapshot_initial_broken_pipe_reaps_process_and_closes_handles(self):
        pg = recovery.Postgres()
        service = recovery.Service(self.service(), "test", True)
        snapshot = recovery.Snapshot(pg, service)
        process = MagicMock()
        process.poll.return_value = None
        process.stdin.write.side_effect = BrokenPipeError("connection refused")
        process.stdout = io.StringIO("")
        with patch.object(pg, "executable", return_value="psql"), patch.object(recovery.subprocess, "Popen", return_value=process):
            with self.assertRaises(BrokenPipeError):
                snapshot.__enter__()
        process.wait.assert_called_once_with(timeout=10)
        process.stdin.close.assert_called_once()
        self.assertTrue(process.stdout.closed)
        self.assertTrue(snapshot.errors.closed)

    def test_snapshot_broken_pipe_on_close_does_not_skip_remaining_cleanup(self):
        pg = recovery.Postgres()
        service = recovery.Service(self.service(), "test", True)
        snapshot = recovery.Snapshot(pg, service)
        snapshot.process = MagicMock()
        snapshot.process.poll.return_value = 1
        snapshot.process.stdin.close.side_effect = BrokenPipeError("closed")
        snapshot.process.stdout = io.StringIO("")
        snapshot.errors = tempfile.TemporaryFile()
        snapshot.__exit__(None, None, None)
        self.assertTrue(snapshot.process.stdout.closed)
        self.assertTrue(snapshot.errors.closed)


if __name__ == "__main__":
    unittest.main()

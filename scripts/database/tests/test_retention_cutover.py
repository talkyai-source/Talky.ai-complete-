"""Retention, off-host replica, bypass-setting backups and cut-over refusals (no database)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cutover  # noqa: E402
import recovery  # noqa: E402
import roles  # noqa: E402

R = roles.Roles("talklee_owner", "talklee_app", "talklee_backup")


def policy(table, qual, permissive=True, row_dependent=False):
    return {"table": table, "policy": table + "_policy", "permissive": permissive, "qual": qual,
            "row_dependent": row_dependent}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def bundle(self, parent: Path, name: str, age_days: float = 0, table_counts=True) -> Path:
        bundle = parent / name
        bundle.mkdir(parents=True)
        archive = bundle / "database.dump"
        archive.write_bytes(name.encode() * 200)
        checksum = recovery.digest(archive)
        inventory = {"heads": ["0073_payment_test_plan"], "counts": {t: 2 for t in recovery.TABLES},
                     "snapshot_time": (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()}
        if table_counts:
            inventory["table_counts"] = {"tenants": 2, "campaigns": 2, "calls": 2, "leads": 2, "plans": 4}
        receipt = {"operation": "backup", "status": "archive_verified", "created_at": recovery.utcnow(),
                   "bytes": archive.stat().st_size, "sha256": checksum, "source_database": "talkyai",
                   "source_endpoint_fingerprint": "a" * 64, "source_cluster_id": "123456789", "inventory": inventory}
        (bundle / "receipt.json").write_text(json.dumps(receipt))
        (bundle / "database.dump.sha256").write_text(checksum + "  database.dump\n")
        return bundle

    def service(self) -> recovery.Service:
        path = self.root / "services.conf"
        path.write_text("[admin]\nhost=127.0.0.1\nport=5433\ndbname=postgres\nuser=admin\nsslmode=disable\n"
                        "[legacy]\nhost=127.0.0.1\nport=5432\ndbname=talkyai\nuser=talkyai\nsslmode=disable\n")
        path.chmod(0o600)  # the tools refuse a service file other users can read (POSIX)
        return recovery.Service(path, "admin", True)


class RetentionTests(Fixture):
    def test_prune_keeps_the_minimum_and_deletes_only_expired_bundles(self):
        backups = self.root / "backups"
        for name, age in (("new", 0), ("week", 7), ("old1", 40), ("old2", 60)):
            self.bundle(backups, name, age)
        (backups / "operator-notes").mkdir()                         # unknown directory: never touched
        (backups / ".talklee-backup-staging").mkdir()               # staging: never touched
        dry = recovery.prune(backups, 30, 1, True)
        self.assertEqual(sorted(dry["would_delete"]), ["old1", "old2"])
        self.assertTrue((backups / "old1").exists())
        result = recovery.prune(backups, 30, 3, False)
        self.assertEqual((result["kept"], result["deleted"]), (["new", "week", "old1"], ["old2"]))
        self.assertEqual(sorted(p.name for p in backups.iterdir()),
                         [".talklee-backup-staging", "new", "old1", "operator-notes", "week"])

    def test_prune_deletes_nothing_when_the_newest_bundle_is_corrupt(self):
        backups = self.root / "backups"
        self.bundle(backups, "old", 90)
        newest = self.bundle(backups, "new", 0)
        (newest / "database.dump").write_bytes(b"corrupt")
        with self.assertRaises(recovery.RecoveryError):
            recovery.prune(backups, 30, 1, False)
        self.assertTrue((backups / "old").exists())

    def test_prune_rejects_unsafe_policy(self):
        self.bundle(self.root, "b", 0)
        for days, minimum in ((0, 1), (30, 0)):
            with self.assertRaises(recovery.RecoveryError):
                recovery.prune(self.root, days, minimum, True)

    def test_replicate_copies_verifies_and_never_overwrites(self):
        bundle = self.bundle(self.root / "local", "20261028T020000Z")
        replica_root = self.root / "offhost"
        result = recovery.replicate(bundle, replica_root)
        self.assertEqual(result["status"], "replica_verified")
        self.assertEqual(recovery.verify_bundle(replica_root / bundle.name)["sha256"], result["sha256"])
        self.assertEqual([p.name for p in replica_root.iterdir()], [bundle.name])  # no staging left
        with self.assertRaises(recovery.RecoveryError):
            recovery.replicate(bundle, replica_root)

    def test_replicate_refuses_a_corrupt_source(self):
        bundle = self.bundle(self.root / "local", "b")
        (bundle / "database.dump").write_bytes(b"tampered")
        with self.assertRaises(recovery.RecoveryError):
            recovery.replicate(bundle, self.root / "offhost")
        self.assertFalse((self.root / "offhost" / "b").exists())

    def test_table_counts_are_validated(self):
        bundle = self.bundle(self.root, "b")
        receipt = json.loads((bundle / "receipt.json").read_text())
        receipt["inventory"]["table_counts"]["plans"] = -1
        (bundle / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_bundle(bundle)


class BypassSettingTests(Fixture):
    def test_bypass_setting_is_scoped_to_the_backup_and_reaches_every_client(self):
        service = self.service()
        self.assertNotIn("app.bypass_rls", service.env()["PGOPTIONS"])
        service.rls_bypass_guc = True
        self.assertIn("-c app.bypass_rls=true", service.env()["PGOPTIONS"])
        self.assertIn("default_transaction_read_only=on", service.env()["PGOPTIONS"])

    def test_backup_refuses_tables_whose_policies_ignore_the_setting(self):
        service, pg = self.service(), MagicMock()
        pg.sql.return_value = "123456789"
        pg.json.side_effect = [{"address": "127.0.0.1", "port": 5432},
                               [policy("legacy_table", "(tenant_id = current_setting('app.x')::uuid)", row_dependent=True)]]
        with self.assertRaises(recovery.RecoveryError) as refused:
            recovery.backup(pg, service, self.root / "out" / "b", 1024, None, .5, rls_bypass_guc=True,
                            rls_settings={"app.current_user_id": "4444"})
        self.assertIn("legacy_table", str(refused.exception))
        self.assertFalse((self.root / "out" / "b").exists())
        self.assertEqual((service.rls_bypass_guc, service.rls_settings), (False, {}))  # reset even on failure

    def coverage(self, policies, evaluated=None, bypass=True, settings=None):
        service, pg = self.service(), MagicMock()
        service.rls_bypass_guc, service.rls_settings = bypass, settings or {}
        pg.json.side_effect = [policies] + ([evaluated] if evaluated is not None else [])
        return recovery.rls_uncovered(pg, service), pg

    def test_canonical_bypass_policy_covers_without_evaluation(self):
        canonical = "(COALESCE((NULLIF(current_setting('app.bypass_rls', true), ''))::boolean, false) OR (tenant_id = x))"
        uncovered, pg = self.coverage([policy("calls", canonical, row_dependent=True)])
        self.assertEqual(uncovered, [])
        self.assertEqual(pg.json.call_count, 1)
        self.assertEqual(self.coverage([policy("calls", canonical, row_dependent=True)], bypass=False)[0], ["calls"])

    def test_row_independent_policy_is_evaluated_in_the_backup_session(self):
        admin_only = "(EXISTS ( SELECT 1 FROM user_profiles up WHERE up.id = current_setting('app.current_user_id')::uuid))"
        self.assertEqual(self.coverage([policy("assistant_action_resolutions", admin_only)], {"0": True},
                                       settings={"app.current_user_id": "4444"})[0], [])
        uncovered, pg = self.coverage([policy("assistant_action_resolutions", admin_only)], {"0": False})
        self.assertEqual(uncovered, ["assistant_action_resolutions"])
        self.assertIn("BEGIN READ ONLY;", pg.json.call_args.args[1])

    def test_row_dependent_or_missing_or_restrictive_policies_are_uncovered(self):
        owner_only = policy("leads", "(owner_id = current_setting('app.current_user_id')::uuid)", row_dependent=True)
        no_policy = {"table": "secrets", "policy": None, "permissive": None, "qual": None, "row_dependent": False}
        canonical = policy("calls", "(COALESCE(current_setting('app.bypass_rls', true)::boolean, false) OR x)", row_dependent=True)
        restrictive = policy("calls", "(region = 'eu')", permissive=False, row_dependent=True)
        uncovered, _ = self.coverage([owner_only, no_policy, canonical, restrictive])
        self.assertEqual(uncovered, ["calls", "leads", "secrets"])

    def test_rls_settings_are_validated(self):
        self.assertEqual(recovery.parse_rls_settings(["app.current_user_id=44444444-4444-4444-8444-444444444444"]),
                         {"app.current_user_id": "44444444-4444-4444-8444-444444444444"})
        for bad in ("app.bypass_rls=true", "search_path=evil", "app.x=a b", "app.x='1'", "app.x=1 -c foo=2"):
            with self.assertRaises(recovery.RecoveryError):
                recovery.parse_rls_settings([bad])
        service = self.service()
        service.rls_settings = {"app.current_user_id": "4444"}
        self.assertIn("-c app.current_user_id=4444", service.env()["PGOPTIONS"])
        self.assertTrue(service.rls_session)

    def test_inventory_counts_every_table(self):
        sql = recovery.inventory_sql()
        self.assertIn("'table_counts'", sql)
        self.assertIn("query_to_xml", sql)


class CutoverTests(Fixture):
    def load(self, bundle, pg=None, **kw):
        args = dict(expected_heads=["0073_payment_test_plan"], receipt_path=self.root / "cutover.json",
                    backup_bypassrls=False)
        args.update(kw)
        return cutover.load(pg or MagicMock(), self.service(), bundle, "talkyai", R, **args)

    def test_refuses_the_source_cluster_and_writes_a_failure_receipt(self):
        bundle = self.bundle(self.root, "b")
        pg = MagicMock()
        pg.sql.return_value = "123456789"   # same cluster id as the bundle's source
        with self.assertRaises(recovery.RecoveryError):
            self.load(bundle, pg)
        receipt = json.loads((self.root / "cutover.json").read_text())
        self.assertEqual((receipt["status"], receipt["database_creation"], receipt["source_contacted"]),
                         ("failed", "not_attempted", False))
        self.assertFalse(any("CREATE DATABASE" in str(c) for c in pg.sql.call_args_list))

    def test_refuses_an_existing_target_database(self):
        bundle = self.bundle(self.root, "b")
        pg = MagicMock()
        pg.sql.side_effect = ["987654321", "1"]   # different cluster, database exists
        pg.json.return_value = {"address": "10.0.0.5", "port": 5432}
        with self.assertRaises(recovery.RecoveryError) as refused:
            self.load(bundle, pg)
        self.assertIn("already exists", str(refused.exception))
        self.assertEqual(pg.sql.call_count, 2)

    def test_refuses_wrong_heads_old_bundles_and_non_maintenance_service(self):
        with self.assertRaises(recovery.RecoveryError):
            self.load(self.bundle(self.root, "a"), expected_heads=["0072_allowance_anchor"])
        with self.assertRaises(recovery.RecoveryError):
            self.load(self.bundle(self.root, "b", table_counts=False), receipt_path=self.root / "r2.json")
        self.service()
        legacy = recovery.Service(self.root / "services.conf", "legacy", True)   # dbname=talkyai
        with self.assertRaises(recovery.RecoveryError):
            cutover.load(MagicMock(), legacy, self.bundle(self.root, "c"), "talkyai", R, ["0073_payment_test_plan"],
                         self.root / "r3.json", backup_bypassrls=False)

    def test_connection_template_has_no_password(self):
        path = self.root / "s.conf"
        path.write_text("[t]\nhost=db.internal\nport=5432\ndbname=postgres\nuser=admin\nsslmode=verify-full\n"
                        "sslrootcert=/etc/talklee/ca.pem\n")
        path.chmod(0o600)
        template = cutover.connection_template(recovery.Service(path, "t"), "talkyai", "talklee_app")
        self.assertEqual(template, "postgresql://talklee_app:<URL-ENCODED talklee_app PASSWORD>@db.internal:5432/"
                                   "talkyai?sslmode=verify-full&sslrootcert=/etc/talklee/ca.pem")


if __name__ == "__main__":
    unittest.main()

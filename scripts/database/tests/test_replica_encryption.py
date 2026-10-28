"""Encrypted off-host replicas: encrypt, prove, retain, decrypt, refuse (real gpg, no database)."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recovery  # noqa: E402
from test_retention_cutover import Fixture  # noqa: E402


def find_gpg() -> str | None:
    candidates = [os.environ.get("TALKLEE_TEST_GPG"), shutil.which("gpg")]
    if os.name == "nt":
        candidates.append(r"C:\Program Files\Git\usr\bin\gpg.exe")
    return next((c for c in candidates if c and Path(c).is_file()), None)


GPG = find_gpg()


@unittest.skipUnless(GPG, "gpg is not installed")
class EncryptedReplicaTests(Fixture):
    def setUp(self):
        super().setUp()
        self.passphrase = self.secret("replica.passphrase", "p" * 40)
        self.gpg = recovery.Gpg(self.passphrase, GPG)

    def secret(self, name: str, value: str) -> Path:
        path = self.root / name
        path.write_text(value + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_replica_leaves_encrypted_and_is_proven_to_decrypt_to_the_original(self):
        bundle = self.bundle(self.root / "local", "20261028T020000Z")
        result = recovery.replicate(bundle, self.root / "offhost", self.gpg)
        replica = self.root / "offhost" / bundle.name
        self.assertEqual((result["status"], result["encrypted"]), ("replica_verified", True))
        self.assertEqual(sorted(p.name for p in replica.iterdir()),
                         ["database.dump.gpg", "database.dump.sha256", "receipt.json", "replica.json"])
        plaintext = (bundle / "database.dump").read_bytes()
        self.assertNotIn(plaintext[:64], (replica / "database.dump.gpg").read_bytes())
        self.assertEqual(recovery.verify_published(replica)["sha256"], result["sha256"])
        self.assertEqual(recovery.verify_published(replica, self.gpg)["sha256"], result["sha256"])
        self.assertEqual([p.name for p in (self.root / "offhost").iterdir()], [bundle.name])  # no staging left

    def test_decrypt_replica_restores_an_ordinary_verified_bundle(self):
        bundle = self.bundle(self.root / "local", "b")
        recovery.replicate(bundle, self.root / "offhost", self.gpg)
        result = recovery.decrypt_replica(self.root / "offhost" / "b", self.root / "restore" / "b", self.gpg)
        self.assertEqual(result["status"], "bundle_verified")
        self.assertEqual((self.root / "restore" / "b" / "database.dump").read_bytes(), (bundle / "database.dump").read_bytes())
        self.assertEqual(recovery.verify_bundle(self.root / "restore" / "b")["sha256"], result["sha256"])
        with self.assertRaises(recovery.RecoveryError):
            recovery.decrypt_replica(self.root / "offhost" / "b", self.root / "restore" / "b", self.gpg)

    def test_wrong_passphrase_is_refused_and_leaves_nothing_behind(self):
        bundle = self.bundle(self.root / "local", "b")
        recovery.replicate(bundle, self.root / "offhost", self.gpg)
        wrong = recovery.Gpg(self.secret("wrong.passphrase", "w" * 40), GPG)
        with self.assertRaises(recovery.RecoveryError) as refused:
            recovery.decrypt_replica(self.root / "offhost" / "b", self.root / "restore" / "b", wrong)
        self.assertIn("did not decrypt", str(refused.exception))
        self.assertEqual(list((self.root / "restore").iterdir()), [])
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_published(self.root / "offhost" / "b", wrong)

    def test_tampered_ciphertext_is_refused_by_checksum_and_by_gpg_integrity(self):
        bundle = self.bundle(self.root / "local", "b")
        recovery.replicate(bundle, self.root / "offhost", self.gpg)
        replica = self.root / "offhost" / "b"
        data = bytearray((replica / "database.dump.gpg").read_bytes())
        data[len(data) // 2] ^= 1
        (replica / "database.dump.gpg").write_bytes(bytes(data))
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_published(replica)
        # Even with the receipt rewritten to match, the cipher's integrity check refuses it.
        meta = json.loads((replica / "replica.json").read_text())
        meta.update(sha256=recovery.digest(replica / "database.dump.gpg"))
        (replica / "replica.json").write_text(json.dumps(meta))
        recovery.verify_published(replica)
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_published(replica, self.gpg)

    def test_retention_and_health_work_on_an_encrypted_replica_root(self):
        replica_root = self.root / "offhost"
        for name, age in (("new", 0), ("old", 45)):
            recovery.replicate(self.bundle(self.root / "local", name, age), replica_root, self.gpg)
        self.assertEqual(recovery.health(replica_root, 26)["bundle"], "new")
        result = recovery.prune(replica_root, 30, 1, False)
        self.assertEqual((result["kept"], result["deleted"]), (["new"], ["old"]))

    def test_an_encrypted_replica_must_not_also_hold_the_clear_archive(self):
        bundle = self.bundle(self.root / "local", "b")
        recovery.replicate(bundle, self.root / "offhost", self.gpg)
        shutil.copyfile(bundle / "database.dump", self.root / "offhost" / "b" / "database.dump")
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_published(self.root / "offhost" / "b")

    def test_passphrase_must_be_long_and_private(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.Gpg(self.secret("short", "too-short"), GPG)
        if os.name != "nt":
            loose = self.secret("loose", "l" * 40)
            loose.chmod(0o644)
            with self.assertRaises(recovery.RecoveryError):
                recovery.Gpg(loose, GPG)

    def test_cli_requires_an_explicit_encryption_choice(self):
        bundle = self.bundle(self.root / "local", "b")
        for extra in ([], ["--passphrase-file", str(self.passphrase), "--allow-unencrypted"]):
            with redirect_stderr(io.StringIO()) as err:
                code = recovery.main(["replicate", "--bundle", str(bundle), "--destination-root", str(self.root / "o"), *extra])
            self.assertEqual(code, 1)
            self.assertIn("exactly one", err.getvalue())
        self.assertFalse((self.root / "o").exists())
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()) as out:
            code = recovery.main(["replicate", "--bundle", str(bundle), "--destination-root", str(self.root / "o"),
                                  "--passphrase-file", str(self.passphrase), "--gpg", GPG])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["encrypted"])
        self.assertTrue((self.root / "o" / "b" / "database.dump.gpg").exists())


if __name__ == "__main__":
    unittest.main()

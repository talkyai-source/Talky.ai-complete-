"""render_config.py tests: standard library only, no database."""
from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "db_server" / "render_config.py"
spec = importlib.util.spec_from_file_location("talklee_render_config", MODULE)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

BASE = ["--listen-address", "10.0.0.5", "--allow", "10.0.0.2/32", "--ssl-cert-file", "/etc/ssl/s.crt",
        "--ssl-key-file", "/etc/ssl/s.key", "--database", "talkyai"]


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, extra=(), base=BASE):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = rc.main([*base, "--out-dir", str(self.out), *extra])
        return code, err.getvalue()

    def read(self, name):
        return (self.out / name).read_text(encoding="utf-8")

    def swap(self, old, new):
        return [new if a == old else a for a in BASE]

    def test_basic_render(self):
        self.assertEqual(self.run_cli()[0], 0)
        conf = self.read("talklee.conf")
        for line in ("listen_addresses = '10.0.0.5'", "ssl = on", "ssl_min_protocol_version = 'TLSv1.2'",
                     "password_encryption = 'scram-sha-256'", "random_page_cost = 1.1", "log_connections = on",
                     "log_disconnections = on", "log_lock_waits = on", "wal_compression = on"):
            self.assertIn(line, conf)
        self.assertNotIn("archive_mode", conf)

    def test_no_trust_and_ordering(self):
        self.run_cli(["--allow", "192.168.1.0/24", "--admin-local-peer", "--allow-replication"])
        hba = self.read("pg_hba.conf")
        self.assertNotIn("trust", hba)
        rules = [l.split() for l in hba.splitlines() if l and not l.startswith("#")]
        self.assertEqual(rules[0][:3], ["local", "all", "postgres"])
        kinds = [r[0] for r in rules]
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "hostnossl"),
                        min(i for i, k in enumerate(kinds) if k == "hostssl"))
        self.assertEqual(kinds.count("hostssl"), 4)  # 2 database + 2 replication
        self.assertEqual(kinds[-2:], ["host", "host"])
        self.assertTrue(all(r[-1] == "reject" for r in rules if r[0] in ("hostnossl", "host")))
        self.assertTrue(any(r[1] == "replication" and r[2] == "talklee_replication" for r in rules))

    def test_admin_role_reaches_only_the_maintenance_database(self):
        self.assertEqual(self.run_cli(["--admin-role", "cutover_admin"])[0], 0)
        rules = [l.split() for l in self.read("pg_hba.conf").splitlines() if l and not l.startswith("#")]
        admin = [r for r in rules if r[0] == "hostssl" and r[1] == "postgres"]
        self.assertEqual([r[2] for r in admin], ["cutover_admin"] * len(admin))
        self.assertTrue(admin and all(r[-1] == "scram-sha-256" for r in admin))
        self.assertEqual(self.run_cli(["--admin-role", "bad name", "--force"])[0], 2)

    def test_no_replication_or_local_by_default(self):
        self.run_cli()
        hba = self.read("pg_hba.conf")
        self.assertNotIn("replication", hba)
        self.assertNotIn("local ", hba)

    def test_cidr_validation(self):
        for bad in ("10.0.0.2/33", "banana", "10.0.0.2/24", "0.0.0.0/0", "::/0"):
            code, err = self.run_cli(base=self.swap("10.0.0.2/32", bad))
            self.assertEqual(code, 2, bad)
            self.assertIn("allow", err)
        self.assertFalse(self.out.exists())
        code, _ = self.run_cli(["--i-understand-public-allow"], base=self.swap("10.0.0.2/32", "0.0.0.0/0"))
        self.assertEqual(code, 0)

    def test_listen_refusal(self):
        for bad in ("0.0.0.0", "*", "::", "db.example.com", ""):
            self.assertEqual(self.run_cli(base=self.swap("10.0.0.5", bad))[0], 2, bad)
            self.assertFalse((self.out / "talklee.conf").exists())
        self.assertEqual(self.run_cli(["--i-understand-public-listen"], base=self.swap("10.0.0.5", "0.0.0.0"))[0], 0)

    def test_archive_command(self):
        d = "/var/lib/postgresql/wal_archive"
        self.assertEqual(self.run_cli(["--wal-archive-dir", d])[0], 0)
        conf = self.read("talklee.conf")
        self.assertIn("archive_mode = on", conf)
        self.assertIn("wal_level = replica", conf)
        self.assertIn("archive_timeout = 60", conf)
        self.assertIn(f"archive_command = 'test ! -f {d}/%f && cp %p {d}/%f.tmp && mv {d}/%f.tmp {d}/%f'", conf)
        for bad in ("/var/lib/with space", "/var/it's", '/var/"x"', "/var/a\nb", "relative/dir", "/var/a;rm", "/var/$X"):
            self.assertEqual(self.run_cli(["--force", "--wal-archive-dir", bad])[0], 2, repr(bad))

    def test_memory_scaling(self):
        small, big = rc.tuning(2048), rc.tuning(16384)
        self.assertEqual(small["shared_buffers"], "512MB")
        self.assertEqual(small["effective_cache_size"], "1536MB")
        self.assertEqual(big["shared_buffers"], "4096MB")
        self.assertEqual(big["effective_cache_size"], "12288MB")
        self.assertEqual(rc.tuning(1536)["work_mem"], "8MB")
        self.assertEqual(rc.tuning(1536)["maintenance_work_mem"], "128MB")
        self.assertGreater(int(big["work_mem"][:-2]), int(small["work_mem"][:-2]))
        with self.assertRaises(rc.ConfigError):
            rc.tuning(100)
        self.run_cli(["--memory-mb", "8192"])
        self.assertIn("shared_buffers = 2048MB", self.read("talklee.conf"))

    def test_refuse_overwrite(self):
        self.assertEqual(self.run_cli()[0], 0)
        (self.out / "pg_hba.conf").write_text("sentinel", encoding="utf-8")
        code, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("refusing to overwrite", err)
        self.assertEqual(self.read("pg_hba.conf"), "sentinel")
        self.assertEqual(self.run_cli(["--force"])[0], 0)
        self.assertNotEqual(self.read("pg_hba.conf"), "sentinel")

    def test_ca_and_bad_paths(self):
        self.run_cli(["--ssl-ca-file", "/etc/ssl/ca.crt"])
        self.assertIn("ssl_ca_file = '/etc/ssl/ca.crt'", self.read("talklee.conf"))
        self.assertEqual(self.run_cli(["--force"], base=self.swap("/etc/ssl/s.crt", "/etc/ssl/it's.crt"))[0], 2)


if __name__ == "__main__":
    unittest.main()

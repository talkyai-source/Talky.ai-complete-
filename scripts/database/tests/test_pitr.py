"""PITR safety tests: standard library only, no database."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
from datetime import timezone
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "pitr.py"
spec = importlib.util.spec_from_file_location("talklee_pitr", MODULE)
pitr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pitr)
Err = pitr.RecoveryError


def make_tar(path: Path, entries):
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                info.linkname = data
                tar.addfile(info)


class PitrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive_command_linux_never_overwrites(self):
        cmd = pitr.archive_command("/var/wal", "linux")
        self.assertEqual(cmd, "test ! -f '/var/wal/%f' && cp '%p' '/var/wal/%f.tmp' && mv '/var/wal/%f.tmp' '/var/wal/%f'")

    def test_archive_command_windows(self):
        cmd = pitr.archive_command("C:/Users/AL AZIZ/wal", "windows")
        self.assertIn('if exist "C:\\Users\\AL AZIZ\\wal\\%f" (exit 1)', cmd)
        self.assertIn('move /Y "C:\\Users\\AL AZIZ\\wal\\%f.tmp" "C:\\Users\\AL AZIZ\\wal\\%f"', cmd)

    def test_archive_dir_rejects_unsafe_characters(self):
        for bad in ("/a'b", '/a"b', "/a\nb", "/a%fb", "", "/a\rb"):
            for platform in ("linux", "windows"):
                with self.assertRaises(Err):
                    pitr.archive_command(bad, platform)
        with self.assertRaises(Err):
            pitr.archive_command("/x", "plan9")

    def test_archive_settings_lines(self):
        lines = pitr.archive_settings("/var/wal", "linux")
        self.assertEqual(lines[:3], ["wal_level = replica", "archive_mode = on", "archive_timeout = 60"])
        self.assertTrue(lines[3].startswith("archive_command = 'test ! -f ''/var/wal/%f'' && cp"))
        self.assertIn("\\\\", pitr.archive_settings("C:/w", "windows")[3])  # backslashes doubled for conf

    def test_target_time_requires_offset(self):
        with self.assertRaises(Err):
            pitr.parse_target_time("2026-10-28T12:00:00")
        with self.assertRaises(Err):
            pitr.parse_target_time("yesterday")
        parsed = pitr.parse_target_time("2026-10-28T14:00:00+02:00")
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.hour, 12)
        self.assertEqual(pitr.parse_target_time("2026-10-28T12:00:00Z"), parsed)

    def test_recovery_conf(self):
        when = pitr.parse_target_time("2026-10-28T12:00:00.5+00:00")
        text = pitr.render_recovery_conf("/var/wal", "linux", when, 55001)
        self.assertIn("restore_command = 'test -f ''/var/wal/%f'' && cp ''/var/wal/%f'' ''%p'''", text)
        self.assertIn("recovery_target_time = '2026-10-28 12:00:00.500000+00'", text)
        self.assertIn("recovery_target_action = 'promote'", text)
        self.assertIn("archive_mode = off", text)
        self.assertIn("port = 55001", text)
        self.assertIn("listen_addresses = '127.0.0.1'", text)
        latest = pitr.render_recovery_conf("/var/wal", "linux", None, None)
        self.assertNotIn("recovery_target_time", latest)
        self.assertNotIn("port =", latest)

    def test_safe_extract_good_and_traversal(self):
        good = self.root / "good.tar.gz"
        make_tar(good, [("sub", "dir", None), ("sub/a.txt", "file", b"hi")])
        dest = self.root / "out"
        dest.mkdir()
        self.assertEqual(pitr.safe_extract(good, dest), 2)
        self.assertEqual((dest / "sub" / "a.txt").read_bytes(), b"hi")
        for name in ("../evil.txt", "a/../../evil.txt", "/abs.txt", "C:/abs.txt", "a\\..\\evil.txt"):
            bad = self.root / "bad.tar.gz"
            make_tar(bad, [("ok.txt", "file", b"x"), (name, "file", b"x")])
            d = self.root / "d"
            d.mkdir(exist_ok=True)
            with self.assertRaises(Err):
                pitr.safe_extract(bad, d)
            self.assertFalse((d / "ok.txt").exists(), "nothing may be written when any member is unsafe")
        self.assertFalse((self.root / "evil.txt").exists())

    def test_safe_extract_rejects_links(self):
        for kind in ("sym", "hard"):
            bad = self.root / f"{kind}.tar.gz"
            make_tar(bad, [("link", kind, "../outside")])
            d = self.root / ("d" + kind)
            d.mkdir()
            with self.assertRaises(Err):
                pitr.safe_extract(bad, d)
            self.assertEqual(list(d.iterdir()), [])

    def fake_base(self):
        base = self.root / "base"
        base.mkdir()
        for name in pitr.BASE_FILES:
            make_tar(base / name, [("PG_VERSION", "file", b"16")]) if name.endswith(".gz") else (base / name).write_text("{}")
        files = {n: {"bytes": (base / n).stat().st_size, "sha256": pitr.digest(base / n)} for n in pitr.BASE_FILES}
        (base / "receipt.json").write_text(json.dumps({"operation": "base_backup", "status": "verified", "files": files,
                                                       "start_lsn": "0/1"}))
        return base

    def test_verify_base_backup_detects_corruption(self):
        base = self.fake_base()
        self.assertEqual(pitr.verify_base_backup(base)["status"], "verified")
        with (base / "base.tar.gz").open("ab") as stream:
            stream.write(b"x")
        with self.assertRaises(Err):
            pitr.verify_base_backup(base)

    def test_verify_base_backup_rejects_extra_file(self):
        base = self.fake_base()
        (base / "16385.tar.gz").write_bytes(b"tablespace")
        with self.assertRaises(Err):
            pitr.verify_base_backup(base)

    def restore_kwargs(self, **extra):
        kw = dict(target_time=None, port=None, start=False, platform="linux",
                  wait_seconds=1, receipt_path=None)
        kw.update(extra)
        return kw

    def test_restore_refuses_non_empty_target(self):
        base, archive, target = self.fake_base(), self.root / "wal", self.root / "data"
        archive.mkdir()
        target.mkdir()
        (target / "keep").write_text("x")
        with self.assertRaisesRegex(Err, "not empty"):
            pitr.restore(None, base, archive, target, **self.restore_kwargs())
        self.assertEqual((target / "keep").read_text(), "x")

    def test_restore_prepares_new_dir_without_starting(self):
        base, archive, target = self.fake_base(), self.root / "wal", self.root / "data"
        archive.mkdir()
        target.mkdir()  # empty existing dir is acceptable
        when = pitr.parse_target_time("2026-10-28T12:00:00Z")
        receipt = pitr.restore(None, base, archive, target, **self.restore_kwargs(target_time=when, port=55555))
        self.assertEqual(receipt["status"], "prepared")
        self.assertTrue((target / "recovery.signal").exists())
        self.assertTrue((target / "pg_wal" / "PG_VERSION").exists())
        self.assertIn("recovery_target_time", (target / "postgresql.auto.conf").read_text())
        self.assertTrue((self.root / "data.restore-receipt.json").exists())

    def test_restore_corrupt_backup_creates_nothing(self):
        base, archive, target = self.fake_base(), self.root / "wal", self.root / "data"
        archive.mkdir()
        with (base / "pg_wal.tar.gz").open("ab") as stream:
            stream.write(b"x")
        with self.assertRaises(Err):
            pitr.restore(None, base, archive, target, **self.restore_kwargs())
        self.assertFalse(target.exists())

    def test_start_requires_port(self):
        base, archive = self.fake_base(), self.root / "wal"
        archive.mkdir()
        with self.assertRaisesRegex(Err, "--port"):
            pitr.restore(None, base, archive, self.root / "data", **self.restore_kwargs(start=True))

    def test_archive_health(self):
        archive = self.root / "wal"
        archive.mkdir()
        with self.assertRaises(Err):
            pitr.archive_health(archive, 5)
        (archive / "000000010000000000000001.tmp").write_text("x")
        (archive / "000000010000000000000002.partial").write_text("x")
        with self.assertRaises(Err):
            pitr.archive_health(archive, 5)
        seg = archive / "000000010000000000000003"
        seg.write_text("x")
        self.assertEqual(pitr.archive_health(archive, 5)["newest_segment"], seg.name)
        old = time.time() - 3600
        os.utime(seg, (old, old))
        with self.assertRaises(Err):
            pitr.archive_health(archive, 5)

    def test_main_exit_codes_and_safe_message(self):
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            code = pitr.main(["archive-health", "--archive-dir", str(self.root / "missing"), "--max-age-minutes", "5"])
        self.assertEqual(code, 1)
        self.assertIn("PITR FAILED", err.getvalue())
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(pitr.main(["archive-settings", "--archive-dir", "/w", "--platform", "linux"]), 0)


if __name__ == "__main__":
    unittest.main()

"""The shipped systemd units must invoke the tools with flags those tools accept,
and agree on where the protected connection files live (no database needed)."""
from __future__ import annotations

import contextlib
import importlib
import io
from pathlib import Path
import re
import shlex
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
UNITS = sorted((TOOLS / "systemd").glob("talklee-db-*.service"))
DEPLOYED = "/opt/talky/scripts/database/"
SECRETS = "/etc/talklee/secrets/"
CA_FILE = SECRETS + "database-ca.pem"


def exec_lines(text: str) -> list[list[str]]:
    """Every command the unit runs (ExecStart, ExecStartPost)."""
    return [exec_args(line) for line in re.findall(r"^ExecStart(?:Post)?=(.+)$", text, re.M)]


def exec_args(line: str) -> list[str]:
    """One command as the tool sees it: systemd escapes undone, the shell wrapper
    unwrapped, the date stamp and optional environment expansions resolved."""
    if line.startswith("/bin/sh -c "):
        line = shlex.split(line[len("/bin/sh -c "):])[0]
        line = line.replace('"$$(date -u +%%Y%%m%%dT%%H%%M%%SZ)"', "20261028T030000Z")
    args = [arg for arg in shlex.split(line) if not arg.startswith("$")]
    return args[1:] if args[0] == "exec" else args


class UnitTests(unittest.TestCase):
    def test_units_exist(self):
        self.assertGreaterEqual(len(UNITS), 4)

    def test_every_unit_runs_a_shipped_tool_with_flags_it_accepts(self):
        checked = 0
        for unit in UNITS:
            for args in exec_lines(unit.read_text(encoding="utf-8")):
                with self.subTest(unit=unit.name, command=" ".join(args[1:3])):
                    self.assertEqual(args[0], "/usr/bin/python3")
                    self.assertTrue(args[1].startswith(DEPLOYED), args[1])
                    script = TOOLS / args[1][len(DEPLOYED):]
                    self.assertTrue(script.is_file(), f"{unit.name} runs {script.name}, which is not in the repository")
                    module = importlib.import_module(script.stem)
                    with contextlib.redirect_stderr(io.StringIO()) as err:
                        try:
                            module.parser().parse_args(args[2:])
                        except SystemExit:
                            self.fail(f"{unit.name}: {script.name} rejects its arguments: {err.getvalue().strip()}")
                    checked += 1
        self.assertGreaterEqual(checked, 6)  # 4 ExecStart + archive-health + prune

    def test_each_unit_reads_only_its_own_users_secrets(self):
        # libpq and the tools refuse a password or service file any other user can
        # read, so one 0600 file cannot serve two unit users: each user has its own
        # /etc/talklee/secrets/<user>/ directory, and a unit reads nothing else
        # there except the public CA certificate.
        for unit in UNITS:
            text = unit.read_text(encoding="utf-8")
            user = re.search(r"^User=talklee-([a-z]+)$", text, re.M).group(1)
            own = f"{SECRETS}{user}/"
            connects = "--service-file" in text or "PGSERVICEFILE" in text
            with self.subTest(unit=unit.name):
                for path in re.findall(r"(?<![\w/])/etc/talklee/\S+", text):
                    if path.split(" ")[0] != CA_FILE:
                        self.assertTrue(path.startswith(own), f"{unit.name} (user talklee-{user}) reads {path}")
                for path in re.findall(r"--service-file (\S+)", text) + re.findall(r"PGSERVICEFILE=(\S+)", text):
                    self.assertEqual(path, own + "pg_service.conf")
                if connects:
                    self.assertIn(f"Environment=PGPASSFILE={own}pgpass", text)

    def test_the_off_host_copy_is_encrypted_by_default(self):
        text = (TOOLS / "systemd" / "talklee-db-backup.service").read_text(encoding="utf-8")
        self.assertIn(f"--replica-passphrase-file {SECRETS}backup/", text)
        self.assertNotIn("--allow-unencrypted-replica", text)

    def test_monitor_writes_only_where_systemd_lets_it(self):
        text = (TOOLS / "systemd" / "talklee-db-monitor.service").read_text(encoding="utf-8")
        state = re.search(r"^StateDirectory=(\S+)$", text, re.M).group(1)
        for flag in ("--state-file", "--prometheus-textfile"):
            self.assertTrue(re.search(rf"{flag} /var/lib/{state}/\S+", text), flag)
        self.assertNotIn("ReadWritePaths", text)


if __name__ == "__main__":
    unittest.main()

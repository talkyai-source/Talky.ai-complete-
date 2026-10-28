"""Offline connection-boundary regressions; live ACL proof is the TLS rehearsal."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("runtime_grants_tested", Path(__file__).parents[1] / "runtime_grants.py")
grants = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grants)


def service(user="runtime", **values):
    return SimpleNamespace(values=dict(host="db.example.invalid", port="5432", dbname="talky",
                           user=user, sslmode="verify-full", sslrootcert="/protected/ca", **values),
                           local_plaintext=False, passfile=Path("/protected/pgpass"))


class BoundaryTests(unittest.TestCase):
    def test_same_role_is_refused_before_connecting(self):
        with patch.object(grants, "connect") as connect:
            with self.assertRaisesRegex(grants.GrantsError, "identities must differ"):
                grants.execute(service(), service(), "apply")
        connect.assert_not_called()

    def test_mismatched_endpoints_refuse_before_connecting(self):
        for key in ("host", "hostaddr", "port", "dbname"):
            with self.subTest(key=key), patch.object(grants, "connect") as connect:
                other = service()
                other.values[key] = "different"
                with self.assertRaisesRegex(grants.GrantsError, "same explicit endpoint"):
                    grants.execute(service("owner"), other, "apply")
                connect.assert_not_called()

    def test_unknown_mode_does_not_connect(self):
        with patch.object(grants, "connect") as connect:
            with self.assertRaisesRegex(grants.GrantsError, "Unknown mode"):
                grants.execute(service("owner"), service(), "unsafe")
        connect.assert_not_called()

    def test_plaintext_is_not_an_operator_option(self):
        with patch("psycopg2.connect") as connect:
            unsafe = service()
            unsafe.local_plaintext = True
            with self.assertRaisesRegex(grants.GrantsError, "certificate-verified TLS"):
                with grants.connect(unsafe):
                    pass
        connect.assert_not_called()

    def test_explicit_ca_and_password_file_required(self):
        for change in ("ca", "password_file"):
            with self.subTest(change=change), patch("psycopg2.connect") as connect:
                unsafe = service()
                if change == "ca":
                    unsafe.values.pop("sslrootcert")
                else:
                    unsafe.passfile = None
                with self.assertRaisesRegex(grants.GrantsError, "explicit CA file"):
                    with grants.connect(unsafe):
                        pass
                connect.assert_not_called()

    def test_ambient_password_service_and_options_cannot_override_pinned_connection(self):
        observed = {}
        connection = SimpleNamespace(close=lambda: observed.update(closed=True))

        def connect(**values):
            self.assertFalse(any(key.startswith("PG") for key in os.environ))
            observed.update(values)
            return connection

        ambient = dict(PGPASSWORD="not-the-designated-secret", PGSERVICE="wrong",
                       PGOPTIONS="-c app.bypass_rls=true", PGHOST="wrong", PGPASSFILE="wrong")
        with patch.dict(os.environ, ambient), patch("psycopg2.connect", side_effect=connect):
            with grants.connect(service()) as actual:
                self.assertIs(actual, connection)
            self.assertTrue(all(os.environ[key] == value for key, value in ambient.items()))
        self.assertEqual(observed["passfile"], str(Path("/protected/pgpass")))
        self.assertEqual(observed["host"], "db.example.invalid")
        self.assertNotIn("password", observed)
        self.assertNotIn("bypass_rls", observed["options"])
        self.assertTrue(observed["closed"])

    def test_login_failure_is_sanitized_and_restores_environment(self):
        import psycopg2
        with patch.dict(os.environ, {"PGPASSWORD": "ambient"}), patch("psycopg2.connect", side_effect=psycopg2.OperationalError("private endpoint and password")):
            with self.assertRaises(grants.GrantsError) as error:
                with grants.connect(service()):
                    pass
            self.assertEqual(os.environ["PGPASSWORD"], "ambient")
        self.assertNotIn("private endpoint", str(error.exception))

    def test_existing_receipt_refuses_before_service_or_database_access(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "prior.json"
            receipt.write_text("prior evidence", encoding="utf-8")
            with patch.object(grants.recovery, "Service") as config:
                with self.assertRaisesRegex(grants.GrantsError, "prior receipt"):
                    grants.main(["apply", "--service-file", "unused", "--owner-service", "owner",
                                 "--runtime-service", "runtime", "--receipt", str(receipt)])
                config.assert_not_called()
            self.assertEqual(receipt.read_text(), "prior evidence")

    def test_identifier_injection_refused(self):
        for value in ('role;DROP TABLE x', 'role"', 'a' * 64, 'a.b'):
            with self.subTest(value=value), self.assertRaises(grants.recovery.RecoveryError):
                grants.ident(value)


if __name__ == "__main__":
    unittest.main()

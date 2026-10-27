"""roles.py safety tests: standard library only, no database."""
from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import roles  # noqa: E402
from recovery import RecoveryError  # noqa: E402

R = roles.Roles("talklee_owner", "talklee_app", "talklee_backup")


def observed(**overrides):
    attributes = {"login": True, "superuser": False, "bypass_rls": False, "create_role": False,
                  "create_database": False, "replication": False}
    base = {
        "roles": {name: dict(attributes) for name in (R.owner, R.runtime, R.backup)},
        "database_owner": R.owner, "schema_owner": R.owner, "objects": 300, "objects_not_owned_by_owner": 0,
        "runtime_owned": 0, "backup_owned": 0, "tables": 120, "runtime_missing_dml": 0,
        "runtime_forbidden_privileges": 0, "backup_missing_select": 0, "sequences": 10,
        "runtime_missing_sequence_use": 0, "runtime_schema_create": False, "runtime_database_create": False,
        "public_schema_create": False, "public_database_connect": False, "runtime_member_of_owner": False,
        "default_privileges": [{"type": "r", "acl": "{talklee_app=arwd/talklee_owner,talklee_backup=r/talklee_owner}"},
                               {"type": "S", "acl": "{talklee_app=rwU/talklee_owner,talklee_backup=r/talklee_owner}"}],
        "rls_forced_tables": 82, "tenant_tables_without_forced_rls": [],
    }
    base.update(overrides)
    return base


class ScramTests(unittest.TestCase):
    def test_verifier_format_and_math(self):
        salt = b"0123456789abcdef"
        verifier = roles.scram_verifier("correct-horse-battery-staple-123", salt)
        method, rest = verifier.split("$", 1)
        params, keys = rest.split("$")
        iterations, salt_b64 = params.split(":")
        stored_b64, server_b64 = keys.split(":")
        self.assertEqual((method, int(iterations), base64.b64decode(salt_b64)), ("SCRAM-SHA-256", 4096, salt))
        salted = hashlib.pbkdf2_hmac("sha256", b"correct-horse-battery-staple-123", salt, 4096)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        self.assertEqual(base64.b64decode(stored_b64), hashlib.sha256(client_key).digest())
        self.assertEqual(base64.b64decode(server_b64), hmac.new(salted, b"Server Key", hashlib.sha256).digest())
        self.assertNotIn("correct-horse", verifier)

    def test_random_salt_and_input_validation(self):
        self.assertNotEqual(roles.scram_verifier("a" * 30), roles.scram_verifier("a" * 30))
        for bad in ("", "has space", "naïve-password-unicode"):
            with self.assertRaises(RecoveryError):
                roles.scram_verifier(bad)

    def test_password_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret.txt"
            roles.generate_password(path)
            value = roles.read_password(path)
            self.assertEqual(len(value), 40)
            self.assertTrue(set(value) <= set(roles.PASSWORD_ALPHABET))
            with self.assertRaises(FileExistsError):
                roles.generate_password(path)
            short = Path(tmp) / "short.txt"
            short.write_text("too-short\n", encoding="utf-8")
            with self.assertRaises(RecoveryError):
                roles.read_password(short)


class SqlTests(unittest.TestCase):
    def test_roles_must_be_distinct_simple_names(self):
        for args in (("a", "a", "b"), ("a", "b; DROP", "c"), ("a" * 64, "b", "c")):
            with self.assertRaises(RecoveryError):
                roles.Roles(*args)

    def test_created_roles_are_restricted_and_runtime_is_never_an_owner_member(self):
        sql = roles.create_roles_sql(R, backup_bypassrls=False)
        for name in (R.owner, R.runtime, R.backup):
            self.assertIn(f'CREATE ROLE "{name}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', sql)
        self.assertNotIn(" BYPASSRLS", sql.replace("NOBYPASSRLS", ""))
        self.assertIn("GRANT %I TO %I", sql)  # administrator -> owner only
        self.assertNotIn(f'GRANT "{R.owner}" TO "{R.runtime}"', sql)
        self.assertIn('"talklee_backup" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS',
                      roles.create_roles_sql(R, backup_bypassrls=True))

    def test_grants_are_dml_only_and_cover_future_objects(self):
        sql = roles.grants_sql(R, "talkyai")
        self.assertTrue(sql.startswith('SET ROLE "talklee_owner";') and sql.rstrip().endswith("RESET ROLE;"))
        self.assertIn('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "talklee_app";', sql)
        self.assertIn('REVOKE CREATE ON SCHEMA public FROM PUBLIC;', sql)
        self.assertIn('REVOKE ALL ON DATABASE "talkyai" FROM PUBLIC;', sql)
        self.assertIn('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "talklee_app";', sql)
        self.assertIn('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO "talklee_backup";', sql)
        for forbidden in ("TRUNCATE", "REFERENCES", "TRIGGER", "ALL PRIVILEGES", "CREATE ON"):
            self.assertNotIn("GRANT " + forbidden, sql)

    def test_ownership_transfer_and_password_sql(self):
        sql = roles.ownership_sql(R, "talkyai", "talkyai_old")
        self.assertIn('REASSIGN OWNED BY "talkyai_old" TO "talklee_owner";', sql)
        self.assertNotIn("REASSIGN", roles.ownership_sql(R, "talkyai", None))
        self.assertEqual(roles.password_sql(R.runtime, "SCRAM-SHA-256$4096:x$y:z"),
                         'ALTER ROLE "talklee_app" PASSWORD \'SCRAM-SHA-256$4096:x$y:z\';\n')
        with self.assertRaises(RecoveryError):
            roles.password_sql(R.runtime, "x'; DROP ROLE y; --")


class EvaluateTests(unittest.TestCase):
    def test_clean_posture_passes(self):
        result = roles.evaluate(observed(), R, backup_bypassrls=False)
        self.assertEqual(result["status"], "passed", [k for k, v in result["checks"].items() if not v])

    def test_each_weakness_is_reported(self):
        superuser = observed()
        superuser["roles"][R.runtime]["superuser"] = True
        cases = {
            "runtime_role_restricted": superuser,
            "every_object_owned_by_owner": observed(objects_not_owned_by_owner=3),
            "runtime_and_backup_own_nothing": observed(runtime_owned=1),
            "runtime_has_row_dml_everywhere": observed(runtime_missing_dml=2),
            "runtime_has_no_truncate_references_trigger": observed(runtime_forbidden_privileges=1),
            "runtime_cannot_create_objects": observed(runtime_schema_create=True),
            "public_cannot_create_or_connect": observed(public_database_connect=True),
            "runtime_not_member_of_owner": observed(runtime_member_of_owner=True),
            "backup_can_read_every_table": observed(backup_missing_select=4),
            "default_privileges_cover_future_migrations": observed(default_privileges=[]),
            "tenant_tables_force_rls": observed(tenant_tables_without_forced_rls=["calls"]),
            "database_and_schema_owned_by_owner": observed(database_owner="talkyai"),
        }
        for check, posture in cases.items():
            result = roles.evaluate(posture, R, backup_bypassrls=False)
            self.assertEqual(result["status"], "needs_attention", check)
            self.assertFalse(result["checks"][check], check)

    def test_backup_bypass_must_match_the_request(self):
        bypass = observed()
        bypass["roles"][R.backup]["bypass_rls"] = True
        self.assertTrue(roles.evaluate(bypass, R, backup_bypassrls=True)["checks"]["backup_bypass_matches_request"])
        self.assertFalse(roles.evaluate(bypass, R, backup_bypassrls=False)["checks"]["backup_bypass_matches_request"])
        owner_bypass = observed()
        owner_bypass["roles"][R.owner]["bypass_rls"] = True
        self.assertFalse(roles.evaluate(owner_bypass, R, backup_bypassrls=False)["checks"]["owner_role_restricted"])


class ApplyTests(unittest.TestCase):
    def test_from_owner_must_be_an_existing_non_superuser(self):
        pg = MagicMock()
        for answer in ({"exists": False, "superuser": False}, {"exists": True, "superuser": True}):
            pg.json.return_value = answer
            with self.assertRaises(RecoveryError):
                roles.apply(pg, MagicMock(), R, "talkyai", from_owner="talkyai", passwords={}, backup_bypassrls=False)
        pg.sql.assert_not_called()
        with self.assertRaises(RecoveryError):
            roles.apply(pg, MagicMock(), R, "talkyai", from_owner=R.owner, passwords={}, backup_bypassrls=False)

    def test_apply_runs_one_transaction_and_never_sends_a_clear_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "app.txt"
            secret.write_text("x" * 32 + "\n", encoding="utf-8")
            pg = MagicMock()
            pg.json.return_value = observed()
            result = roles.apply(pg, MagicMock(), R, "talkyai", from_owner=None,
                                 passwords={R.runtime: secret}, backup_bypassrls=False)
        sent = pg.sql.call_args.args[1]
        self.assertTrue(sent.startswith("BEGIN;\n") and sent.endswith("COMMIT;\n"))
        self.assertNotIn("x" * 32, sent)
        self.assertIn('ALTER ROLE "talklee_app" PASSWORD \'SCRAM-SHA-256$4096:', sent)
        self.assertEqual((result["operation"], result["status"], result["passwords_set"]),
                         ("roles_apply", "passed", [R.runtime]))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Least-privilege PostgreSQL roles for Talklee: provision and verify.

Three roles, one job each:
  owner    owns the schema and every application object; used only by
           migrations (MIGRATION_DATABASE_URL). Not a superuser.
  runtime  what DATABASE_URL uses: CONNECT/TEMP, schema USAGE, row DML,
           sequence use and function EXECUTE. No DDL, no TRUNCATE, owns
           nothing, NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOCREATEROLE, so the
           tables' FORCE row-level security applies to it.
  backup   SELECT on everything for checked logical backups. BYPASSRLS only
           when --backup-bypassrls is given and the administrator may grant
           it; otherwise backups use the app.bypass_rls setting
           (recovery.py backup --rls-bypass-guc).

Default privileges make every object a later migration creates (as the
owner) usable by the runtime and backup roles without another grant.

Passwords never travel in clear: they are read from protected files and sent
to PostgreSQL as SCRAM-SHA-256 verifiers computed here, the way psql
\\password does. Connections use recovery.py's libpq named services.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import string
import sys

import recovery
from recovery import RecoveryError, identifier

SCRAM_ITERATIONS = 4096
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-_"
RUNTIME_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
FORBIDDEN_TABLE_PRIVILEGES = ("TRUNCATE", "REFERENCES", "TRIGGER")


def scram_verifier(password: str, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS) -> str:
    """RFC 5802/7677 verifier in PostgreSQL's stored format."""
    if not password or not all(32 < ord(ch) < 127 for ch in password):
        raise RecoveryError("passwords must be printable ASCII without spaces")
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def read_password(path: Path) -> str:
    value = recovery.private_file(path).read_text(encoding="utf-8").strip()
    if len(value) < 24:
        raise RecoveryError("role passwords must have at least 24 characters")
    return value


def generate_password(path: Path) -> None:
    """Write a new random password to a new protected file; never print it."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x", encoding="utf-8") as stream:
        if os.name != "nt":
            os.chmod(path, 0o600)
        stream.write("".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(40)) + "\n")


class Roles:
    def __init__(self, owner: str, runtime: str, backup: str):
        names = [owner, runtime, backup]
        for name in names:
            identifier(name)
        if len(set(names)) != 3:
            raise RecoveryError("owner, runtime and backup roles must be three different roles")
        self.owner, self.runtime, self.backup = owner, runtime, backup


def _literal(value: str) -> str:
    if "'" in value or "\\" in value or "\n" in value:
        raise RecoveryError("unsupported character in a generated SQL literal")
    return "'" + value + "'"


def create_roles_sql(roles: Roles, *, backup_bypassrls: bool) -> str:
    """Create missing roles with explicit attributes; existing ones are verified, not altered."""
    statements = []
    for name, bypass in ((roles.owner, "NOBYPASSRLS"), (roles.runtime, "NOBYPASSRLS"),
                         (roles.backup, "BYPASSRLS" if backup_bypassrls else "NOBYPASSRLS")):
        statements.append(
            f"DO $$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname={_literal(name)}) THEN "
            f"CREATE ROLE {identifier(name)} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            f"{bypass}; END IF; END$$;")
    # The administrator acts as the owner for ownership transfer, pg_restore
    # --role and default privileges. A superuser already can. A CREATEROLE
    # administrator on PostgreSQL 16+ receives only ADMIN OPTION on a role it
    # creates (no SET, no INHERIT), so it must grant itself a usable
    # membership; on 15, membership alone allows SET ROLE. Never the other
    # way round: runtime is not a member of owner.
    statements.append(
        f"DO $$BEGIN IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) "
        f"AND NOT pg_has_role(current_user, {_literal(roles.owner)}, CASE WHEN "
        f"current_setting('server_version_num')::int >= 160000 THEN 'SET' ELSE 'MEMBER' END) THEN "
        f"EXECUTE format('GRANT %I TO %I', {_literal(roles.owner)}, current_user); END IF; END$$;")
    return "\n".join(statements) + "\n"


def password_sql(name: str, verifier: str) -> str:
    return f"ALTER ROLE {identifier(name)} PASSWORD {_literal(verifier)};\n"


def ownership_sql(roles: Roles, database: str, from_owner: str | None) -> str:
    owner = identifier(roles.owner)
    sql = f"ALTER DATABASE {identifier(database)} OWNER TO {owner};\nALTER SCHEMA public OWNER TO {owner};\n"
    if from_owner:
        # Moves every object the old owner holds in THIS database (and the
        # databases it owns) to the owner role. Guarded in apply().
        sql += f"REASSIGN OWNED BY {identifier(from_owner)} TO {owner};\n"
    return sql


def grants_sql(roles: Roles, database: str) -> str:
    """Run as the owner (SET ROLE) so every grant is made by the object owner."""
    db, owner, app, bak = identifier(database), identifier(roles.owner), identifier(roles.runtime), identifier(roles.backup)
    dml = ", ".join(RUNTIME_TABLE_PRIVILEGES)
    return f"""SET ROLE {owner};
REVOKE ALL ON DATABASE {db} FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE {db} TO {app};
GRANT CONNECT ON DATABASE {db} TO {bak};
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO {app}, {bak};
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {app};
GRANT {dml} ON ALL TABLES IN SCHEMA public TO {app};
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {app};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {app};
GRANT SELECT ON ALL TABLES IN SCHEMA public TO {bak};
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {bak};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {dml} ON TABLES TO {app};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {app};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {app};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {bak};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO {bak};
RESET ROLE;
"""


def verify_sql(roles: Roles, database: str) -> str:
    o, a, b, d = (_literal(value) for value in (roles.owner, roles.runtime, roles.backup, database))
    forbidden = " OR ".join(f"has_table_privilege({a}, c.oid, '{p}')" for p in FORBIDDEN_TABLE_PRIVILEGES)
    dml = " AND ".join(f"has_table_privilege({a}, c.oid, '{p}')" for p in RUNTIME_TABLE_PRIVILEGES)
    # Extension members (pgcrypto, pg_trgm...) belong to the extension, not the app.
    objects = ("FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
               "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f') "
               "AND NOT EXISTS (SELECT 1 FROM pg_depend e WHERE e.classid='pg_class'::regclass "
               "AND e.objid=c.oid AND e.deptype='e')")
    tables = objects.replace("('r','p','v','m','S','f')", "('r','p','v','m','f')")
    sequences = objects.replace("('r','p','v','m','S','f')", "('S')")
    return f"""BEGIN READ ONLY;
SELECT json_build_object(
 'roles', (SELECT json_object_agg(rolname, json_build_object('login', rolcanlogin, 'superuser', rolsuper,
            'bypass_rls', rolbypassrls, 'create_role', rolcreaterole, 'create_database', rolcreatedb,
            'replication', rolreplication)) FROM pg_roles WHERE rolname IN ({o}, {a}, {b})),
 'database_owner', (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname={d}),
 'schema_owner', (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'),
 'objects', (SELECT count(*) {objects}),
 'objects_not_owned_by_owner', (SELECT count(*) {objects} AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname={o})),
 'runtime_owned', (SELECT count(*) FROM pg_class WHERE relowner=(SELECT oid FROM pg_roles WHERE rolname={a})),
 'backup_owned', (SELECT count(*) FROM pg_class WHERE relowner=(SELECT oid FROM pg_roles WHERE rolname={b})),
 'tables', (SELECT count(*) {tables}),
 'runtime_missing_dml', (SELECT count(*) {tables} AND NOT ({dml})),
 'runtime_forbidden_privileges', (SELECT count(*) {tables} AND ({forbidden})),
 'backup_missing_select', (SELECT count(*) {tables} AND NOT has_table_privilege({b}, c.oid, 'SELECT')),
 'sequences', (SELECT count(*) {sequences}),
 'runtime_missing_sequence_use', (SELECT count(*) {sequences}
        AND NOT CASE WHEN c.relkind = 'S' THEN has_sequence_privilege({a}, c.oid, 'USAGE') ELSE true END),
 'runtime_schema_create', has_schema_privilege({a}, 'public', 'CREATE'),
 'runtime_database_create', has_database_privilege({a}, {d}, 'CREATE'),
 'public_schema_create', EXISTS (SELECT 1 FROM pg_namespace n, aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) x
                                 WHERE n.nspname='public' AND x.grantee=0 AND x.privilege_type='CREATE'),
 'public_database_connect', EXISTS (SELECT 1 FROM pg_database db, aclexplode(COALESCE(db.datacl, acldefault('d', db.datdba))) x
                                    WHERE db.datname={d} AND x.grantee=0 AND x.privilege_type='CONNECT'),
 'runtime_member_of_owner', pg_has_role({a}, {o}, 'MEMBER'),
 'default_privileges', (SELECT COALESCE(json_agg(json_build_object('type', defaclobjtype, 'acl', defaclacl::text)), '[]'::json)
                        FROM pg_default_acl WHERE defaclrole=(SELECT oid FROM pg_roles WHERE rolname={o})
                        AND defaclnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public')),
 'rls_forced_tables', (SELECT count(*) {tables} AND c.relrowsecurity AND c.relforcerowsecurity),
 'tenant_tables_without_forced_rls', (SELECT COALESCE(json_agg(c.relname ORDER BY c.relname), '[]'::json) {tables}
        AND c.relkind IN ('r','p') AND EXISTS (SELECT 1 FROM pg_attribute t WHERE t.attrelid=c.oid AND t.attname='tenant_id' AND NOT t.attisdropped)
        AND NOT (c.relrowsecurity AND c.relforcerowsecurity))
);
ROLLBACK;
"""


def evaluate(observed: dict, roles: Roles, *, backup_bypassrls: bool) -> dict:
    found = observed.get("roles") or {}
    def safe(name: str, allow_bypass: bool = False) -> bool:
        attributes = found.get(name)
        return bool(attributes and attributes["login"] and not any(
            attributes[key] for key in ("superuser", "create_role", "create_database", "replication"))
            and (allow_bypass or not attributes["bypass_rls"]))
    defaults = " ".join(entry["acl"] for entry in observed.get("default_privileges") or [])
    checks = {
        "owner_role_restricted": safe(roles.owner),
        "runtime_role_restricted": safe(roles.runtime),
        "backup_role_restricted": safe(roles.backup, allow_bypass=True),
        "backup_bypass_matches_request": bool(found.get(roles.backup)) and found[roles.backup]["bypass_rls"] == backup_bypassrls,
        "database_and_schema_owned_by_owner": observed["database_owner"] == roles.owner and observed["schema_owner"] == roles.owner,
        "every_object_owned_by_owner": observed["objects"] > 0 and observed["objects_not_owned_by_owner"] == 0,
        "runtime_and_backup_own_nothing": observed["runtime_owned"] == 0 and observed["backup_owned"] == 0,
        "runtime_has_row_dml_everywhere": observed["runtime_missing_dml"] == 0,
        "runtime_has_no_truncate_references_trigger": observed["runtime_forbidden_privileges"] == 0,
        "runtime_can_use_every_sequence": observed["runtime_missing_sequence_use"] == 0,
        "runtime_cannot_create_objects": not observed["runtime_schema_create"] and not observed["runtime_database_create"],
        "public_cannot_create_or_connect": not observed["public_schema_create"] and not observed["public_database_connect"],
        "runtime_not_member_of_owner": not observed["runtime_member_of_owner"],
        "backup_can_read_every_table": observed["backup_missing_select"] == 0,
        "default_privileges_cover_future_migrations": all(
            f"{name}=" in defaults for name in (roles.runtime, roles.backup)),
        "tenant_tables_force_rls": observed["tenant_tables_without_forced_rls"] == [],
    }
    return {"operation": "roles_verify", "observed_at": recovery.utcnow(), "observations": observed,
            "checks": checks, "status": "passed" if all(checks.values()) else "needs_attention"}


def verify(pg: recovery.Postgres, service: recovery.Service, roles: Roles, database: str,
           *, backup_bypassrls: bool) -> dict:
    return evaluate(pg.json(service, verify_sql(roles, database), database=database), roles,
                    backup_bypassrls=backup_bypassrls)


def apply(pg: recovery.Postgres, service: recovery.Service, roles: Roles, database: str, *,
          from_owner: str | None, passwords: dict[str, Path], backup_bypassrls: bool) -> dict:
    identifier(database)
    if from_owner:
        identifier(from_owner)
        if from_owner in (roles.owner, roles.runtime, roles.backup):
            raise RecoveryError("--from-owner must be the old owner, not one of the new roles")
        old = pg.json(service, "SELECT json_build_object('exists', count(*)=1, 'superuser', COALESCE(bool_or(rolsuper), false)) "
                      f"FROM pg_roles WHERE rolname={_literal(from_owner)};", database=database)
        if not old["exists"] or old["superuser"]:
            raise RecoveryError("--from-owner must name an existing non-superuser role")
    verifiers = {name: scram_verifier(read_password(path)) for name, path in passwords.items()}
    for name in verifiers:
        if name not in (roles.owner, roles.runtime, roles.backup):
            raise RecoveryError("passwords can only be set for the three managed roles")
    sql = (create_roles_sql(roles, backup_bypassrls=backup_bypassrls)
           + "".join(password_sql(name, verifier) for name, verifier in verifiers.items())
           + ownership_sql(roles, database, from_owner) + grants_sql(roles, database))
    # One transaction: a failure leaves roles, ownership and grants untouched.
    pg.sql(service, "BEGIN;\n" + sql + "COMMIT;\n", database=database, readonly=False)
    result = verify(pg, service, roles, database, backup_bypassrls=backup_bypassrls)
    result.update(operation="roles_apply", passwords_set=sorted(verifiers), from_owner=from_owner)
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--service-file", type=Path, default=os.environ.get("PGSERVICEFILE"))
    result.add_argument("--bin-dir", type=Path)
    result.add_argument("--allow-local-plaintext", action="store_true", help="synthetic loopback rehearsal only")
    commands = result.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate-password", help="write a new random password to a new protected file")
    generate.add_argument("--output", type=Path, required=True)
    for name in ("plan", "apply", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--database", required=True)
        command.add_argument("--owner", default="talklee_owner")
        command.add_argument("--runtime", default="talklee_app")
        command.add_argument("--backup", default="talklee_backup")
        command.add_argument("--backup-bypassrls", action="store_true")
        if name != "plan":
            command.add_argument("--service", required=True, help="administrator service (superuser or CREATEROLE)")
            command.add_argument("--receipt", type=Path, required=True)
        if name != "verify":
            command.add_argument("--from-owner", help="existing non-superuser owner whose objects move to --owner")
        if name == "apply":
            for role in ("owner", "runtime", "backup"):
                command.add_argument(f"--{role}-password-file", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "generate-password":
            generate_password(args.output)
            print(json.dumps({"operation": "generate_password", "status": "written", "file": str(args.output)}))
            return 0
        roles = Roles(args.owner, args.runtime, args.backup)
        if args.command == "plan":
            print(create_roles_sql(roles, backup_bypassrls=args.backup_bypassrls)
                  + ownership_sql(roles, args.database, args.from_owner) + grants_sql(roles, args.database))
            return 0
        if args.service_file is None:
            raise RecoveryError("--service-file or PGSERVICEFILE is required")
        if args.receipt.exists() or args.receipt.is_symlink():
            raise RecoveryError("receipt already exists")
        pg = recovery.Postgres(args.bin_dir)
        service = recovery.Service(Path(args.service_file), args.service, args.allow_local_plaintext)
        if args.command == "apply":
            passwords = {name: path for name, path in ((roles.owner, args.owner_password_file),
                                                       (roles.runtime, args.runtime_password_file),
                                                       (roles.backup, args.backup_password_file)) if path}
            payload = apply(pg, service, roles, args.database, from_owner=args.from_owner,
                            passwords=passwords, backup_bypassrls=args.backup_bypassrls)
        else:
            payload = verify(pg, service, roles, args.database, backup_bypassrls=args.backup_bypassrls)
        recovery.write_json_new(args.receipt, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["status"] == "passed" else 1
    except (RecoveryError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, RecoveryError) else "local file/configuration operation failed; inspect protected inputs"
        print("ROLES FAILED: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

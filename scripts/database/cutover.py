#!/usr/bin/env python3
"""Load a verified backup into the separate production database server.

The source database is never contacted: the input is a published bundle from
``recovery.py backup`` (taken read-only, with ``--rls-bypass-guc`` on today's
cluster). The target is a different PostgreSQL cluster; the database must not
exist yet, so nothing is overwritten. Objects are restored as the owner role
(``pg_restore --role``), every table's row count and the Alembic heads must
match the snapshot exactly, then roles.py applies the least-privilege grants
and verifies them. The receipt carries counts and metadata, never rows or
credentials, plus the connection settings to configure (without passwords).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import recovery
import roles as roles_tool
from recovery import RecoveryError, identifier


def _target_inventory(pg: recovery.Postgres, service: recovery.Service, database: str,
                      rls_settings: dict[str, str]) -> dict:
    # Counted through the application's own RLS settings, so a non-superuser
    # administrator can verify; coverage is proven first so no table can be
    # counted filtered.
    service.rls_bypass_guc, service.rls_settings = True, dict(rls_settings)
    try:
        uncovered = recovery.rls_uncovered(pg, service, database)
        if uncovered:
            raise RecoveryError("restored tables would be counted filtered (name the needed --rls-setting): "
                                + ", ".join(map(str, uncovered[:20])))
        return pg.json(service, "BEGIN READ ONLY; " + recovery.inventory_sql() + " ROLLBACK;", database=database)
    finally:
        service.rls_bypass_guc, service.rls_settings = False, {}


def connection_template(service: recovery.Service, database: str, role: str) -> str:
    values = service.values
    query = "sslmode=" + values["sslmode"]
    if values.get("sslrootcert"):
        query += "&sslrootcert=" + values["sslrootcert"]
    return f"postgresql://{role}:<URL-ENCODED {role} PASSWORD>@{values['host']}:{values['port']}/{database}?{query}"


def load(pg: recovery.Postgres, service: recovery.Service, bundle: Path, database: str,
         roles: roles_tool.Roles, expected_heads: list[str], receipt_path: Path, *,
         backup_bypassrls: bool, rls_settings: dict[str, str] | None = None) -> dict:
    receipt = recovery.verify_bundle(bundle)
    identifier(database)
    if receipt_path.exists() or receipt_path.is_symlink():
        raise RecoveryError("cut-over receipt already exists")
    if not expected_heads or set(receipt["inventory"]["heads"]) != set(expected_heads):
        raise RecoveryError("archive schema heads differ from the approved expected heads")
    if "table_counts" not in receipt["inventory"]:
        raise RecoveryError("bundle has no per-table counts; take a new backup with this release")
    if service.values["dbname"] != "postgres":
        raise RecoveryError("target service must name the postgres maintenance database")
    started = time.monotonic()
    result = {"operation": "cutover_load", "started_at": recovery.utcnow(), "database": database,
              "archive_sha256": receipt["sha256"], "snapshot_time": receipt["inventory"]["snapshot_time"],
              "status": "failed", "database_creation": "not_attempted", "source_contacted": False}
    try:
        if recovery.cluster_identifier(pg, service) == receipt["source_cluster_id"]:
            raise RecoveryError("target is the source cluster; the separate database must be a different server")
        if recovery.endpoint_fingerprint(pg, service) == receipt["source_endpoint_fingerprint"]:
            raise RecoveryError("target endpoint is the source endpoint")
        if pg.sql(service, f"SELECT 1 FROM pg_database WHERE datname='{database}';"):
            raise RecoveryError("target database already exists; cut-over never overwrites a database")
        pg.sql(service, roles_tool.create_roles_sql(roles, backup_bypassrls=backup_bypassrls), readonly=False)
        result["database_creation"] = "attempted_unconfirmed"
        pg.sql(service, f"CREATE DATABASE {identifier(database)} OWNER {identifier(roles.owner)} "
               "TEMPLATE template0 ENCODING 'UTF8';", readonly=False)
        result["database_creation"] = "created"
        restore_started = time.monotonic()
        pg.run("pg_restore", ["--no-password", "--exit-on-error", "--single-transaction", "--no-owner",
                              "--no-privileges", "--role", roles.owner, "--dbname", service.connection(database),
                              str(bundle / "database.dump")], service, readonly=False)
        result["restore_seconds"] = round(time.monotonic() - restore_started, 3)
        restored = _target_inventory(pg, service, database, rls_settings or {})
        expected = receipt["inventory"]
        mismatched = sorted(name for name in set(expected["table_counts"]) | set(restored["table_counts"])
                            if expected["table_counts"].get(name) != restored["table_counts"].get(name))
        if mismatched or restored["heads"] != expected["heads"]:
            raise RecoveryError("restored data differs from the snapshot in: " + ", ".join(mismatched[:20] or ["heads"]))
        result["tables_verified"] = len(restored["table_counts"])
        result["rows_verified"] = sum(restored["table_counts"].values())
        result["critical_counts"] = restored["counts"]
        result["heads"] = restored["heads"]
        pg.sql(service, "BEGIN;\n" + roles_tool.ownership_sql(roles, database, None)
               + roles_tool.grants_sql(roles, database) + "COMMIT;\n", database=database, readonly=False)
        verification = roles_tool.verify(pg, service, roles, database, backup_bypassrls=backup_bypassrls)
        result["roles"] = {"status": verification["status"], "checks": verification["checks"]}
        if verification["status"] != "passed":
            raise RecoveryError("least-privilege verification failed: " + ", ".join(
                name for name, ok in verification["checks"].items() if not ok))
        result["configure"] = {
            "DATABASE_URL": connection_template(service, database, roles.runtime),
            "MIGRATION_DATABASE_URL (operator-only file)": connection_template(service, database, roles.owner),
            "DATABASE_REQUIRE_RESTRICTED_ROLE": "true",
            "backup service user": roles.backup,
        }
        result["status"] = "loaded_and_verified"
        return result
    except RecoveryError as exc:
        result["failure"] = str(exc)
        raise
    finally:
        result.update(completed_at=recovery.utcnow(), duration_seconds=round(time.monotonic() - started, 3),
                      database_retained=result["database_creation"] != "not_attempted")
        recovery.write_json_new(receipt_path, result)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--service-file", type=Path, default=os.environ.get("PGSERVICEFILE"))
    result.add_argument("--bin-dir", type=Path)
    result.add_argument("--allow-local-plaintext", action="store_true", help="synthetic loopback rehearsal only")
    commands = result.add_subparsers(dest="command", required=True)
    command = commands.add_parser("load", help="restore a verified bundle into a new database on the target server")
    command.add_argument("--service", required=True, help="target administrator service (dbname=postgres)")
    command.add_argument("--bundle", type=Path, required=True)
    command.add_argument("--database", required=True)
    command.add_argument("--owner", default="talklee_owner")
    command.add_argument("--runtime", default="talklee_app")
    command.add_argument("--backup", default="talklee_backup")
    command.add_argument("--backup-bypassrls", action="store_true")
    command.add_argument("--expected-head", action="append", required=True)
    command.add_argument("--rls-setting", action="append", default=[], metavar="app.NAME=VALUE",
                         help="session setting needed to count every row, e.g. app.current_user_id=<platform admin id>")
    command.add_argument("--receipt", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.service_file is None:
            raise RecoveryError("--service-file or PGSERVICEFILE is required")
        service = recovery.Service(Path(args.service_file), args.service, args.allow_local_plaintext)
        payload = load(recovery.Postgres(args.bin_dir), service, args.bundle, args.database,
                       roles_tool.Roles(args.owner, args.runtime, args.backup), args.expected_head,
                       args.receipt, backup_bypassrls=args.backup_bypassrls,
                       rls_settings=recovery.parse_rls_settings(args.rls_setting))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (RecoveryError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, RecoveryError) else "local file/configuration operation failed; inspect protected inputs"
        print("CUTOVER FAILED: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

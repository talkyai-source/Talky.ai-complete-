#!/usr/bin/env python3
"""Database audit, consistent backup and isolated restore rehearsal.

Only libpq named services are accepted. Passwords are read by libpq from a
protected password file, never passed as command-line arguments or receipts.
No command migrates a database, starts Talklee or contacts a provider.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone


TABLES = ("tenants", "campaigns", "calls", "leads")
SCOPED_TABLES = ("campaigns", "calls", "leads")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
RESTORE_NAME = re.compile(r"talky_restore_[a-z0-9_]{1,49}\Z")
ALLOWED_SERVICE_KEYS = {
    "host", "hostaddr", "port", "dbname", "user", "sslmode", "sslrootcert",
    "sslcert", "sslkey", "connect_timeout", "passfile", "channel_binding",
    "target_session_attrs", "gssencmode",
}
SERVICE_ENV = {
    "host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT",
    "dbname": "PGDATABASE", "user": "PGUSER", "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT", "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
    "connect_timeout": "PGCONNECT_TIMEOUT", "channel_binding": "PGCHANNELBINDING",
    "target_session_attrs": "PGTARGETSESSIONATTRS", "gssencmode": "PGGSSENCMODE",
}


class RecoveryError(RuntimeError):
    """A safe error message containing no database output or credentials."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(value: str) -> str:
    if not NAME.fullmatch(value):
        raise RecoveryError("database and role identifiers must be simple names, at most 63 characters")
    return '"' + value + '"'


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sync_file(path: Path) -> None:
    # Windows _commit (used by os.fsync) rejects read-only descriptors with
    # EBADF. r+b supplies a writable handle without truncating/changing bytes.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def private_file(path: Path) -> Path:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise RecoveryError("configuration path must name a regular file")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RecoveryError("configuration and password files must have mode 0600 or stricter")
    return path


def write_json_new(path: Path, payload: dict) -> None:
    """Publish a private receipt without replacing an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x", encoding="utf-8") as stream:
        if os.name != "nt":
            os.chmod(path, 0o600)
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


class Service:
    def __init__(self, file: Path, name: str, allow_local_plaintext: bool = False):
        if not NAME.fullmatch(name):
            raise RecoveryError("invalid service name")
        self.file = private_file(file)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.file, encoding="utf-8")
        if parser.defaults() or name not in parser:
            raise RecoveryError("named service is missing or uses unsupported DEFAULT values")
        self.values = dict(parser[name])
        if set(self.values) - ALLOWED_SERVICE_KEYS:
            raise RecoveryError("service has unsupported settings; keep passwords in a password file and omit options")
        if not all(self.values.get(key) for key in ("host", "port", "dbname", "user", "sslmode")):
            raise RecoveryError("service requires explicit host, port, dbname, user and sslmode")
        for value in self.values.values():
            if "\n" in value or "\r" in value:
                raise RecoveryError("multiline connection settings are not allowed")
        identifier(self.values["dbname"])
        identifier(self.values["user"])
        if not self.values["port"].isdigit() or not 1 <= int(self.values["port"]) <= 65535:
            raise RecoveryError("service has an invalid port")
        host = self.values["host"]
        if "," in host or "/" in host or "\\" in host or not host:
            raise RecoveryError("use one explicit TCP database host")
        loopback = host.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        # Explicit loopback exception is only for synthetic local rehearsal.
        if self.values["sslmode"] != "verify-full":
            if not (allow_local_plaintext and loopback and self.values["sslmode"] == "disable"
                    and "hostaddr" not in self.values):
                raise RecoveryError("require sslmode=verify-full; plaintext exception is loopback-only")
        self.local_plaintext = self.values["sslmode"] == "disable"
        password_file = self.values.get("passfile") or os.environ.get("PGPASSFILE")
        if password_file:
            self.passfile = private_file(Path(password_file))
        else:
            self.passfile = None
        self.name = name

    def env(self, *, database: str | None = None, readonly: bool = True) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("PG") and key not in {"DATABASE_URL", "READ_DATABASE_URL", "MIGRATION_DATABASE_URL"}}
        # Pin the parsed endpoint for the whole operation. Re-reading a mutable
        # service file between identity checks and CREATE/restore could redirect
        # a later subprocess after its target had already been verified.
        env.update({SERVICE_ENV[key]: value for key, value in self.values.items() if key in SERVICE_ENV})
        env.update(PGCONNECT_TIMEOUT="10", PGAPPNAME="talklee-recovery",
                   PGOPTIONS="-c lock_timeout=5000 -c statement_timeout=3600000"
                             + (" -c default_transaction_read_only=on" if readonly else ""))
        if self.passfile:
            env["PGPASSFILE"] = str(self.passfile)
        return env

    def connection(self, database: str | None = None) -> str:
        # Only a safe database name goes on argv; all other settings come from
        # the pinned environment, with passwords kept in the protected file.
        return "dbname=" + identifier(database or self.values["dbname"])[1:-1]


class Postgres:
    def __init__(self, bin_dir: Path | None = None, timeout: int = 3600):
        self.bin_dir = bin_dir
        self.timeout = timeout

    def executable(self, name: str) -> str:
        executable = str(self.bin_dir / (name + (".exe" if os.name == "nt" else ""))) if self.bin_dir else shutil.which(name)
        if not executable or not Path(executable).is_file():
            raise RecoveryError(f"required PostgreSQL client is unavailable: {name}")
        return executable

    def run(self, name: str, args: list[str], service: Service | None = None,
            *, sql: str | None = None, readonly: bool = True) -> str:
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            result = subprocess.run([self.executable(name), *args], input=sql, text=True,
                                    encoding="utf-8", capture_output=True, timeout=self.timeout,
                                    env=service.env(readonly=readonly) if service else None, **kwargs)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise RecoveryError(f"{name} could not complete; verify client/access and timeout") from exc
        if result.returncode:
            # PostgreSQL diagnostics may include connection details or customer
            # values. Intentionally never echo subprocess output in receipts.
            raise RecoveryError(f"{name} failed (exit {result.returncode}); inspect privately using the configured service")
        return result.stdout.strip()

    def sql(self, service: Service, sql: str, *, database: str | None = None, readonly: bool = True) -> str:
        return self.run("psql", ["-X", "-qAt", "--no-password", "--set", "ON_ERROR_STOP=1",
                                 "--dbname", service.connection(database)], service,
                        sql=sql, readonly=readonly)

    def json(self, service: Service, sql: str, **kwargs) -> dict | list:
        try:
            return json.loads(self.sql(service, sql, **kwargs))
        except json.JSONDecodeError as exc:
            raise RecoveryError("database returned invalid verification data") from exc


AUDIT_SQL = """
BEGIN READ ONLY;
SELECT json_build_object(
 'database', current_database(), 'server_version_num', current_setting('server_version_num'),
 'server_address', inet_server_addr()::text, 'server_port', inet_server_port(),
 'superuser', r.rolsuper, 'bypass_rls', r.rolbypassrls,
 'create_role', r.rolcreaterole, 'create_database', r.rolcreatedb,
 'tls', COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()), false),
 'database_bytes', pg_database_size(current_database()),
 'extensions', (SELECT COALESCE(json_object_agg(extname, extversion), '{}'::json) FROM pg_extension)
) FROM pg_roles r WHERE rolname=current_user;
ROLLBACK;
"""


def inventory_sql() -> str:
    counts = ", ".join(f"'{table}', (SELECT count(*) FROM public.{table})" for table in TABLES)
    return ("SELECT json_build_object('heads', (SELECT COALESCE(json_agg(version_num ORDER BY version_num), '[]'::json) "
            "FROM public.alembic_version), 'counts', json_build_object(" + counts + "), "
            "'snapshot_time', transaction_timestamp());")


def endpoint_fingerprint(pg: Postgres, service: Service) -> str:
    endpoint = pg.json(service, "SELECT json_build_object('address', inet_server_addr()::text, 'port', inet_server_port());")
    if not endpoint.get("address") or not endpoint.get("port"):
        raise RecoveryError("cannot establish the actual TCP database endpoint")
    return hashlib.sha256(json.dumps(endpoint, sort_keys=True).encode()).hexdigest()


def cluster_identifier(pg: Postgres, service: Service) -> str:
    """A cluster identity cannot change when a second NIC/alias is selected."""
    value = pg.sql(service, "SELECT system_identifier::text FROM pg_catalog.pg_control_system();")
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise RecoveryError("could not establish PostgreSQL cluster identity")
    return value


class Snapshot:
    """Keep one read-only transaction open while pg_dump imports its snapshot."""
    def __init__(self, pg: Postgres, service: Service):
        self.pg, self.service = pg, service
        self.process = None
        self.errors = None
        self.reader = None

    def __enter__(self):
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            self.errors = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
            self.process = subprocess.Popen(
                [self.pg.executable("psql"), "-X", "-qAt", "--no-password", "--set", "ON_ERROR_STOP=1",
                 "--dbname", self.service.connection()], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.errors, text=True, encoding="utf-8", bufsize=1, env=self.service.env(), **kwargs)
            self.lines = queue.Queue()
            def read_lines():
                for line in self.process.stdout:
                    self.lines.put(line.rstrip("\r\n"))
                self.lines.put(None)
            self.reader = threading.Thread(target=read_lines, daemon=True)
            self.reader.start()
            self.process.stdin.write("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
                                     "SET LOCAL row_security=off;\n"
                                     "SELECT pg_export_snapshot();\n" + inventory_sql() + "\n")
            self.process.stdin.flush()
            self.snapshot = self.read()
            if not re.fullmatch(r"[A-Fa-f0-9]+-[A-Fa-f0-9]+-\d+", self.snapshot):
                raise RecoveryError("invalid exported snapshot")
            self.inventory = json.loads(self.read())
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def read(self) -> str:
        try:
            result = self.lines.get(timeout=min(self.pg.timeout, 60))
        except queue.Empty as exc:
            raise RecoveryError("snapshot transaction timed out") from exc
        if result is None:
            raise RecoveryError("snapshot transaction failed; backup role must read every protected table")
        return result

    def __exit__(self, *args):
        try:
            if self.process and self.process.poll() is None:
                try:
                    self.process.stdin.write("ROLLBACK;\n\\q\n")
                    self.process.stdin.flush()
                except (OSError, ValueError):
                    # Connection failure may close stdin before the first
                    # write or while attempting rollback. Still reap psql.
                    pass
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
        finally:
            if self.process:
                try:
                    self.process.stdin.close()
                except (OSError, ValueError):
                    pass
                if self.reader and self.reader.ident is not None:
                    self.reader.join(timeout=2)
                try:
                    self.process.stdout.close()
                except (OSError, ValueError):
                    pass
            if self.errors:
                self.errors.close()


def audit(pg: Postgres, service: Service, app_addresses: list[str]) -> dict:
    observed = pg.json(service, AUDIT_SQL)
    checks = {
        "restricted_runtime_role": not any(observed[key] for key in ("superuser", "bypass_rls", "create_role", "create_database")),
        "tls_verified": bool(observed["tls"] and service.values["sslmode"] == "verify-full"),
        "separate_endpoint": None,
    }
    if app_addresses:
        try:
            addresses = {ipaddress.ip_address(value) for value in app_addresses}
            peer = ipaddress.ip_address(observed["server_address"])
            checks["separate_endpoint"] = not peer.is_loopback and peer not in addresses
        except ValueError as exc:
            raise RecoveryError("app-address must be an IP address") from exc
    observed.pop("server_address", None)
    return {"operation": "audit", "observed_at": utcnow(), "observations": observed,
            "checks": checks, "status": "passed" if all(value is True for value in checks.values()) else "needs_attention",
            "limits": ["endpoint comparison does not establish independent physical hosting",
                       "backup retention, off-host survivability, alerts and PITR require operating evidence"]}


def verify_bundle(bundle: Path) -> dict:
    if bundle.is_symlink() or not bundle.is_dir():
        raise RecoveryError("backup must be a published bundle directory")
    receipt_path, archive = bundle / "receipt.json", bundle / "database.dump"
    if receipt_path.is_symlink() or archive.is_symlink():
        raise RecoveryError("backup bundle must not contain symlinks")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("operation") != "backup" or receipt.get("status") != "archive_verified":
            raise RecoveryError("backup receipt is not a successful published archive")
        if archive.stat().st_size != receipt["bytes"] or digest(archive) != receipt["sha256"]:
            raise RecoveryError("backup size/checksum does not match its receipt")
        if (bundle / "database.dump.sha256").read_text(encoding="ascii").strip() != receipt["sha256"] + "  database.dump":
            raise RecoveryError("backup checksum file does not match receipt")
        if set(receipt["inventory"]["counts"]) != set(TABLES):
            raise RecoveryError("backup inventory is incomplete")
        if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
                   for value in receipt["inventory"]["counts"].values()):
            raise RecoveryError("backup counts are invalid")
        if not re.fullmatch(r"[0-9]{1,20}", receipt["source_cluster_id"]):
            raise RecoveryError("backup cluster identity is invalid")
        if not re.fullmatch(r"[a-f0-9]{64}", receipt["source_endpoint_fingerprint"]):
            raise RecoveryError("backup endpoint fingerprint is invalid")
        snapshot_time = datetime.fromisoformat(receipt["inventory"]["snapshot_time"])
        if snapshot_time.tzinfo is None:
            raise RecoveryError("backup snapshot timestamp must include its UTC offset")
        return receipt
    except (KeyError, OSError, ValueError, TypeError) as exc:
        raise RecoveryError("backup bundle is incomplete or unreadable") from exc


def backup(pg: Postgres, service: Service, destination: Path, minimum_bytes: int,
           previous: Path | None, minimum_ratio: float) -> dict:
    if destination.exists() or destination.is_symlink():
        raise RecoveryError("refusing to replace an existing backup bundle")
    if minimum_bytes < 1024 or not 0 < minimum_ratio <= 1:
        raise RecoveryError("minimum bytes must be >=1024 and minimum ratio must be in (0, 1]")
    prior = verify_bundle(previous) if previous else None
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".talklee-backup-", dir=destination.parent))
    started = time.monotonic()
    try:
        archive = staging / "database.dump"
        source_fingerprint = endpoint_fingerprint(pg, service)
        source_cluster = cluster_identifier(pg, service)
        with Snapshot(pg, service) as snapshot:
            pg.run("pg_dump", ["--no-password", "--format=custom", "--no-owner", "--no-privileges",
                               "--snapshot", snapshot.snapshot, "--file", str(archive),
                               "--dbname", service.connection()], service)
            inventory = snapshot.inventory
        pg.run("pg_restore", ["--list", str(archive)])
        size = archive.stat().st_size
        if size < minimum_bytes or (prior and size < prior["bytes"] * minimum_ratio):
            raise RecoveryError("backup failed size plausibility; investigate, do not lower a guard to hide data loss")
        checksum = digest(archive)
        receipt = {"operation": "backup", "status": "archive_verified", "created_at": utcnow(),
                   "bytes": size, "sha256": checksum, "inventory": inventory,
                   "source_database": service.values["dbname"],
                   "source_endpoint_fingerprint": source_fingerprint,
                   "source_cluster_id": source_cluster,
                   "duration_seconds": round(time.monotonic() - started, 3),
                   "client": pg.run("pg_dump", ["--version"]),
                   "retention_applied": False, "off_host_verified": False, "restored": False}
        write_json_new(staging / "receipt.json", receipt)
        (staging / "database.dump.sha256").write_text(checksum + "  database.dump\n", encoding="ascii")
        for path in staging.iterdir():
            if os.name != "nt":
                path.chmod(0o600)
            sync_file(path)
        # Rename the complete directory on the same filesystem; no partly
        # published archive is ever treated as a successful backup bundle.
        staging.rename(destination)
        return receipt
    finally:
        if staging.exists():
            if staging.resolve().parent != destination.parent.resolve() or not staging.name.startswith(".talklee-backup-"):
                raise RecoveryError("refusing cleanup outside the owned backup staging directory")
            shutil.rmtree(staging)


def validate_restore_target(target: str, source_database: str) -> None:
    if not RESTORE_NAME.fullmatch(target) or target == source_database:
        raise RecoveryError("restore requires a new talky_restore_* database distinct from the source")


def rls_probe(pg: Postgres, service: Service, database: str, runtime_role: str) -> dict:
    role = identifier(runtime_role)
    attributes = pg.json(service,
        f"SELECT json_build_object('exists', count(*)=1, 'unsafe', COALESCE(bool_or(rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb), true), "
        f"'same_as_restorer', COALESCE(bool_or(rolname=current_user), true)) FROM pg_roles WHERE rolname='{runtime_role}';",
        database=database)
    if not attributes["exists"] or attributes["unsafe"] or attributes["same_as_restorer"]:
        raise RecoveryError("restore runtime probe requires an existing distinct restricted role")
    tables = ", ".join("public." + table for table in SCOPED_TABLES)
    policies = pg.json(service, "SELECT json_build_object('enabled', bool_and(relrowsecurity), 'tables', count(*)) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('campaigns','calls','leads');", database=database)
    if not policies["enabled"] or policies["tables"] != len(SCOPED_TABLES):
        raise RecoveryError("critical restored tables do not have row security enabled")
    pg.sql(service, f"GRANT CONNECT ON DATABASE {identifier(database)} TO {role}; "
           f"GRANT USAGE ON SCHEMA public TO {role}; GRANT SELECT ON {tables} TO {role};",
           database=database, readonly=False)
    populated = " AND ".join(f"EXISTS (SELECT 1 FROM public.{table} r WHERE r.tenant_id=t.id)" for table in SCOPED_TABLES)
    tenants = pg.json(service, "SELECT COALESCE(json_agg(id), '[]'::json) FROM "
                     f"(SELECT t.id FROM public.tenants t WHERE {populated} ORDER BY t.id LIMIT 2) covered;", database=database)
    if len(tenants) < 2:
        raise RecoveryError("insufficient RLS coverage: require two restored tenants with rows in every covered table")
    for tenant in tenants:
        # PostgreSQL UUID values are parsed before being included in SQL.
        import uuid
        tenant = str(uuid.UUID(tenant))
        counts = ", ".join(f"'{table}', (SELECT count(*) FROM public.{table} WHERE tenant_id='{tenant}'::uuid)" for table in SCOPED_TABLES)
        expected = pg.json(service, "SET row_security=off; SELECT json_build_object(" + counts + ");", database=database)
        observed = pg.json(service, f"BEGIN READ ONLY; SET LOCAL ROLE {role}; SET LOCAL app.bypass_rls='false'; "
            f"SET LOCAL app.current_tenant_id='{tenant}'; SELECT json_build_object(" +
            ", ".join(f"'{table}', (SELECT count(*) FROM public.{table})" for table in SCOPED_TABLES) + "); ROLLBACK;", database=database)
        if observed != expected:
            raise RecoveryError("restored tenant visibility differs from its authorized rows")
        foreign = pg.json(service, f"BEGIN READ ONLY; SET LOCAL ROLE {role}; SET LOCAL app.bypass_rls='false'; "
            f"SET LOCAL app.current_tenant_id='{tenant}'; SELECT json_build_object(" +
            ", ".join(f"'{table}', (SELECT count(*) FROM public.{table} WHERE tenant_id IS DISTINCT FROM '{tenant}'::uuid)" for table in SCOPED_TABLES) + "); ROLLBACK;", database=database)
        if any(foreign.values()):
            raise RecoveryError("foreign tenant rows are visible in restored data")
    return {"tenants_checked": len(tenants), "tables_checked": list(SCOPED_TABLES), "status": "passed"}


def restore(pg: Postgres, service: Service, bundle: Path, database: str, runtime_role: str,
            expected_heads: list[str], receipt_path: Path) -> dict:
    receipt = verify_bundle(bundle)
    validate_restore_target(database, receipt["source_database"])
    identifier(runtime_role)
    if receipt_path.exists() or receipt_path.is_symlink():
        raise RecoveryError("restore receipt already exists")
    if not expected_heads or set(receipt["inventory"]["heads"]) != set(expected_heads):
        raise RecoveryError("archive schema heads differ from the approved expected heads; rehearse migrations separately")
    # Maintenance connection must not target an application database.
    if service.values["dbname"] != "postgres":
        raise RecoveryError("restore service must name the postgres maintenance database")
    started, created = time.monotonic(), False
    result = {"operation": "restore", "started_at": utcnow(), "database": database,
              "archive_sha256": receipt["sha256"], "status": "failed", "database_retained": False,
              "database_creation": "not_attempted",
              "limits": ["no application lifespan, migrations, recording/key recovery or provider actions executed",
                         "SQL tenant checks cover campaigns, calls and leads only"]}
    try:
        if cluster_identifier(pg, service) == receipt["source_cluster_id"]:
            raise RecoveryError("restore source cluster is prohibited, including alternate addresses")
        if endpoint_fingerprint(pg, service) == receipt["source_endpoint_fingerprint"]:
            raise RecoveryError("restore requires a separate database endpoint; source server is prohibited")
        exists = pg.sql(service, f"SELECT 1 FROM pg_database WHERE datname='{database}';")
        if exists:
            raise RecoveryError("restore target already exists; no existing database may be overwritten")
        result["database_creation"] = "attempted_unconfirmed"
        pg.sql(service, f"CREATE DATABASE {identifier(database)} TEMPLATE template0;", readonly=False)
        created = True
        result["database_creation"] = "created"
        pg.run("pg_restore", ["--no-password", "--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges",
                               "--dbname", service.connection(database), str(bundle / "database.dump")], service, readonly=False)
        restored = pg.json(service, "BEGIN READ ONLY; SET LOCAL row_security=off; " + inventory_sql() + " ROLLBACK;", database=database)
        if restored["counts"] != receipt["inventory"]["counts"] or restored["heads"] != receipt["inventory"]["heads"]:
            raise RecoveryError("restored schema/counts do not match the exact backup snapshot")
        result["inventory"] = restored
        result["tenant_isolation"] = rls_probe(pg, service, database, runtime_role)
        # Compile current read contracts without returning customer records.
        pg.sql(service, "BEGIN READ ONLY; SELECT id, tenant_id, status FROM public.campaigns LIMIT 0; "
               "SELECT id, tenant_id, direction, status FROM public.calls LIMIT 0; ROLLBACK;", database=database)
        result["status"] = "sql_restore_verified"
        return result
    except RecoveryError as exc:
        result["failure"] = str(exc)
        raise
    finally:
        retained = True if created else (None if result["database_creation"] == "attempted_unconfirmed" else False)
        result.update(completed_at=utcnow(), duration_seconds=round(time.monotonic() - started, 3), database_retained=retained)
        write_json_new(receipt_path, result)


def health(root: Path, maximum_age_hours: float) -> dict:
    if maximum_age_hours <= 0:
        raise RecoveryError("maximum age must be positive")
    bundles = sorted((path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    if not bundles:
        raise RecoveryError("no published backup bundles exist")
    receipt = verify_bundle(bundles[0])
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(receipt["inventory"]["snapshot_time"])).total_seconds() / 3600
    except (KeyError, ValueError, TypeError) as exc:
        raise RecoveryError("backup timestamp is invalid") from exc
    if age < -0.02 or age > maximum_age_hours:
        raise RecoveryError("latest backup is stale or has a future timestamp")
    return {"operation": "health", "status": "passed", "checked_at": utcnow(),
            "bundle": bundles[0].name, "age_hours": round(age, 3), "sha256": receipt["sha256"],
            "limits": ["local archive age/checksum only; no off-host or restore assertion"]}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--service-file", type=Path, default=os.environ.get("PGSERVICEFILE"))
    result.add_argument("--bin-dir", type=Path, help="directory containing PostgreSQL client executables")
    result.add_argument("--allow-local-plaintext", action="store_true", help="synthetic loopback rehearsal only")
    commands = result.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("audit", help="read-only runtime privilege, TLS and endpoint checks")
    inspect.add_argument("--service", required=True)
    inspect.add_argument("--app-address", action="append", default=[])
    inspect.add_argument("--receipt", type=Path, required=True)
    dump = commands.add_parser("backup", help="publish a snapshot-consistent checked archive bundle")
    dump.add_argument("--service", required=True)
    dump.add_argument("--destination", type=Path, required=True)
    dump.add_argument("--minimum-bytes", type=int, default=1048576)
    dump.add_argument("--previous", type=Path)
    dump.add_argument("--minimum-ratio", type=float, default=0.5)
    replay = commands.add_parser("restore", help="restore only into a newly created scratch database")
    replay.add_argument("--service", required=True, help="isolated-server maintenance service (dbname=postgres)")
    replay.add_argument("--bundle", type=Path, required=True)
    replay.add_argument("--database", required=True)
    replay.add_argument("--runtime-role", required=True)
    replay.add_argument("--expected-head", action="append", required=True)
    replay.add_argument("--receipt", type=Path, required=True)
    check = commands.add_parser("health", help="verify latest archive checksum and maximum age")
    check.add_argument("--backup-root", type=Path, required=True)
    check.add_argument("--maximum-age-hours", type=float, default=26)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        pg = Postgres(args.bin_dir)
        if args.command == "health":
            payload = health(args.backup_root, args.maximum_age_hours)
        else:
            if args.service_file is None:
                raise RecoveryError("--service-file or PGSERVICEFILE is required")
            service = Service(Path(args.service_file), args.service, args.allow_local_plaintext)
            if args.command == "audit":
                payload = audit(pg, service, args.app_address)
                write_json_new(args.receipt, payload)
            elif args.command == "backup":
                payload = backup(pg, service, args.destination, args.minimum_bytes, args.previous, args.minimum_ratio)
            else:
                payload = restore(pg, service, args.bundle, args.database, args.runtime_role, args.expected_head, args.receipt)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["status"] != "needs_attention" else 1
    except (RecoveryError, OSError, configparser.Error, ValueError) as exc:
        message = str(exc) if isinstance(exc, RecoveryError) else "local file/configuration operation failed; inspect protected inputs"
        print("RECOVERY FAILED: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

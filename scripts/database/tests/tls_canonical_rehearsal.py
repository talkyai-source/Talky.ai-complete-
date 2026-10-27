#!/usr/bin/env python3
"""Opt-in full-schema recovery regression on two disposable local Docker servers.

Uses only generated credentials/data and the repository's recovery CLI. Does
not accept existing database endpoints, launch the app, or contact providers.
Requires Linux, a local Docker daemon, OpenSSL, PostgreSQL 16 clients and the
installer's Python dependency lock. Containers share one host: this is not
off-host disaster recovery, a production permissions review, or an RPO/RTO test.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("canonical_recovery", ROOT / "scripts/database/recovery.py")
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)
LABEL = "org.talklee.synthetic-recovery"
SOURCE_DB = "talky_synthetic_source"
RESTORED_DB = "talky_restore_canonical"
TENANTS = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command, *, input=None, env=None, cwd=None, timeout=180, redactions=None):
    result = subprocess.run(command, input=input, text=True, capture_output=True,
                            env=env, cwd=cwd, timeout=timeout)
    # Even synthetic passwords/DSNs must not leak into a public CI log.
    if result.returncode:
        detail = "output withheld"
        if redactions is not None:
            detail = (result.stdout + result.stderr)[-4000:]
            for value in redactions:
                detail = detail.replace(value, "[redacted fixture password]")
        raise RuntimeError(f"{Path(command[0]).name} failed with exit {result.returncode}; {detail}")
    return result.stdout.strip()


def write_private(path, text):
    with path.open("x", encoding="utf-8") as handle:
        path.chmod(0o600)
        handle.write(text)


class Fixture:
    def __init__(self, work, image):
        self.work, self.image = work, image
        self.token = uuid4().hex
        self.network = None
        self.docker_verified = False
        self.network_name = f"talklee-recovery-{self.token}"
        self.containers = []
        self.container_names = set()
        self.passwords = {role: secrets.token_urlsafe(32) for role in
                          ("fixture_admin", "fixture_migration", "fixture_runtime", "fixture_backup", "fixture_restore")}
        self.service_rows = []
        self.pass_rows = []
        self.services = work / "services.conf"
        self.passfile = work / "passwords"

    def docker(self, *arguments, **kwargs):
        return run(["docker", *arguments], redactions=self.passwords.values(), **kwargs)

    def prepare(self):
        require(sys.platform == "linux", "This fixture requires Linux and a local Docker daemon")
        require(not os.environ.get("DOCKER_HOST") or os.environ["DOCKER_HOST"].startswith("unix://"),
                "Remote Docker hosts are not accepted")
        endpoint = json.loads(self.docker("context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"))
        require(endpoint.startswith("unix://"), "The Docker context must use a local Unix socket")
        require(self.docker("info", "--format", "{{.OSType}}") == "linux", "Linux Docker is required")
        self.docker_verified = True
        self.docker("pull", self.image, timeout=300)
        observed = json.loads(self.docker("image", "inspect", self.image))[0]
        self.image_id = observed["Id"]
        self.image_digests = observed["RepoDigests"]
        self.network = self.docker("network", "create", "--internal", "--label", f"{LABEL}={self.token}",
                                   self.network_name)
        network = json.loads(self.docker("network", "inspect", self.network))[0]
        subnet = ipaddress.ip_network(network["IPAM"]["Config"][0]["Subnet"])
        require(network["Internal"] and subnet.version == 4 and subnet.is_private and subnet.num_addresses >= 8,
                "Fixture requires its own private internal IPv4 bridge")
        addresses = []
        write_private(self.work / "bootstrap-password", self.passwords["fixture_admin"])
        # No plaintext TCP fallback, including inside the Docker bridge.
        write_private(self.work / "pg_hba.conf", "local all all trust\nhostssl all all all scram-sha-256\nhostnossl all all all reject\n")
        for label in ("source", "target"):
            name = f"talklee-recovery-{label}-{self.token}"
            # Register the unique name before create: a timeout can occur after
            # Docker creates an object but before it returns its ID.
            self.container_names.add(name)
            container = self.docker(
                "create", "--name", name, "--label", f"{LABEL}={self.token}",
                "--network", self.network, "--memory", "768m",
                "--tmpfs", "/var/lib/postgresql/data:rw,nosuid,nodev,size=536870912",
                "--mount", f"type=bind,source={self.work},target=/fixture,readonly",
                "--env", "POSTGRES_USER=fixture_admin", "--env", "POSTGRES_DB=postgres",
                "--env", "POSTGRES_PASSWORD_FILE=/fixture/bootstrap-password",
                "--env", "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
                self.image_id, "bash", "-ec",
                "for attempt in {1..60}; do test -f /fixture/tls-ready && break; sleep 1; done\n"
                "test -f /fixture/tls-ready\n"
                "install -o postgres -g postgres -m 0600 /fixture/server.key /tmp/server.key\n"
                "install -o postgres -g postgres -m 0644 /fixture/server.crt /tmp/server.crt\n"
                "install -o postgres -g postgres -m 0600 /fixture/pg_hba.conf /tmp/pg_hba.conf\n"
                "exec docker-entrypoint.sh postgres -c ssl=on -c ssl_min_protocol_version=TLSv1.2 "
                "-c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key -c hba_file=/tmp/pg_hba.conf")
            self.containers.append(container)
            self.docker("start", container)
            observed = json.loads(self.docker("inspect", container))[0]
            endpoints = list(observed["NetworkSettings"]["Networks"].values())
            require(not observed["HostConfig"]["PortBindings"] and len(endpoints) == 1
                    and endpoints[0]["NetworkID"] == self.network,
                    "Fixture must use only its assigned unpublished bridge address")
            host = endpoints[0]["IPAddress"]
            require(ipaddress.ip_address(host) in subnet and host not in addresses,
                    "Fixture endpoints must be distinct addresses on the owned bridge")
            addresses.append(host)
            self.add_service(f"{label}_admin", host, "postgres", "fixture_admin")
            self.add_service(f"{label}_migration", host, SOURCE_DB if label == "source" else RESTORED_DB, "fixture_migration")
            self.add_service(f"{label}_runtime", host, SOURCE_DB if label == "source" else RESTORED_DB, "fixture_runtime")
            self.add_service(f"{label}_backup", host, SOURCE_DB if label == "source" else RESTORED_DB, "fixture_backup")
            self.add_service(f"{label}_restore", host, "postgres", "fixture_restore")
        # Docker allocates addresses before PostgreSQL starts. Sign only those
        # exact SANs, then release the bounded startup wait in both containers.
        self.create_certificates(addresses)
        write_private(self.work / "tls-ready", "ready\n")
        write_private(self.services, "\n".join(self.service_rows))
        write_private(self.passfile, "\n".join(self.pass_rows) + "\n")

    def create_certificates(self, addresses):
        for name in ("ca", "untrusted-ca"):
            run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                 "-subj", f"/CN=Talklee synthetic {name}", "-keyout", str(self.work / f"{name}.key"),
                 "-out", str(self.work / f"{name}.crt")])
            (self.work / f"{name}.key").chmod(0o600)
        run(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1",
             "-keyout", str(self.work / "server.key"), "-out", str(self.work / "server.csr")])
        (self.work / "server.key").chmod(0o600)
        write_private(self.work / "server.ext", "subjectAltName=" + ",".join("IP:" + host for host in addresses)
                      + "\nextendedKeyUsage=serverAuth\n")
        run(["openssl", "x509", "-req", "-in", str(self.work / "server.csr"), "-CA", str(self.work / "ca.crt"),
             "-CAkey", str(self.work / "ca.key"), "-CAcreateserial", "-days", "2", "-extfile", str(self.work / "server.ext"),
             "-out", str(self.work / "server.crt")])

    def add_service(self, name, host, database, user):
        self.service_rows.append(f"[{name}]\nhost={host}\nport=5432\ndbname={database}\nuser={user}\n"
                                 f"sslmode=verify-full\nsslrootcert={self.work / 'ca.crt'}\npassfile={self.passfile}\n")
        # A wildcard database is needed only because restore creates a new DB.
        self.pass_rows.append(f"{host}:5432:*:{user}:{self.passwords[user]}")

    def service(self, name):
        return recovery.Service(self.services, name)

    def cleanup(self):
        if not self.docker_verified:
            return
        failures = []
        containers = self.docker("container", "ls", "--all", "--quiet", "--no-trunc", "--filter", f"label={LABEL}={self.token}").splitlines()
        for container in containers:
            try:
                observed = json.loads(self.docker("inspect", container))[0]
                require(observed["Id"] == container and observed["Config"]["Labels"].get(LABEL) == self.token
                        and observed["Name"].lstrip("/") in self.container_names,
                        "Refusing cleanup of an unowned container")
                self.docker("rm", "--force", "--volumes", container)
            except Exception:
                failures.append("owned container cleanup failed")
        networks = self.docker("network", "ls", "--quiet", "--no-trunc", "--filter", f"label={LABEL}={self.token}").splitlines()
        for network in networks:
            try:
                observed = json.loads(self.docker("network", "inspect", network))[0]
                require(observed["Id"] == network and observed["Labels"].get(LABEL) == self.token
                        and observed["Name"] == self.network_name,
                        "Refusing cleanup of an unowned network")
                self.docker("network", "rm", network)
            except Exception:
                failures.append("owned network cleanup failed")
        require(not failures, "; ".join(failures))


def wait_ready(pg, service):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            require(pg.sql(service, "SELECT 1") == "1", "Unexpected readiness response")
            return
        except recovery.RecoveryError:
            time.sleep(1)
    raise RuntimeError("Synthetic PostgreSQL did not become TLS-ready")


def bootstrap(pg, fixture):
    for label in ("source", "target"):
        admin = fixture.service(f"{label}_admin")
        wait_ready(pg, admin)
        for role in ("fixture_migration", "fixture_runtime", "fixture_backup", "fixture_restore"):
            privileges = "BYPASSRLS" if role in ("fixture_backup", "fixture_restore") else "NOBYPASSRLS"
            privileges += " CREATEDB" if role == "fixture_restore" else " NOCREATEDB"
            pg.sql(admin, f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEROLE {privileges} "
                         f"PASSWORD '{fixture.passwords[role]}';", readonly=False)
        pg.sql(admin, "GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO fixture_backup, fixture_restore;",
               readonly=False)
        # The recovery tool enters the restricted role for its tenant probe;
        # membership is granted in this direction only, never to the runtime.
        pg.sql(admin, "GRANT fixture_runtime TO fixture_restore;", readonly=False)
    source_admin = fixture.service("source_admin")
    pg.sql(source_admin, f"CREATE DATABASE {SOURCE_DB} OWNER fixture_migration TEMPLATE template0;", readonly=False)
    migration = fixture.service("source_migration")
    pg.sql(migration, "SET app.bypass_rls='true';\n" + (ROOT / "backend/database/complete_schema.sql").read_text(encoding="utf-8"),
           readonly=False)
    query = urlencode({"sslmode": "verify-full", "sslrootcert": str(fixture.work / "ca.crt")})
    # No credential on argv or in the Alembic config/logging URL.
    env = dict(os.environ, MIGRATION_DATABASE_URL=
               f"postgresql://fixture_migration:{fixture.passwords['fixture_migration']}@{migration.values['host']}:"
               f"{migration.values['port']}/{SOURCE_DB}?{query}", PYTHON_DOTENV_DISABLED="1")
    for arguments in (("stamp", "0008_tenant_voice_tuning"), ("upgrade", "heads")):
        run([sys.executable, "-m", "alembic", *arguments], env=env, cwd=ROOT / "backend", timeout=180)
    observed = pg.json(migration, recovery.AUDIT_SQL)
    require(observed["tls"] and not any(observed[key] for key in
            ("superuser", "bypass_rls", "create_role", "create_database")),
            "Migration owner lost its restricted role or verified TLS contract")


def grant_runtime(pg, service, database):
    # Fixture grants are explicit and separate from pg_restore --no-privileges.
    # Runtime cannot alter the revision marker, own schema objects, or truncate.
    pg.sql(service, f"REVOKE ALL ON DATABASE {database} FROM PUBLIC; GRANT CONNECT ON DATABASE {database} TO fixture_runtime; "
           f"GRANT CONNECT ON DATABASE {database} TO fixture_backup; "
           "REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT USAGE ON SCHEMA public TO fixture_runtime; "
           "GRANT SELECT ON ALL TABLES IN SCHEMA public TO fixture_runtime; "
           "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO fixture_runtime; "
           "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO fixture_runtime; "
           "DO $$ DECLARE r record; BEGIN FOR r IN SELECT tablename FROM pg_tables "
           "WHERE schemaname='public' AND tablename<>'alembic_version' LOOP "
           "EXECUTE format('GRANT INSERT, UPDATE, DELETE ON public.%I TO fixture_runtime', r.tablename); END LOOP; END $$;",
           database=database, readonly=False)


def seed(pg, fixture):
    migration = fixture.service("source_migration")
    statements = ["SET app.bypass_rls='true';"]
    for number, tenant in enumerate(TENANTS, 1):
        campaign, lead, call = (str(uuid4()) for _ in range(3))
        statements += [
            f"INSERT INTO tenants(id,business_name) VALUES('{tenant}','Synthetic recovery tenant {number}');",
            f"INSERT INTO campaigns(id,tenant_id,name,status,direction) VALUES('{campaign}','{tenant}','Synthetic recovery campaign','draft','outbound');",
            f"INSERT INTO leads(id,tenant_id,campaign_id,phone_number) VALUES('{lead}','{tenant}','{campaign}','+1202555010{number}');",
            f"INSERT INTO calls(id,tenant_id,campaign_id,lead_id,phone_number,status,direction) VALUES('{call}','{tenant}','{campaign}','{lead}','+1202555010{number}','completed','outbound');",
        ]
    pg.sql(migration, "\n".join(statements), readonly=False)
    pg.sql(migration, "GRANT USAGE ON SCHEMA public TO fixture_backup; GRANT SELECT ON ALL TABLES IN SCHEMA public TO fixture_backup; "
           "GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO fixture_backup;", readonly=False)
    grant_runtime(pg, migration, SOURCE_DB)


def fingerprint(pg, service, database=None, schema_file=None):
    tables = pg.json(service, "SELECT json_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname='public';", database=database)
    rows = []
    for table in tables:
        name = recovery.identifier(table)
        rows.append(f"SELECT '{table}' AS name, (SELECT jsonb_build_object('count',count(*),'rows',"
                    f"COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)) FROM public.{name} t) AS payload")
    data = pg.json(service, "SELECT json_object_agg(name,payload ORDER BY name) FROM (" + " UNION ALL ".join(rows) + ") inventory;", database=database)
    sequences = pg.json(service, "SELECT COALESCE(json_agg(c.relname ORDER BY c.relname),'[]'::json) "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S';", database=database)
    sequence_values = {}
    for sequence in sequences:
        sequence_values[sequence] = pg.json(service, "SELECT row_to_json(s) FROM (SELECT last_value,is_called FROM public."
                                            + recovery.identifier(sequence) + ") s;", database=database)
    schema = pg.run("pg_dump", ["--no-password", "--schema-only", "--no-owner", "--no-privileges", "--dbname", service.connection(database)], service)
    # Newer patched pg_dump emits a random \restrict token; it is not schema.
    schema = "\n".join(line for line in schema.splitlines() if line and not line.startswith(("--", "\\restrict ", "\\unrestrict ")))
    if schema_file:
        write_private(schema_file, schema)
    return {"table_count": len(tables), "row_counts": {name: body["count"] for name, body in data.items()},
            "records_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
            "schema_sha256": hashlib.sha256(schema.encode()).hexdigest(), "sequences": sequence_values}


def runtime_checks(fixture, label, heads):
    import psycopg2
    service = fixture.service(f"{label}_runtime")
    with psycopg2.connect(**service.values) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user, rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole, "
                           "pg_has_role(current_user,'fixture_migration','MEMBER'), "
                           "(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()) FROM pg_roles WHERE rolname=current_user")
            require(cursor.fetchone() == ("fixture_runtime", False, False, True), "Runtime identity/TLS/role restriction mismatch")
            cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
            require([row[0] for row in cursor.fetchall()] == heads, "Runtime sees an unexpected migration head")
            cursor.execute("SELECT bool_and(has_table_privilege(current_user,c.oid,'SELECT') AND "
                           "NOT has_table_privilege(current_user,c.oid,'TRUNCATE') AND NOT pg_has_role(current_user,c.relowner,'MEMBER')) "
                           "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'")
            require(cursor.fetchone()[0] is True, "Runtime table grants/ownership mismatch")
            cursor.execute("SELECT has_schema_privilege(current_user,'public','CREATE'), "
                           "has_database_privilege(current_user,current_database(),'CREATE'), "
                           "has_table_privilege(current_user,'public.alembic_version','UPDATE')")
            require(cursor.fetchone() == (False, False, False), "Runtime has unwanted schema/migration privileges")
            for table in recovery.SCOPED_TABLES:
                cursor.execute(f"SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='public.{table}'::regclass")
                require(cursor.fetchone()[0] is True, "Tenant table lost forced RLS")
            cursor.execute("SET LOCAL app.bypass_rls='false'; SET LOCAL app.current_tenant_id=''")
            for table in recovery.SCOPED_TABLES:
                cursor.execute(f"SELECT count(*) FROM {table}")
                require(cursor.fetchone()[0] == 0, "Unset tenant unexpectedly sees records")
            for tenant in TENANTS:
                cursor.execute("SELECT set_config('app.current_tenant_id',%s,true)", (tenant,))
                for table in recovery.SCOPED_TABLES:
                    cursor.execute(f"SELECT count(*), count(*) FILTER (WHERE tenant_id<>%s::uuid) FROM {table}", (tenant,))
                    require(cursor.fetchone() == (1, 0), "Runtime tenant visibility mismatch")
                cursor.execute("UPDATE campaigns SET name='must not change' WHERE tenant_id<>%s::uuid", (tenant,))
                require(cursor.rowcount == 0, "Foreign tenant update was allowed")
                foreign = next(value for value in TENANTS if value != tenant)
                cursor.execute("SAVEPOINT foreign_write")
                try:
                    cursor.execute("INSERT INTO campaigns(tenant_id,name) VALUES(%s,'foreign write must fail')", (foreign,))
                except psycopg2.errors.InsufficientPrivilege:
                    cursor.execute("ROLLBACK TO SAVEPOINT foreign_write")
                else:
                    raise RuntimeError("Foreign tenant insert was allowed")
                cursor.execute("RELEASE SAVEPOINT foreign_write")
            cursor.execute("INSERT INTO campaigns(tenant_id,name) VALUES(%s,'owned runtime write') RETURNING id", (TENANTS[-1],))
            campaign = cursor.fetchone()[0]
            cursor.execute("INSERT INTO leads(tenant_id,campaign_id,phone_number) VALUES(%s,%s,'+12025550109') RETURNING id", (TENANTS[-1], campaign))
            lead = cursor.fetchone()[0]
            cursor.execute("INSERT INTO calls(tenant_id,campaign_id,lead_id,phone_number) VALUES(%s,%s,%s,'+12025550109') RETURNING id", (TENANTS[-1], campaign, lead))
            call = cursor.fetchone()[0]
            for table, record in (("calls", call), ("leads", lead), ("campaigns", campaign)):
                cursor.execute(f"DELETE FROM {table} WHERE id=%s", (record,))
                require(cursor.rowcount == 1, "Own-tenant runtime write/delete failed")
        # Acceptance writes never persist or create worker/call jobs.
        connection.rollback()


def tls_negative_checks(pg, fixture):
    service = fixture.service("source_runtime")
    for overrides, expected_error in (
            ({"PGSSLROOTCERT": str(fixture.work / "untrusted-ca.crt")}, "certificate verify failed"),
            ({"PGHOST": "wrong-name.invalid", "PGHOSTADDR": service.values["host"], "PGPASSWORD": fixture.passwords["fixture_runtime"]}, "does not match host name"),
            ({"PGSSLMODE": "disable"}, "pg_hba.conf rejects connection")):
        result = subprocess.run([pg.executable("psql"), "-X", "-qAt", "--no-password", "--dbname", service.connection(), "-c", "SELECT 1"],
                                env={**service.env(), **overrides}, text=True, capture_output=True, timeout=15)
        require(result.returncode != 0, "Untrusted certificate, wrong hostname, or plaintext unexpectedly connected")
        require(expected_error in result.stderr, "Negative TLS case failed for an unexpected reason")
    require(pg.sql(service, "SELECT 1") == "1", "Valid TLS connection failed after the refusal checks")


def recovery_cli(pg, fixture, *arguments):
    return json.loads(run([sys.executable, str(ROOT / "scripts/database/recovery.py"),
                           "--service-file", str(fixture.services), "--bin-dir", str(pg.bin_dir), *arguments]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--bin-dir", type=Path, default=Path("/usr/lib/postgresql/16/bin"))
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--image", default="postgres:16-bookworm")
    args = parser.parse_args()
    require(sys.platform == "linux", "This fixture requires Linux and a local Docker daemon")
    require(args.image == "postgres:16-bookworm" or re.fullmatch(r"postgres@sha256:[a-f0-9]{64}", args.image),
            "Only the official PostgreSQL16 fixture image or a reviewed official digest is accepted")
    require(not args.receipt.exists(), "Refusing to replace an existing receipt")
    for key in list(os.environ):
        if key.startswith("PG") or key in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "READ_DATABASE_URL"}:
            os.environ.pop(key)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    from alembic.script import ScriptDirectory
    heads = sorted(ScriptDirectory(str(ROOT / "backend/Alembic")).get_heads())
    require(heads, "The source migration graph has no head")
    pg = recovery.Postgres(args.bin_dir, timeout=180)
    require(re.search(r"\b16\.", pg.run("psql", ["--version"])), "PostgreSQL16 client binaries are required")
    work = Path(tempfile.mkdtemp(prefix="talklee-canonical-tls-"))
    work.chmod(0o700)
    fixture = Fixture(work, args.image)
    stage = "fixture setup"
    receipt = {"status": "failed", "schema_heads": heads, "checks": [], "production_accessed": False,
               "scope": "full canonical schema and synthetic data on two TLS containers sharing one host",
               "limits": ["not off-host survivability, retention/PITR, RPO/RTO, or alert-delivery acceptance",
                          "no application lifespan, provider, payment, call, or recording/encryption-key recovery",
                          "runtime data-operation checks cover campaigns, leads and calls; grants are fixture-specific"]}
    inputs = [ROOT / name for name in ("backend/database/complete_schema.sql", "backend/Alembic/env.py",
              "backend/alembic.ini", "scripts/database/recovery.py", "scripts/database/tests/tls_canonical_rehearsal.py",
              "scripts/installation/requirements-linux-py312.lock")]
    inputs += sorted((ROOT / "backend/Alembic/versions").glob("*.py"))
    receipt["source_sha256"] = {path.relative_to(ROOT).as_posix(): recovery.digest(path) for path in inputs}
    receipt["source_git_sha"] = run(["git", "rev-parse", "HEAD"], cwd=ROOT)
    receipt["source_worktree_dirty"] = bool(run(["git", "status", "--porcelain"], cwd=ROOT))
    try:
        fixture.prepare()
        receipt.update(postgres_image_id=fixture.image_id, postgres_image_digests=fixture.image_digests)
        print("Prepared two isolated TLS PostgreSQL fixtures", flush=True)
        stage = "canonical bootstrap and migrations"
        bootstrap(pg, fixture)
        seed(pg, fixture)
        receipt["checks"].append("canonical schema loaded by restricted migration owner and upgraded from 0008 to actual heads")
        stage = "source runtime and TLS validation"
        runtime_checks(fixture, "source", heads)
        tls_negative_checks(pg, fixture)
        receipt["checks"].append("restricted runtime TLS, grants, populated tenant read/write isolation and three TLS refusal cases")
        source = fixture.service("source_backup")
        before = fingerprint(pg, source, schema_file=work / "source-schema.sql")
        receipt["source"] = before
        stage = "backup and restore CLI"
        bundle = work / "backups" / "canonical"
        backup = recovery_cli(pg, fixture, "backup", "--service", "source_backup", "--destination", str(bundle), "--minimum-bytes", "1024")
        require(backup["inventory"]["heads"] == heads, "Backup schema heads differ from the source graph")
        restore_arguments = ["restore", "--service", "target_restore", "--bundle", str(bundle), "--database", RESTORED_DB,
                             "--runtime-role", "fixture_runtime", "--receipt", str(work / "restore.json")]
        for head in heads:
            restore_arguments += ["--expected-head", head]
        restored = recovery_cli(pg, fixture, *restore_arguments)
        require(restored["status"] == "sql_restore_verified", "The existing restore CLI did not verify the archive")
        receipt["checks"].append("existing snapshot backup/restore CLI passed on distinct TLS cluster IDs and TCP endpoints")
        target = fixture.service("target_restore")
        stage = "full restored schema and record comparison"
        after = fingerprint(pg, target, RESTORED_DB, schema_file=work / "restored-schema.sql")
        receipt["restored"] = after
        if after != before:
            receipt["mismatched_fields"] = [key for key in before if before[key] != after[key]]
            if "schema_sha256" in receipt["mismatched_fields"]:
                differences = list(difflib.unified_diff((work / "source-schema.sql").read_text().splitlines(),
                                   (work / "restored-schema.sql").read_text().splitlines(),
                                   fromfile="synthetic-source-schema", tofile="synthetic-restored-schema", n=2))
                print("\n".join(differences[:120]), file=sys.stderr)
            raise RuntimeError("Restored fingerprint differs in: " + ", ".join(receipt["mismatched_fields"]))
        print("Full canonical schema and records matched after restore", flush=True)
        stage = "restored ownership, grants and runtime validation"
        # Reapply ownership/grants explicitly: pg_restore intentionally omits
        # original roles and ACLs. These changes affect only the scratch target.
        pg.sql(fixture.service("target_admin"), "REASSIGN OWNED BY fixture_restore TO fixture_migration;",
               database=RESTORED_DB, readonly=False)
        grant_runtime(pg, fixture.service("target_migration"), RESTORED_DB)
        runtime_checks(fixture, "target", heads)
        receipt["checks"].append("restored migration/runtime separation, grants, real restricted login and populated tenant write/read checks")
        stage = "source unchanged and backup health"
        require(fingerprint(pg, source) == before, "Source schema or records changed during recovery acceptance")
        health = recovery_cli(pg, fixture, "health", "--backup-root", str(bundle.parent), "--maximum-age-hours", "1")
        require(health["status"] == "passed", "Backup age/checksum verification failed")
        receipt["checks"].append("all source public-table records and schema unchanged; archive checksum/age passed")
        receipt.update(status="passed", archive_sha256=backup["sha256"], archive_bytes=backup["bytes"],
                       tenant_isolation=restored["tenant_isolation"], postgres_client=pg.run("psql", ["--version"]))
    except Exception as error:
        receipt["failed_stage"] = stage
        if isinstance(error, (RuntimeError, recovery.RecoveryError, KeyError)):
            receipt["failure"] = str(error)
        # These are exclusively generated fixture servers. Redact their known
        # passwords before printing bounded server diagnostics for CI repairs.
        for container in fixture.containers:
            logs = subprocess.run(["docker", "logs", "--tail", "45", container], capture_output=True,
                                  text=True, timeout=15)
            detail = logs.stdout + logs.stderr
            for password in fixture.passwords.values():
                detail = detail.replace(password, "[redacted fixture password]")
            print(detail, file=sys.stderr)
        raise
    finally:
        try:
            fixture.cleanup()
            receipt["owned_resources_removed"] = True
        except Exception:
            receipt.update(status="failed", owned_resources_removed=False)
            raise
        finally:
            receipt["completed_at"] = recovery.utcnow()
            recovery.write_json_new(args.receipt, receipt)
            # Only the directory returned by this invocation's mkdtemp is owned.
            if receipt.get("owned_resources_removed"):
                shutil.rmtree(work)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        detail = str(error) if isinstance(error, (RuntimeError, recovery.RecoveryError, KeyError)) else type(error).__name__
        print(f"Synthetic TLS recovery failed: {detail}; inspect the nonsecret receipt's failed_stage", file=sys.stderr)
        raise SystemExit(1) from None

#!/usr/bin/env python3
"""Explicit end-to-end local rehearsal of deliverable 4: separate database & recovery.

Synthetic data on disposable loopback clusters under the ignored test-output
directory; it never reads DATABASE_URL or contacts an existing server.

  legacy    today's production shape: plaintext loopback, the real schema
            (complete_schema.sql + every Alembic migration), a NOSUPERUSER
            NOBYPASSRLS app role that owns every table (FORCE RLS)
  external  the separate server: rendered TLS-only, allow-listed config,
            administered by a CREATEROLE/CREATEDB non-superuser (the shape of
            a managed provider's admin), WAL archiving on
  recovery  a disposable restore target

Stages: legacy -> read-only source backup -> external server -> preflight ->
cut-over -> migrations as owner -> application as the restricted role ->
the cost ledger writing under forced row-level security ->
the running application survives a server restart -> runtime password
rotation -> backups with an encrypted off-host replica and retention ->
restore drill from the decrypted off-host copy -> point-in-time recovery ->
monitoring -> legacy database proven unchanged.

Run with the backend's Python (it drives Alembic and the application's own
pool code):
  python scripts/database/tests/local_separation_rehearsal.py \
      --bin-dir "C:/Program Files/PostgreSQL/16/bin" --run --receipt PATH
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
REPO = TOOLS.parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(TOOLS))
import cutover  # noqa: E402
import recovery  # noqa: E402
import roles as roles_tool  # noqa: E402

_spec = importlib.util.spec_from_file_location("talklee_rehearsal_base", HERE / "local_postgres_rehearsal.py")
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
free_port, run = _base.free_port, _base.run

TENANTS = {"a": "11111111-1111-4111-8111-111111111111",
           "b": "22222222-2222-4222-8222-222222222222",
           "c": "33333333-3333-4333-8333-333333333333"}
CAMPAIGNS = {"a": 2, "b": 3, "c": 1}
PLATFORM_ADMIN = "44444444-4444-4444-8444-444444444444"
PRINCIPAL = {"app.current_user_id": PLATFORM_ADMIN}
ROLES = roles_tool.Roles("talklee_owner", "talklee_app", "talklee_backup")

SEED_SQL = """
BEGIN;
SET LOCAL app.bypass_rls = 'true';
INSERT INTO tenants (id, business_name) VALUES
  ('{a}', 'Synthetic Tenant A'), ('{b}', 'Synthetic Tenant B'), ('{c}', 'Synthetic Tenant C');
INSERT INTO campaigns (tenant_id, name)
  SELECT t.id, 'Synthetic campaign ' || g
    FROM (VALUES ('{a}'::uuid, {na}), ('{b}'::uuid, {nb}), ('{c}'::uuid, {nc})) AS t(id, n),
         generate_series(1, t.n) AS g;
INSERT INTO leads (tenant_id, campaign_id, phone_number)
  SELECT c.tenant_id, c.id, '+1555' || lpad((row_number() OVER (ORDER BY c.id, g))::text, 7, '0')
    FROM campaigns c, generate_series(1, 4) AS g;
INSERT INTO calls (tenant_id, campaign_id, lead_id, phone_number)
  SELECT l.tenant_id, l.campaign_id, l.id, l.phone_number FROM leads l;
-- A platform-admin-only table whose policy deliberately ignores app.bypass_rls (0062).
INSERT INTO user_profiles (id, email, role, is_active, is_verified, email_verified_at, verification_token)
  VALUES ('{admin}', 'platform.admin@example.invalid', 'platform_admin', true, true, now(), NULL);
INSERT INTO assistant_actions (id, tenant_id, type) VALUES ('55555555-5555-4555-8555-555555555555', '{a}', 'synthetic');
SET LOCAL app.current_user_id = '{admin}';
INSERT INTO assistant_action_resolutions (id, tenant_id, action_id, actor_id, actor_role, request_id, source_digest,
    reason, original_status, original_output, original_timestamps, recovered_status, provider_status, kind)
  VALUES ('66666666-6666-4666-8666-666666666666', '{a}', '55555555-5555-4555-8555-555555555555', '{admin}',
    'platform_admin', '77777777-7777-4777-8777-777777777777', repeat('ab', 32), 'synthetic acknowledgement',
    'unknown', '{{}}'::jsonb, '{{}}'::jsonb, 'completed', 'accepted', 'saved_gmail_acknowledgement');
COMMIT;
"""

FINGERPRINT_SQL = """
SELECT json_build_object(
  'inventory', {inventory},
  'columns', (SELECT md5(string_agg(table_name || '.' || column_name || ':' || data_type, ',' ORDER BY table_name, column_name))
                FROM information_schema.columns WHERE table_schema = 'public'),
  'policies', (SELECT md5(string_agg(tablename || ':' || policyname || ':' || qual, ',' ORDER BY tablename, policyname))
                 FROM pg_policies WHERE schemaname = 'public'),
  'owners', (SELECT json_object_agg(o, n) FROM (SELECT pg_get_userbyid(relowner) o, count(*) n FROM pg_class c
               JOIN pg_namespace s ON s.oid = c.relnamespace WHERE s.nspname = 'public' GROUP BY 1) x),
  'roles', (SELECT json_agg(rolname ORDER BY rolname) FROM pg_roles WHERE rolname !~ '^pg_'),
  'databases', (SELECT json_agg(datname ORDER BY datname) FROM pg_database));
"""

APP_SMOKE = r'''
import asyncio, json, os, sys, time
from pathlib import Path
import asyncpg

async def survive_restart(db):
    """Hold a warm pool, let the rehearsal restart the server, then measure how
    long the unchanged application process needs to serve queries again."""
    pool = await db.init_db_pool()
    try:
        async def query():
            async with pool.acquire() as conn:
                return await conn.fetchval("SELECT pg_backend_pid()")
        before = await asyncio.gather(*[query() for _ in range(3)])
        Path(os.environ["SMOKE_READY"]).write_text("ready")
        deadline = time.monotonic() + 180
        while not Path(os.environ["SMOKE_GO"]).exists():
            if time.monotonic() > deadline:
                raise SystemExit("restart signal never came")
            await asyncio.sleep(0.2)
        started, errors = time.monotonic(), []
        while True:
            try:
                await query()
                break
            except (asyncpg.PostgresError, OSError, asyncpg.InterfaceError) as exc:
                errors.append(type(exc).__name__)
                if time.monotonic() - started > 60:
                    raise
                await asyncio.sleep(0.25)
        recovered = round(time.monotonic() - started, 2)
        after = await asyncio.gather(*[query() for _ in range(4)])
        print(json.dumps({"recovered_seconds": recovered, "failed_attempts": len(errors), "errors": sorted(set(errors)),
                          "new_backends": not set(after) & set(before), "queries_after_restart": len(after)}))
    finally:
        await db.close_db_pool()

async def main():
    from app.core import db
    from app.core.database_policy import DatabasePolicyError, POSTURE_SQL
    from app.core.db import _apply_rls_context
    mode, tenants, campaigns = sys.argv[1], json.loads(os.environ["SMOKE_TENANTS"]), json.loads(os.environ["SMOKE_CAMPAIGNS"])
    if mode == "resilience":
        await survive_restart(db)
        return
    if mode == "expect_refusal":
        try:
            await db.init_db_pool()
        except DatabasePolicyError as exc:
            print(json.dumps({"refused": True, "reason": str(exc)}))
            return
        await db.close_db_pool()
        raise SystemExit("unrestricted role was admitted")
    out = {}
    pool = await db.init_db_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(POSTURE_SQL)
            out["posture"] = {k: row[k] for k in row.keys()}
            out["tls_version"] = await conn.fetchval("SELECT version FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            async def scoped(tenant, bypass, sql):
                async with conn.transaction():
                    await _apply_rls_context(conn, tenant, bypass)
                    return await conn.fetchval(sql)
            out["own_tenant_campaigns"] = {k: await scoped(t, False, "SELECT count(*) FROM campaigns") for k, t in tenants.items()}
            out["own_tenant_foreign_rows"] = {k: await scoped(t, False, f"SELECT count(*) FROM campaigns WHERE tenant_id <> '{t}'")
                                              for k, t in tenants.items()}
            out["no_context_campaigns"] = await scoped(None, False, "SELECT count(*) FROM campaigns")
            out["worker_bypass_campaigns"] = await scoped(None, True, "SELECT count(*) FROM campaigns")
            out["worker_bypass_counts"] = {t: await scoped(None, True, f"SELECT count(*) FROM {t}")
                                           for t in ("tenants", "campaigns", "leads", "calls")}
            assert out["own_tenant_campaigns"] == {k: campaigns[k] for k in tenants}, out
            assert not any(out["own_tenant_foreign_rows"].values()), out
            assert out["no_context_campaigns"] == 0 and out["worker_bypass_campaigns"] == sum(campaigns.values()), out
            if mode == "full":
                tr = conn.transaction(); await tr.start()
                await _apply_rls_context(conn, tenants["a"], False)
                await conn.execute("INSERT INTO campaigns (tenant_id, name) VALUES ($1, 'smoke own-tenant write')", tenants["a"])
                await tr.rollback()
                out["own_tenant_write"] = "allowed (rolled back)"
                denied = {}
                for label, sql, tenant in (
                    ("cross_tenant_insert", f"INSERT INTO campaigns (tenant_id, name) VALUES ('{tenants['b']}', 'smoke foreign write')", tenants["a"]),
                    ("create_table", "CREATE TABLE smoke_should_fail (id int)", None),
                    ("truncate", "TRUNCATE campaigns", None),
                    ("disable_rls", "ALTER TABLE campaigns DISABLE ROW LEVEL SECURITY", None),
                    ("drop_table", "DROP TABLE calls", None),
                ):
                    tr = conn.transaction(); await tr.start()
                    try:
                        await _apply_rls_context(conn, tenant, False)
                        await conn.execute(sql)
                        denied[label] = "ALLOWED"
                    except asyncpg.PostgresError as exc:
                        denied[label] = type(exc).__name__
                    finally:
                        await tr.rollback()
                out["denied"] = denied
                assert "ALLOWED" not in denied.values(), denied
    finally:
        await db.close_db_pool()
    print(json.dumps(out, default=str))

asyncio.run(main())
'''


COST_LEDGER_SMOKE = r'''
import asyncio, json, os
from app.core import db
from app.domain.services import provider_cost_ledger as ledger

async def main():
    pool = await db.init_db_pool()
    try:
        for unit in ("tokens_in", "tokens_out"):
            ledger.record(ledger.CostEvent(tenant_id=os.environ["SMOKE_TENANT"], provider="groq", provider_role="llm",
                                           unit=unit, quantity=12.5, cost_usd=0.0001))
        written = await ledger._flush_once(pool)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                rows = await conn.fetchval("SELECT count(*) FROM tenant_provider_cost_events")
        print(json.dumps({"written": written, "rows": rows, "buffered": ledger.buffer_size()}))
    finally:
        await db.close_db_pool()

asyncio.run(main())
'''


class Rehearsal:
    def __init__(self, bin_dir: Path, work: Path, python: str):
        self.bin_dir, self.work, self.python = bin_dir, work, python
        self.clusters: list[Path] = []
        self.checks: list[str] = []
        self.timings: dict[str, float] = {}
        self.passwords: dict[tuple[str, str], str] = {}
        self.pgpass = work / "pgpass"
        self.services = work / "services.conf"
        self.service_text = ""
        self.pg = recovery.Postgres(bin_dir, timeout=900)

    # ---------------------------------------------------------------- helpers
    def check(self, condition: bool, text: str) -> None:
        if not condition:
            raise AssertionError("FAILED: " + text)
        self.checks.append(text)
        print("  ok  " + text, flush=True)

    def secret(self, cluster: str, role: str) -> str:
        value = self.passwords.setdefault((cluster, role), secrets.token_urlsafe(30))
        lines = [f"127.0.0.1:{port}:*:{user}:{password}" for (port, user), password in self.passwords.items()]
        self.pgpass.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return value

    def password_file(self, cluster: str, role: str) -> Path:
        path = self.work / "secrets" / f"{cluster}-{role}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(self.secret(cluster, role) + "\n", encoding="utf-8")
        return path

    def service(self, name: str, port: int, dbname: str, user: str, tls: bool) -> recovery.Service:
        entry = f"[{name}]\nhost=127.0.0.1\nport={port}\ndbname={dbname}\nuser={user}\n"
        entry += f"sslmode=verify-full\nsslrootcert={self.ca}\n" if tls else "sslmode=disable\n"
        if f"[{name}]" not in self.service_text:
            self.service_text += entry + "\n"
            self.services.write_text(self.service_text, encoding="utf-8")
        return recovery.Service(self.services, name, allow_local_plaintext=not tls)

    def dsn(self, port: int, dbname: str, user: str, tls: bool) -> str:
        password = quote(self.secret(str(port), user), safe="")
        query = f"?sslmode=verify-full&sslrootcert={quote(str(self.ca), safe='')}" if tls else "?sslmode=disable"
        return f"postgresql://{user}:{password}@127.0.0.1:{port}/{dbname}{query}"

    def tool(self, script: str, *args: str, ok_codes=(0,)) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and "DATABASE_URL" not in k}
        env["PGPASSFILE"] = str(self.pgpass)
        result = subprocess.run([self.python, str(TOOLS / script), *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=env, timeout=1200, stdin=subprocess.DEVNULL)
        if result.returncode not in ok_codes:
            raise AssertionError(f"{script} exit {result.returncode}: {result.stdout[-1500:]} {result.stderr[-1500:]}")
        return result

    def backend(self, args: list[str], env_extra: dict[str, str], timeout: int = 900) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and "DATABASE_URL" not in k}
        env.update({"PYTHONPATH": str(BACKEND), "ENVIRONMENT": "development"})
        env.update(env_extra)
        result = subprocess.run([self.python, *args], cwd=BACKEND, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=env, timeout=timeout, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise AssertionError(f"backend command failed ({result.returncode}): {result.stdout[-2000:]} {result.stderr[-3000:]}")
        return result

    def cluster(self, label: str, superuser: str) -> tuple[Path, int]:
        data, port = self.work / label, free_port()
        self.clusters.append(data)
        pwfile = self.work / f"{label}-pw.txt"
        pwfile.write_text(self.secret(str(port), superuser) + "\n", encoding="utf-8")
        run(self.bin_dir, "initdb", ["-D", str(data), "-U", superuser, f"--pwfile={pwfile}", "--auth=scram-sha-256",
                                     "--encoding=UTF8", "--locale=C", "--data-checksums"])
        pwfile.unlink()
        with (data / "postgresql.conf").open("a", encoding="utf-8") as conf:
            conf.write(f"\nport = {port}\nlisten_addresses = '127.0.0.1'\npassword_encryption = 'scram-sha-256'\n")
        (data / "pg_hba.conf").write_text("host all all 127.0.0.1/32 scram-sha-256\n", encoding="utf-8")
        return data, port

    def start(self, data: Path) -> None:
        run(self.bin_dir, "pg_ctl", ["-D", str(data), "-l", str(data.with_suffix(".log")), "-w", "-t", "120", "start"])

    def stop(self, data: Path) -> None:
        if (data / "postmaster.pid").exists():
            run(self.bin_dir, "pg_ctl", ["-D", str(data), "-m", "fast", "-w", "stop"])

    def sql(self, service: recovery.Service, text: str, **kwargs) -> str:
        return self.pg.sql(service, text, **kwargs)

    def fingerprint(self, service: recovery.Service) -> dict:
        return self.pg.json(service, FINGERPRINT_SQL.format(inventory="(" + recovery.inventory_sql().rstrip(";")[6:].replace(
            "'snapshot_time', transaction_timestamp()", "'snapshot_time', NULL") + ")"))

    def certificates(self) -> None:
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\mingw64\bin\openssl.exe"
        certs = self.work / "certs"
        certs.mkdir()
        def ssl(*args):
            subprocess.run([openssl, *args], cwd=certs, check=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        ssl("req", "-x509", "-new", "-nodes", "-newkey", "rsa:2048", "-keyout", "ca.key", "-out", "ca.crt",
            "-days", "2", "-subj", "/CN=talklee-rehearsal-ca")
        ssl("req", "-new", "-nodes", "-newkey", "rsa:2048", "-keyout", "server.key", "-out", "server.csr", "-subj", "/CN=localhost")
        (certs / "ext.cnf").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
                                       "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
        ssl("x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
            "-out", "server.crt", "-days", "2", "-extfile", "ext.cnf")
        self.ca = (certs / "ca.crt").as_posix()
        self.server_cert, self.server_key = (certs / "server.crt").as_posix(), (certs / "server.key").as_posix()

    # ----------------------------------------------------------------- stages
    def legacy(self) -> None:
        print("== legacy database (today's production shape)", flush=True)
        data, self.legacy_port = self.cluster("legacy", "fixture_superuser")
        self.start(data)
        port = self.legacy_port
        admin = self.service("legacy_postgres", port, "postgres", "fixture_superuser", tls=False)
        app_pw = roles_tool.scram_verifier(self.secret(str(port), "talkyai"))
        self.sql(admin, f"CREATE ROLE talkyai LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '{app_pw}';"
                        "CREATE DATABASE talkyai OWNER talkyai;", readonly=False)
        self.legacy_super = self.service("legacy_super", port, "talkyai", "fixture_superuser", tls=False)
        self.sql(self.legacy_super, 'CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; '
                                    "CREATE EXTENSION IF NOT EXISTS pg_trgm;", readonly=False)
        self.legacy_app = self.service("legacy_app", port, "talkyai", "talkyai", tls=False)
        started = time.monotonic()
        self.pg.run("psql", ["-X", "-q", "--no-password", "--set", "ON_ERROR_STOP=1", "--dbname",
                             self.legacy_app.connection(), "-f", str(BACKEND / "database" / "complete_schema.sql")],
                    self.legacy_app, readonly=False)
        legacy_dsn = self.dsn(port, "talkyai", "talkyai", tls=False)
        self.backend(["-m", "alembic", "-c", "alembic.ini", "stamp", "0008_tenant_voice_tuning"], {"DATABASE_URL": legacy_dsn})
        self.backend(["-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], {"DATABASE_URL": legacy_dsn})
        self.timings["legacy_schema_and_migrations_seconds"] = round(time.monotonic() - started, 1)
        self.heads = json.loads(self.sql(self.legacy_super, "SELECT json_agg(version_num) FROM alembic_version;"))
        self.sql(self.legacy_app, SEED_SQL.format(na=CAMPAIGNS["a"], nb=CAMPAIGNS["b"], nc=CAMPAIGNS["c"], admin=PLATFORM_ADMIN, **TENANTS), readonly=False)
        posture = self.pg.json(self.legacy_app, "SELECT json_build_object('superuser', rolsuper, 'bypass', rolbypassrls, "
                               "'owned', (SELECT count(*) FROM pg_class WHERE relowner = r.oid)) FROM pg_roles r WHERE rolname = current_user;")
        self.check(not posture["superuser"] and not posture["bypass"] and posture["owned"] > 100,
                   f"legacy app role is NOSUPERUSER NOBYPASSRLS and owns {posture['owned']} relations, like production")
        self.before = self.fingerprint(self.legacy_super)
        counts = self.before["inventory"]["table_counts"]
        self.check(self.heads and counts["tenants"] == 3 and counts["campaigns"] == sum(CAMPAIGNS.values()),
                   f"legacy schema at {self.heads} with {len(counts)} tables and synthetic tenants seeded")

    def source_backup(self) -> None:
        print("== read-only backup of the legacy database", flush=True)
        try:
            recovery.backup(self.pg, self.legacy_app, self.work / "legacy-backups" / "no-bypass", 1024, None, .5)
            raise AssertionError("a NOBYPASSRLS backup without the bypass setting must be refused")
        except recovery.RecoveryError:
            self.check(True, "backup without a BYPASSRLS role or the bypass setting is refused (would be filtered)")
        try:
            recovery.backup(self.pg, self.legacy_app, self.work / "legacy-backups" / "bypass-only", 1024, None, .5,
                            rls_bypass_guc=True)
            raise AssertionError("a bypass-only backup must be refused: assistant_action_resolutions ignores it")
        except recovery.RecoveryError as exc:
            self.check("assistant_action_resolutions" in str(exc),
                       "bypass-only backup refused: assistant_action_resolutions would be dumped empty "
                       "(what today's db-backup.sh produces)")
        started = time.monotonic()
        self.bundle = self.work / "legacy-backups" / "cutover"
        receipt = recovery.backup(self.pg, self.legacy_app, self.bundle, 1024, None, .5, rls_bypass_guc=True,
                                  rls_settings=PRINCIPAL)
        self.timings["source_backup_seconds"] = round(time.monotonic() - started, 1)
        self.check(receipt["inventory"]["table_counts"] == self.before["inventory"]["table_counts"]
                   and receipt["inventory"]["table_counts"]["assistant_action_resolutions"] == 1,
                   f"source bundle (app.bypass_rls + platform-admin principal, read-only) holds every row of all "
                   f"{len(receipt['inventory']['table_counts'])} tables, including the admin-only table")
        self.check(self.fingerprint(self.legacy_super) == self.before,
                   "legacy database unchanged after the backup (rows, schema, policies, owners, roles)")

    def external(self) -> None:
        print("== external database server (TLS only, allow-listed, non-superuser admin)", flush=True)
        self.certificates()
        data, port = self.cluster("external", "bootstrap_superuser")
        self.ext_port, self.ext_data = port, data
        self.start(data)
        boot = self.service("ext_bootstrap", port, "postgres", "bootstrap_superuser", tls=False)
        admin_pw = roles_tool.scram_verifier(self.secret(str(port), "managed_admin"))
        repl_pw = roles_tool.scram_verifier(self.secret(str(port), "talklee_replication"))
        self.sql(boot, f"CREATE ROLE managed_admin LOGIN CREATEROLE CREATEDB NOSUPERUSER PASSWORD '{admin_pw}';"
                       f"CREATE ROLE talklee_replication LOGIN REPLICATION NOSUPERUSER PASSWORD '{repl_pw}';", readonly=False)
        self.stop(data)
        conf = self.work / "external-conf"
        rendered = subprocess.run([self.python, str(TOOLS / "db_server" / "render_config.py"), "--listen-address", "127.0.0.1",
                                   "--allow", "127.0.0.1/32", "--ssl-cert-file", self.server_cert, "--ssl-key-file", self.server_key,
                                   "--database", "talkyai", "--out-dir", str(conf), "--memory-mb", "1024", "--port", str(port),
                                   "--admin-role", "managed_admin", "--allow-replication"],
                                  capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        if rendered.returncode:
            raise AssertionError("render_config failed: " + rendered.stderr)
        self.archive = self.work / "wal-archive"
        self.archive.mkdir()
        archive = self.tool("pitr.py", "archive-settings", "--archive-dir", str(self.archive), "--platform",
                            "windows" if os.name == "nt" else "linux").stdout
        with (conf / "talklee.conf").open("a", encoding="utf-8") as stream:
            stream.write(archive)
        with (data / "postgresql.conf").open("a", encoding="utf-8") as stream:
            stream.write(f"\ninclude '{(conf / 'talklee.conf').as_posix()}'\n")
        shutil.copyfile(conf / "pg_hba.conf", data / "pg_hba.conf")
        self.start(data)
        self.ext_admin = self.service("ext_admin", port, "postgres", "managed_admin", tls=True)
        attributes = self.pg.json(self.ext_admin, "SELECT json_build_object('super', rolsuper, 'tls', "
                                  "(SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid())) FROM pg_roles WHERE rolname = current_user;")
        self.check(attributes == {"super": False, "tls": True}, "external admin is not a superuser and connects over verify-full TLS")
        plaintext = self.service("ext_plain", port, "postgres", "managed_admin", tls=False)
        try:
            self.sql(plaintext, "SELECT 1;")
            raise AssertionError("plaintext connection admitted")
        except recovery.RecoveryError:
            self.check(True, "external server refuses a plaintext connection (hostnossl reject)")

    def preflight(self) -> None:
        print("== preflight of the external server before the window (read-only)", flush=True)
        common = ["--service-file", str(self.services), "--bin-dir", str(self.bin_dir), "--allow-local-plaintext",
                  "--target-service", "ext_admin", "--source-service", "legacy_app", "--database", "talkyai"]
        result = json.loads(self.tool("preflight.py", *common, "--receipt", str(self.work / "receipts" / "preflight.json")).stdout)
        statuses = {name: check["status"] for name, check in result["checks"].items()}
        print("     preflight: " + json.dumps(statuses), flush=True)
        self.check(result["status"] == "passed" and set(statuses.values()) == {"ok"},
                   f"preflight passes: version, TLS, non-superuser admin, {result['checks']['connection_capacity']['detail']}, "
                   f"extensions, collation, round trip {result['observations']['round_trip']['p95_ms']} ms p95, "
                   "forced row-level security in the source")
        self.recommended_limit = result["recommendations"]["runtime_connection_limit"]
        refused = self.tool("preflight.py", *common, "--pool-budget", "1000", "--receipt",
                            str(self.work / "receipts" / "preflight-oversized.json"), ok_codes=(1,))
        verdict = json.loads(refused.stdout)
        self.check(verdict["status"] == "failed" and verdict["checks"]["connection_capacity"]["status"] == "fail",
                   "preflight refuses a server too small for the application's connection budget")
        self.check(self.fingerprint(self.legacy_super) == self.before, "legacy database unchanged after the preflight")

    def cut_over(self) -> None:
        print("== cut-over: load the verified bundle into the external server", flush=True)
        started = time.monotonic()
        result = cutover.load(self.pg, self.ext_admin, self.bundle, "talkyai", ROLES, self.heads,
                              self.work / "receipts" / "cutover.json", backup_bypassrls=False, rls_settings=PRINCIPAL)
        self.check(result["status"] == "loaded_and_verified" and result["roles"]["status"] == "passed",
                   f"cut-over loaded {result['tables_verified']} tables / {result['rows_verified']} rows with exact counts "
                   "and passed least-privilege verification")
        self.ext_db_admin = self.service("ext_db_admin", self.ext_port, "talkyai", "managed_admin", tls=True)
        passwords = {role: self.password_file(str(self.ext_port), role) for role in (ROLES.owner, ROLES.runtime, ROLES.backup)}
        applied = roles_tool.apply(self.pg, self.ext_db_admin, ROLES, "talkyai", from_owner=None,
                                   passwords=passwords, backup_bypassrls=False,
                                   runtime_connection_limit=self.recommended_limit)
        self.timings["cutover_seconds"] = round(time.monotonic() - started, 1)
        self.check(applied["status"] == "passed" and sorted(applied["passwords_set"]) == sorted(passwords),
                   "role passwords set as client-side SCRAM verifiers; verification still passes")
        limit = applied["observations"]["roles"][ROLES.runtime]["connection_limit"]
        self.check(limit == self.recommended_limit,
                   f"runtime role capped at the preflight's recommended {limit} connections, so a leaking pool "
                   "cannot lock out backups, monitoring or an operator")
        self.check(self.fingerprint(self.legacy_super) == self.before, "legacy database unchanged after the cut-over")

    def migrations(self) -> None:
        print("== migrations as the owner role over TLS", flush=True)
        owner_dsn = self.dsn(self.ext_port, "talkyai", ROLES.owner, tls=True)
        current = self.backend(["-m", "alembic", "-c", "alembic.ini", "current"], {"MIGRATION_DATABASE_URL": owner_dsn})
        self.backend(["-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], {"MIGRATION_DATABASE_URL": owner_dsn})
        self.check(all(head in current.stdout + current.stderr for head in self.heads),
                   "Alembic connects as the owner via MIGRATION_DATABASE_URL (verify-full) and is at head")
        owner = self.service("ext_owner", self.ext_port, "talkyai", ROLES.owner, tls=True)
        self.sql(owner, "CREATE TABLE zz_future_migration_probe (id int);", readonly=False)
        grants = self.pg.json(self.ext_db_admin, "SELECT json_build_object('app', has_table_privilege('talklee_app', "
                              "'zz_future_migration_probe', 'INSERT'), 'backup', has_table_privilege('talklee_backup', "
                              "'zz_future_migration_probe', 'SELECT'), 'truncate', has_table_privilege('talklee_app', "
                              "'zz_future_migration_probe', 'TRUNCATE'));")
        self.sql(owner, "DROP TABLE zz_future_migration_probe;", readonly=False)
        self.check(grants == {"app": True, "backup": True, "truncate": False},
                   "a table created by a later migration is usable by runtime/backup without another grant (default privileges)")

    def app_smoke(self, port: int, database: str, mode: str, label: str) -> dict:
        script = self.work / "app_smoke.py"
        script.write_text(APP_SMOKE, encoding="utf-8")
        env = {"DATABASE_URL": self.dsn(port, database, ROLES.runtime, tls=True), "ENVIRONMENT": "production",
               "DATABASE_REQUIRE_RESTRICTED_ROLE": "true", "SMOKE_TENANTS": json.dumps(TENANTS),
               "SMOKE_CAMPAIGNS": json.dumps(CAMPAIGNS), "PG_POOL_MIN_SIZE": "1", "PG_POOL_MAX_SIZE": "2"}
        out = json.loads(self.backend([str(script), mode], env).stdout.strip().splitlines()[-1])
        print(f"     {label}: " + json.dumps(out)[:600], flush=True)
        return out

    def application(self) -> None:
        print("== the application on the external database as the restricted role", flush=True)
        out = self.app_smoke(self.ext_port, "talkyai", "full", "app")
        self.check(out["posture"]["role"] == ROLES.runtime and out["posture"]["tls"] and out["posture"]["owned_relations"] == 0
                   and not out["posture"]["superuser"] and not out["posture"]["bypass_rls"],
                   f"application pool boots with DATABASE_REQUIRE_RESTRICTED_ROLE=true as {ROLES.runtime} over {out['tls_version']}")
        self.check(True, "tenant reads see only their own rows; no tenant context sees none; worker bypass sees all")
        self.check(set(out["denied"].values()) - {"ALLOWED"} == set(out["denied"].values()),
                   "runtime role is refused cross-tenant writes, CREATE, TRUNCATE, DROP and disabling RLS: "
                   + ", ".join(f"{k}={v}" for k, v in out["denied"].items()))
        env = {"DATABASE_URL": self.dsn(self.ext_port, "talkyai", ROLES.owner, tls=True), "ENVIRONMENT": "production",
               "DATABASE_REQUIRE_RESTRICTED_ROLE": "true", "SMOKE_TENANTS": "{}", "SMOKE_CAMPAIGNS": "{}",
               "PG_POOL_MIN_SIZE": "1", "PG_POOL_MAX_SIZE": "1"}
        refused = json.loads(self.backend([str(self.work / "app_smoke.py"), "expect_refusal"], env).stdout.strip().splitlines()[-1])
        self.check(refused["refused"] and "owns" in refused["reason"],
                   "the application refuses to boot as the owner role when the restricted role is required")

    def cost_ledger(self) -> None:
        print("== provider cost ledger writes as the restricted role under forced row-level security", flush=True)
        # The table comes from a standalone SQL file outside the Alembic lineage;
        # created here as the owner, exactly as production may hold it, then dropped.
        owner = self.service("ext_owner", self.ext_port, "talkyai", ROLES.owner, tls=True)
        ddl = (BACKEND / "database" / "migrations" / "20260505_add_tenant_provider_cost_events.sql").read_text(encoding="utf-8")
        self.sql(owner, ddl + "\nALTER TABLE tenant_provider_cost_events FORCE ROW LEVEL SECURITY;\n", readonly=False)
        try:
            script = self.work / "cost_ledger_smoke.py"
            script.write_text(COST_LEDGER_SMOKE, encoding="utf-8")
            env = {"DATABASE_URL": self.dsn(self.ext_port, "talkyai", ROLES.runtime, tls=True), "ENVIRONMENT": "production",
                   "DATABASE_REQUIRE_RESTRICTED_ROLE": "true", "SMOKE_TENANT": TENANTS["a"],
                   "PG_POOL_MIN_SIZE": "1", "PG_POOL_MAX_SIZE": "2"}
            out = json.loads(self.backend([str(script)], env).stdout.strip().splitlines()[-1])
        finally:
            self.sql(owner, "DROP TABLE tenant_provider_cost_events;", readonly=False)
        self.check(out == {"written": 2, "rows": 2, "buffered": 0},
                   "the provider cost ledger flushes as talklee_app into a FORCE row-level-security table "
                   "(batched INSERT; PostgreSQL refuses COPY FROM whenever row-level security applies)")

    def resilience(self) -> None:
        print("== the running application survives a database restart (provider maintenance, failover)", flush=True)
        script, ready, go = self.work / "app_smoke.py", self.work / "smoke-ready", self.work / "smoke-go"
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and "DATABASE_URL" not in k}
        env.update({"PYTHONPATH": str(BACKEND), "ENVIRONMENT": "production", "DATABASE_REQUIRE_RESTRICTED_ROLE": "true",
                    "DATABASE_URL": self.dsn(self.ext_port, "talkyai", ROLES.runtime, tls=True),
                    "SMOKE_TENANTS": "{}", "SMOKE_CAMPAIGNS": "{}", "SMOKE_READY": str(ready), "SMOKE_GO": str(go),
                    "PG_POOL_MIN_SIZE": "2", "PG_POOL_MAX_SIZE": "4"})
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen([self.python, str(script), "resilience"], cwd=BACKEND, env=env,
                                       stdout=out, stderr=err, stdin=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 180
                while not ready.exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError("application pool did not start for the restart test")
                    time.sleep(0.2)
                started = time.monotonic()
                self.stop(self.ext_data)
                self.start(self.ext_data)
                self.timings["database_restart_seconds"] = round(time.monotonic() - started, 1)
                go.write_text("go", encoding="utf-8")
                process.wait(timeout=180)
            finally:
                if process.poll() is None:
                    process.kill()
            out.seek(0)
            err.seek(0)
            stdout, stderr = out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")
        if process.returncode:
            raise AssertionError("restart test failed: " + stdout[-1000:] + stderr[-2000:])
        result = json.loads(stdout.strip().splitlines()[-1])
        print("     restart: " + json.dumps(result), flush=True)
        self.timings["application_recovered_after_restart_seconds"] = result["recovered_seconds"]
        self.check(result["queries_after_restart"] == 4 and result["new_backends"],
                   f"the same application process served queries {result['recovered_seconds']} s after the server came "
                   f"back from a restart, on fresh connections, without being restarted "
                   f"({result['failed_attempts']} failed attempts while reconnecting)")

    def rotation(self) -> None:
        print("== rotating the runtime role's password", flush=True)
        old_password = self.passwords[(str(self.ext_port), ROLES.runtime)]
        new_password = secrets.token_urlsafe(30)
        new_file = self.work / "secrets" / "talklee_app-rotated.txt"
        new_file.write_text(new_password + "\n", encoding="utf-8")
        receipt = json.loads(self.tool("roles.py", "--service-file", str(self.services), "--bin-dir", str(self.bin_dir),
                                       "rotate-password", "--service", "ext_db_admin", "--database", "talkyai",
                                       "--role", ROLES.runtime, "--password-file", str(new_file),
                                       "--receipt", str(self.work / "receipts" / "rotate.json")).stdout)
        self.check(receipt["status"] == "passed" and receipt["new_password_login"] and old_password not in json.dumps(receipt)
                   and new_password not in json.dumps(receipt),
                   "rotate-password changed only the runtime password and proved the new one logs in (no secret in the receipt)")
        runtime = self.service("ext_runtime", self.ext_port, "talkyai", ROLES.runtime, tls=True)
        try:
            self.sql(runtime, "SELECT 1;")
            raise AssertionError("the old runtime password still logs in")
        except recovery.RecoveryError:
            self.check(True, "the old runtime password is refused after rotation")
        self.passwords[(str(self.ext_port), ROLES.runtime)] = new_password
        self.secret(str(self.ext_port), ROLES.runtime)
        out = self.app_smoke(self.ext_port, "talkyai", "readonly", "app after rotation")
        self.check(out["posture"]["role"] == ROLES.runtime and out["worker_bypass_campaigns"] == sum(CAMPAIGNS.values()),
                   "the application boots and reads with the new runtime password")

    def backups(self) -> None:
        print("== automated backups of the external database: replica + retention", flush=True)
        backup = self.service("ext_backup", self.ext_port, "talkyai", ROLES.backup, tls=True)
        root, replica = self.work / "ext-backups", self.work / "offhost-replica"
        started = time.monotonic()
        first = recovery.backup(self.pg, backup, root / "20261028T020000Z", 1024, None, .5, rls_bypass_guc=True,
                                rls_settings=PRINCIPAL)
        self.timings["external_backup_seconds"] = round(time.monotonic() - started, 1)
        self.check(first["inventory"]["table_counts"] == self.before["inventory"]["table_counts"],
                   "backup role (verify-full TLS, app.bypass_rls) captures every row from the external server")
        passphrase = self.work / "secrets" / "backup-replica.passphrase"
        passphrase.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
        gpg_path = shutil.which("gpg") or r"C:\Program Files\Git\usr\bin\gpg.exe"
        self.gpg = recovery.Gpg(passphrase, gpg_path)
        copy = recovery.replicate(root / "20261028T020000Z", replica, self.gpg)
        replica_files = sorted(path.name for path in (replica / "20261028T020000Z").iterdir())
        self.check(copy["status"] == "replica_verified" and copy["encrypted"] and copy["sha256"] == first["sha256"]
                   and "database.dump" not in replica_files,
                   "bundle copied off-host only as AES-256 ciphertext, proven to decrypt to the original archive")
        self.check(recovery.health(replica, 26)["status"] == "passed"
                   and recovery.prune(replica, 30, 1, False)["kept"] == ["20261028T020000Z"],
                   "retention and health checks work on the encrypted off-host root without the passphrase")
        self.replica_bundle = replica / "20261028T020000Z"
        aged = root / "20260901T020000Z"
        shutil.copytree(root / "20261028T020000Z", aged)
        receipt = json.loads((aged / "receipt.json").read_text(encoding="utf-8"))
        receipt["inventory"]["snapshot_time"] = "2026-09-01T02:00:00+00:00"
        (aged / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        pruned = recovery.prune(root, 30, 1, False)
        self.check(pruned["deleted"] == [aged.name] and pruned["kept"] == ["20261028T020000Z"],
                   "retention deletes the bundle older than 30 days and keeps the newest verified one")
        self.check(recovery.health(root, 26)["status"] == "passed", "backup health: newest bundle verified and fresh")
        self.ext_backup_root = root
        self.ext_bundle = root / "20261028T020000Z"

    def restore_drill(self) -> None:
        print("== restore drill from the off-host copy on a separate cluster, then the application on it", flush=True)
        # The drill starts from what survives losing both servers: the encrypted off-host replica.
        started_decrypt = time.monotonic()
        fetched = self.work / "fetched-from-offhost" / self.replica_bundle.name
        decrypted = recovery.decrypt_replica(self.replica_bundle, fetched, self.gpg)
        self.timings["offhost_decrypt_seconds"] = round(time.monotonic() - started_decrypt, 1)
        self.check(decrypted["status"] == "bundle_verified" and decrypted["sha256"] == recovery.verify_bundle(self.ext_bundle)["sha256"],
                   "off-host replica decrypted with the escrowed passphrase into a verified bundle identical to the original")
        self.ext_bundle = fetched
        data, port = self.cluster("recovery", "restore_operator")
        self.start(data)
        operator = self.service("restore_admin", port, "postgres", "restore_operator", tls=False)
        self.sql(operator, "CREATE ROLE restore_probe NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;", readonly=False)
        sql_restore = recovery.restore(self.pg, operator, self.ext_bundle, "talky_restore_sql", "restore_probe", self.heads,
                                       self.work / "receipts" / "restore-sql.json")
        self.check(sql_restore["status"] == "sql_restore_verified" and sql_restore["tenant_isolation"]["status"] == "passed",
                   "SQL restore: exact counts/heads and restricted-role tenant isolation on two restored tenants")
        # The restore target speaks TLS too, so the restored application path matches production.
        self.stop(data)
        with (data / "postgresql.conf").open("a", encoding="utf-8") as stream:
            stream.write(f"\nssl = on\nssl_cert_file = '{self.server_cert}'\nssl_key_file = '{self.server_key}'\n")
        (data / "pg_hba.conf").write_text("hostssl all all 127.0.0.1/32 scram-sha-256\nhost all restore_operator 127.0.0.1/32 scram-sha-256\n",
                                          encoding="utf-8")
        self.start(data)
        started = time.monotonic()
        loaded = cutover.load(self.pg, operator, self.ext_bundle, "talky_restore_app", ROLES, self.heads,
                              self.work / "receipts" / "restore-app.json", backup_bypassrls=False, rls_settings=PRINCIPAL)
        restored_admin = self.service("restore_db_admin", port, "talky_restore_app", "restore_operator", tls=False)
        passwords = {ROLES.runtime: self.password_file(str(port), ROLES.runtime)}
        roles_tool.apply(self.pg, restored_admin, ROLES, "talky_restore_app", from_owner=None, passwords=passwords,
                         backup_bypassrls=False)
        out = self.app_smoke(port, "talky_restore_app", "readonly", "restored app")
        self.timings["restore_to_application_seconds"] = round(time.monotonic() - started, 1)
        self.check(loaded["status"] == "loaded_and_verified"
                   and out["worker_bypass_counts"] == {t: self.before["inventory"]["table_counts"][t]
                                                       for t in ("tenants", "campaigns", "leads", "calls")},
                   f"application reads tenants/campaigns/leads/calls from the restored copy over TLS "
                   f"({self.timings['restore_to_application_seconds']} s from bundle to verified application)")

    def point_in_time(self) -> None:
        print("== point-in-time recovery on the external server (TLS replication)", flush=True)
        replication = self.service("ext_replication", self.ext_port, "postgres", "talklee_replication", tls=True)
        base = self.work / "pitr-base" / "20261028T030000Z"
        receipt = json.loads(self.tool("pitr.py", "base-backup", "--service-file", str(self.services), "--service",
                                       replication.name, "--destination", str(base), "--bin-dir", str(self.bin_dir)).stdout)
        self.check(receipt["status"] == "verified", "base backup over verify-full TLS replication published and checksummed")
        self.service("ext_runtime", self.ext_port, "talkyai", ROLES.runtime, tls=True)
        super_db = self.service("ext_super_db", self.ext_port, "talkyai", "bootstrap_superuser", tls=True)
        self.sql(super_db, "BEGIN; SET LOCAL app.bypass_rls='true'; UPDATE tenants SET business_name = 'PITR before' "
                           f"WHERE id = '{TENANTS['c']}'; COMMIT;", readonly=False)
        self.sql(super_db, "SELECT pg_switch_wal();", readonly=False)
        time.sleep(2)
        target = self.sql(super_db, "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"');")
        time.sleep(2)
        self.sql(super_db, "BEGIN; SET LOCAL app.bypass_rls='true'; UPDATE tenants SET business_name = 'PITR after' "
                           f"WHERE id = '{TENANTS['c']}'; COMMIT;", readonly=False)
        wal = self.sql(super_db, "SELECT pg_walfile_name(pg_current_wal_lsn());")
        self.sql(super_db, "SELECT pg_switch_wal();", readonly=False)
        deadline = time.monotonic() + 120
        while not (self.archive / wal).exists():
            if time.monotonic() > deadline:
                raise AssertionError("WAL segment was not archived")
            time.sleep(1)
        # Retention before the restore: the pruned archive must still reach the target.
        before = {path.name for path in self.archive.iterdir()}
        pruned = json.loads(self.tool("pitr.py", "prune", "--base-root", str(base.parent), "--archive-dir",
                                      str(self.archive), "--keep", "1").stdout)
        self.check(pruned["kept"] == [base.name] and pruned["deleted_segments"] >= 1
                   and (self.archive / pruned["oldest_needed_segment"]).exists()
                   and pruned["oldest_needed_segment"] in before,
                   f"WAL retention removed {pruned['deleted_segments']} segments older than the base backup's start "
                   f"({pruned['oldest_needed_segment']}, a real archived file name) and kept the rest")
        restored, port = self.work / "pitr-restored", free_port()
        self.clusters.append(restored)
        started = time.monotonic()
        result = json.loads(self.tool("pitr.py", "restore", "--base-backup", str(base), "--archive-dir", str(self.archive),
                                      "--target-data-dir", str(restored), "--target-time", target, "--port", str(port),
                                      "--start", "--bin-dir", str(self.bin_dir), "--wait-seconds", "300").stdout)
        self.timings["pitr_restore_seconds"] = round(time.monotonic() - started, 1)
        restored_db = self.service("pitr_db", port, "talkyai", "bootstrap_superuser", tls=True)
        self.passwords[(str(port), "bootstrap_superuser")] = self.passwords[(str(self.ext_port), "bootstrap_superuser")]
        self.secret(str(port), "bootstrap_superuser")
        name = self.sql(restored_db, f"SELECT business_name FROM tenants WHERE id = '{TENANTS['c']}';")
        self.check(result["status"] == "recovered" and name == "PITR before",
                   f"restore to {target} brings back the state between the two changes, over TLS ({self.timings['pitr_restore_seconds']} s)")
        self.stop(restored)

    def monitoring(self) -> None:
        print("== database monitoring", flush=True)
        prom = self.work / "talklee_db.prom"
        result = self.tool("monitor.py", "--service-file", str(self.services), "--service", "ext_runtime", "--bin-dir",
                           str(self.bin_dir), "check", "--capacity-bytes", str(10 * 1024 ** 3), "--latency-warning-ms", "20000",
                           "--require-archiving", "--archive-max-age-minutes", "60", "--backup-root", str(self.ext_backup_root),
                           "--prometheus-textfile", str(prom), ok_codes=(0, 1, 2))
        payload = json.loads(result.stdout)
        statuses = {check["name"]: check["status"] for check in payload["checks"]}
        print("     monitor: " + json.dumps(statuses), flush=True)
        self.check(result.returncode == 0 and set(statuses.values()) == {"ok"},
                   "monitor against the external server: connect, TLS, restricted role, size, connections, "
                   "long transactions, wraparound, WAL archiving and backup freshness all ok")
        self.check("talklee_db_up 1" in prom.read_text(encoding="utf-8"), "Prometheus textfile written")

    def final(self) -> None:
        after = self.fingerprint(self.legacy_super)
        self.check(after == self.before, "legacy database identical at the end of the rehearsal (nothing written to it)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--keep", action="store_true", help="keep the work directory (clusters are always stopped)")
    args = parser.parse_args()
    output = REPO / "test-output" / "database-separation"
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="rehearsal-", dir=output)).resolve()
    os.environ.pop("PGPASSFILE", None)
    rehearsal = Rehearsal(args.bin_dir, work, sys.executable)
    os.environ["PGPASSFILE"] = str(rehearsal.pgpass)
    rehearsal.pgpass.write_text("", encoding="utf-8")
    started = time.monotonic()
    status = "failed"
    try:
        for stage in (rehearsal.legacy, rehearsal.source_backup, rehearsal.external, rehearsal.preflight,
                      rehearsal.cut_over, rehearsal.migrations, rehearsal.application, rehearsal.cost_ledger,
                      rehearsal.resilience,
                      rehearsal.rotation, rehearsal.backups, rehearsal.restore_drill, rehearsal.point_in_time,
                      rehearsal.monitoring, rehearsal.final):
            stage()
        status = "passed"
    finally:
        payload = {"status": status, "completed_at": recovery.utcnow(), "checks": rehearsal.checks,
                   "timings_seconds": rehearsal.timings, "total_seconds": round(time.monotonic() - started, 1),
                   "alembic_heads": getattr(rehearsal, "heads", None),
                   "postgres": rehearsal.pg.run("psql", ["--version"]) if shutil.which("psql") or args.bin_dir else None,
                   "production_accessed": False,
                   "scope": "synthetic loopback rehearsal with the real schema and application code; "
                            "not production data, a real network, a managed provider or Ubuntu"}
        errors = []
        for data in reversed(rehearsal.clusters):
            try:
                rehearsal.stop(data)
            except Exception as exc:
                errors.append(str(exc)[:300])
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        if errors:
            print("cluster stop errors (work dir retained): " + "; ".join(errors), file=sys.stderr)
        elif not args.keep and status == "passed":
            shutil.rmtree(work, ignore_errors=True)
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

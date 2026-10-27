#!/usr/bin/env python3
"""Explicit, synthetic local test: two private loopback PostgreSQL clusters.

Does not use DATABASE_URL, existing servers, real customers or providers.
Requires initdb, pg_ctl and client binaries; run as an unprivileged account.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile

MODULE = Path(__file__).resolve().parents[1] / "recovery.py"
spec = importlib.util.spec_from_file_location("talklee_recovery", MODULE)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(bin_dir, name, arguments):
    executable = bin_dir / (name + (".exe" if os.name == "nt" else ""))
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k not in {"DATABASE_URL", "READ_DATABASE_URL"}}
    # Files avoid communicate() waiting forever when a Windows bootstrap
    # descendant inherited an output pipe after its parent was terminated.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen([str(executable), *arguments], stdout=stdout, stderr=stderr, env=env, **flags)
        try:
            process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            raise RuntimeError(f"synthetic {name} timed out; its owned process tree was terminated")
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read().decode("utf-8", errors="replace")
        errors = stderr.read().decode("utf-8", errors="replace")
    if process.returncode:
        raise RuntimeError(f"synthetic {name} failed: " + output[-1000:] + errors[-2000:])
    return subprocess.CompletedProcess(process.args, process.returncode, output, errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    output = root / "test-output" / "database-recovery"
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="synthetic-", dir=output)).resolve()
    clusters = []
    checks = []
    try:
        definitions = []
        for label in ("source", "target"):
            data = work / label
            # Register ownership before commands that can time out after
            # launching descendants; cleanup never searches other clusters.
            clusters.append(data)
            run(args.bin_dir, "initdb", ["-D", str(data), "-U", "fixture_owner", "--auth=trust", "--encoding=UTF8", "--locale=C"])
            port = free_port()
            run(args.bin_dir, "pg_ctl", ["-D", str(data), "-l", str(work / (label + ".log")),
                                        "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"])
            definitions.append(f"[{label}]\nhost=127.0.0.1\nport={port}\ndbname=postgres\nuser=fixture_owner\nsslmode=disable\n")
        service_file = work / "services.conf"
        service_file.write_text("\n".join(definitions), encoding="utf-8")
        service_file.chmod(0o600)
        pg = recovery.Postgres(args.bin_dir, timeout=60)
        source = recovery.Service(service_file, "source", True)
        target = recovery.Service(service_file, "target", True)
        pg.sql(source, "CREATE DATABASE fixture_source;", readonly=False)
        # A separate named source service prevents implicit database overrides.
        service_file.write_text("\n".join(definitions) + "\n" + definitions[0].replace("[source]", "[backup]").replace("dbname=postgres", "dbname=fixture_source"), encoding="utf-8")
        source = recovery.Service(service_file, "backup", True)
        pg.sql(target, "CREATE ROLE recovery_probe NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;", readonly=False)
        fixture = "CREATE TABLE alembic_version(version_num text PRIMARY KEY); INSERT INTO alembic_version VALUES ('local_fixture_head'); "
        fixture += "CREATE TABLE tenants(id uuid PRIMARY KEY); INSERT INTO tenants VALUES ('11111111-1111-1111-1111-111111111111'), ('22222222-2222-2222-2222-222222222222'); "
        for table in recovery.SCOPED_TABLES:
            fixture += f"CREATE TABLE {table}(id uuid PRIMARY KEY, tenant_id uuid REFERENCES tenants(id), status text, direction text); "
            fixture += f"INSERT INTO {table} SELECT id, id, 'test', 'outbound' FROM tenants; "
            fixture += f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY; "
            fixture += f"CREATE POLICY tenant_read ON {table} USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid); "
        pg.sql(source, fixture, readonly=False)
        bundle = work / "backups" / "first"
        original_run = pg.run
        inserted = False
        def concurrent_run(name, arguments, service=None, **kwargs):
            nonlocal inserted
            if name == "pg_dump" and "--snapshot" in arguments and not inserted:
                inserted = True
                # This commits after snapshot export and must NOT enter dump.
                original_run("psql", ["-X", "-qAt", "--no-password", "--set", "ON_ERROR_STOP=1", "--dbname", source.connection()], source,
                    sql="INSERT INTO tenants VALUES ('33333333-3333-3333-3333-333333333333');", readonly=False)
            return original_run(name, arguments, service, **kwargs)
        pg.run = concurrent_run
        first = recovery.backup(pg, source, bundle, 1024, None, .5)
        pg.run = original_run
        assert first["inventory"]["counts"]["tenants"] == 2
        assert pg.sql(source, "SELECT count(*) FROM tenants;") == "3"
        checks.append("snapshot excludes a concurrent committed insert and records exact source counts")
        assert recovery.health(bundle.parent, 26)["status"] == "passed"
        checks.append("published archive checksum and age verified")
        restored = recovery.restore(pg, target, bundle, "talky_restore_fixture", "recovery_probe", ["local_fixture_head"], work / "restore.json")
        assert restored["status"] == "sql_restore_verified"
        assert restored["inventory"]["counts"] == first["inventory"]["counts"]
        checks.append("separate-cluster restore preserves schema heads and exact snapshot counts")
        checks.append("restricted-role own-tenant reads and foreign-tenant denials pass for two tenants and three tables")
        for label, service, database in (("existing target", target, "talky_restore_fixture"), ("source endpoint", recovery.Service(service_file, "source", True), "talky_restore_forbidden")):
            try:
                recovery.restore(pg, service, bundle, database, "recovery_probe", ["local_fixture_head"], work / (label.replace(" ", "_") + ".json"))
            except recovery.RecoveryError:
                checks.append(label + " refused without overwriting data")
            else:
                raise AssertionError(label + " unexpectedly admitted")
        try:
            recovery.backup(pg, source, work / "backups" / "undersize", first["bytes"] + 1000000, bundle, .5)
        except recovery.RecoveryError:
            assert not (work / "backups" / "undersize").exists()
            checks.append("failed size guard publishes no bundle and preserves previous backup")
        else:
            raise AssertionError("size guard unexpectedly admitted")
        (bundle / "database.dump").write_bytes(b"corrupt")
        try:
            recovery.restore(pg, target, bundle, "talky_restore_corrupt", "recovery_probe", ["local_fixture_head"], work / "corrupt.json")
        except recovery.RecoveryError:
            assert pg.sql(target, "SELECT 1 FROM pg_database WHERE datname='talky_restore_corrupt';") == ""
            checks.append("corrupt archive rejected before database creation")
        else:
            raise AssertionError("corruption unexpectedly admitted")
        payload = {"status": "passed", "completed_at": recovery.utcnow(), "checks": checks,
                   "postgres_client": pg.run("psql", ["--version"]), "production_accessed": False,
                   "scope": "synthetic two-cluster loopback rehearsal; not production data or full schema qualification"}
        recovery.write_json_new(args.receipt, payload)
        print(json.dumps(payload, indent=2))
        return 0
    finally:
        cleanup_errors = []
        for data in reversed(clusters):
            if not (data / "postmaster.pid").exists():
                continue
            try:
                run(args.bin_dir, "pg_ctl", ["-D", str(data), "-m", "immediate", "-w", "stop"])
            except Exception as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            raise RuntimeError("owned cluster cleanup failed; retained " + str(work) + ": " + "; ".join(cleanup_errors))
        if work.parent == output.resolve() and work.name.startswith("synthetic-"):
            shutil.rmtree(work)


if __name__ == "__main__":
    raise SystemExit(main())

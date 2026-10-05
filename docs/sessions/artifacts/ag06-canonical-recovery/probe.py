"""Fresh canonical database acceptance; execution requires explicit review.

Only the UUID database created here is bootstrapped, migrated or test-written.
The existing disposable database gets read-only public snapshots. Generated role
and database are removed exactly; unrelated sessions are never terminated.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
PROTECTED_DSN = "postgresql://talky@127.0.0.1:55434/cp04_acceptance_test"
FOLDER = Path(__file__).resolve().parent
MODULE = "tests/integration/test_saved_acknowledgement_canonical.py"


def source_hashes():
    paths = [*sorted((BACKEND / "Alembic/versions").glob("*.py")), BACKEND / "Alembic/env.py",
             BACKEND / "alembic.ini", BACKEND / "database/complete_schema.sql", BACKEND / MODULE,
             Path(__file__).resolve(), ROOT / "docs/sessions/artifacts/ag06-saved-ack-recovery/pg_probe.py"]
    paths += [BACKEND / p for p in (
        "app/api/v1/endpoints/admin/actions.py", "app/api/v1/endpoints/admin/_serialization.py",
        "app/api/v1/dependencies.py", "app/services/saved_acknowledgement.py", "app/services/action_execution.py",
        "app/services/email_service.py", "app/core/postgres_adapter.py", "app/core/db.py", "app/core/db_utils.py",
        "app/core/jwt_security.py", "app/core/tenant_middleware.py", "app/core/security/principal.py",
        "app/core/security/sessions/queries.py", "app/core/security/tenant_isolation.py",
        "app/domain/services/voice_pipeline/action_execution.py")]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in paths}


def runtime_evidence():
    assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
    assert os.environ.get("PYTHONPATH") == str(BACKEND)
    modules = {"asyncpg": "asyncpg", "alembic": "alembic", "sqlalchemy": "SQLAlchemy", "pytest": "pytest",
               "pytest_asyncio": "pytest-asyncio", "httpx": "httpx", "fastapi": "fastapi", "jwt": "PyJWT", "pydantic": "pydantic"}
    dependencies = {}
    for module, distribution in modules.items():
        location = Path(importlib.util.find_spec(module).origin).resolve()
        assert location.is_relative_to(Path(sys.prefix).resolve()), "Unexpected dependency outside selected venv"
        dependencies[module] = {"version": importlib.metadata.version(distribution), "location": str(location)}
    return {"executable": sys.executable, "version": sys.version, "prefix": sys.prefix,
            "pythonpath": os.environ["PYTHONPATH"], "dont_write_bytecode": sys.dont_write_bytecode,
            "dependencies": dependencies}


def guard_network():
    original_connect, original_ex, original_pair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
    original_resolve = socket.getaddrinfo
    original_transport = asyncio.BaseEventLoop.create_connection
    local = threading.local()
    counts = {"database_socket_attempts": 0, "database_transport_attempts": 0, "database_address_resolutions": 0, "internal_socketpairs": 0, "prohibited_attempts": 0}
    def connect(sock, address, original):
        if getattr(local, "socketpair", False) and address[0] in {"127.0.0.1", "::1"}:
            return original(sock, address)
        if isinstance(address, tuple) and address[:2] == ("127.0.0.1", 55434):
            counts["database_socket_attempts"] += 1
            return original(sock, address)
        counts["prohibited_attempts"] += 1
        raise AssertionError("Only designated local PostgreSQL endpoint is permitted")
    def pair(*args, **kwargs):
        local.socketpair = True
        try:
            result = original_pair(*args, **kwargs)
            counts["internal_socketpairs"] += 1
            return result
        finally:
            local.socketpair = False
    async def transport(loop, factory, host=None, port=None, **kwargs):
        address = kwargs["sock"].getpeername() if kwargs.get("sock") else (host, port)
        if address[:2] != ("127.0.0.1", 55434):
            counts["prohibited_attempts"] += 1
            raise AssertionError("Only designated local PostgreSQL transport is permitted")
        counts["database_transport_attempts"] += 1
        return await original_transport(loop, factory, host, port, **kwargs)
    def resolve(host, port, *args, **kwargs):
        if getattr(local, "socketpair", False) and host in {"127.0.0.1", "::1"}:
            return original_resolve(host, port, *args, **kwargs)
        if (host, port) != ("127.0.0.1", 55434):
            counts["prohibited_attempts"] += 1
            raise AssertionError("Only designated local PostgreSQL address resolution is permitted")
        counts["database_address_resolutions"] += 1
        return original_resolve(host, port, *args, **kwargs)
    socket.socket.connect = lambda sock, addr: connect(sock, addr, original_connect)
    socket.socket.connect_ex = lambda sock, addr: connect(sock, addr, original_ex)
    socket.socketpair = pair
    socket.getaddrinfo = resolve
    asyncio.BaseEventLoop.create_connection = transport
    return counts


def fresh_dsn(dsn):
    parsed = urlparse(dsn)
    assert parsed.hostname == "127.0.0.1" and parsed.port == 55434
    assert re.fullmatch(r"/ack_canonical_[0-9a-f]{32}_test", parsed.path), "Refusing existing database"
    return parsed.path[1:]


def snapshot_loader():
    path = ROOT / "docs/sessions/artifacts/ag06-saved-ack-recovery/pg_probe.py"
    spec = importlib.util.spec_from_file_location("previous_ack_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.snapshot


async def prepare(name, role, report):
    assert re.fullmatch(r"ack_canonical_[0-9a-f]{32}_test", name)
    assert re.fullmatch(r"ack_canonical_app_[0-9a-f]{32}", role)
    owner = await asyncpg.connect(PROTECTED_DSN, timeout=5, command_timeout=30)
    try:
        report["protected_before"] = await snapshot_loader()(owner)
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", name)
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        await owner.execute(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS')
        report["role_created"] = True
        await owner.execute(f'CREATE DATABASE "{name}"')
        report["database_created"] = True
        await owner.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
        await owner.execute(f'GRANT CONNECT ON DATABASE "{name}" TO "{role}"')
    finally:
        await owner.close()
    dsn = f"postgresql://talky@127.0.0.1:55434/{name}"
    fresh_dsn(dsn)
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=120)
    try:
        assert await conn.fetchval("SELECT current_database()") == name
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname='public')")
        await conn.execute((BACKEND / "database/complete_schema.sql").read_text(encoding="utf-8"))
        report["bootstrap_completed"] = True
    finally:
        await conn.close()


async def grants(owner_dsn, role, report):
    fresh_dsn(owner_dsn)
    conn = await asyncpg.connect(owner_dsn, timeout=5, command_timeout=30)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0062_saved_acknowledgement"
        await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await conn.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"')
        await conn.execute(f'GRANT INSERT,UPDATE,DELETE ON public.assistant_actions,public.assistant_action_resolutions TO "{role}"')
        await conn.execute(f'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
        report["canonical_before_tests"] = await snapshot_loader()(conn)
        report["fixture_grants"] = "Fresh database only: CONNECT; public schema USAGE; all public tables SELECT; assistant_actions/resolutions INSERT UPDATE DELETE; sequences USAGE SELECT. Explicit fixture setup, not production grant validation."
    finally:
        await conn.close()


async def cleanup(name, role, report):
    owner = await asyncpg.connect(PROTECTED_DSN, timeout=5, command_timeout=30)
    try:
        if report.get("database_created"):
            assert re.fullmatch(r"ack_canonical_[0-9a-f]{32}_test", name)
            # Do not force-drop or terminate any sessions, even on failure.
            report["fresh_connections_remaining"] = await owner.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=$1", name)
            await owner.execute(f'DROP DATABASE "{name}"')
            report["database_removed"] = not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", name)
        if report.get("role_created"):
            assert re.fullmatch(r"ack_canonical_app_[0-9a-f]{32}", role)
            await owner.execute(f'DROP ROLE "{role}"')
            report["role_removed"] = not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        report["protected_after"] = await snapshot_loader()(owner)
        report["protected_unchanged"] = report["protected_before"] == report["protected_after"]
    finally:
        await owner.close()


def migrate(output):
    owner_dsn = os.environ["TALKY_ACK_CANONICAL_OWNER_DSN"]
    fresh_dsn(owner_dsn)
    assert os.environ.get("DATABASE_URL") == owner_dsn
    from alembic import command
    from alembic.config import Config
    os.chdir(BACKEND)
    config = Config(str(BACKEND / "alembic.ini"))
    steps = []
    command.stamp(config, "0008_tenant_voice_tuning")
    steps.append("stamp 0008_tenant_voice_tuning (consolidated canonical baseline)")
    command.upgrade(config, "head")
    steps.append("upgrade head (actual 0009 through 0062 migration chain)")
    output.write_text(json.dumps({"steps": steps, "network": NETWORK}, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--migrate", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    assert not output.exists(), "Preserve previous evidence"
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.migrate:
        runtime_evidence()
        migrate(output)
        return 0
    name, role = "ack_canonical_" + uuid4().hex + "_test", "ack_canonical_app_" + uuid4().hex
    owner_dsn = f"postgresql://talky@127.0.0.1:55434/{name}"
    app_dsn = f"postgresql://{role}@127.0.0.1:55434/{name}"
    report = {"base_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_before": source_hashes(), "database": name, "role": role, "network": NETWORK,
              "runtime": runtime_evidence()}
    env = {**os.environ, "ENVIRONMENT": "test", "DATABASE_URL": owner_dsn, "TALKY_ACK_CANONICAL_OWNER_DSN": owner_dsn,
           "TALKY_ACK_CANONICAL_DSN": app_dsn, "JWT_SECRET": "synthetic-process-only-" + secrets.token_hex(32),
           "RECORDINGS_STORAGE_DIR": str(ROOT / "tmp/canonical-recovery-storage"), "PYTHONUTF8": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(BACKEND)}
    try:
        asyncio.run(prepare(name, role, report))
        migration_result = output.with_name(output.stem + "-migration.json")
        argv = [sys.executable, str(Path(__file__).resolve()), "--migrate", "--output", str(migration_result)]
        completed = subprocess.run(argv, cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
        output.with_name(output.stem + "-migration.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        report["migration_command"] = argv
        report["migration_exit_code"] = completed.returncode
        assert completed.returncode == 0, "Canonical migration failed; see retained migration log"
        report["migration_result"] = json.loads(migration_result.read_text())
        asyncio.run(grants(owner_dsn, role, report))
        os.environ.update(env, DATABASE_URL=app_dsn)
        sys.path.insert(0, str(BACKEND))
        os.chdir(BACKEND)
        import pytest
        report["pytest_argv"] = [MODULE, "-q", "--tb=short", "--disable-warnings", "-o", "addopts="]
        report["pytest_exit_code"] = int(pytest.main(report["pytest_argv"]))
    finally:
        try:
            asyncio.run(cleanup(name, role, report))
        finally:
            report["source_after"] = source_hashes()
            report["source_unchanged"] = report["source_before"] == report["source_after"]
            output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    assert report["source_unchanged"] and report["protected_unchanged"] and report["database_removed"] and report["role_removed"]
    assert NETWORK["prohibited_attempts"] == report["migration_result"]["network"]["prohibited_attempts"] == 0
    return report["pytest_exit_code"]


if __name__ == "__main__":
    NETWORK = guard_network()
    raise SystemExit(main())

"""Reviewed opt-in runner: private partial schema, restricted role, no public writes.

Provisioning and teardown use the known disposable local owner only. Pytest uses
one generated non-bypass login with database CREATE solely to own its private
fixtures. Public relation data and full public DDL metadata are hashed before
and after. No credentials or provider payloads are recorded.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[4]
DSN = "postgresql://talky@127.0.0.1:55434/cp04_acceptance_test"
SOURCE = (
    "backend/app/services/saved_acknowledgement.py",
    "backend/app/api/v1/endpoints/admin/actions.py",
    "backend/Alembic/versions/0062_saved_acknowledgement.py",
    "backend/tests/unit/test_saved_acknowledgement.py",
    "backend/tests/integration/test_saved_acknowledgement.py",
    "backend/app/services/action_execution.py",
    "backend/app/services/email_service.py",
    "backend/app/core/db_utils.py",
    "backend/app/core/security/principal.py",
    "backend/app/core/security/sessions/queries.py",
)


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for name in SOURCE}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def _snapshot(conn):
    result = {"head": await conn.fetchval("SELECT version_num FROM public.alembic_version"), "tables": {}}
    tables = await conn.fetch("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname""")
    for row in tables:
        name = row["relname"]
        assert re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name), "Unexpected public relation identifier"
        result["tables"][name] = dict(await conn.fetchrow(f'''SELECT count(*) AS count,
          md5(coalesce(string_agg(h,'|' ORDER BY h),'')) AS data_hash
          FROM (SELECT md5(row_to_json(t)::text) AS h FROM public."{name}" t) s'''))
    metadata = {}
    queries = {
      "relations": "SELECT c.relname,c.relkind::text,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname",
      "columns": "SELECT table_name,column_name,udt_name,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position",
      "constraints": "SELECT c.relname,p.conname,p.contype::text,pg_get_constraintdef(p.oid) AS definition FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,p.conname",
      "policies": "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname",
      "functions": "SELECT p.oid::regprocedure::text AS name,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind IN ('f','p') ORDER BY p.oid::regprocedure::text",
      "triggers": "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,t.tgname",
    }
    for key, sql in queries.items():
        rows = [dict(r) for r in await conn.fetch(sql)]
        metadata[key] = {"count": len(rows), "sha256": digest(rows)}
    result["schema"] = metadata
    return result


async def snapshot(conn):
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        await conn.execute("SET LOCAL app.bypass_rls='on'")
        await conn.execute("SET LOCAL app.current_tenant_id='00000000-0000-0000-0000-000000000000'")
        return await _snapshot(conn)


async def provision(role, report):
    conn = await asyncpg.connect(DSN, timeout=5, command_timeout=20)
    try:
        assert await conn.fetchval("SELECT current_database()") == "cp04_acceptance_test"
        report["public_before"] = await snapshot(conn)
        assert report["public_before"]["head"] == "0061_dnc_runtime_contract"
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        await conn.execute(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS')
        report["role_created"] = True
        await conn.execute(f'GRANT CREATE ON DATABASE cp04_acceptance_test TO "{role}"')
        report["public_write_grants"] = await conn.fetchval("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='public' AND c.relkind IN ('r','p') AND (has_table_privilege($1,c.oid,'INSERT') OR
          has_table_privilege($1,c.oid,'UPDATE') OR has_table_privilege($1,c.oid,'DELETE') OR has_table_privilege($1,c.oid,'TRUNCATE'))""", role)
        assert report["public_write_grants"] == 0
        assert not await conn.fetchval("SELECT has_schema_privilege($1,'public','CREATE')", role)
    finally:
        await conn.close()


async def teardown(role, report):
    conn = await asyncpg.connect(DSN, timeout=5, command_timeout=20)
    try:
        if report.get("role_created"):
            leftovers = await conn.fetch("SELECT n.nspname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE r.rolname=$1", role)
            report["fixture_schema_leftovers"] = [r["nspname"] for r in leftovers]
            for row in leftovers:
                schema = row["nspname"]
                assert re.fullmatch(r"ack_recovery_[0-9a-f]{32}", schema), "Refusing unrelated schema cleanup"
                await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await conn.execute(f'REVOKE CREATE ON DATABASE cp04_acceptance_test FROM "{role}"')
            await conn.execute(f'DROP ROLE "{role}"')
            report["role_removed"] = not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        report["public_after"] = await snapshot(conn)
        report["public_unchanged"] = report.get("public_before") == report["public_after"]
        assert report["public_unchanged"], "Public evidence changed during probe"
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    assert not output.exists(), "Preserve prior evidence"
    output.parent.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(DSN)
    assert parsed.hostname == "127.0.0.1" and parsed.port == 55434 and parsed.path == "/cp04_acceptance_test"
    role = "ack_probe_" + uuid4().hex
    report = {"source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_before": hashes(), "role": role,
              "scope": "Synthetic partial-schema SQL and actual 0062 migration proof; not full migrated acceptance or live Gmail."}
    os.environ.update(ENVIRONMENT="test", DATABASE_URL="postgresql://test:test@127.0.0.1:1/unavailable_test",
                      TALKY_ACK_TEST_DATABASE_URL=f"postgresql://{role}@127.0.0.1:55434/cp04_acceptance_test",
                      RECORDINGS_STORAGE_DIR=str(ROOT / "tmp/ack-recovery-storage"))
    sys.path.insert(0, str(ROOT / "backend"))
    # Initialize asyncio before the guard. Later Windows loop self-pipes get
    # only a thread-local allowance during the original socketpair call.
    asyncio.run(asyncio.sleep(0))
    connect, connect_ex, resolve = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    socketpair, local = socket.socketpair, threading.local()
    create_connection = asyncio.BaseEventLoop.create_connection
    network = {"database_socket_attempts": 0, "database_transport_attempts": 0,
               "internal_socketpairs": 0, "prohibited_attempts": 0}
    report["network"] = network
    def allowed(address):
        if isinstance(address, tuple) and getattr(local, "socketpair", False) and address[0] in {"127.0.0.1", "::1"}:
            return
        if isinstance(address, tuple) and address[:2] == ("127.0.0.1", 55434):
            network["database_socket_attempts"] += 1
            return
        network["prohibited_attempts"] += 1
        raise AssertionError("Probe permits only its designated disposable database")
    def guarded_connect(sock, address):
        allowed(address)
        return connect(sock, address)
    def guarded_connect_ex(sock, address):
        allowed(address)
        return connect_ex(sock, address)
    def guarded_resolve(host, *values, **kwargs):
        if host != "127.0.0.1" or not values or values[0] != 55434:
            network["prohibited_attempts"] += 1
            raise AssertionError("Probe forbids other DNS/network targets")
        return resolve(host, *values, **kwargs)
    def guarded_socketpair(*args, **kwargs):
        local.socketpair = True
        try:
            pair = socketpair(*args, **kwargs)
            network["internal_socketpairs"] += 1
            return pair
        finally:
            local.socketpair = False
    async def guarded_transport(loop, protocol_factory, host=None, port=None, **kwargs):
        # Windows Proactor ConnectEx does not call socket.connect/connect_ex.
        sock = kwargs.get("sock")
        address = sock.getpeername() if sock is not None else (host, port)
        if address[:2] != ("127.0.0.1", 55434):
            network["prohibited_attempts"] += 1
            raise AssertionError("Probe forbids other async transport targets")
        network["database_transport_attempts"] += 1
        return await create_connection(loop, protocol_factory, host, port, **kwargs)
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = guarded_connect, guarded_connect_ex, guarded_resolve
    socket.socketpair = guarded_socketpair
    asyncio.BaseEventLoop.create_connection = guarded_transport
    code = 1
    try:
        asyncio.run(provision(role, report))
        import pytest
        os.chdir(ROOT / "backend")
        report["pytest_argv"] = ["tests/integration/test_saved_acknowledgement.py", "-q", "--tb=short", "--disable-warnings", "-o", "addopts="]
        code = int(pytest.main(report["pytest_argv"]))
        report["pytest_exit_code"] = code
    finally:
        try:
            asyncio.run(teardown(role, report))
        finally:
            report["source_after"] = hashes()
            report["source_unchanged"] = report["source_before"] == report["source_after"]
            output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
            socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, resolve
            socket.socketpair = socketpair
            asyncio.BaseEventLoop.create_connection = create_connection
    assert report["source_unchanged"] and report.get("role_removed") and not report.get("fixture_schema_leftovers") and network["prohibited_attempts"] == 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())

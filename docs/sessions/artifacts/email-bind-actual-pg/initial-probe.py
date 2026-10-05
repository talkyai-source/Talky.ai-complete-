"""Actual EmailService SQL against a private LIKE table and restricted PG role.

Only loopback PostgreSQL is contacted. Connector selection, current-account
admission, encryption and provider send are synthetic. This does not test the
new reviewed-account selector SQL, provider delivery, or the full FK graph.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg


ROOT = Path(__file__).resolve().parents[4]
DSN = "postgresql://talky@127.0.0.1:55434/cp04_acceptance_test"
SOURCE_PATHS = (
    "backend/app/services/email_service.py",
    "backend/app/services/connector_resolver.py",
    "backend/app/core/db_utils.py",
    "backend/app/core/postgres_adapter.py",
)
TENANT, OTHER, CONNECTOR, ACCOUNT = [uuid4() for _ in range(4)]


def hashes():
    return {
        name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in SOURCE_PATHS
    }


async def snapshot(conn):
    return {
        "head": await conn.fetchval("SELECT version_num FROM public.alembic_version"),
        "rows": dict(await conn.fetchrow("""SELECT count(*) AS n,
            md5(coalesce(string_agg(h,'|' ORDER BY h),'')) AS digest
            FROM (SELECT md5(row_to_json(t)::text) AS h
                  FROM public.assistant_actions t) s""")),
        "catalog": dict(await conn.fetchrow("""SELECT relrowsecurity,
            relforcerowsecurity,relacl::text FROM pg_class
            WHERE oid='public.assistant_actions'::regclass""")),
        "columns": [dict(r) for r in await conn.fetch("""SELECT column_name,data_type,
            is_nullable,column_default FROM information_schema.columns
            WHERE table_schema='public' AND table_name='assistant_actions'
            ORDER BY ordinal_position""")],
        "constraints": [dict(r) for r in await conn.fetch("""SELECT conname,contype,
            pg_get_constraintdef(oid) AS definition FROM pg_constraint
            WHERE conrelid='public.assistant_actions'::regclass ORDER BY conname""")],
        "indexes": [dict(r) for r in await conn.fetch("""SELECT indexname,indexdef
            FROM pg_indexes WHERE schemaname='public' AND tablename='assistant_actions'
            ORDER BY indexname""")],
        "policies": [dict(r) for r in await conn.fetch("""SELECT policyname,permissive,
            roles::text,cmd,qual,with_check FROM pg_policies
            WHERE schemaname='public' AND tablename='assistant_actions'
            ORDER BY policyname""")],
    }


async def scope(conn, tenant=TENANT):
    await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(tenant))


async def seed(conn, *, status="pending", input_data=None):
    action_id = uuid4()
    async with conn.transaction():
        await scope(conn)
        await conn.execute("""INSERT INTO assistant_actions(id,tenant_id,type,status,input_data)
            VALUES($1,$2,'send_email',$3,$4::jsonb)""", action_id, TENANT, status,
            None if input_data is None else json.dumps(input_data))
    return action_id


async def row(conn, action_id):
    async with conn.transaction():
        await scope(conn)
        found = await conn.fetchrow("SELECT * FROM assistant_actions WHERE id=$1", UUID(str(action_id)))
    result = dict(found) if found else None
    if result:
        for key in ("input_data", "output_data"):
            if isinstance(result[key], str):
                result[key] = json.loads(result[key])
    return result


class FaultPool:
    """All SQL executes in PostgreSQL; faults occur inside real transactions."""

    def __init__(self, pool, mode):
        self.pool, self.mode = pool, mode
        self.bind_acknowledgements = []
        self.faults = 0

    @asynccontextmanager
    async def acquire(self, **kwargs):
        async with self.pool.acquire(**kwargs) as conn:
            owner = self

            class Proxy:
                def __getattr__(self, name):
                    return getattr(conn, name)

                async def execute(self, sql, *args):
                    normalized = " ".join(sql.split())
                    binding = normalized.startswith("UPDATE assistant_actions SET status='running', input_data=")
                    status_write = normalized.startswith("UPDATE assistant_actions SET status =")
                    if (status_write and owner.mode == "all_receipts_fail") or (
                        status_write and owner.mode == "completed_receipt_fails" and args[0] == "completed"
                    ):
                        owner.faults += 1
                        return await conn.execute("SELECT 1/0")
                    result = await conn.execute(sql, *args)
                    if binding:
                        owner.bind_acknowledgements.append(result)
                        if owner.mode == "bind_ack_fails":
                            owner.faults += 1
                            raise RuntimeError("synthetic loss of bind acknowledgement before commit")
                    return result

            yield Proxy()


async def expect_runtime_error(operation):
    try:
        await operation
    except RuntimeError:
        return
    raise AssertionError("Expected an unacknowledged/mismatched bind to reject")


async def run(output):
    # Isolated worktrees contain no live dotenv; do not load a user .env.
    assert not (ROOT / "backend/.env").exists()
    assert not output.exists(), "Preserve previous evidence; choose a fresh output path"
    os.environ.update(ENVIRONMENT="test", DATABASE_URL="postgresql://test:test@127.0.0.1:1/unavailable_test")
    sys.path.insert(0, str(ROOT / "backend"))
    from app.services import email_service as module
    from app.services.connector_resolver import reviewed_authorization_identity

    report = {"scope": __doc__, "cases": [], "source_before": hashes(),
              "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "synthetic_ports": ["connector selection", "current-account admission and its unused compatibility-client facade", "provider send", "encryption", "template validation"],
              "schema_limit": "LIKE INCLUDING ALL copies columns/defaults/checks/indexes; no public data, FKs, triggers, or public RLS policy copied. Private FORCE RLS is strict tenant-only."}
    owner = await asyncpg.connect(DSN, timeout=5, command_timeout=10)
    suffix = uuid4().hex
    schema = role = "email_bind_actual_" + suffix
    assert re.fullmatch(r"email_bind_actual_[0-9a-f]{32}", schema)
    report.update(schema=schema, role=role)
    schema_created = role_created = False
    pool = reader = None

    def passed(name, **details):
        report["cases"].append({"case": name, "passed": True, **details})

    try:
        assert await owner.fetchval("SELECT current_database()") == "cp04_acceptance_test"
        report["server_version"] = await owner.fetchval("SHOW server_version")
        report["public_before"] = await snapshot(owner)
        await owner.execute(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS')
        role_created = True
        await owner.execute(f'CREATE SCHEMA "{schema}"')
        schema_created = True
        await owner.execute(f'CREATE TABLE "{schema}".assistant_actions (LIKE public.assistant_actions INCLUDING ALL)')
        await owner.execute(f'ALTER TABLE "{schema}".assistant_actions ENABLE ROW LEVEL SECURITY')
        await owner.execute(f'ALTER TABLE "{schema}".assistant_actions FORCE ROW LEVEL SECURITY')
        await owner.execute(f'''CREATE POLICY private_tenant ON "{schema}".assistant_actions
            USING(tenant_id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)
            WITH CHECK(tenant_id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)''')
        await owner.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON "{schema}".assistant_actions TO "{role}"')
        privileges = await owner.fetchval("""SELECT count(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
            AND c.relkind IN ('r','p') AND (has_table_privilege($1,c.oid,'INSERT')
            OR has_table_privilege($1,c.oid,'UPDATE') OR has_table_privilege($1,c.oid,'DELETE')
            OR has_table_privilege($1,c.oid,'TRUNCATE'))""", role)
        assert privileges == 0, "Restricted role must have no public table write privilege"
        flags = dict(await owner.fetchrow("""SELECT rolsuper,rolbypassrls,rolcreatedb,
            rolcreaterole,rolinherit FROM pg_roles WHERE rolname=$1""", role))
        assert not any(flags.values())
        report["role_flags"] = flags
        report["public_writable_tables"] = privileges
        private_dsn = f"postgresql://{role}@127.0.0.1:55434/cp04_acceptance_test"
        options = {"server_settings": {"search_path": schema}, "timeout": 5, "command_timeout": 8}
        reader = await asyncpg.connect(private_dsn, **options)
        pool = await asyncpg.create_pool(private_dsn, min_size=1, max_size=1, **options)
        assert await reader.fetchval("SHOW search_path") == schema

        connector = SimpleNamespace(tenant_id=str(TENANT), account_row_id=str(ACCOUNT), external_account_id=None)
        identity = reviewed_authorization_identity(connector, str(CONNECTOR), "gmail")
        template = SimpleNamespace(validate_content=lambda *args: None)

        def service(db=pool):
            with patch.object(module, "get_encryption_service", return_value=object()):
                return module.EmailService(db, template_manager=template)

        svc = service()
        original = {"to": ["synthetic@example.invalid"], "subject": "Synthetic verification",
                    "nested": {"preserve": True}, "reviewed_connector": {"stale": "old"}}
        action = await seed(reader, input_data=original)
        audit = FaultPool(pool, "observe")
        await service(audit)._bind_action_authorization(str(TENANT), str(action), identity)
        stored = await row(reader, action)
        assert stored["input_data"] == {**original, "reviewed_connector": identity}
        assert stored["status"] == "running" and audit.bind_acknowledgements == ["UPDATE 1"]
        passed("acknowledged pending-to-running claim merges proof and preserves unrelated input", acknowledgement="UPDATE 1")

        action = await seed(reader)
        await svc._bind_action_authorization(str(TENANT), str(action), identity)
        assert (await row(reader, action))["input_data"] == {"reviewed_connector": identity}
        passed("SQL NULL input becomes an object with original authorization")

        action = await seed(reader, input_data=original)
        before = await row(reader, action)
        audit = FaultPool(pool, "observe")
        await expect_runtime_error(service(audit)._bind_action_authorization(str(OTHER), str(action), identity))
        assert audit.bind_acknowledgements == ["UPDATE 0"] and await row(reader, action) == before
        async with reader.transaction():
            await scope(reader, OTHER)
            assert await reader.fetchval("SELECT count(*) FROM assistant_actions") == 0
        assert await reader.fetchval("SELECT count(*) FROM assistant_actions") == 0
        passed("foreign tenant bind acknowledges UPDATE 0 and cannot read or change owned rows; scope does not leak")

        for status in ("running", "cancelled", "completed", "unknown"):
            action = await seed(reader, status=status, input_data=original)
            before = await row(reader, action)
            audit = FaultPool(pool, "observe")
            await expect_runtime_error(service(audit)._bind_action_authorization(str(TENANT), str(action), identity))
            assert audit.bind_acknowledgements == ["UPDATE 0"] and await row(reader, action) == before
            passed(f"{status} intent is not rebound", acknowledgement="UPDATE 0")

        audit = FaultPool(pool, "observe")
        await expect_runtime_error(service(audit)._bind_action_authorization(str(TENANT), str(uuid4()), identity))
        assert audit.bind_acknowledgements == ["UPDATE 0"]
        passed("missing intent rejects acknowledged UPDATE 0")

        action = await seed(reader, input_data=original)
        before = await row(reader, action)
        fault = FaultPool(pool, "bind_ack_fails")
        await expect_runtime_error(service(fault)._bind_action_authorization(str(TENANT), str(action), identity))
        assert fault.bind_acknowledgements == ["UPDATE 1"] and fault.faults == 1
        assert await row(reader, action) == before
        await svc._bind_action_authorization(str(TENANT), str(action), identity)
        assert (await row(reader, action))["input_data"]["reviewed_connector"] == identity
        passed("bind acknowledgement fault rolls back real UPDATE and releases connection for retry")

        async def send_case(mode):
            db = FaultPool(pool, mode)
            instance = service(db)
            attempts, observed, competing_rows = [], [], []

            async def select_connector(*args, **kwargs):
                if mode in ("preclaimed_cancelled", "preclaimed_running"):
                    async with reader.transaction():
                        await scope(reader)
                        changed = await reader.fetchrow("""UPDATE assistant_actions
                            SET status=$1, input_data=input_data||jsonb_build_object('other_owner',true)
                            WHERE input_data->>'subject'=$2 RETURNING id""",
                            mode.removeprefix("preclaimed_"), "Synthetic " + mode)
                    competing_rows.append(await row(reader, changed["id"]))
                return connector, str(CONNECTOR), "gmail"

            async def send(**kwargs):
                attempts.append(kwargs)
                async with reader.transaction():
                    await scope(reader)
                    saved = await reader.fetchrow("""SELECT id,input_data,status FROM assistant_actions
                        WHERE input_data->>'subject'=$1 ORDER BY created_at DESC LIMIT 1""", "Synthetic " + mode)
                assert saved and saved["status"] == "running"
                proof = json.loads(saved["input_data"])["reviewed_connector"]
                assert proof == identity
                observed.append(proof)
                return SimpleNamespace(id="synthetic-message-" + mode, thread_id="synthetic-thread")

            connector.send_email = send
            instance._get_active_email_connector = select_connector
            with patch.object(module, "check_reviewed_authorization_current", return_value=identity), patch.object(
                module, "PostgresClient", side_effect=lambda db: SimpleNamespace(pool=db)
            ):
                result = await instance.send_email(str(TENANT), ["synthetic@example.invalid"],
                    "Synthetic " + mode, "Synthetic verification body")
            saved = await row(reader, result["action_id"])
            if competing_rows:
                assert saved == competing_rows[0]
            return result, saved, attempts, observed, db

        result, saved, attempts, observed, fault = await send_case("bind_ack_fails")
        assert len(attempts) == 0 and not result["success"] and result["status"] == "failed"
        assert saved["input_data"]["reviewed_connector"] is None
        assert fault.bind_acknowledgements == ["UPDATE 1"] and fault.faults == 1
        passed("actual service refuses send after rolled-back bind acknowledgement", synthetic_send_attempts=0)

        for mode in ("preclaimed_cancelled", "preclaimed_running"):
            result, saved, attempts, observed, fault = await send_case(mode)
            assert not result["success"] and result["status"] == "failed" and attempts == []
            assert saved["status"] == mode.removeprefix("preclaimed_")
            assert fault.bind_acknowledgements == ["UPDATE 0"]
            passed(mode + " rejects send and failure cleanup preserves the other phase/row",
                   synthetic_send_attempts=0, acknowledgement="UPDATE 0")

        result, saved, attempts, observed, fault = await send_case("normal")
        assert len(attempts) == 1 and len(observed) == 1
        assert result["success"] and result["status"] == "accepted" and saved["status"] == "completed"
        assert saved["input_data"]["reviewed_connector"] == identity
        assert saved["output_data"]["message_id"] == result["message_id"]
        passed("actual service commits original authorization before one synthetic accepted send and saves receipt",
               synthetic_send_attempts=1, separate_connection_observed_pre_send_commit=True,
               receipt_is_synthetic=True, recipient_delivery_proven=False)

        for mode, expected_status, count in (("completed_receipt_fails", "unknown", 1),
                                             ("all_receipts_fail", "running", 2)):
            result, saved, attempts, observed, fault = await send_case(mode)
            assert len(attempts) == len(observed) == 1 and fault.faults == count
            assert not result["success"] and result["status"] == "unknown" and not result["confirmation_allowed"]
            assert saved["status"] == expected_status
            assert saved["input_data"]["reviewed_connector"] == identity
            assert result["message_id"] == "synthetic-message-" + mode
            if expected_status == "unknown":
                assert saved["output_data"]["message_id"] == result["message_id"]
            else:
                assert saved["output_data"] is None
            passed(mode + " retains original durable intent; no second send in this invocation",
                   synthetic_send_attempts=1, durable_status=expected_status,
                   actual_pg_fault="division_by_zero", receipt_is_synthetic=True)
        report["passed"] = True
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        if reader:
            await reader.close()
        if pool:
            await asyncio.wait_for(pool.close(), 10)
        if schema_created:
            assert re.fullmatch(r"email_bind_actual_[0-9a-f]{32}", schema)
            assert await owner.fetchval("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=$1", schema) == await owner.fetchval("SELECT current_user")
            await owner.execute(f'DROP SCHEMA "{schema}" CASCADE')
        if role_created:
            assert role == schema
            await owner.execute(f'DROP ROLE "{role}"')
        report["public_after"] = await snapshot(owner)
        report["public_unchanged"] = report.get("public_before") == report["public_after"]
        report["schema_removed"] = not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=$1)", schema)
        report["role_removed"] = not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        report["source_after"] = hashes()
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["probe_sha256_lf"] = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        report["blocked_external_network_attempts"] = len(network_attempts)
        await owner.close()
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    assert report["passed"] and report["public_unchanged"] and report["source_unchanged"]
    print(json.dumps({"cases_passed": len(report["cases"]), **{key: report[key] for key in (
        "passed", "public_unchanged", "source_unchanged", "schema_removed", "role_removed")}}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-source-frozen", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Set only after Windows asyncio creates its private loop sockets.
    original_connect, original_connect_ex, original_getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    network_attempts = []

    def require_loopback(address):
        if not isinstance(address, tuple) or address[0] != "127.0.0.1" or address[1] != 55434:
            network_attempts.append(repr(address))
            raise AssertionError("Only the designated disposable PostgreSQL socket is allowed")

    def connect(sock, address):
        require_loopback(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        require_loopback(address)
        return original_connect_ex(sock, address)

    def resolve(host, port, *rest, **kwargs):
        require_loopback((host, int(port)))
        return original_getaddrinfo(host, port, *rest, **kwargs)

    async def guarded():
        with patch.object(socket.socket, "connect", connect), patch.object(socket.socket, "connect_ex", connect_ex), patch.object(socket, "getaddrinfo", resolve):
            await asyncio.wait_for(run(args.output), 90)
        assert network_attempts == [], "An unexpected external network attempt was blocked"

    asyncio.run(guarded())

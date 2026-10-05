"""Actual reviewed selector through PostgresClient on private canonical LIKE tables.

No public writes or provider calls. Canonical column metadata is loaded from the
owner connection into the adapter cache because the restricted role cannot see
public information_schema columns. Query results retain native PostgreSQL types.
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
from unittest.mock import patch
from uuid import uuid4

import asyncpg


ROOT = Path(__file__).resolve().parents[4]
DSN = "postgresql://talky@127.0.0.1:55434/cp04_acceptance_test"
TENANT, OTHER, CID, AID = [uuid4() for _ in range(4)]
SOURCE = ("backend/app/services/connector_resolver.py", "backend/app/core/postgres_adapter.py", "backend/app/core/db.py")
os.environ.update(ENVIRONMENT="test", DATABASE_URL="postgresql://test:test@127.0.0.1:1/unavailable_test",
                  RECORDINGS_STORAGE_DIR=str(ROOT / "tmp/selector-storage"))
sys.path.insert(0, str(ROOT / "backend"))
from app.core import postgres_adapter as adapter  # noqa: E402
from app.core.security import tenant_isolation  # noqa: E402
from app.services import connector_resolver as resolver  # noqa: E402


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for name in SOURCE}


async def snapshot(conn):
    result = {"head": await conn.fetchval("SELECT version_num FROM public.alembic_version")}
    for table in ("connectors", "connector_accounts"):
        result[table] = {
            "data": dict(await conn.fetchrow(f"""SELECT count(*) AS n,
                md5(coalesce(string_agg(h,'|' ORDER BY h),'')) AS digest
                FROM (SELECT md5(row_to_json(t)::text) AS h FROM public.{table} t) s""")),
            "catalog": dict(await conn.fetchrow("""SELECT relrowsecurity,relforcerowsecurity,relacl::text
                FROM pg_class WHERE oid=$1::regclass""", "public." + table)),
            "columns": [dict(r) for r in await conn.fetch("""SELECT column_name,udt_name,is_nullable,column_default
                FROM information_schema.columns WHERE table_schema='public' AND table_name=$1
                ORDER BY ordinal_position""", table)],
            "constraints": [dict(r) for r in await conn.fetch("""SELECT conname,contype::text,
                pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=$1::regclass
                ORDER BY conname""", "public." + table)],
            "policies": [dict(r) for r in await conn.fetch("""SELECT policyname,permissive,roles::text,cmd,qual,with_check
                FROM pg_policies WHERE schemaname='public' AND tablename=$1 ORDER BY policyname""", table)],
        }
    return result


async def scope(conn, tenant=TENANT):
    await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(tenant))


async def run(output, baseline):
    assert not output.exists(), "Preserve previous result evidence"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"baseline": baseline, "source_before": source_hashes(), "cases": [],
              "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "metadata_routing": "Actual public metadata preloaded into adapter cache after private column/type parity; metadata discovery branch not validated."}
    owner = await asyncpg.connect(DSN, timeout=5, command_timeout=10)
    schema = role = "reviewed_selector_" + uuid4().hex
    report.update(schema=schema, role=role)
    schema_created = role_created = False
    private = None
    try:
        assert await owner.fetchval("SELECT current_database()") == "cp04_acceptance_test"
        report["public_before"] = await snapshot(owner)
        assert report["public_before"]["head"] == "0061_dnc_runtime_contract"
        report["prior_objects"] = {
            "schemas": await owner.fetchval("SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'reviewed_selector_%'"),
            "roles": await owner.fetchval("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'reviewed_selector_%'"),
        }
        assert report["prior_objects"] == {"schemas": 0, "roles": 0}
        await owner.execute(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS')
        role_created = True
        await owner.execute(f'CREATE SCHEMA "{schema}"')
        schema_created = True
        adapter._TABLE_COLUMN_TYPES_CACHE.clear()
        for table in ("connectors", "connector_accounts"):
            await owner.execute(f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)')
            await owner.execute(f'ALTER TABLE "{schema}".{table} ENABLE ROW LEVEL SECURITY')
            await owner.execute(f'ALTER TABLE "{schema}".{table} FORCE ROW LEVEL SECURITY')
            await owner.execute(f'''CREATE POLICY private_tenant ON "{schema}".{table}
                USING(tenant_id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)
                WITH CHECK(tenant_id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)''')
            canonical = {r["column_name"]: r["udt_name"] for r in report["public_before"][table]["columns"]}
            clone = {r["column_name"]: r["udt_name"] for r in await owner.fetch("""SELECT column_name,udt_name
                FROM information_schema.columns WHERE table_schema=$1 AND table_name=$2""", schema, table)}
            assert clone == canonical
            adapter._TABLE_COLUMN_TYPES_CACHE[table] = canonical
        await owner.execute(f'ALTER TABLE "{schema}".connector_accounts ADD FOREIGN KEY(connector_id) REFERENCES "{schema}".connectors(id) ON DELETE CASCADE')
        await owner.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        report["public_write_grants"] = await owner.fetchval("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p') AND
            (has_table_privilege($1,c.oid,'INSERT') OR has_table_privilege($1,c.oid,'UPDATE') OR
             has_table_privilege($1,c.oid,'DELETE') OR has_table_privilege($1,c.oid,'TRUNCATE'))""", role)
        assert report["public_write_grants"] == 0
        private_dsn = f"postgresql://{role}@127.0.0.1:55434/cp04_acceptance_test?search_path={schema}"
        private = await asyncpg.connect(private_dsn, timeout=5, command_timeout=8)
        assert await private.fetchval("SELECT current_schema()") == schema
        report["private_relations"] = {table: await private.fetchval(f"SELECT '{table}'::regclass::oid") for table in ("connectors", "connector_accounts")}
        for table, oid in report["private_relations"].items():
            assert oid != await owner.fetchval(f"SELECT 'public.{table}'::regclass::oid")
        async with private.transaction():
            await scope(private)
            await private.execute("""INSERT INTO connectors(id,tenant_id,type,provider,status)
                VALUES($1,$2,'email','gmail','active')""", CID, TENANT)
            await private.execute("""INSERT INTO connector_accounts(id,tenant_id,connector_id,status,
                access_token_encrypted,refresh_token_encrypted,token_expires_at,created_at)
                VALUES($1,$2,$3,'active','synthetic-access','synthetic-refresh',now()+interval '1 day',now())""", AID, TENANT, CID)
        with patch.object(adapter, "_DATABASE_URL", private_dsn):
            tenant_isolation.set_current_tenant_id(str(TENANT))
            client = adapter.PostgresClient(None)
            rows = client.table("connector_accounts").select("id,created_at").eq("tenant_id", str(TENANT)).execute()
            assert not rows.error and len(rows.data) == 1
            observed = rows.data[0]
            report["native_row"] = {"id_type": type(observed["id"]).__module__ + "." + type(observed["id"]).__name__,
                                    "id_is_string": isinstance(observed["id"], str),
                                    "created_at_type": type(observed["created_at"]).__name__}
            try:
                selected = resolver._reviewed_account_row(client, str(TENANT), str(CID), "gmail", str(AID))
            except resolver.ReviewedConnectorChanged as exc:
                report["selection"] = {"success": False, "error": str(exc), "cause": str(exc.__cause__)}
            else:
                report["selection"] = {"success": True, "selected_id_type": type(selected["id"]).__name__, "selected_id_matches": str(selected["id"]) == str(AID)}
            if baseline:
                assert report["native_row"]["id_is_string"] is False
                assert report["selection"]["success"] is False and report["selection"]["cause"] == "missing authorization row"
                report["cases"].append({"case": "baseline actual adapter UUID is rejected by reviewed selector", "passed": True})
            else:
                assert report["selection"]["success"] and report["selection"]["selected_id_matches"]
                report["cases"].append({"case": "actual adapter UUID accepted as same reviewed authorization", "passed": True})
        report["passed"] = True
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        if private:
            await private.close()
        if schema_created:
            assert re.fullmatch(r"reviewed_selector_[0-9a-f]{32}", schema)
            assert await owner.fetchval("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=$1", schema) == await owner.fetchval("SELECT current_user")
            await owner.execute(f'DROP SCHEMA "{schema}" CASCADE')
        if role_created:
            await owner.execute(f'DROP ROLE "{role}"')
        report["public_after"] = await snapshot(owner)
        report["public_unchanged"] = report.get("public_before") == report["public_after"]
        report["schema_removed"] = not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=$1)", schema)
        report["role_removed"] = not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        report["source_after"] = source_hashes()
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["probe_sha256_lf"] = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        report["blocked_network_attempts"] = network_attempts
        await owner.close()
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    assert report["passed"] and report["public_unchanged"] and report["source_unchanged"]
    print(json.dumps({key: report[key] for key in ("baseline", "native_row", "selection", "public_unchanged", "source_unchanged", "schema_removed", "role_removed")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original_connect, original_connect_ex, original_getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    network_attempts = []

    def require_loopback(address):
        if not isinstance(address, tuple) or address[0] != "127.0.0.1" or address[1] != 55434:
            network_attempts.append(repr(address))
            raise AssertionError("Only designated loopback PostgreSQL is allowed")

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
            await asyncio.wait_for(run(args.output, args.baseline), 90)
        assert network_attempts == []

    asyncio.run(guarded())

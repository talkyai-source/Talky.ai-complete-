"""Actual reviewed selector through PostgresClient on private canonical LIKE tables.

No public writes or provider calls. Canonical column metadata is loaded from the
owner connection into the adapter cache because the restricted role cannot see
public information_schema columns. Query results retain native PostgreSQL types.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

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
from app.infrastructure.connectors.base import OAuthTokens  # noqa: E402


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


async def final_cases(client, conn, report):
    """Actual selector/refresh SQL; synthetic credentials and provider token port."""
    stamp = datetime.now(timezone.utc) - timedelta(days=1)
    fresh_expiry = datetime.now(timezone.utc) + timedelta(days=1)
    newer_id = uuid4()

    def passed(name, **details):
        report["cases"].append({"case": name, "passed": True, **details})

    async def write(sql, *args):
        async with conn.transaction():
            await scope(conn)
            return await conn.execute(sql, *args)

    async def seed():
        async with conn.transaction():
            await scope(conn)
            await conn.execute("DELETE FROM connector_accounts WHERE tenant_id=$1", TENANT)
            await conn.execute("DELETE FROM connectors WHERE tenant_id=$1", TENANT)
            await conn.execute("INSERT INTO connectors(id,tenant_id,type,provider,status) VALUES($1,$2,'email','gmail','active')", CID, TENANT)
            await conn.execute("""INSERT INTO connector_accounts(id,tenant_id,connector_id,status,created_at,last_refreshed_at,
                access_token_encrypted,refresh_token_encrypted,token_expires_at)
                VALUES($1,$2,$3,'active',$4,$4,'synthetic-access','synthetic-refresh',$5)""", AID,TENANT,CID,stamp,fresh_expiry)

    async def add_newer(created=None):
        await write("""INSERT INTO connector_accounts(id,tenant_id,connector_id,status,created_at,last_refreshed_at,
            access_token_encrypted,refresh_token_encrypted,token_expires_at)
            VALUES($1,$2,$3,'active',$4,$5,'synthetic-newer-access','synthetic-newer-refresh',$6)""",
            newer_id,TENANT,CID,created if created is not None else stamp+timedelta(seconds=1),stamp-timedelta(seconds=1),fresh_expiry)

    def held(fn, expected=resolver.ReviewedConnectorChanged):
        try:
            fn()
        except expected:
            return
        raise AssertionError("Changed authorization was admitted")

    def choose(pin=None):
        return resolver._reviewed_account_row(client,str(TENANT),str(CID),"gmail",pin)

    await seed()
    await add_newer()
    assert choose()["id"] == str(newer_id)
    held(lambda: choose(str(AID)))
    passed("creation-time selection beats newer refresh time and denies an older exact pin")

    await seed()
    await add_newer(stamp)
    held(choose)
    passed("equal creation times fail closed on actual ordered rows")

    await seed()
    await write("UPDATE connector_accounts SET created_at=NULL WHERE id=$1", AID)
    held(choose)
    passed("canonical nullable creation time fails closed")

    class Connector:
        def __init__(self):
            self.tenant_id,self.connector_id,self.provider_name=str(TENANT),str(CID),"gmail"
            self.refresh_calls,self.install_calls=0,0
            self.refresh_hook=self.install_hook=None
        def apply_config(self, _config):
            pass
        def config_from_tokens(self, _tokens):
            return {}
        async def refresh_tokens(self, _refresh):
            self.refresh_calls += 1
            if self.refresh_hook:
                await self.refresh_hook()
            return OAuthTokens(access_token="new-access",refresh_token="new-refresh",expires_at=fresh_expiry)
        async def set_access_token(self, token):
            self.install_calls += 1
            self.access_token=token
            if self.install_hook:
                await self.install_hook()

    encryption=SimpleNamespace(encrypt=lambda value:"synthetic-"+value,decrypt=lambda value:value.removeprefix("synthetic-"))

    async def resolve(connector, *, refresh=False):
        with patch.object(resolver,"get_encryption_service",return_value=encryption), patch.object(resolver.ConnectorFactory,"create",return_value=connector):
            return await resolver.resolve_active_connector(client,str(TENANT),"email",connector_id=str(CID),account_id=str(AID),
                                                           force_refresh=refresh,reviewed_authorization=True)

    async def held_resolve(connector, *, refresh=False, expected=resolver.ReviewedConnectorChanged):
        try:
            await resolve(connector,refresh=refresh)
        except expected:
            return
        raise AssertionError("Changed authorization was returned for use")

    await seed()
    original=Connector()
    actual,cid,provider=await resolve(original,refresh=True)
    identity=resolver.reviewed_authorization_identity(actual,cid,provider)
    assert original.refresh_calls == original.install_calls == 1
    assert identity["account_row_id"] == str(AID) and "external_account_id" not in identity
    saved=client.table("connector_accounts").select("*").eq("id",str(AID)).execute().data[0]
    assert saved["access_token_encrypted"] == "synthetic-new-access" and saved["refresh_token_encrypted"] == "synthetic-new-refresh"
    assert original._authorization_snapshot == resolver._authorization_snapshot_for_row(str(TENANT),str(CID),"gmail",saved)
    assert choose()["id"] == str(AID)
    passed("same-row forced refresh persists native timestamps and acknowledged generation before returning original proof")

    await seed()
    original=Connector()
    original.refresh_hook=add_newer
    await held_resolve(original,refresh=True)
    assert original.refresh_calls == 1 and choose()["id"] == str(newer_id)
    held(lambda:choose(str(AID)))
    saved=client.table("connector_accounts").select("access_token_encrypted").eq("id",str(AID)).execute().data[0]
    assert saved["access_token_encrypted"] == "synthetic-new-access"
    passed("reconnect during synthetic provider refresh prevents return and old refresh cannot promote its row",
           note="Old still-active token row is updated; no remote effect is dispatched by this selector probe.")

    for mutation in ("revoke","delete"):
        await seed()
        original=Connector()
        async def lose_account(operation=mutation):
            if operation == "revoke":
                await write("UPDATE connector_accounts SET status='revoked' WHERE id=$1",AID)
            else:
                await write("DELETE FROM connector_accounts WHERE id=$1",AID)
        original.refresh_hook=lose_account
        await held_resolve(original,refresh=True,expected=resolver.ConnectorLookupError)
        assert original.refresh_calls == 1 and original.install_calls == 0
        remaining=client.table("connector_accounts").select("access_token_encrypted,status").eq("id",str(AID)).execute().data
        assert (not remaining if mutation == "delete" else remaining[0]["access_token_encrypted"] == "synthetic-access")
        passed(mutation+" during refresh makes real active-row UPDATE acknowledge zero and prevents token installation")

    for mutation in ("parent_inactive","external_identity_changed","newer_row"):
        await seed()
        original=Connector()
        async def change_at_install(operation=mutation):
            if operation == "parent_inactive":
                await write("UPDATE connectors SET status='expired' WHERE id=$1",CID)
            elif operation == "external_identity_changed":
                await write("UPDATE connector_accounts SET external_account_id='genuine-synthetic-subject' WHERE id=$1",AID)
            else:
                await add_newer()
        original.install_hook=change_at_install
        await held_resolve(original)
        assert original.refresh_calls == 0 and original.install_calls == 1
        passed(mutation+" during awaited token installation is denied by final current-account admission")

    await seed()
    try:
        tenant_isolation.set_current_tenant_id(str(OTHER))
        assert client.table("connector_accounts").select("id").execute().data == []
        held(choose)
        tenant_isolation.set_current_tenant_id(None)
        assert client.table("connector_accounts").select("id").execute().data == []
        held(choose)
    finally:
        tenant_isolation.set_current_tenant_id(str(TENANT))
    assert choose()["id"] == str(AID)
    passed("foreign and absent tenant context cannot see or admit authorization; restored tenant remains usable")


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
                                    "id_is_uuid": isinstance(observed["id"], UUID),
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
                await final_cases(client, private, report)
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
        report["allowed_event_loop_selfpipe_connections"] = len(selfpipe_connections)
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
    original_socketpair = socket.socketpair
    network_attempts = []
    selfpipe_connections = []
    local = threading.local()

    def require_loopback(address):
        # QueryBuilder creates a fresh asyncio loop in its worker thread.
        # Windows socketpair uses one loopback TCP connect for the loop pipe.
        if getattr(local, "inside_socketpair", False) and isinstance(address, tuple) and address[0] == "127.0.0.1":
            selfpipe_connections.append(address)
            return
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

    def socketpair(*args, **kwargs):
        local.inside_socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            local.inside_socketpair = False

    async def guarded():
        with patch.object(socket.socket, "connect", connect), patch.object(socket.socket, "connect_ex", connect_ex), patch.object(socket, "getaddrinfo", resolve), patch.object(socket, "socketpair", socketpair):
            await asyncio.wait_for(run(args.output, args.baseline), 90)
        assert network_attempts == []

    asyncio.run(guarded())

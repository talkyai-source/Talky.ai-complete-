"""Catalog/parser proof on the exact disposable DB; never writes customer rows.

Test infrastructure alone creates a random role, grants the explicit contract,
and uses SET SESSION AUTHORIZATION. The production preflight never does so.
All owned role grants are removed and effective table/schema ACLs compared.
"""
import os
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from scripts import verify_admin_runtime_privileges as gate
from scripts.verify_alembic_current_heads import _repository_scripts
from tests.integration.test_admin_real_auth_journey import reviewed_database

pytestmark = pytest.mark.integration


async def acl_snapshot(conn):
    # Treat NULL/default ACL and its explicit equivalent identically, as the
    # existing normal-auth fixture does; retain every grant and grant option.
    return [tuple(row) for row in await conn.fetch("""
        SELECT 'table:'||c.relname AS object,
          ARRAY(SELECT format('%s:%s:%s:%s',a.grantor,a.grantee,a.privilege_type,a.is_grantable)
                FROM aclexplode(COALESCE(c.relacl,acldefault(
                    CASE WHEN c.relkind='S' THEN 'S'::"char" ELSE 'r'::"char" END,c.relowner))) a
                ORDER BY a.grantor,a.grantee,a.privilege_type,a.is_grantable) AS acl
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','S')
        UNION ALL
        SELECT 'schema:'||n.nspname,
          ARRAY(SELECT format('%s:%s:%s:%s',a.grantor,a.grantee,a.privilege_type,a.is_grantable)
                FROM aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
                ORDER BY a.grantor,a.grantee,a.privilege_type,a.is_grantable)
        FROM pg_namespace n WHERE n.nspname='public' ORDER BY object
    """)]


@pytest_asyncio.fixture
async def privilege_db():
    configured = os.getenv("TEST_ADMIN_DATABASE_URL")
    if not configured:
        pytest.skip("Explicit reviewed disposable TEST_ADMIN_DATABASE_URL required")
    dsn = reviewed_database(configured, os.getenv("GITHUB_ACTIONS"))
    owner = await asyncpg.connect(dsn, timeout=5, command_timeout=5)
    role_conn = None
    role = "admin_privilege_" + uuid4().hex
    created = False
    before = None
    try:
        before = await acl_snapshot(owner)
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
        created = True
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT SELECT ON public.alembic_version TO "{role}"')
        role_conn = await asyncpg.connect(dsn, timeout=5, command_timeout=5)
        # Both current_user and session_user must be restricted. SET ROLE alone
        # retains privileged session authority and is deliberately insufficient.
        await role_conn.execute(f'SET SESSION AUTHORIZATION "{role}"')
        yield owner, role_conn, role
    finally:
        try:
            if role_conn is not None:
                try:
                    await role_conn.close(timeout=2)
                except Exception:
                    role_conn.terminate()
            if created:
                await owner.execute(f'DROP OWNED BY "{role}"')
                await owner.execute(f'DROP ROLE "{role}"')
            if before is not None:
                assert await acl_snapshot(owner) == before
            assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        finally:
            try:
                await owner.close(timeout=2)
            except Exception:
                owner.terminate()


async def test_real_catalog_restricted_session_reports_exact_missing_insert_then_passes_explicit_grants(privilege_db):
    owner, conn, role = privilege_db
    manifest = gate.contract()
    heads = _repository_scripts(gate.BACKEND_ROOT).get_heads()
    # Grant only the documented operations, intentionally omitting the new
    # incident writer permission to reproduce a split migration/runtime owner.
    for name, requirement in manifest["tables"].items():
        operations = [op for op in requirement["operations"] if not (name == "incidents" and op == "INSERT")]
        await owner.execute(f'GRANT {",".join(operations)} ON public."{name}" TO "{role}"')
    snapshot = await gate.inspect_connection(conn, manifest)
    assert snapshot["role"]["session_role_matches"] is True
    assert all(snapshot["role"][key] is False for key in (
        "rolsuper", "rolbypassrls", "session_rolsuper", "session_rolbypassrls"))
    result = gate.evaluate_catalog(snapshot, manifest, heads, require_admin=True)
    assert set(result["failures"]) == {"missing:incidents:INSERT",
        "default_sequence_usage_missing:inbound_audit_events:id"}, result["failures"]
    # A table INSERT grant alone does not authorize its SERIAL default. Keep
    # the exact sequence failure proof before supplying this fixture grant.
    assert await owner.fetchval("SELECT pg_get_serial_sequence('public.inbound_audit_events','id')") == "public.inbound_audit_events_id_seq"
    await owner.execute(f'GRANT USAGE ON SEQUENCE public.inbound_audit_events_id_seq TO "{role}"')
    snapshot = await gate.inspect_connection(conn, manifest)
    result = gate.evaluate_catalog(snapshot, manifest, heads, require_admin=True)
    assert result["failures"] == ["missing:incidents:INSERT"], result["failures"]
    assert not any(row["owner_capability"] for row in result["tables"])

    await owner.execute(f'GRANT INSERT ON public.incidents TO "{role}"')
    snapshot = await gate.inspect_connection(conn, manifest)
    result = gate.evaluate_catalog(snapshot, manifest, heads, require_admin=True)
    assert result["status"] == "passed", result["failures"]
    assert result["warnings"] == []


async def test_real_catalog_owner_access_cannot_masquerade_as_runtime_rls_proof(privilege_db):
    owner, _, _ = privilege_db
    manifest = gate.contract()
    snapshot = await gate.inspect_connection(owner, manifest)
    # Both reviewed local and CI fixture owners are superusers; this is fixture
    # capacity, not evidence of the unknown production application's role.
    assert snapshot["role"]["rolsuper"] is True
    result = gate.evaluate_catalog(snapshot, manifest, _repository_scripts(gate.BACKEND_ROOT).get_heads(), require_admin=True)
    assert result["status"] == "failed"
    assert "runtime_role_bypasses_rls" in result["failures"]
    assert "login_role_retains_rls_bypass_authority" in result["failures"]

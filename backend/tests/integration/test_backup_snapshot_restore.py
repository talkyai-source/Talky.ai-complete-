"""Populated0062 receipt backup/restore under a real restricted dump role.

Only two new UUID databases and one owned role are mutated. The provisioner DB
is read-only except CREATE/DROP of those exact resources. No Docker or provider.
"""
import asyncio
import importlib.util
from importlib import import_module
import os
from pathlib import Path
import re
import secrets
import subprocess
from types import SimpleNamespace
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest

BACKEND = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("backup_snapshot_real", BACKEND / "deploy/backup_snapshot.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
RUN_EVIDENCE = []
pytestmark = pytest.mark.integration


def run(coro):
    return asyncio.run(coro)


def reviewed_provisioner(value, github_actions):
    parsed = urlparse(value)
    assert parsed.scheme == "postgresql" and parsed.hostname == "127.0.0.1"
    assert parsed.username and not parsed.query and not parsed.fragment
    local = parsed.port == 55434 and parsed.path in {
        "/admin_premortem_20261021_test", "/cp04_acceptance_test"}
    ci = (github_actions == "true" and parsed.port == 5432
          and parsed.path == "/talky_alembic_test")
    assert local or ci, "Only the explicitly configured local or real CI provisioner is allowed"
    return parsed


def reviewed_client_versions(versions, server_version_num):
    assert set(versions) == {"psql", "pg_dump", "pg_restore"}
    majors = []
    for value in versions.values():
        match = re.search(r"\(PostgreSQL\)\s+(\d+)\.", value)
        assert match, "PostgreSQL client version could not be established"
        majors.append(int(match.group(1)))
    assert len(set(majors)) == 1, "Use one consistent PostgreSQL client toolchain"
    assert majors[0] >= int(server_version_num) // 10000, "pg_dump cannot read a newer server"
    # A newer client can read an older server, but its output is not guaranteed
    # to restore there. The unmodified real restore below is that final gate.
    return majors[0]


@pytest.mark.parametrize("majors,server,accepted", [
    ((15, 15, 15), 150017, True),
    ((16, 16, 16), 150017, True),
    ((16, 16, 16), 160014, True),
    ((15, 15, 15), 160014, False),
    ((15, 16, 16), 150017, False),
    ((17, 16, 17), 150017, False),
])
def test_client_version_guard(majors, server, accepted):
    versions = {tool: f"{tool} (PostgreSQL) {major}.14" for tool, major in zip(
        ("psql", "pg_dump", "pg_restore"), majors)}
    if accepted:
        assert reviewed_client_versions(versions, server) == majors[0]
    else:
        with pytest.raises(AssertionError):
            reviewed_client_versions(versions, server)


def test_client_version_guard_rejects_unknown_output():
    with pytest.raises(AssertionError):
        reviewed_client_versions({tool: "unrecognized" for tool in ("psql", "pg_dump", "pg_restore")}, 150017)


@pytest.mark.parametrize("dsn,ci,accepted", [
    ("postgresql://talky@127.0.0.1:55434/admin_premortem_20261021_test", "", True),
    ("postgresql://talky@127.0.0.1:55434/cp04_acceptance_test", "", True),
    ("postgresql://talky@127.0.0.1:5432/talky_alembic_test", "true", True),
    ("postgresql://talky@127.0.0.1:5432/talky_alembic_test", "", False),
    ("postgresql://talky@127.0.0.1:5432/talky_alembic_test", "false", False),
    ("postgresql://talky@127.0.0.1:5432/talkyai", "true", False),
    ("postgresql://talky@production.invalid:5432/talky_alembic_test", "true", False),
    ("postgresql://talky@127.0.0.1:5432/talky_alembic_test?host=other", "true", False),
    ("postgresql://talky@127.0.0.1:5432/talky_alembic_test#other", "true", False),
    ("postgresql://127.0.0.1:5432/talky_alembic_test", "true", False),
])
def test_provisioner_target_guard(dsn, ci, accepted):
    if accepted:
        assert reviewed_provisioner(dsn, ci).hostname == "127.0.0.1"
    else:
        with pytest.raises(AssertionError):
            reviewed_provisioner(dsn, ci)


async def query(dsn, sql, *args):
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=60)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


@pytest.fixture
def database_pair(monkeypatch, tmp_path):
    configured = os.environ.get("TALKY_BACKUP_TEST_ADMIN_DSN", "")
    if not configured:
        pytest.skip("Explicit local backup restore provisioner required")
    parsed = reviewed_provisioner(configured, os.getenv("GITHUB_ACTIONS"))
    pg_bin = Path(os.environ["TALKY_BACKUP_PG_BIN"])
    suffix = ".exe" if os.name == "nt" else ""
    tools = {name: pg_bin / (name + suffix) for name in ("psql", "pg_dump", "pg_restore")}
    assert all(path.is_file() and os.access(path, os.X_OK) for path in tools.values())
    versions = {name: subprocess.run([str(path), "--version"], check=True, capture_output=True,
        text=True, timeout=5).stdout for name, path in tools.items()}
    token = uuid4().hex
    source, restored, role = ("backup_" + token + value for value in ("_source_test", "_restored_test", "_role"))
    password = secrets.token_urlsafe(32)
    source_dsn = urlunparse(parsed._replace(path="/" + source))
    restore_dsn = urlunparse(parsed._replace(path="/" + restored))
    principal, tenant, action, receipt, request = (uuid4() for _ in range(5))
    state = {"source_created": False, "restore_created": False, "role_created": False, "removed": False}
    RUN_EVIDENCE.append(state)

    async def prepare():
        owner = await asyncpg.connect(configured, timeout=5, command_timeout=60)
        try:
            state["server_version_num"] = int(await owner.fetchval("SHOW server_version_num"))
            state["client_major"] = reviewed_client_versions(versions, state["server_version_num"])
            flags = await owner.fetchrow("SELECT rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")
            assert flags["rolcreatedb"] and flags["rolcreaterole"]
            assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
            await owner.execute(await owner.fetchval(
                "SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD %L', $1::text,$2::text)", role, password))
            state["role_created"] = True
            for name, key in ((source, "source_created"), (restored, "restore_created")):
                assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", name)
                await owner.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
                state[key] = True
        finally:
            await owner.close()
        conn = await asyncpg.connect(source_dsn, timeout=5, command_timeout=60)
        try:
            await conn.execute((BACKEND / "database/complete_schema.sql").read_text(encoding="utf-8"))
            # Execute the published0062 DDL over canonical base tables. No copied
            # or weaker RLS policy: this is the exact policy the backup must obey.
            migration = import_module("Alembic.versions.0062_saved_acknowledgement")
            statements = []
            with monkeypatch.context() as patch:
                patch.setattr(migration, "op", SimpleNamespace(execute=statements.append))
                migration.upgrade()
            async with conn.transaction():
                for sql in statements:
                    await conn.execute(sql)
            await conn.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic backup tenant')", tenant)
            await conn.execute("""INSERT INTO user_profiles(id,email,role,is_active,is_verified,email_verified_at)
                VALUES($1,'backup@example.invalid','platform_admin',true,true,clock_timestamp())""", principal)
            await conn.execute("""INSERT INTO assistant_actions(id,tenant_id,type,status,output_data)
                VALUES($1,$2,'send_email','unknown','{"synthetic":true}')""", action, tenant)
            await conn.execute("""INSERT INTO assistant_action_resolutions
                (id,tenant_id,action_id,actor_id,actor_role,request_id,source_digest,reason,
                 original_status,original_output,original_timestamps,recovered_status,provider_status)
                VALUES($1,$2,$3,$4,'platform_admin',$5,$6,'Synthetic restore receipt',
                       'unknown','{"synthetic":true}','{"completed_at":null}','completed','accepted')""",
                receipt, tenant, action, principal, request, "a" * 64)
            await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            await conn.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"')
            await conn.execute(f'GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
            state["expected_receipt"] = await conn.fetchval("SELECT to_jsonb(r)::text FROM assistant_action_resolutions r")
            posture = await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='assistant_action_resolutions'::regclass")
            assert tuple(posture) == (True, True)
        finally:
            await conn.close()

    async def cleanup():
        owner = await asyncpg.connect(configured, timeout=5, command_timeout=60)
        try:
            for name, key in ((restored, "restore_created"), (source, "source_created")):
                if state[key]:
                    assert await owner.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=$1", name) == 0
                    await owner.execute(f'DROP DATABASE "{name}"')
            if state["role_created"]:
                await owner.execute(f'DROP ROLE "{role}"')
            state["removed"] = True
        finally:
            await owner.close()

    try:
        run(prepare())
        monkeypatch.setenv("PGPASSWORD", password)
        connect = ["-h", parsed.hostname, "-p", str(parsed.port), "-U", role, "-d", source]
        flags = run(query(source_dsn, "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=$1", role))[0]
        assert not flags["rolsuper"] and not flags["rolbypassrls"]
        yield SimpleNamespace(source=source_dsn, restored=restore_dsn, principal=principal,
            psql=[str(tools["psql"]), *connect], dump=[str(tools["pg_dump"]), *connect],
            pg_restore=str(tools["pg_restore"]), state=state, archive=tmp_path / "owned.dump",
            role=role, owner_password=parsed.password or "", owner_user=parsed.username,
            host=parsed.hostname, port=parsed.port, restored_name=restored)
    finally:
        run(cleanup())


@pytest.mark.parametrize("deactivate_after_export", [False, True])
def test_populated_receipt_roundtrip_uses_validated_snapshot(database_pair, monkeypatch, deactivate_after_export):
    db = database_pair
    original = subprocess.Popen
    started = []

    def interleave(argv, **kwargs):
        if Path(argv[0]).name.startswith("pg_dump"):
            assert any(arg.startswith("--snapshot=") for arg in argv)
            if deactivate_after_export:
                run(query(db.source, "UPDATE user_profiles SET is_active=false WHERE id=$1", db.principal))
                # New snapshots really lose receipt visibility; the dump must
                # use the earlier authorized snapshot, not merely set the GUC.
                env = dict(os.environ, PGOPTIONS=f"-c app.bypass_rls=true -c app.current_user_id={db.principal}")
                visible = subprocess.run([*db.psql, "-X", "-A", "-t", "-c", "SELECT count(*) FROM assistant_action_resolutions"],
                    env=env, capture_output=True, text=True, timeout=20)
                assert visible.returncode == 0 and visible.stdout.strip() == "0"
                db.state["new_snapshot_receipt_count"] = 0
            started.append(True)
        return original(argv, **kwargs)

    monkeypatch.setattr(backup.subprocess, "Popen", interleave)
    with db.archive.open("wb") as output:
        backup.snapshot_dump(db.psql, db.dump, str(db.principal), output, timeout=120)
    assert started == [True]
    monkeypatch.setattr(backup.subprocess, "Popen", original)
    restore_env = dict(os.environ, PGPASSWORD=db.owner_password)
    restored = subprocess.run([db.pg_restore, "-h", db.host, "-p", str(db.port), "-U", db.owner_user,
        "-d", db.restored_name, "--exit-on-error", "--single-transaction", str(db.archive)],
        env=restore_env, capture_output=True, timeout=120)
    assert restored.returncode == 0, "owned archive failed to restore"
    rows = run(query(db.restored, "SELECT to_jsonb(r)::text AS value FROM assistant_action_resolutions r"))
    assert [row["value"] for row in rows] == [db.state["expected_receipt"]]
    assert run(query(db.restored, "SELECT is_active FROM user_profiles WHERE id=$1", db.principal))[0]["is_active"]
    db.state["populated_receipt_exact"] = True
    db.state["snapshot_profile_active"] = True
    db.state["deactivation_interleaved"] = deactivate_after_export
    # Evidence contains only fixture flags; the synthetic row can still include
    # opaque receipt IDs, so do not export it in the shared summary.
    del db.state["expected_receipt"]


@pytest.mark.parametrize("invalid", ["absent", "inactive", "unverified", "wrong_role"])
def test_invalid_existing_principal_cannot_start_dump(database_pair, monkeypatch, invalid):
    db = database_pair
    principal = str(uuid4()) if invalid == "absent" else str(db.principal)
    if invalid != "absent":
        assignments = {"inactive": "is_active=false", "unverified": "is_verified=false", "wrong_role": "role='user'"}
        run(query(db.source, "UPDATE user_profiles SET " + assignments[invalid] + " WHERE id=$1", db.principal))
    original = subprocess.Popen
    started = []

    def observe(argv, **kwargs):
        started.append(Path(argv[0]).name)
        return original(argv, **kwargs)

    monkeypatch.setattr(backup.subprocess, "Popen", observe)
    with db.archive.open("wb") as output, pytest.raises(backup.BackupError, match="principal"):
        backup.snapshot_dump(db.psql, db.dump, principal, output, timeout=30)
    assert len(started) == 1 and started[0].startswith("psql")
    assert db.archive.stat().st_size == 0
    db.state["invalid_principal_rejected_before_dump"] = invalid
    del db.state["expected_receipt"]

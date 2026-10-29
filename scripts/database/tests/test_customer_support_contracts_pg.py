"""0083 against isolated native PostgreSQL16; never accepts an existing DSN.

Set TEST_POSTGRES_BIN to a directory with initdb/pg_ctl/postgres. Each run creates
its own loopback cluster, canonical 0082 template and synthetic databases. The
ordinary unit suite skips this fixture unless that explicit opt-in is present.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
VERSIONS = BACKEND / "Alembic/versions"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


migration = module("support_contracts_0083", VERSIONS / "0083_customer_support_contracts.py")
native = module("support_native_cluster", Path(__file__).with_name("local_postgres_rehearsal.py"))
TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"


def upgrade(conn, mod=migration):
    original = mod.op
    try:
        mod.op = Operations(MigrationContext.configure(conn))
        mod.upgrade()
    finally:
        mod.op = original


@pytest.fixture(scope="module")
def cluster():
    binary = os.getenv("TEST_POSTGRES_BIN")
    if not binary:
        pytest.skip("TEST_POSTGRES_BIN required for isolated PostgreSQL16 fixture")
    bin_dir = Path(binary).resolve()
    output = (ROOT / "test-output/database-support-contracts").resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="synthetic-", dir=output)).resolve()
    assert work.parent == output
    data = work / "cluster"
    port = native.free_port()
    admin = None
    started = False
    try:
        native.run(bin_dir, "initdb", ["-D", str(data), "-U", "fixture_admin", "--auth=trust", "--encoding=UTF8", "--locale=C", "--no-sync"])
        # This is a schema/transaction test, not crash-recovery evidence. Avoid
        # fsync-heavy template copies on the disposable synthetic-only cluster.
        options = (f"-h 127.0.0.1 -p {port} -c fsync=off -c synchronous_commit=off"
                   " -c full_page_writes=off" + (" -k ''" if os.name != "nt" else ""))
        try:
            native.run(bin_dir, "pg_ctl", ["-D", str(data), "-l", str(work / "postgres.log"),
                "-o", options, "-w", "start"])
            started = True
        except RuntimeError as error:
            log = work / "postgres.log"
            raise RuntimeError(log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.exists() else str(error)) from error
        admin = create_engine(f"postgresql+psycopg2://fixture_admin@127.0.0.1:{port}/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            assert 160000 <= int(conn.execute(text("SHOW server_version_num")).scalar()) < 170000
            conn.exec_driver_sql("CREATE ROLE support_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION")
            conn.exec_driver_sql("CREATE ROLE support_runtime LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION")
            conn.exec_driver_sql("CREATE ROLE support_other LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION")
            conn.exec_driver_sql("CREATE DATABASE support_baseline OWNER support_owner TEMPLATE template0")
        url = f"postgresql://support_owner@127.0.0.1:{port}/support_baseline"
        owner = create_engine(url)
        try:
            with owner.begin() as conn:
                conn.exec_driver_sql("SET LOCAL app.bypass_rls='true'")
                conn.exec_driver_sql((BACKEND / "database/complete_schema.sql").read_text(encoding="utf-8"), execution_options={"no_parameters": True})
        finally:
            owner.dispose()
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k not in ("DATABASE_URL", "READ_DATABASE_URL")}
        env.update(MIGRATION_DATABASE_URL=url, PYTHON_DOTENV_DISABLED="1")
        for arguments in (("stamp", "0008_tenant_voice_tuning"), ("upgrade", "0082_ubuntu_admin_merge")):
            result = subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=BACKEND,
                                    env=env, capture_output=True, text=True, timeout=180)
            assert result.returncode == 0, result.stderr[-5000:]
        yield admin, port
    finally:
        if admin is not None:
            admin.dispose()
        if started or (data / "postmaster.pid").is_file():
            native.run(bin_dir, "pg_ctl", ["-D", str(data), "-m", "immediate", "-w", "stop"])
        # Resolve and verify the exact directory created by this fixture.
        if work.is_dir() and work.resolve().parent == output:
            shutil.rmtree(work)


@pytest.fixture
def database(cluster):
    admin, port = cluster
    name = "support_case_" + uuid.uuid4().hex[:12]
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER support_owner TEMPLATE support_baseline')
    engine = create_engine(f"postgresql://support_owner@127.0.0.1:{port}/{name}")
    try:
        yield engine, admin, port, name
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')


def seed(conn):
    conn.exec_driver_sql("SET LOCAL app.bypass_rls='true'")
    for tenant, label in ((TENANT_A, "A"), (TENANT_B, "B")):
        conn.execute(text("INSERT INTO tenants (id,business_name) VALUES (:id,:name)"), dict(id=tenant, name=f"Synthetic support {label}"))


def legacy(conn):
    """Historical creators, independent of the new migration's table definitions."""
    upgrade(conn, module("original_0005", VERSIONS / "0005_tenant_telephony_credentials.py"))
    for path in ("migrations/_archive/20260425_add_tenant_ai_credentials.sql", "migrations/20260425_add_tenant_recording_policy.sql"):
        conn.exec_driver_sql((BACKEND / "database" / path).read_text(encoding="utf-8"), execution_options={"no_parameters": True})
    upgrade(conn, module("original_0067", VERSIONS / "0067_ai_credentials_history.py"))
    snapshot = (BACKEND / "database/schema/baseline_2026-06-02.sql").read_text(encoding="utf-8")
    for statement in re.findall(r"(?:CREATE TABLE public\.emergency_access_requests \([\s\S]*?\n\);|"
                                r"ALTER TABLE ONLY public\.emergency_access_requests\s+[\s\S]*?;|"
                                r"CREATE INDEX idx_emergency_access_.*?;)", snapshot):
        conn.exec_driver_sql(statement)
    for table in ("tenant_ai_credentials", "tenant_recording_policy", "tenant_telephony_credentials"):
        policies = conn.execute(text("SELECT polname FROM pg_policy WHERE polrelid=to_regclass(:rel)"), dict(rel="public." + table)).scalars().all()
        for policy in policies:
            conn.exec_driver_sql(f'DROP POLICY "{policy}" ON public.{table}')
        conn.exec_driver_sql(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"CREATE POLICY {table}_tenant_isolation ON public.{table} FOR ALL USING ({migration.TENANT_POLICY}) WITH CHECK ({migration.TENANT_POLICY})")


def test_fresh_0082_missing_contract_reproduced_then_repaired(database):
    engine, _, port, name = database
    with engine.begin() as conn:
        assert all(conn.execute(text("SELECT to_regclass(:rel)"), dict(rel="public." + table)).scalar() is None
                   for table in migration.TABLES)
        assert conn.execute(text("SELECT count(*) FROM pg_attribute WHERE attrelid='public.tenants'::regclass AND attname='active_telephony_provider'")).scalar() == 0
        upgrade(conn)
        seed(conn)
        conn.execute(text("INSERT INTO tenant_ai_credentials (tenant_id,provider,encrypted_key) VALUES (:tenant,'groq','synthetic-ciphertext')"), dict(tenant=TENANT_A))
        conn.execute(text("INSERT INTO tenant_recording_policy (tenant_id) VALUES (:tenant)"), dict(tenant=TENANT_A))
        conn.execute(text("INSERT INTO tenant_telephony_credentials (tenant_id,provider,credentials_encrypted) VALUES (:tenant,'vonage','synthetic-ciphertext')"), dict(tenant=TENANT_A))
        conn.exec_driver_sql("GRANT USAGE ON SCHEMA public TO support_runtime")
        for table in migration.TABLES:
            conn.exec_driver_sql(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {table} TO support_runtime")
    runtime = create_engine(f"postgresql://support_runtime@127.0.0.1:{port}/{name}")
    try:
        for tenant, count in ((TENANT_A, 1), (TENANT_B, 0), ("", 0)):
            with runtime.begin() as conn:
                conn.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), dict(tenant=tenant))
                for table in ("tenant_ai_credentials", "tenant_recording_policy", "tenant_telephony_credentials"):
                    assert conn.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar() == count
        with pytest.raises(DBAPIError):
            with runtime.begin() as conn:
                conn.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), dict(tenant=TENANT_B))
                conn.execute(text("INSERT INTO tenant_recording_policy(tenant_id) VALUES (:tenant)"), dict(tenant=TENANT_A))
    finally:
        runtime.dispose()
    with engine.begin() as conn:
        upgrade(conn)
        conn.exec_driver_sql("SET LOCAL app.bypass_rls='true'")
        assert conn.exec_driver_sql("SELECT default_consent_mode,retention_days FROM tenant_recording_policy").one() == ("two_party", 90)
        assert conn.exec_driver_sql("SELECT active_telephony_provider FROM tenants LIMIT 1").scalar() == "none"


def test_known_existing_contract_preserves_data_oid_policy_acl(database):
    engine, _, _, _ = database
    with engine.begin() as conn:
        legacy(conn)
        seed(conn)
        conn.execute(text("INSERT INTO tenant_ai_credentials(tenant_id,provider,encrypted_key,status) VALUES (:tenant,'groq','history-1','disabled'),(:tenant,'groq','history-2','disabled')"), dict(tenant=TENANT_A))
        conn.exec_driver_sql("GRANT SELECT ON tenant_ai_credentials TO support_runtime")
        before = conn.exec_driver_sql("SELECT c.oid,c.relname,c.relacl::text,p.oid,p.polname FROM pg_class c LEFT JOIN pg_policy p ON p.polrelid=c.oid WHERE c.relnamespace='public'::regnamespace ORDER BY c.oid,p.oid").all()
        upgrade(conn)
        after = conn.exec_driver_sql("SELECT c.oid,c.relname,c.relacl::text,p.oid,p.polname FROM pg_class c LEFT JOIN pg_policy p ON p.polrelid=c.oid WHERE c.relnamespace='public'::regnamespace ORDER BY c.oid,p.oid").all()
        assert before == after
        assert conn.exec_driver_sql("SELECT encrypted_key FROM tenant_ai_credentials ORDER BY encrypted_key").scalars().all() == ["history-1", "history-2"]


@pytest.mark.parametrize("drift,fragment", [
    ("ALTER TABLE tenant_ai_credentials ADD COLUMN unexpected text", "columns/defaults"),
    ("ALTER TABLE tenant_recording_policy ALTER COLUMN retention_days SET DEFAULT 3650", "columns/defaults"),
    ("ALTER TABLE tenant_recording_policy DROP CONSTRAINT tenant_recording_policy_retention_days_check", "constraints"),
    ("DROP INDEX uq_tenant_ai_credentials_active", "indexes"),
    ("ALTER TABLE tenant_telephony_credentials DROP CONSTRAINT tenant_telephony_credentials_tenant_id_fkey", "foreign keys"),
    ("ALTER TABLE tenant_recording_policy NO FORCE ROW LEVEL SECURITY", "ownership/RLS"),
    ("CREATE POLICY unreviewed ON tenant_recording_policy USING (true)", "RLS policy semantics"),
    ("ALTER FUNCTION tenant_recording_policy_touch_updated_at() SECURITY DEFINER", "function body/ownership"),
    ("ALTER TABLE tenant_ai_credentials DISABLE TRIGGER trg_tenant_ai_credentials_updated_at", "trigger behavior"),
    ("ALTER TABLE tenants ALTER COLUMN active_telephony_provider SET DEFAULT 'sip'", "active provider column/default"),
])
def test_unknown_drift_refuses_atomically(database, drift, fragment):
    engine, _, _, _ = database
    with engine.begin() as conn:
        legacy(conn)
        conn.exec_driver_sql(drift)
        # Prove validation happens before creating another missing table.
        conn.exec_driver_sql("DROP TABLE emergency_access_requests")
    with pytest.raises(RuntimeError, match=fragment):
        with engine.begin() as conn:
            upgrade(conn)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT to_regclass('public.emergency_access_requests')").scalar() is None


def test_mixed_owner_refused_without_changing_ownership(database):
    engine, _, port, name = database
    with engine.begin() as conn:
        legacy(conn)
    admin_db = create_engine(f"postgresql://fixture_admin@127.0.0.1:{port}/{name}")
    try:
        with admin_db.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE tenant_recording_policy OWNER TO support_other")
        with pytest.raises((RuntimeError, DBAPIError)):
            with engine.begin() as conn:
                upgrade(conn)
        with admin_db.connect() as conn:
            assert conn.exec_driver_sql("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.tenant_recording_policy'::regclass").scalar() == "support_other"
    finally:
        admin_db.dispose()


def test_emergency_scrub_removes_only_plaintext_code_preserving_order(database):
    engine, _, _, _ = database
    approvals = [
        {"approver_id": "synthetic-1", "method": "totp", "verification_code": "012345", "approved_at": "2026-01-01"},
        {"approver_id": "synthetic-2", "method": "totp", "verification": "totp_step_up", "extra": {"preserve": True}},
    ]
    with engine.begin() as conn:
        legacy(conn)
        conn.exec_driver_sql("SET LOCAL app.bypass_rls='true'")
        actor = conn.exec_driver_sql("INSERT INTO user_profiles(email) VALUES('synthetic-support@example.invalid') RETURNING id").scalar()
        request = conn.execute(text("""INSERT INTO emergency_access_requests
            (requestor_id,scenario,justification,requested_access,approvals,status,expires_at,review_notes)
            VALUES(:actor,'security_incident','Synthetic private incident',ARRAY['existing:permission'],
                   CAST(:approvals AS jsonb),'approved',now()+interval '1 hour','Preserve review') RETURNING request_id"""),
            dict(actor=actor, approvals=json.dumps(approvals))).scalar()
        before = conn.execute(text("SELECT to_jsonb(r)-'approvals' FROM emergency_access_requests r WHERE request_id=:id"), dict(id=request)).scalar()
        upgrade(conn)
        upgrade(conn)
        after, remaining = conn.execute(text("SELECT to_jsonb(r)-'approvals',approvals FROM emergency_access_requests r WHERE request_id=:id"), dict(id=request)).one()
        assert before == after
        assert remaining == [{k: v for k, v in item.items() if k != "verification_code"} for item in approvals]
        assert "verification" not in remaining[0]  # Never authorize an old unverified approval.


def test_non_array_approval_evidence_refuses_without_coercion(database):
    engine, _, _, _ = database
    with engine.begin() as conn:
        legacy(conn)
        conn.exec_driver_sql("SET LOCAL app.bypass_rls='true'")
        actor = conn.exec_driver_sql("INSERT INTO user_profiles(email) VALUES('synthetic-bad-shape@example.invalid') RETURNING id").scalar()
        conn.execute(text("""INSERT INTO emergency_access_requests
            (requestor_id,scenario,justification,requested_access,approvals,expires_at)
            VALUES(:actor,'security_incident','Synthetic incident',ARRAY['existing:permission'],
                   '"unknown-format"'::jsonb,now())"""), dict(actor=actor))
    with pytest.raises(RuntimeError, match="non-array approvals"):
        with engine.begin() as conn:
            upgrade(conn)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT approvals FROM emergency_access_requests").scalar() == "unknown-format"


def test_downgrade_never_erases_support_evidence():
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


def test_reviewed_preserved_schema_matches_live_metadata_fingerprints(database):
    engine, _, _, _ = database
    grants = module("native_preserved_grants", Path(__file__).parents[1] / "runtime_grants.py")
    preserved = module("native_preserved_fixture", Path(__file__).with_name("preserved_inventory_acceptance.py"))
    profile = grants.select_profile(grants.PRESERVED_PROFILE["profile"])
    with engine.begin() as conn:
        conn.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
        upgrade(conn)
        conn.exec_driver_sql(preserved.schema_sql(), execution_options={"no_parameters": True})
        cursor = conn.connection.driver_connection.cursor()
        try:
            grants.inspect_preserved(cursor, profile)
            assert conn.exec_driver_sql("SELECT count(*) FROM pg_tables WHERE schemaname='public'").scalar() == 123
            conn.exec_driver_sql("GRANT SELECT(tenant_id) ON tenant_ai_configs_backup_20260907 TO PUBLIC,support_runtime")
            assert conn.exec_driver_sql("""SELECT count(*) FROM pg_attribute a,LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid='public.tenant_ai_configs_backup_20260907'::regclass
                AND x.grantee IN (0,(SELECT oid FROM pg_roles WHERE rolname='support_runtime'))""").scalar() == 2
            conn.exec_driver_sql("REVOKE ALL ON TABLE tenant_ai_configs_backup_20260907 FROM PUBLIC,support_runtime")
            assert conn.exec_driver_sql("""SELECT count(*) FROM pg_attribute a,LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid='public.tenant_ai_configs_backup_20260907'::regclass
                AND x.grantee IN (0,(SELECT oid FROM pg_roles WHERE rolname='support_runtime'))""").scalar() == 0
            conn.exec_driver_sql("ALTER TABLE worker_status ALTER COLUMN status SET DEFAULT 'unknown'")
            with pytest.raises(grants.GrantsError, match="structural fingerprint"):
                grants.inspect_preserved(cursor, profile)
        finally:
            cursor.close()

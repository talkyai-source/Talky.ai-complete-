"""An empty PostgreSQL database plus this repository gives a working product.

Real PostgreSQL, CI only. Needs an explicit loopback, test-named TEST_DATABASE_URL
whose user may CREATE DATABASE and CREATE ROLE (the "Migrated inbound database
integration" step of .github/workflows/ci.yml). Everything runs in one throw-away
database with a unique name that is dropped in ``finally``.

The database is built the documented way (backend/database/MIGRATIONS.md and
backend/docs/clean-install-runbook.md): load complete_schema.sql, stamp
0008_tenant_voice_tuning, upgrade to head. A NOSUPERUSER NOBYPASSRLS runtime role
then receives the grants of backend/database/runtime_role_grants.sql, the one copy the
runbook points to.

The runtime role is entered with SET ROLE on the provisioning connection (the way
tests/helpers/support_db.py does it): the role is a real login-capable role without
SUPERUSER or BYPASSRLS, so row security is enforced exactly as for it, and no
password or pg_hba entry is needed.

NOT covered here, on purpose: a sign-up through ``signup_complete``. It needs a live
Redis (verification-code payload and Lua claim scripts), the legal-acceptance writer, the
session/JWT issuers and the SIP-trunk seeder, and a test that stubs all of those would not
prove the database. The first platform administrator IS covered (last test).
"""
from __future__ import annotations

import asyncio
import os
import re
import runpy
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from app.core.db_utils import acquire_with_tenant
from app.core.security.rbac import ROLE_DEFAULT_PERMISSIONS, UserRole
from tests.integration.test_support_migration import _alembic, _repository_head

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
GRANTS_FILE = BACKEND / "database" / "runtime_role_grants.sql"
RUNBOOK = BACKEND / "docs" / "clean-install-runbook.md"
GRANTS_ROLE_TOKEN = "talky_runtime"  # the role name inside runtime_role_grants.sql

# The runbook's "first platform administrator" statement. test_runbook_keeps_one_copy
# asserts the runbook contains it verbatim.
PROMOTE_SQL = (
    "UPDATE user_profiles SET role = 'platform_admin' "
    "WHERE lower(email) = lower($1) AND is_active AND is_verified"
)

# Tables the plans the product sells live in (migrations 0071, 0073, 0083).
SOLD_PLANS = {"basic", "business", "payg", "payment_test"}

# The tenant SQL inventory only lists statements it can parse as SQL text and only for
# tables of the tenant-scoped set, so these names are added by hand: the cost ledger is
# written with COPY (provider_cost_ledger.py copy_records_to_table), and the rest are
# read by the sign-up, RBAC and billing code without a tenant predicate.
EXTRA_TABLES = {
    "tenant_provider_cost_events", "tenants", "plans", "roles", "permissions",
    "role_permissions", "platform_runtime_controls", "alembic_version",
}


def admin_dsn() -> str:
    """Copied from tests/integration/test_bootstrap_manual_parity.py."""
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("not run on PostgreSQL: explicit TEST_DATABASE_URL is required")
    parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
    if parsed.scheme != "postgresql" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("clean install requires a loopback PostgreSQL test DSN", pytrace=False)
    if parsed.query or parsed.fragment:
        pytest.fail("clean install DSN must not contain connection-routing overrides", pytrace=False)
    if not re.search(r"(?:^|[_-])(?:test|ci|tmp)(?:[_-]|$)", parsed.path.lstrip("/"), re.I):
        pytest.fail("clean install requires a test-named provisioning database", pytrace=False)
    return urlunsplit(parsed)


async def _owner_tx(dsn, work):
    """Run ``work(conn)`` inside one transaction with the internal RLS bypass on."""
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            return await work(conn)
    finally:
        await conn.close()


async def _build(admin, name):
    await admin.execute(f'CREATE DATABASE "{name}"')
    parts = urlsplit(admin_dsn())
    target = urlunsplit(parts._replace(path=f"/{name}"))
    conn = await asyncpg.connect(target, timeout=10)
    try:
        await conn.execute((BACKEND / "database" / "complete_schema.sql").read_text(encoding="utf-8"))
    finally:
        await conn.close()
    return target


@pytest.fixture(scope="module")
def clean_db():
    """The documented clean-install build, once per module (sync: pytest-asyncio 0.23 loop scopes)."""
    dsn = admin_dsn()
    name = f"qa_clean_install_{uuid.uuid4().hex}_test"
    assert re.fullmatch(r"qa_clean_install_[a-f0-9]{32}_test", name)
    role = f"talky_rt_{uuid.uuid4().hex[:12]}"
    loop = asyncio.new_event_loop()
    admin = loop.run_until_complete(asyncpg.connect(dsn, timeout=10))
    created_db = created_role = False
    try:
        target = loop.run_until_complete(_build(admin, name))
        created_db = True
        for step in (("stamp", "0008_tenant_voice_tuning"), ("upgrade", "head")):
            result = _alembic(target, *step)
            assert result.returncode == 0, result.stderr[-3000:]

        async def provision():
            conn = await asyncpg.connect(target, timeout=10)
            try:
                await conn.execute(
                    f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE'
                )
                grants = GRANTS_FILE.read_text(encoding="utf-8").replace(GRANTS_ROLE_TOKEN, f'"{role}"')
                await conn.execute(grants)
            finally:
                await conn.close()

        loop.run_until_complete(provision())
        created_role = True
        ids = SimpleNamespace(
            a=uuid.uuid4(), b=uuid.uuid4(), c=uuid.uuid4(),
            campaign_a=uuid.uuid4(), campaign_b=uuid.uuid4(),
            lead_a=uuid.uuid4(), lead_b=uuid.uuid4(),
            call_a=uuid.uuid4(), call_b=uuid.uuid4(),
        )

        async def seed(conn):
            for tenant, label in ((ids.a, "A"), (ids.b, "B"), (ids.c, "C")):
                await conn.execute("INSERT INTO tenants (id, business_name) VALUES ($1, $2)",
                                   tenant, f"Clean install tenant {label}")
            for tenant, campaign, lead, call, phone in (
                (ids.a, ids.campaign_a, ids.lead_a, ids.call_a, "+14155552671"),
                (ids.b, ids.campaign_b, ids.lead_b, ids.call_b, "+14155552672"),
            ):
                await conn.execute("INSERT INTO campaigns (id, tenant_id, name) VALUES ($1, $2, 'seed campaign')",
                                   campaign, tenant)
                await conn.execute(
                    "INSERT INTO leads (id, tenant_id, campaign_id, phone_number) VALUES ($1, $2, $3, $4)",
                    lead, tenant, campaign, phone)
                await conn.execute(
                    "INSERT INTO calls (id, tenant_id, campaign_id, lead_id, phone_number, status, direction) "
                    "VALUES ($1, $2, $3, $4, $5, 'in_progress', 'outbound')",
                    call, tenant, campaign, lead, phone)
            await conn.execute(
                "INSERT INTO tenant_recording_policy (tenant_id, default_consent_mode) VALUES ($1, 'one_party'), ($2, 'two_party')",
                ids.a, ids.b)

        loop.run_until_complete(_owner_tx(target, seed))
        yield SimpleNamespace(dsn=target, role=role, ids=ids, name=name)
    finally:
        try:
            if created_db:
                loop.run_until_complete(admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            if created_role:
                loop.run_until_complete(admin.execute(f'DROP ROLE IF EXISTS "{role}"'))
        finally:
            loop.run_until_complete(admin.close())
            loop.close()


@asynccontextmanager
async def runtime_pool(db):
    async def setup(conn):
        await conn.execute(f'SET ROLE "{db.role}"')

    pool = await asyncpg.create_pool(db.dsn, min_size=1, max_size=2, timeout=10, setup=setup)
    try:
        yield pool
    finally:
        await pool.close()


async def _owner(db):
    return await asyncpg.connect(db.dsn, timeout=10)


# -- a. the documented build ---------------------------------------------------------------


async def test_a_alembic_version_equals_the_repository_head(clean_db):
    conn = await _owner(clean_db)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == _repository_head()
    finally:
        await conn.close()


# -- b. seeds ------------------------------------------------------------------------------


async def test_b_seeds_plans_roles_permissions_and_runtime_controls(clean_db):
    conn = await _owner(clean_db)
    try:
        plans = {r["id"] for r in await conn.fetch("SELECT id FROM plans")}
        assert SOLD_PLANS <= plans, f"missing plans: {SOLD_PLANS - plans}"

        roles = {r["name"] for r in await conn.fetch("SELECT name FROM roles")}
        assert {r.value for r in UserRole} <= roles

        permissions = {r["name"] for r in await conn.fetch("SELECT name FROM permissions")}
        every_code_permission = {p.value for perms in ROLE_DEFAULT_PERMISSIONS.values() for p in perms}
        assert every_code_permission <= permissions, f"permissions the code uses but no migration seeds: {every_code_permission - permissions}"
        rows = await conn.fetch(
            """SELECT r.name AS role, p.name AS permission
                 FROM role_permissions rp JOIN roles r ON r.id = rp.role_id
                 JOIN permissions p ON p.id = rp.permission_id""")
        granted: dict[str, set[str]] = {}
        for row in rows:
            granted.setdefault(row["role"], set()).add(row["permission"])
        for role, wanted in ROLE_DEFAULT_PERMISSIONS.items():
            assert granted.get(role.value, set()) == {p.value for p in wanted}, role.value

        assert await conn.fetchval("SELECT count(*) FROM platform_runtime_controls WHERE id = 1") == 1
    finally:
        await conn.close()


# -- c. every table the application reads or writes exists ---------------------------------


async def test_c_every_table_the_application_uses_exists(clean_db):
    # Reuses the repository's static SQL inventory (scripts/tenant_sql_inventory.py, the one
    # tests/unit/test_tenant_sql_ratchet.py loads): the tables of every SQL statement under app/.
    inventory = runpy.run_path(str(BACKEND / "scripts" / "tenant_sql_inventory.py"))
    findings = inventory["scan_repository"](BACKEND)
    used = {table for finding in findings for table in finding["tables"]} | EXTRA_TABLES
    assert "tenant_recording_policy" in used and "tenant_provider_cost_events" in used
    conn = await _owner(clean_db)
    try:
        existing = {r["n"] for r in await conn.fetch(
            "SELECT relname AS n FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r','v','m','p')")}
    finally:
        await conn.close()
    # Without migration 0099 this is exactly {tenant_recording_policy, tenant_provider_cost_events}.
    assert sorted(used - existing) == []


# -- d. row security -----------------------------------------------------------------------


async def test_d_every_tenant_table_has_forced_row_security_and_a_policy(clean_db):
    conn = await _owner(clean_db)
    try:
        # The posture query of scripts/verify_rls.py (no exception list exists there).
        rows = await conn.fetch("""
            SELECT c.relname AS t, c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced,
                   (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS pols,
                   EXISTS (SELECT 1 FROM pg_attribute a
                            WHERE a.attrelid=c.oid AND a.attname='tenant_id'
                              AND a.attnum>0 AND NOT a.attisdropped) AS has_tenant
            FROM pg_class c
            WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'
            ORDER BY c.relname""")
    finally:
        await conn.close()
    tenant_tables = [r for r in rows if r["has_tenant"]]
    assert tenant_tables
    assert [r["t"] for r in tenant_tables if not r["enabled"]] == []
    assert [r["t"] for r in tenant_tables if not r["forced"]] == []
    assert [r["t"] for r in tenant_tables if r["pols"] == 0] == []
    by_name = {r["t"]: r for r in rows}
    for table in ("tenant_recording_policy", "tenant_provider_cost_events"):
        assert by_name[table]["enabled"] and by_name[table]["forced"] and by_name[table]["pols"] == 1, table


# -- e. a runtime role without SUPERUSER or BYPASSRLS --------------------------------------


TENANT_TABLES = ("campaigns", "leads", "calls", "tenant_recording_policy")


async def test_e1_the_runtime_role_cannot_bypass_row_security(clean_db):
    async with runtime_pool(clean_db) as pool:
        async with pool.acquire() as conn:
            flags = await conn.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            assert tuple(flags.values()) == (False, False)
            assert await conn.fetchval("SELECT current_user") == clean_db.role


async def test_e2_a_tenant_sees_only_its_own_rows(clean_db):
    ids = clean_db.ids
    async with runtime_pool(clean_db) as pool:
        async with acquire_with_tenant(pool, str(ids.a)) as conn:
            for table in TENANT_TABLES:
                visible = {r["tenant_id"] for r in await conn.fetch(f"SELECT DISTINCT tenant_id FROM {table}")}
                assert visible == {ids.a}, table
                assert await conn.fetchval(f"SELECT count(*) FROM {table} WHERE tenant_id = $1", ids.b) == 0, table
        async with acquire_with_tenant(pool, str(ids.b)) as conn:
            for table in TENANT_TABLES:
                visible = {r["tenant_id"] for r in await conn.fetch(f"SELECT DISTINCT tenant_id FROM {table}")}
                assert visible == {ids.b}, table
        # No tenant context at all: nothing is visible.
        async with pool.acquire() as conn:
            for table in TENANT_TABLES:
                assert await conn.fetchval(f"SELECT count(*) FROM {table}") == 0, table


async def test_e3_a_tenant_cannot_insert_into_or_update_another_tenant(clean_db):
    ids = clean_db.ids
    inserts = {
        "campaigns": ("INSERT INTO campaigns (id, tenant_id, name) VALUES ($1, $2, 'planted')",
                      (uuid.uuid4(), ids.b)),
        "leads": ("INSERT INTO leads (id, tenant_id, campaign_id, phone_number) VALUES ($1, $2, $3, '+14155552673')",
                  (uuid.uuid4(), ids.b, ids.campaign_b)),
        "calls": ("INSERT INTO calls (id, tenant_id, campaign_id, lead_id, phone_number, status, direction) "
                  "VALUES ($1, $2, $3, $4, '+14155552672', 'in_progress', 'outbound')",
                  (uuid.uuid4(), ids.b, ids.campaign_b, ids.lead_b)),
        "tenant_recording_policy": ("INSERT INTO tenant_recording_policy (tenant_id) VALUES ($1)", (ids.c,)),
    }
    async with runtime_pool(clean_db) as pool:
        for table, (sql, args) in inserts.items():
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with acquire_with_tenant(pool, str(ids.a)) as conn:
                    await conn.execute(sql, *args)
        for table in TENANT_TABLES:
            async with acquire_with_tenant(pool, str(ids.a)) as conn:
                # Another tenant's rows match nothing visible, so nothing is updated.
                status = await conn.execute(f"UPDATE {table} SET tenant_id = tenant_id WHERE tenant_id = $1", ids.b)
                assert status == "UPDATE 0", table
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with acquire_with_tenant(pool, str(ids.a)) as conn:
                await conn.execute("UPDATE tenant_recording_policy SET tenant_id = $1 WHERE tenant_id = $2",
                                   ids.b, ids.a)
    owner = await _owner(clean_db)
    try:
        await owner.execute("SET app.bypass_rls = 'on'")
        assert await owner.fetchval("SELECT count(*) FROM campaigns WHERE name = 'planted'") == 0
        assert await owner.fetchval("SELECT count(*) FROM leads WHERE phone_number = '+14155552673'") == 0
        assert await owner.fetchval("SELECT count(*) FROM tenant_recording_policy WHERE tenant_id = $1", ids.c) == 0
        assert await owner.fetchval(
            "SELECT default_consent_mode FROM tenant_recording_policy WHERE tenant_id = $1", ids.b) == "two_party"
    finally:
        await owner.close()


async def test_e4_a_tenant_saves_and_reads_its_recording_policy(clean_db):
    ids = clean_db.ids
    # The statement of put_recording_policy (app/api/v1/endpoints/recordings.py) and the read of
    # RecordingPolicyService._load_row (app/domain/services/recording_policy_service.py).
    save = """
        INSERT INTO tenant_recording_policy (
            tenant_id, default_consent_mode, announcement_text, opt_out_dtmf_digit,
            two_party_country_codes, retention_days, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5::text[], $6, NOW(), NOW())
        ON CONFLICT (tenant_id) DO UPDATE SET
            default_consent_mode = EXCLUDED.default_consent_mode,
            announcement_text = EXCLUDED.announcement_text,
            opt_out_dtmf_digit = EXCLUDED.opt_out_dtmf_digit,
            two_party_country_codes = EXCLUDED.two_party_country_codes,
            retention_days = EXCLUDED.retention_days,
            updated_at = NOW()
        RETURNING *"""
    async with runtime_pool(clean_db) as pool:
        for mode, days in (("two_party", 30), ("one_party", 45)):  # insert path, then ON CONFLICT path
            async with acquire_with_tenant(pool, str(ids.a)) as conn:
                row = await conn.fetchrow(save, ids.a, mode, "Recorded for quality.", "9", ["GB", "US-CA"], days)
            assert row["default_consent_mode"] == mode and row["retention_days"] == days
        async with acquire_with_tenant(pool, str(ids.a)) as conn:
            row = await conn.fetchrow("SELECT * FROM tenant_recording_policy WHERE tenant_id = $1", ids.a)
        assert (row["default_consent_mode"], row["retention_days"]) == ("one_party", 45)
        assert row["two_party_country_codes"] == ["GB", "US-CA"]
        async with acquire_with_tenant(pool, str(ids.b)) as conn:
            assert await conn.fetchrow("SELECT * FROM tenant_recording_policy WHERE tenant_id = $1", ids.a) is None


async def test_e5_the_cost_ledger_flushes_as_the_runtime_role(clean_db):
    from app.domain.services import provider_cost_ledger as ledger

    ids = clean_db.ids
    ledger._buffer.clear()
    ledger.record(ledger.CostEvent(tenant_id=str(ids.a), provider="groq", provider_role="llm",
                                   unit="tokens_in", quantity=10.0))
    async with runtime_pool(clean_db) as pool:
        assert await ledger._flush_once(pool) == 1
    assert ledger.buffer_size() == 0
    owner = await _owner(clean_db)
    try:
        await owner.execute("SET app.bypass_rls = 'on'")
        assert await owner.fetchval(
            "SELECT count(*) FROM tenant_provider_cost_events WHERE tenant_id = $1", ids.a) == 1
    finally:
        await owner.close()


# -- f. the first platform administrator ---------------------------------------------------


async def test_f_the_documented_first_administrator_passes_the_platform_admin_check(clean_db):
    from fastapi import HTTPException

    from app.api.v1.dependencies import CurrentUser, require_platform_admin
    from app.core.security.principal import PrincipalUnavailable, load_current_principal

    user_id, email = uuid.uuid4(), f"first-admin-{uuid.uuid4().hex[:8]}@example.invalid"

    async def create(conn):
        # A verified, active account with NO tenant and NO membership, as sign-up on a
        # database with no tenants yet would leave it before the promotion below.
        await conn.execute(
            """INSERT INTO user_profiles (id, email, name, tenant_id, role, is_active, is_verified, email_verified_at)
               VALUES ($1, $2, 'First admin', NULL, 'user', TRUE, TRUE, now())""", user_id, email)

    await _owner_tx(clean_db.dsn, create)
    async with runtime_pool(clean_db) as pool:
        # Before the promotion a tenant-less 'user' has no principal at all.
        async with acquire_with_tenant(pool, None) as conn:
            with pytest.raises(PrincipalUnavailable):
                await load_current_principal(conn, str(user_id))

    async def promote(conn):
        return await conn.execute(PROMOTE_SQL, email)

    assert await _owner_tx(clean_db.dsn, promote) == "UPDATE 1"

    async with runtime_pool(clean_db) as pool:
        async with acquire_with_tenant(pool, None) as conn:
            principal = await load_current_principal(conn, str(user_id))
    assert principal["role"] == "platform_admin"
    admin = CurrentUser(id=str(user_id), email=email, role=principal["role"], tenant_id=None, name="First admin")
    assert await require_platform_admin(current_user=admin) is admin
    with pytest.raises(HTTPException) as denied:
        await require_platform_admin(current_user=CurrentUser(
            id=str(uuid.uuid4()), email="x@example.invalid", role="tenant_admin", tenant_id=str(clean_db.ids.a), name="x"))
    assert denied.value.status_code == 403


def test_runbook_keeps_one_copy_of_the_grants_and_the_promotion():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "database/runtime_role_grants.sql" in runbook
    assert PROMOTE_SQL.replace("$1", "'<email>'") in runbook
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public" in GRANTS_FILE.read_text(encoding="utf-8")

"""0096 and 0105 on disposable loopback PostgreSQL: the contact list permission confirmation."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.test_touch_trigger_repair_pg import database, load  # noqa: F401

pytestmark = pytest.mark.integration
LINEAGE = (
    "CREATE TABLE user_profiles (id uuid PRIMARY KEY)",
    """CREATE TABLE contact_lists (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), campaign_id uuid NOT NULL, tenant_id uuid,
        name text NOT NULL, source text NOT NULL DEFAULT 'manual', is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now())""",
)
CONFIRMED_AT = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
GOOD = {"consent_confirmed_at": CONFIRMED_AT, "consent_basis": "express_consent",
        "consent_source": "Signed web form, Oct 2026", "consent_permission_date": date(2026, 11, 1),
        "consent_statement_version": "v1"}


def migration():
    module = load("0096_contact_list_permission")
    assert (module.revision, module.down_revision) == ("0096_contact_list_permission", "0095_webhook_receipt_dismissed")
    return module


def migration_0105():
    module = load("0105_contact_list_consent_complete")
    assert (module.revision, module.down_revision) == ("0105_list_consent_complete", "0104_paypal_review_dismissed")
    return module


async def run_0105(conn, method="upgrade"):
    """Run the statements of 0105 on one connection in one transaction; return the server notices."""
    module = migration_0105()
    statements, notices = [], []
    module.op = SimpleNamespace(execute=statements.append)
    getattr(module, method)()
    conn.add_log_listener(lambda _c, message: notices.append(message))
    async with conn.transaction():
        for statement in statements:
            await conn.execute(statement)
    return notices


async def insert(conn, **consent):
    columns = ["campaign_id", "name", *consent]
    marks = ", ".join(f"${i + 1}" for i in range(len(columns)))
    return await conn.fetchval(f"INSERT INTO contact_lists ({', '.join(columns)}) VALUES ({marks}) RETURNING id",
                               uuid4(), "list", *consent.values())


async def migrated(database):  # noqa: F811
    await database.build(LINEAGE)
    await database.apply(migration())
    await database.apply(migration_0105())


async def test_every_column_null_and_a_complete_confirmation_are_accepted(database):  # noqa: F811
    await migrated(database)
    async with database.pool.acquire() as conn:
        await insert(conn)
        await insert(conn, **GOOD, consent_confirmed_by=None)
        user = uuid4()
        await conn.execute("INSERT INTO user_profiles VALUES ($1)", user)
        await insert(conn, **GOOD, consent_confirmed_by=user)
        for basis in ("existing_customer", "made_an_enquiry", "other_documented", "pre_existing_unverified"):
            await insert(conn, **{**GOOD, "consent_basis": basis})
        # permission date equal to the confirmation date is allowed
        await insert(conn, **{**GOOD, "consent_permission_date": date(2026, 11, 16)})


@pytest.mark.parametrize("override", [
    {"consent_confirmed_at": None},                                  # basis etc. without a timestamp
    {"consent_source": None},                                        # timestamp without a source
    {"consent_source": "abcd"},                                      # four characters
    {"consent_source": "     abc     "},                             # short once trimmed
    {"consent_source": "x" * 501},
    {"consent_basis": "because"},                                    # unknown basis
    {"consent_basis": None},
    {"consent_permission_date": date(2026, 11, 17)},                 # after the confirmation date
    {"consent_permission_date": None},
    {"consent_statement_version": ""},                               # empty version
    {"consent_statement_version": "v" * 41},
    {"consent_statement_version": None},
])
async def test_partial_or_invalid_confirmations_are_rejected(database, override):  # noqa: F811
    await migrated(database)
    async with database.pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(conn, **{**GOOD, **override})


async def test_a_lone_user_reference_without_a_confirmation_is_rejected(database):  # noqa: F811
    await migrated(database)
    async with database.pool.acquire() as conn:
        user = uuid4()
        await conn.execute("INSERT INTO user_profiles VALUES ($1)", user)
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(conn, consent_confirmed_by=user)


async def test_deleting_the_confirming_user_keeps_the_confirmation(database):  # noqa: F811
    await migrated(database)
    async with database.pool.acquire() as conn:
        user = uuid4()
        await conn.execute("INSERT INTO user_profiles VALUES ($1)", user)
        list_id = await insert(conn, **GOOD, consent_confirmed_by=user)
        await conn.execute("DELETE FROM user_profiles WHERE id = $1", user)
        row = await conn.fetchrow("SELECT * FROM contact_lists WHERE id = $1", list_id)
        assert row["consent_confirmed_by"] is None
        assert row["consent_confirmed_at"] == CONFIRMED_AT and row["consent_basis"] == "express_consent"


async def test_upgrade_twice_is_harmless_and_existing_lists_stay_unconfirmed(database):  # noqa: F811
    await database.build(LINEAGE)
    async with database.pool.acquire() as conn:
        old = await conn.fetchval("INSERT INTO contact_lists (campaign_id, name) VALUES ($1, 'old') RETURNING id", uuid4())
    await database.apply(migration())
    await database.apply(migration())
    async with database.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM contact_lists WHERE id = $1", old)
        assert all(row[c] is None for c in row.keys() if c.startswith("consent_"))
        assert await conn.fetchval("""SELECT count(*) FROM pg_constraint
            WHERE conrelid = 'contact_lists'::regclass AND conname = 'contact_lists_consent_complete'""") == 1
        assert await conn.fetchval("""SELECT convalidated FROM pg_constraint
            WHERE conname = 'contact_lists_consent_complete'""") is True


async def test_downgrade_refuses_while_a_confirmation_exists_and_succeeds_when_none(database):  # noqa: F811
    await migrated(database)
    async with database.pool.acquire() as conn:
        confirmed = await insert(conn, **GOOD)
        await insert(conn)
    with pytest.raises(Exception, match="refuses to drop contact list permission"):
        await database.apply(migration(), "downgrade")
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("SELECT consent_basis FROM contact_lists WHERE id = $1", confirmed) == "express_consent"
        await conn.execute("DELETE FROM contact_lists WHERE id = $1", confirmed)
    await database.apply(migration(), "downgrade")
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("""SELECT count(*) FROM information_schema.columns
            WHERE table_name = 'contact_lists' AND starts_with(column_name, 'consent_')""") == 0
        assert await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conname = 'contact_lists_consent_complete'") == 0


INCOMPLETE = {**GOOD, "consent_source": None}  # allowed by the 0096 rule, refused by 0105
OLD_RULE_ONLY = ({"consent_source": None}, {"consent_basis": None}, {"consent_statement_version": None})


async def test_a_row_written_before_0105_does_not_fail_it_and_new_ones_are_refused(database):  # noqa: F811
    await database.build(LINEAGE)
    await database.apply(migration())
    async with database.pool.acquire() as conn:
        bad = await insert(conn, **INCOMPLETE)  # the old constraint accepts it
        await insert(conn, **GOOD)
        notices = await run_0105(conn)  # must not raise
        assert any("1 contact list(s) hold an incomplete permission confirmation" in getattr(n, "message", str(n)) for n in notices)
        assert await conn.fetchval(
            "SELECT convalidated FROM pg_constraint WHERE conname = 'contact_lists_consent_complete'") is False
        assert await conn.fetchval("SELECT consent_confirmed_at FROM contact_lists WHERE id = $1", bad) == CONFIRMED_AT
        for override in OLD_RULE_ONLY:  # each refused statement runs in its own transaction
            with pytest.raises(asyncpg.CheckViolationError):
                await insert(conn, **{**GOOD, **override})
        await insert(conn, **GOOD)


async def test_0105_applied_through_alembic_succeeds_with_a_bad_row_present(database):  # noqa: F811
    await database.build(LINEAGE)
    await database.apply(migration())
    async with database.pool.acquire() as conn:
        await insert(conn, **INCOMPLETE)
    await database.apply(migration_0105())
    async with database.pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT convalidated FROM pg_constraint WHERE conname = 'contact_lists_consent_complete'") is False


async def test_without_a_bad_row_the_constraint_ends_validated(database):  # noqa: F811
    await migrated(database)
    async with database.pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT convalidated FROM pg_constraint WHERE conname = 'contact_lists_consent_complete'") is True
        assert await conn.fetchval("""SELECT count(*) FROM pg_constraint
            WHERE conrelid = 'contact_lists'::regclass AND conname = 'contact_lists_consent_complete'""") == 1


async def test_0105_downgrade_puts_the_0096_rule_back(database):  # noqa: F811
    await migrated(database)
    await database.apply(migration_0105(), "downgrade")
    async with database.pool.acquire() as conn:
        for override in OLD_RULE_ONLY:
            await insert(conn, **{**GOOD, **override})  # the old rule accepts these again
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(conn, **{**GOOD, "consent_source": "abcd"})
        assert await conn.fetchval(
            "SELECT convalidated FROM pg_constraint WHERE conname = 'contact_lists_consent_complete'") is True

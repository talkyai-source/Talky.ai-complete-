"""Confirming the contacts that have no list, on a migrated PostgreSQL.

Runs the real ``_INSERT_LIST_SQL`` / ``_MOVE_SQL`` / ``_WAITING_SQL`` constants
against a migrated disposable localhost ``*_test`` database (it skips without
TEST_DATABASE_URL). Written to the style of test_running_campaign_new_contacts_pg.

A statement that is expected to be REFUSED runs in its own transaction block: a
refused statement aborts its transaction and would undo earlier accepted ones.
"""
import os
from datetime import date, datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.api.v1.endpoints import contact_permission_confirm as cpc
from app.domain.services.dialer import permission_gate as pg

pytestmark = pytest.mark.integration

BASIS = "express_consent"
SOURCE = "Website sign-up form, March 2026"
PERMISSION_DATE = date(2026, 3, 14)
VERSION = "2026-11-v1"


class _Abort(Exception):
    """Raised inside a transaction block to force its rollback."""


@pytest_asyncio.fixture
async def db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("accepts only a disposable localhost *_test database")
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    tenant, other = uuid4(), uuid4()
    campaign, other_campaign, inbound = uuid4(), uuid4(), uuid4()
    user = uuid4()
    try:
        for t in (tenant, other):
            await admin.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic ungrouped')", t)
        await admin.execute(
            "INSERT INTO user_profiles(id,email,tenant_id,role) VALUES($1,$2,$3,'user')",
            user, f"ungrouped-{user}@example.invalid", tenant)
        for c, t, direction in ((campaign, tenant, "outbound"), (other_campaign, other, "outbound"),
                                (inbound, tenant, "inbound")):
            await admin.execute(
                "INSERT INTO campaigns(id,tenant_id,name,status,direction) VALUES($1,$2,'Synthetic','running',$3)",
                c, t, direction)
        yield admin, tenant, other, campaign, other_campaign, inbound, user
    finally:
        for t in (tenant, other):
            await admin.execute("DELETE FROM leads WHERE tenant_id=$1", t)
            await admin.execute("DELETE FROM contact_lists WHERE tenant_id=$1", t)
            await admin.execute("DELETE FROM campaigns WHERE tenant_id=$1", t)
        await admin.execute("DELETE FROM user_profiles WHERE id=$1", user)
        for t in (tenant, other):
            await admin.execute("DELETE FROM tenants WHERE id=$1", t)
        await admin.close()


_phone = iter(range(15550004000, 15550009999))


async def _lead(admin, tenant, campaign, *, list_id=None, status="pending", dnc=False):
    lead = uuid4()
    await admin.execute(
        "INSERT INTO leads(id,tenant_id,campaign_id,phone_number,status,list_id,do_not_call) "
        "VALUES($1,$2,$3,$4,$5,$6,$7)",
        lead, tenant, campaign, f"+{next(_phone)}", status, list_id, dnc)
    return lead


async def _insert_list(conn, tenant, campaign, user, name="Earlier contacts"):
    list_id = uuid4()
    row = await conn.fetchrow(
        cpc._INSERT_LIST_SQL, str(tenant), str(campaign), str(list_id), name,
        str(user), BASIS, SOURCE, PERMISSION_DATE, VERSION, datetime.now(timezone.utc))
    return list_id, row


async def _confirmed_list(admin, tenant, campaign, user):
    async with admin.transaction():
        list_id, _row = await _insert_list(admin, tenant, campaign, user, name=f"Confirmed {uuid4()}")
    return list_id


async def test_insert_satisfies_the_0096_check_and_the_outbound_trigger(db):
    admin, tenant, _other, campaign, _oc, _inbound, user = db
    async with admin.transaction():
        list_id, row = await _insert_list(admin, tenant, campaign, user)
    assert row["id"] == list_id
    stored = await admin.fetchrow("SELECT * FROM contact_lists WHERE id=$1", list_id)
    assert stored["tenant_id"] == tenant and stored["campaign_id"] == campaign
    assert stored["source"] == "manual" and stored["is_active"] is True
    assert stored["consent_confirmed_at"] is not None and stored["consent_confirmed_by"] == user
    assert (stored["consent_basis"], stored["consent_source"], stored["consent_permission_date"],
            stored["consent_statement_version"]) == (BASIS, SOURCE, PERMISSION_DATE, VERSION)


async def test_insert_is_refused_for_an_inbound_or_foreign_campaign(db):
    """The route checks ownership first; the database is the second wall."""
    admin, tenant, other, campaign, other_campaign, inbound, user = db
    for tenant_id, campaign_id in ((tenant, inbound), (other, campaign), (tenant, other_campaign)):
        with pytest.raises(asyncpg.PostgresError):
            async with admin.transaction():  # its own block: a refusal aborts it
                await _insert_list(admin, tenant_id, campaign_id, user)
    assert await admin.fetchval(
        "SELECT count(*) FROM contact_lists WHERE tenant_id = ANY($1::uuid[])", [tenant, other]) == 0


async def test_the_trigger_refusal_text_names_outbound(db):
    """The route maps a database refusal containing 'outbound' to the 409."""
    admin, tenant, _other, _campaign, _oc, inbound, _user = db
    with pytest.raises(asyncpg.PostgresError) as exc:
        async with admin.transaction():
            await admin.execute(
                "INSERT INTO contact_lists(id,campaign_id,tenant_id,name) VALUES($1,$2,$3,'x')",
                uuid4(), inbound, tenant)
    assert "outbound" in str(exc.value)


async def test_a_future_permission_date_is_refused_by_the_check(db):
    admin, tenant, _other, campaign, _oc, _inbound, user = db
    with pytest.raises(asyncpg.CheckViolationError):
        async with admin.transaction():
            await admin.fetchrow(
                cpc._INSERT_LIST_SQL, str(tenant), str(campaign), str(uuid4()), "x",
                str(user), BASIS, SOURCE, date(2999, 1, 1), VERSION, datetime.now(timezone.utc))


async def test_move_respects_the_composite_foreign_key_and_only_listless_contacts(db):
    admin, tenant, other, campaign, other_campaign, _inbound, user = db
    existing = await _confirmed_list(admin, tenant, campaign, user)
    mine = [await _lead(admin, tenant, campaign) for _ in range(3)]
    grouped = await _lead(admin, tenant, campaign, list_id=existing)
    deleted = await _lead(admin, tenant, campaign, status="deleted")
    foreign = await _lead(admin, other, other_campaign)

    async with admin.transaction():
        list_id, _row = await _insert_list(admin, tenant, campaign, user)
        moved = await admin.fetch(cpc._MOVE_SQL, str(tenant), str(campaign), str(list_id))
    assert {r["id"] for r in moved} == set(mine)  # composite FK (id, campaign, tenant) accepted
    assert await admin.fetchval("SELECT list_id FROM leads WHERE id=$1", grouped) == existing
    assert await admin.fetchval("SELECT list_id FROM leads WHERE id=$1", deleted) is None
    assert await admin.fetchval("SELECT list_id FROM leads WHERE id=$1", foreign) is None


async def test_a_mismatch_rolls_back_both_statements(db):
    admin, tenant, _other, campaign, _oc, _inbound, user = db
    mine = [await _lead(admin, tenant, campaign) for _ in range(2)]
    list_id = None
    with pytest.raises(_Abort):
        async with admin.transaction():
            list_id, _row = await _insert_list(admin, tenant, campaign, user)
            moved = await admin.fetch(cpc._MOVE_SQL, str(tenant), str(campaign), str(list_id))
            assert len(moved) == 2
            raise _Abort  # what the route does when the count differs from expected_count
    assert await admin.fetchval("SELECT count(*) FROM contact_lists WHERE id=$1", list_id) == 0
    for lead in mine:
        assert await admin.fetchval("SELECT list_id FROM leads WHERE id=$1", lead) is None


async def test_another_tenants_listless_contacts_are_neither_moved_nor_counted(db):
    admin, tenant, other, campaign, other_campaign, _inbound, user = db
    await _lead(admin, tenant, campaign)
    foreign = [await _lead(admin, other, other_campaign) for _ in range(4)]

    assert tuple(await admin.fetchrow(pg._WAITING_SQL, str(tenant), str(campaign))) == (1, 1)
    # asking for the other tenant's campaign with THIS tenant's id counts nothing
    assert tuple(await admin.fetchrow(pg._WAITING_SQL, str(tenant), str(other_campaign))) == (0, 0)
    async with admin.transaction():
        list_id, _row = await _insert_list(admin, tenant, campaign, user)
        moved = await admin.fetch(cpc._MOVE_SQL, str(tenant), str(other_campaign), str(list_id))
    assert moved == []
    for lead in foreign:
        assert await admin.fetchval("SELECT list_id FROM leads WHERE id=$1", lead) is None


async def test_waiting_sql_counts_like_the_owner_query(db):
    admin, tenant, _other, campaign, _oc, _inbound, user = db
    confirmed = await _confirmed_list(admin, tenant, campaign, user)
    async with admin.transaction():
        unconfirmed = uuid4()
        await admin.execute(
            "INSERT INTO contact_lists(id,campaign_id,tenant_id,name) VALUES($1,$2,$3,'Old import')",
            unconfirmed, campaign, tenant)
        switched_off = uuid4()
        await admin.execute(
            "INSERT INTO contact_lists(id,campaign_id,tenant_id,name,is_active) VALUES($1,$2,$3,'Off',false)",
            switched_off, campaign, tenant)
    await _lead(admin, tenant, campaign)                                    # no list, pending: counted, ungrouped
    await _lead(admin, tenant, campaign, status="calling")                  # no list, calling: counted, ungrouped
    await _lead(admin, tenant, campaign, list_id=unconfirmed)               # unconfirmed list: counted
    await _lead(admin, tenant, campaign, list_id=confirmed)                 # confirmed: not counted
    await _lead(admin, tenant, campaign, list_id=switched_off)              # list switched off: not counted
    await _lead(admin, tenant, campaign, dnc=True)                          # do not call: not counted
    await _lead(admin, tenant, campaign, status="completed")                # finished: not counted
    await _lead(admin, tenant, campaign, status="deleted")                  # deleted: not counted

    waiting, ungrouped = tuple(await admin.fetchrow(pg._WAITING_SQL, str(tenant), str(campaign)))
    assert (waiting, ungrouped) == (3, 2)


async def test_lead_permission_sql_distinguishes_the_three_states(db):
    admin, tenant, other, campaign, other_campaign, _inbound, user = db
    confirmed = await _confirmed_list(admin, tenant, campaign, user)
    unconfirmed = uuid4()
    await admin.execute(
        "INSERT INTO contact_lists(id,campaign_id,tenant_id,name) VALUES($1,$2,$3,'Old import')",
        unconfirmed, campaign, tenant)
    ok = await _lead(admin, tenant, campaign, list_id=confirmed)
    old = await _lead(admin, tenant, campaign, list_id=unconfirmed)
    none = await _lead(admin, tenant, campaign)
    foreign = await _lead(admin, other, other_campaign)

    async def missing(t, lead):
        row = await admin.fetchrow(pg._LEAD_PERMISSION_SQL, str(t), str(lead))
        return None if row is None else row["missing"]

    assert [await missing(tenant, x) for x in (ok, old, none)] == [False, True, True]
    assert await missing(tenant, foreign) is None  # another tenant's lead: no row (= missing)

"""Staff side of number requests against real PostgreSQL (CI only: needs a disposable *_test database).

Every staff statement here runs on the RESTRICTED role (NOSUPERUSER NOBYPASSRLS), the way the
application runs it: a pool whose connections do ``SET ROLE`` to the fixture role, and
``acquire_with_tenant(pool, None, ...)`` (bypass on; the explicit ``tenant_id`` predicates do the
scoping). Rows are seeded as the owner. Skipped (never passed) when TEST_DATABASE_URL is not set.

The restricted role's rights on the two tables come from the GRANT in the ``staff`` fixture
below: the migration issues none, and database/runtime_role_grants.sql grants the whole set
(SELECT, INSERT, UPDATE, DELETE on all tables) to ``talky_runtime``, a role this test cannot use,
so the fixture grants the subset the staff code needs (the same way the sibling number-request
database tests grant theirs).

There is deliberately no audit test here: the decision route needs a real platform-admin
principal and a real security session, and no database test in this repository drives a route
with those for this feature; the route's audit call is asserted by
tests/unit/test_number_request_staff_routes.py.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant
from app.domain.services.number_requests import staff_service as ss
from tests.integration.test_billing_price_options import billing_db  # noqa: F401

pytestmark = pytest.mark.integration

_HEX = "a" * 64
_NOW = datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def staff(billing_db):  # noqa: F811 - imported pytest fixture
    fx = billing_db
    fx.a, fx.b = fx.tenants
    fx.reviewer = uuid4()
    async with acquire_with_tenant(fx.pool, None) as conn:
        # The role's rights on the new tables and on the two lookup tables staff reads.
        await conn.execute(
            f'GRANT SELECT,UPDATE ON telephony_number_requests,telephony_number_request_documents TO "{fx.role}"')
        await conn.execute(f'GRANT SELECT ON tenants,user_profiles TO "{fx.role}"')

    async def role_setup(conn):
        await conn.execute(f'SET ROLE "{fx.role}"')

    fx.rpool = await asyncpg.create_pool(
        os.environ["TEST_DATABASE_URL"], min_size=1, max_size=6, timeout=5, command_timeout=10,
        init=_register_jsonb_codecs, setup=role_setup,
    )
    fx.requests = {fx.a: [], fx.b: []}   # tenant -> [request ids], newest first
    fx.docs = {}                         # tenant -> (request id, document id)
    fx.profile = uuid4()                 # a user of account A
    fx.email = f"{fx.profile}@example.invalid"
    try:
        async with acquire_with_tenant(fx.pool, None) as conn:
            await conn.execute(
                "INSERT INTO user_profiles(id,email,tenant_id,role,is_active,is_verified,email_verified_at) "
                "VALUES($1,$2,$3,'user',TRUE,TRUE,NOW())", fx.profile, fx.email, fx.a)
            # A has three requests, B has two, all submitted, with distinct submitted_at.
            for tenant, count, base in ((fx.a, 3, 0), (fx.b, 2, 30)):
                for i in range(count):
                    rid = uuid4()
                    fx.requests[tenant].append(rid)
                    await conn.execute(
                        "INSERT INTO telephony_number_requests (id, tenant_id, requested_by_user_id, use_type, "
                        "idempotency_key, request_fingerprint, submitted_at) "
                        "VALUES ($1, $2, $3, 'business', $4, $5, $6)",
                        rid, tenant, fx.profile if tenant == fx.a else uuid4(), uuid4().hex, _HEX,
                        _NOW - timedelta(hours=base + i))
            for tenant in (fx.a, fx.b):
                rid, did = fx.requests[tenant][0], uuid4()
                fx.docs[tenant] = (rid, did)
                await conn.execute(
                    "INSERT INTO telephony_number_request_documents (id, tenant_id, request_id, kind, "
                    "storage_key, file_name, content_type, size_bytes, sha256, wrapped_key, key_provider) "
                    "VALUES ($1,$2,$3,'business_registration',$4,'scan.pdf','application/pdf',44,$5,$6,'local')",
                    did, tenant, rid, f"{tenant}/{did}.tnr", "b" * 64, b"wrapped")
        yield fx
    finally:
        await fx.rpool.close()
        async with acquire_with_tenant(fx.pool, None) as conn:
            await conn.execute(
                "DELETE FROM telephony_number_request_documents WHERE tenant_id=ANY($1::uuid[])", fx.tenants)
            await conn.execute("DELETE FROM telephony_number_requests WHERE tenant_id=ANY($1::uuid[])", fx.tenants)
            await conn.execute("DELETE FROM user_profiles WHERE id=$1", fx.profile)


def _ours(staff):
    return {str(r) for rids in staff.requests.values() for r in rids}


async def test_the_role_is_restricted(staff):
    async with staff.rpool.acquire() as conn:
        flags = await conn.fetchrow("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    assert (flags["rolsuper"], flags["rolbypassrls"]) == (False, False)


async def test_list_returns_both_accounts_newest_first_with_the_right_has_more(staff):
    async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
        rows, more = await ss.list_requests(conn, status="submitted", limit=1000, offset=0)
        total = len(rows)
        assert more is False
        assert _ours(staff) <= {str(r["id"]) for r in rows}
        assert {r["tenant_id"] for r in rows if str(r["id"]) in _ours(staff)} == {staff.a, staff.b}
        stamps = [(r["submitted_at"], str(r["id"])) for r in rows]
        assert stamps == sorted(stamps, reverse=True)
        # Page boundary: one short of everything has more; exactly everything does not.
        short, more_short = await ss.list_requests(conn, status="submitted", limit=total - 1, offset=0)
        assert len(short) == total - 1 and more_short is True
        full, more_full = await ss.list_requests(conn, status="submitted", limit=total, offset=0)
        assert len(full) == total and more_full is False
        last, more_last = await ss.list_requests(conn, status="submitted", limit=1, offset=total - 1)
        assert [str(r["id"]) for r in last] == [str(rows[-1]["id"])] and more_last is False
        # A status nobody has gives an empty page.
        approved_only, _ = await ss.list_requests(conn, status="approved", limit=1000, offset=0)
        assert not (_ours(staff) & {str(r["id"]) for r in approved_only})


async def test_get_request_needs_the_matching_account_in_the_path(staff):
    a_req, b_req = staff.requests[staff.a][0], staff.requests[staff.b][0]
    async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
        own = await ss.get_request(conn, str(staff.a), str(a_req))
        # Bypass is on here, so row security allows B's row: it is the explicit
        # tenant_id predicate in the statement, not row security, that refuses.
        assert await ss.get_request(conn, str(staff.a), str(b_req)) is None
        assert await ss.fetch_request_row(conn, str(staff.b), str(a_req)) is None
    assert own is not None and str(own["id"]) == str(a_req) and own["status"] == "submitted"
    assert own["requester_email"] == staff.email
    assert own["business_name"] == "Synthetic CP02"
    assert [str(d["id"]) for d in own["documents"]] == [str(staff.docs[staff.a][1])]
    assert "storage_key" not in own["documents"][0] and "wrapped_key" not in own["documents"][0]


async def test_get_document_needs_the_matching_account_request_and_document(staff):
    a_req, a_doc = staff.docs[staff.a]
    b_req, b_doc = staff.docs[staff.b]
    async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
        own = await ss.get_document(conn, str(staff.a), str(a_req), str(a_doc))
        # Explicit predicates (bypass is on): B's document under A's path is refused.
        assert await ss.get_document(conn, str(staff.a), str(a_req), str(b_doc)) is None
        assert await ss.get_document(conn, str(staff.a), str(b_req), str(b_doc)) is None
        assert await ss.get_document(conn, str(staff.b), str(a_req), str(a_doc)) is None
    assert own is not None and str(own["id"]) == str(a_doc)
    assert own["storage_key"] == f"{staff.a}/{a_doc}.tnr" and own["wrapped_key"] == b"wrapped"


async def test_requester_email_is_scoped_to_the_account(staff):
    async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
        assert await ss.requester_email(conn, str(staff.a), str(staff.profile)) == staff.email
        assert await ss.requester_email(conn, str(staff.b), str(staff.profile)) is None


async def test_decide_sets_the_review_fields_and_a_second_decision_gets_none(staff):
    rid = staff.requests[staff.a][1]
    async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
        first = await ss.decide(conn, tenant_id=str(staff.a), request_id=str(rid), decision="approved",
                                note=None, reviewer_id=str(staff.reviewer))
    assert first is not None and first["status"] == "approved" and first["reviewed_at"] is not None
    assert str(first["id"]) == str(rid)
    async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
        again = await ss.decide(conn, tenant_id=str(staff.a), request_id=str(rid), decision="rejected",
                                note="too late", reviewer_id=str(staff.reviewer))
        row = await ss.fetch_request_row(conn, str(staff.a), str(rid))
    assert again is None  # the route answers 409 already_decided
    assert row["status"] == "approved" and row["review_note"] is None
    assert str(row["reviewed_by_user_id"]) == str(staff.reviewer) and row["reviewed_at"] is not None


async def test_decide_rejected_keeps_the_note_and_meets_the_table_checks(staff):
    rid = staff.requests[staff.a][2]
    async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
        out = await ss.decide(conn, tenant_id=str(staff.a), request_id=str(rid), decision="rejected",
                              note="Photo id is unreadable.", reviewer_id=str(staff.reviewer))
        row = await ss.fetch_request_row(conn, str(staff.a), str(rid))
    assert out is not None and out["status"] == "rejected"
    assert row["review_note"] == "Photo id is unreadable." and row["reviewed_at"] is not None
    # The pending-submitted-iff-no-review CHECKs held, and the same request cannot be decided again.
    async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
        assert await ss.decide(conn, tenant_id=str(staff.a), request_id=str(rid), decision="approved",
                               note=None, reviewer_id=str(staff.reviewer)) is None


async def test_decide_under_the_wrong_account_changes_nothing(staff):
    b_req = staff.requests[staff.b][1]
    async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
        assert await ss.decide(conn, tenant_id=str(staff.a), request_id=str(b_req), decision="approved",
                               note=None, reviewer_id=str(staff.reviewer)) is None
        row = await ss.fetch_request_row(conn, str(staff.b), str(b_req))
    assert row["status"] == "submitted" and row["reviewed_at"] is None


async def test_two_concurrent_decisions_give_one_winner_and_one_none(staff):
    rid = staff.requests[staff.b][0]

    async def decide(decision):
        async with acquire_with_tenant(staff.rpool, None, user_id=staff.reviewer) as conn:
            return await ss.decide(conn, tenant_id=str(staff.b), request_id=str(rid), decision=decision,
                                   note="synthetic" if decision == "rejected" else None,
                                   reviewer_id=str(staff.reviewer))

    results = await asyncio.gather(decide("approved"), decide("rejected"))
    assert sum(r is not None for r in results) == 1
    async with acquire_with_tenant(staff.rpool, None) as conn:
        row = await ss.fetch_request_row(conn, str(staff.b), str(rid))
    assert row["status"] in ("approved", "rejected") and row["reviewed_at"] is not None


async def test_row_security_hides_the_other_account_without_bypass(staff):
    a_req, a_doc = staff.docs[staff.a]
    b_req, b_doc = staff.docs[staff.b]
    # Account A set, bypass NOT set: a bare SELECT sees only A's rows.
    async with acquire_with_tenant(staff.rpool, staff.a) as conn:
        requests = await conn.fetch("SELECT id, tenant_id FROM telephony_number_requests")
        documents = await conn.fetch("SELECT id, tenant_id FROM telephony_number_request_documents")
        assert await conn.fetchval("SELECT count(*) FROM telephony_number_requests WHERE id = $1",
                                   staff.requests[staff.b][0]) == 0
        assert await conn.fetchval("SELECT count(*) FROM telephony_number_request_documents WHERE id = $1",
                                   b_doc) == 0
        # The staff statements with B's ids, on A's scope, also find nothing.
        assert await ss.fetch_request_row(conn, str(staff.b), str(b_req)) is None
        assert await ss.get_document(conn, str(staff.b), str(b_req), str(b_doc)) is None
    assert {r["tenant_id"] for r in requests} == {staff.a}
    assert {str(r["id"]) for r in requests} >= {str(r) for r in staff.requests[staff.a]}
    assert {r["tenant_id"] for r in documents} == {staff.a} and str(a_doc) in {str(r["id"]) for r in documents}


async def test_no_account_and_no_bypass_sees_nothing(staff):
    async with acquire_with_tenant(staff.pool, None) as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM telephony_number_requests WHERE tenant_id=ANY($1::uuid[])", staff.tenants) == 5
        assert await conn.fetchval(
            "SELECT count(*) FROM telephony_number_request_documents WHERE tenant_id=ANY($1::uuid[])",
            staff.tenants) == 2
    # A plain restricted connection: neither app.current_tenant_id nor app.bypass_rls is set.
    async with staff.rpool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM telephony_number_requests") == 0
        assert await conn.fetchval("SELECT count(*) FROM telephony_number_request_documents") == 0

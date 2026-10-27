"""Normal-password/MFA retained supplier evidence with a restricted real role.

Only CI's disposable DB or an explicitly owned supplier clone is eligible.
The source fixture must be empty; teardown truncates only the two wholly owned
evidence tables after checking every actor/account, before shared cleanup.
"""
import asyncio
import json
import os
import re
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import call, signed_in

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@pytest_asyncio.fixture
async def supplier_db(journey_db):  # noqa: F811
    db = journey_db
    name = await db.owner.fetchval("SELECT current_database()")
    assert (os.getenv("GITHUB_ACTIONS") == "true" and name == "talky_alembic_test") or (
        re.fullmatch(r"supplier_[0-9a-f]{32}_test", name)
        and os.getenv("TALKY_SUPPLIER_TEST_OWNED_CLONE") == name)
    for table in ("supplier_statement_evidence", "supplier_statement_reviews"):
        assert await db.owner.fetchval(f"SELECT count(*) FROM {table}") == 0
    db.account = uuid4()
    db.client_ip = "127.0.2." + str(1 + uuid4().int % 250)
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    await db.owner.execute(f'GRANT SELECT,INSERT ON supplier_statement_evidence,supplier_statement_reviews TO "{role}"')
    record = {"owned_cleanup": False}
    RUN_EVIDENCE.append(record)
    db.supplier_record = record
    try:
        yield db
    finally:
        async with db.owner.transaction():
            # No production/parent table is eligible, and no nonfixture row
            # may be erased. TRUNCATE is not granted to the application role.
            assert await db.owner.fetchval("SELECT current_database()") == name
            assert await db.owner.fetchval("SELECT count(*) FROM supplier_statement_evidence WHERE imported_by<>$1 OR account_id<>$2", db.users[0], db.account) == 0
            assert await db.owner.fetchval("SELECT count(*) FROM supplier_statement_reviews WHERE reviewer_id<>$1", db.users[0]) == 0
            record["saved_evidence_count"] = await db.owner.fetchval("SELECT count(*) FROM supplier_statement_evidence")
            record["saved_review_count"] = await db.owner.fetchval("SELECT count(*) FROM supplier_statement_reviews")
            await db.owner.execute("TRUNCATE supplier_statement_reviews,supplier_statement_evidence")
        record["owned_cleanup"] = True


def payload(db, **changes):
    source = {"start": "2026-09-01", "end": "2026-09-30", "resolution": {"units": "day", "amount": 1},
              "results": [{"dollars": 12.34, "grouping": {"start": "2026-09-15", "end": "2026-09-15"}}]}
    body = {"request_id": str(uuid4()), "account_id": str(db.account), "reference": "Owned synthetic source",
            "source_content": json.dumps(source), "unfiltered_project_export": True}
    body.update(changes)
    return body


async def import_one(db, admin, body=None):
    body = body or payload(db)
    result = await call(db, admin, "POST", "/admin/usage/statements", json=body)
    assert result.status_code == 200
    return body, result.json()


@pytest.mark.parametrize("amount,expected", [("12.34", "12.34000000"), ("0.00000000", "0.00000000")])
async def test_normal_mfa_import_replay_private_bytes_review_and_difference(supplier_db, amount, expected):
    db = supplier_db
    async with signed_in(db) as admin:
        body = payload(db)
        body["source_content"] = "\ufeff" + body["source_content"].replace("12.34", amount)
        concurrent = await asyncio.gather(*(call(db, admin, "POST", "/admin/usage/statements", json=body) for _ in range(2)))
        assert [result.status_code for result in concurrent] == [200, 200]
        saved = concurrent[0].json()
        assert concurrent[1].json()["id"] == saved["id"]
        assert saved["stage"] == "imported" and saved["normalized"]["net_spend"] is None
        assert saved["reported_charges"] == expected
        assert saved["comparison"]["matched"] is False
        replay = await call(db, admin, "POST", "/admin/usage/statements", json=body)
        assert replay.status_code == 200 and replay.json()["id"] == saved["id"]
        exact = await call(db, admin, "GET", f"/admin/usage/statements/imports/{body['request_id']}")
        assert exact.status_code == 200 and exact.json()["id"] == saved["id"]
        downloaded = await call(db, admin, "GET", f"/admin/usage/statements/{saved['id']}/source")
        assert downloaded.status_code == 200 and downloaded.content == body["source_content"].encode()
        assert downloaded.headers["cache-control"] == "private, no-store"
        review = {"request_id": str(uuid4()), "source_sha256": saved["source_sha256"], "stage": "reviewed",
                  "review_reference": "Owned finance source observation", "notes": "Synthetic original project source compared.",
                  "source_account_and_period_checked": True, "accept_unallocated_difference": False}
        path = f"/admin/usage/statements/{saved['id']}/reviews"
        first = await call(db, admin, "POST", path, json=review)
        assert first.status_code == 200 and first.json()["stage"] == "reviewed"
        again = await call(db, admin, "POST", path, json=review)
        assert again.status_code == 200 and again.json()["reviews"] == first.json()["reviews"]
        reconciled = {**review, "request_id": str(uuid4()), "stage": "reconciled", "accept_unallocated_difference": True}
        final = await call(db, admin, "POST", path, json=reconciled)
        assert final.status_code == 200 and final.json()["stage"] == "reconciled"
        assert final.json()["comparison"]["unallocated_charges"] == expected
        assert final.json()["comparison"]["net_spend"] is None
        count = await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1 AND resource_type='supplier_statement'", db.users[0])
        assert count == 4  # import, private source download, review, reconciliation
        db.supplier_record.update(normal_mfa=True, exact_private_source=True, replay_no_extra_audit=True,
                                  complete_spend_claim=False, atomic_audits=count)


async def test_conflicting_replay_correction_and_stale_source_review(supplier_db):
    db = supplier_db
    async with signed_in(db) as admin:
        body, original = await import_one(db, admin)
        conflict = {**body, "source_content": body["source_content"].replace("12.34", "99.99")}
        assert (await call(db, admin, "POST", "/admin/usage/statements", json=conflict)).status_code == 409
        correction = {**conflict, "request_id": str(uuid4()), "reference": "Owned corrected capture", "supersedes_id": original["id"]}
        response = await call(db, admin, "POST", "/admin/usage/statements", json=correction)
        assert response.status_code == 200
        retained = await call(db, admin, "GET", f"/admin/usage/statements/{original['id']}")
        assert retained.json()["superseded_by"] == response.json()["id"]
        assert retained.json()["reported_charges"] == "12.34000000"
        review = {"request_id": str(uuid4()), "source_sha256": original["source_sha256"], "stage": "reviewed",
                  "review_reference": "Stale original review", "notes": "Synthetic stale review must not be accepted.",
                  "source_account_and_period_checked": True}
        assert (await call(db, admin, "POST", f"/admin/usage/statements/{original['id']}/reviews", json=review)).status_code == 409
        assert await db.owner.fetchval("SELECT count(*) FROM supplier_statement_reviews") == 0


async def test_current_role_denial_and_bypass_alone_cannot_read_or_rewrite(supplier_db):
    db = supplier_db
    async with signed_in(db) as admin:
        _, saved = await import_one(db, admin)
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls='on'")
                assert await conn.fetchval("SELECT count(*) FROM supplier_statement_evidence") == 0
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.current_user_id',$1,true)", str(db.users[1]))
                await conn.execute("SET LOCAL app.bypass_rls='on'")
                assert await conn.fetchval("SELECT count(*) FROM supplier_statement_evidence") == 0
        # No UPDATE/DELETE application grants, and immutable trigger is a second
        # boundary even for the fixture owner; neither boundary is weakened.
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            await db.owner.execute("UPDATE supplier_statement_evidence SET reference='rewrite' WHERE id=$1", UUID(saved["id"]))
        await db.owner.execute("UPDATE user_profiles SET role='tenant_admin' WHERE id=$1", db.users[0])
        denied = await call(db, admin, "GET", f"/admin/usage/statements/{saved['id']}")
        assert denied.status_code in (401, 403)
        assert await db.owner.fetchval("SELECT count(*) FROM supplier_statement_evidence") == 1


async def test_audit_failure_rolls_back_import_and_reconciliation_requires_source_review(supplier_db, monkeypatch):
    from app.domain.services.audit_logger import AuditLogger
    db = supplier_db
    async with signed_in(db) as admin:
        async def fail_audit(*_args, **_kwargs):
            raise RuntimeError("Owned synthetic audit failure")
        with monkeypatch.context() as patch:
            patch.setattr(AuditLogger, "log_in_transaction", fail_audit)
            response = await call(db, admin, "POST", "/admin/usage/statements", json=payload(db))
        assert response.status_code == 503
        assert await db.owner.fetchval("SELECT count(*) FROM supplier_statement_evidence") == 0
        _, saved = await import_one(db, admin)
        review = {"request_id": str(uuid4()), "source_sha256": saved["source_sha256"], "stage": "reconciled",
                  "review_reference": "Unreviewed source", "notes": "Synthetic source not yet checked against supplier.",
                  "source_account_and_period_checked": True, "accept_unallocated_difference": True}
        assert (await call(db, admin, "POST", f"/admin/usage/statements/{saved['id']}/reviews", json=review)).status_code == 409
        assert await db.owner.fetchval("SELECT count(*) FROM supplier_statement_reviews") == 0

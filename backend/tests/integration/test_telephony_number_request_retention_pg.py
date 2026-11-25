"""Number-request retention against real PostgreSQL (CI only: needs a disposable *_test database).

Proves the real statements R1-R6 and the table CHECK constraints: a deleted document has its
key and file name cleared; nothing else is touched.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant
from app.domain.services.number_requests.store import DocumentStore
from app.workers.number_request_retention import purge_number_request_documents
from tests.integration.test_billing_price_options import billing_db  # noqa: F401

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)
OLD = NOW - timedelta(days=100)
RECENT = NOW - timedelta(days=5)


@pytest_asyncio.fixture
async def rt_db(billing_db, tmp_path, monkeypatch):  # noqa: F811
    fixture = billing_db
    for name in ("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "NUMBER_REQUEST_RELEASED_RETENTION_DAYS"):
        monkeypatch.delenv(name, raising=False)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            f'GRANT SELECT,INSERT,UPDATE ON telephony_number_requests,telephony_number_request_documents '
            f'TO "{fixture.role}"')
        await conn.execute(f'GRANT SELECT ON tenants TO "{fixture.role}"')

    async def role_setup(conn):
        await conn.execute(f'SET ROLE "{fixture.role}"')

    pool = await asyncpg.create_pool(
        os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4, timeout=5, command_timeout=10,
        init=_register_jsonb_codecs, setup=role_setup,
    )
    fixture.service_pool = pool
    fixture.store = DocumentStore(str(tmp_path / "docs"))
    try:
        yield fixture
    finally:
        await pool.close()
        async with acquire_with_tenant(fixture.pool, None) as conn:
            await conn.execute(
                "DELETE FROM telephony_number_request_documents WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)
            await conn.execute(
                "DELETE FROM telephony_number_requests WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)


async def seed(fx, tenant, *, status, reviewed_at=None, released_at=None):
    """One request with two documents and two real files. Returns (request_id, [(doc_id, key)])."""
    rid = uuid4()
    reviewer = uuid4() if status != "submitted" else None
    docs = []
    async with acquire_with_tenant(fx.pool, None) as conn:
        await conn.execute(
            "INSERT INTO telephony_number_requests (id, tenant_id, requested_by_user_id, use_type, status, "
            "reviewed_by_user_id, reviewed_at, number_released_at, idempotency_key, request_fingerprint) "
            "VALUES ($1,$2,$3,'business',$4,$5,$6,$7,$8,$9)",
            rid, tenant, uuid4(), status, reviewer, reviewed_at, released_at, uuid4().hex, "a" * 64)
        for kind in ("business_registration", "business_photo_id"):
            did = uuid4()
            key = f"{tenant}/{did}.tnr"
            path = fx.store.path_for(key)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"TNR1" + b"x" * 40)
            await conn.execute(
                "INSERT INTO telephony_number_request_documents (id, tenant_id, request_id, kind, storage_key, "
                "file_name, content_type, size_bytes, sha256, wrapped_key, key_provider) "
                "VALUES ($1,$2,$3,$4,$5,'scan.pdf','application/pdf',44,$6,$7,'local')",
                did, tenant, rid, kind, key, "b" * 64, b"wrapped")
            docs.append((did, key))
    return rid, docs


async def state(fx, tenant, rid):
    async with acquire_with_tenant(fx.pool, None) as conn:
        req = await conn.fetchrow(
            "SELECT files_deleted_at FROM telephony_number_requests WHERE tenant_id=$1 AND id=$2", tenant, rid)
        docs = await conn.fetch(
            "SELECT files_deleted_at, wrapped_key, file_name FROM telephony_number_request_documents "
            "WHERE tenant_id=$1 AND request_id=$2", tenant, rid)
    return req["files_deleted_at"], docs


def on_disk(fx, docs):
    return [os.path.exists(fx.store.path_for(key)) for _, key in docs]


@pytest.mark.asyncio
async def test_nothing_is_deleted_when_no_time_is_set(rt_db):
    a = rt_db.tenants[0]
    rid, docs = await seed(rt_db, a, status="rejected", reviewed_at=OLD)
    result = await purge_number_request_documents(rt_db.service_pool, dry_run=False, store=rt_db.store)
    assert result["rules"] == "none" and result["requests"] == 0
    deleted_at, rows = await state(rt_db, a, rid)
    assert deleted_at is None and all(r["wrapped_key"] and r["file_name"] for r in rows)
    assert all(on_disk(rt_db, docs))


@pytest.mark.asyncio
async def test_rejected_past_cutoff_loses_files_others_untouched(rt_db, monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    a, b = rt_db.tenants[0], rt_db.tenants[1]
    old, old_docs = await seed(rt_db, a, status="rejected", reviewed_at=OLD)
    new, new_docs = await seed(rt_db, a, status="rejected", reviewed_at=RECENT)
    sub, sub_docs = await seed(rt_db, a, status="submitted")
    app, app_docs = await seed(rt_db, a, status="approved", reviewed_at=OLD)
    other, other_docs = await seed(rt_db, b, status="approved", reviewed_at=OLD)

    result = await purge_number_request_documents(rt_db.service_pool, dry_run=False, store=rt_db.store)
    assert result["requests"] == 1 and result["files_removed"] == 2 and result["error"] is None

    deleted_at, rows = await state(rt_db, a, old)
    assert deleted_at is not None
    assert all(r["files_deleted_at"] and r["wrapped_key"] is None and r["file_name"] is None for r in rows)
    assert not any(on_disk(rt_db, old_docs))
    for rid, docs in ((new, new_docs), (sub, sub_docs), (app, app_docs)):
        deleted_at, rows = await state(rt_db, a, rid)
        assert deleted_at is None and all(r["wrapped_key"] and r["file_name"] for r in rows)
        assert all(on_disk(rt_db, docs))
    deleted_at, rows = await state(rt_db, b, other)
    assert deleted_at is None and all(on_disk(rt_db, other_docs))

    again = await purge_number_request_documents(rt_db.service_pool, dry_run=False, store=rt_db.store)
    assert again["requests"] == 0 and again["error"] is None


@pytest.mark.asyncio
async def test_approved_needs_a_release_date(rt_db, monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_RELEASED_RETENTION_DAYS", "30")
    a = rt_db.tenants[0]
    no_date, no_date_docs = await seed(rt_db, a, status="approved", reviewed_at=OLD)
    released, released_docs = await seed(rt_db, a, status="approved", reviewed_at=OLD, released_at=OLD)
    await purge_number_request_documents(rt_db.service_pool, dry_run=False, store=rt_db.store)
    deleted_at, _ = await state(rt_db, a, no_date)
    assert deleted_at is None and all(on_disk(rt_db, no_date_docs))
    deleted_at, rows = await state(rt_db, a, released)
    assert deleted_at is not None and not any(on_disk(rt_db, released_docs))
    assert all(r["wrapped_key"] is None and r["file_name"] is None for r in rows)


# --- the leftover sweep, for real: files on disk, rows in PostgreSQL ------------------

TWO_HOURS_AGO = (NOW - timedelta(hours=2)).timestamp()


def _file(fx, tenant, document=None, *, age_seconds=7200):
    """A published file with no row of its own. Returns (document_id, key, path)."""
    document = document or uuid4()
    key = f"{tenant}/{document}.tnr"
    path = fx.store.path_for(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"TNR1" + b"x" * 40)
    when = (NOW - timedelta(seconds=age_seconds)).timestamp()
    os.utime(path, (when, when))
    return document, key, path


async def _seed_with_old_files(fx, tenant, *, status="submitted"):
    """Like seed(), but the two files are two hours old."""
    rid, docs = await seed(fx, tenant, status=status, reviewed_at=OLD if status != "submitted" else None)
    for _, key in docs:
        os.utime(fx.store.path_for(key), (TWO_HOURS_AGO, TWO_HOURS_AGO))
    return rid, docs


async def _sweep(fx):
    return await purge_number_request_documents(fx.service_pool, dry_run=False, store=fx.store)


@pytest.mark.asyncio
async def test_sweep_keeps_live_young_and_unknown_account_files_removes_the_rest(rt_db):
    a = rt_db.tenants[0]
    _, live = await _seed_with_old_files(rt_db, a)  # old file, live row: KEPT
    _, gone = await _seed_with_old_files(rt_db, a)  # old file, row marked deleted: REMOVED
    async with acquire_with_tenant(rt_db.pool, None) as conn:
        await conn.execute(
            "UPDATE telephony_number_request_documents SET files_deleted_at = now(), wrapped_key = NULL, "
            "file_name = NULL WHERE tenant_id = $1 AND id = ANY($2::uuid[])", a, [d for d, _ in gone])
    _, _, no_row = _file(rt_db, a)  # old file, no row: REMOVED
    _, _, young = _file(rt_db, a, age_seconds=60)  # young file, no row: KEPT
    stranger = uuid4()  # an account id this database does not have
    _, _, foreign = _file(rt_db, stranger)  # KEPT

    result = await _sweep(rt_db)

    assert all(on_disk(rt_db, live))
    assert not any(on_disk(rt_db, gone))
    assert not os.path.exists(no_row)
    assert os.path.exists(young)
    assert os.path.exists(foreign)
    assert result["orphans_removed"] == 3 and result["account_failures"] == 1
    assert result["error"] is None


@pytest.mark.asyncio
async def test_sweep_brake_21_old_rowless_files_removes_none(rt_db):
    a = rt_db.tenants[0]
    paths = [_file(rt_db, a)[2] for _ in range(21)]
    result = await _sweep(rt_db)
    assert all(os.path.exists(p) for p in paths)
    assert result["error"] == "sweep_brake" and result["orphans_removed"] == 0


@pytest.mark.asyncio
async def test_sweep_20_old_rowless_files_are_all_removed(rt_db):
    a = rt_db.tenants[0]
    paths = [_file(rt_db, a)[2] for _ in range(20)]
    result = await _sweep(rt_db)
    assert not any(os.path.exists(p) for p in paths)
    assert result["error"] is None and result["orphans_removed"] == 20


@pytest.mark.asyncio
async def test_sweep_removes_nothing_when_the_account_setting_is_not_in_force(rt_db, monkeypatch):
    """A connection that never set the account: row security reads nothing; must not delete."""
    from contextlib import asynccontextmanager

    from app.workers import number_request_retention as nrr

    a = rt_db.tenants[0]
    _, live = await _seed_with_old_files(rt_db, a)
    _, _, no_row = _file(rt_db, a)

    @asynccontextmanager
    async def plain(pool, _tenant, **_kw):
        async with pool.acquire() as conn:
            async with conn.transaction():
                yield conn  # role is restricted, but no app.current_tenant_id is set

    monkeypatch.setattr(nrr, "acquire_with_tenant", plain)
    result = await _sweep(rt_db)
    assert os.path.exists(no_row) and all(on_disk(rt_db, live))
    assert result["orphans_removed"] == 0 and result["account_failures"] >= 1


@pytest.mark.asyncio
async def test_dry_run_changes_nothing_in_the_database_or_on_disk(rt_db, monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    a = rt_db.tenants[0]
    rid, docs = await seed(rt_db, a, status="rejected", reviewed_at=OLD)
    _, _, no_row = _file(rt_db, a)
    result = await purge_number_request_documents(rt_db.service_pool, dry_run=True, store=rt_db.store)
    deleted_at, rows = await state(rt_db, a, rid)
    assert deleted_at is None and all(r["wrapped_key"] and r["file_name"] for r in rows)
    assert all(on_disk(rt_db, docs)) and os.path.exists(no_row)
    assert result["dry_run"] is True and result["would_retire"] == 1
    assert result["would_remove_files"] == 2 and result["would_remove_orphans"] == 1
    assert result["requests"] == 0 and result["orphans_removed"] == 0

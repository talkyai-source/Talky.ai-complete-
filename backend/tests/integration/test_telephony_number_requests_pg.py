"""Number-request intake against real PostgreSQL (CI only: needs a disposable *_test database).

Proves the SQL, the unique idempotency row, the composite foreign key and forced row
security. Files go to a temp folder; the KMS is a fake that wraps keys.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant
from app.domain.services.number_requests import errors, service
from app.domain.services.number_requests.intake import SubmissionReader
from app.domain.services.number_requests.store import DocumentStore
from tests.integration.test_billing_price_options import billing_db  # noqa: F401

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"A" * 200
JPG = b"\xff\xd8\xff\xe0" + b"B" * 200


class FakeBackend:
    provider_name = "fake_kms"

    async def wrap_key(self, dek):
        return b"W" + dek


def build(parts, boundary="XBOUNDARYX"):
    out = b""
    for name, filename, data in parts:
        out += f"--{boundary}\r\n".encode()
        disp = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disp += f'; filename="{filename}"'
        out += disp.encode() + b"\r\n\r\n" + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode()


@pytest_asyncio.fixture
async def nr_db(billing_db, tmp_path, monkeypatch):  # noqa: F811
    fixture = billing_db
    monkeypatch.setenv("NUMBER_REQUEST_DOCUMENTS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr("app.core.kms.get_kms_backend", lambda: FakeBackend())
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            f'GRANT SELECT,INSERT ON telephony_number_requests,telephony_number_request_documents '
            f'TO "{fixture.role}"')
        await conn.execute(f'GRANT SELECT ON tenants TO "{fixture.role}"')

    async def role_setup(conn):
        await conn.execute(f'SET ROLE "{fixture.role}"')

    pool = await asyncpg.create_pool(
        os.environ["TEST_DATABASE_URL"], min_size=1, max_size=6, timeout=5, command_timeout=10,
        init=_register_jsonb_codecs, setup=role_setup,
    )
    fixture.service_pool = pool
    fixture.store = DocumentStore()
    fixture.root = str(tmp_path / "docs")
    fixture.user = str(uuid4())
    try:
        yield fixture
    finally:
        await pool.close()
        async with acquire_with_tenant(fixture.pool, None) as conn:
            await conn.execute(
                "DELETE FROM telephony_number_request_documents WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)
            await conn.execute(
                "DELETE FROM telephony_number_requests WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)


def _seal(fixture, tenant, files=None):
    parts = [("use_type", None, b"business"),
             ("business_registration", "r.pdf", (files or {}).get("a", PDF)),
             ("business_photo_id", "i.jpg", (files or {}).get("b", JPG))]
    reader = SubmissionReader("multipart/form-data; boundary=XBOUNDARYX", tenant_id=str(tenant),
                              store=fixture.store)
    body = build(parts)
    reader.feed(body)
    return reader.finish()


async def _submit(fixture, tenant, key, files=None, store=None):
    sealed = _seal(fixture, tenant, files)
    return await service.submit(
        fixture.service_pool, tenant_id=str(tenant), user_id=fixture.user,
        idempotency_key=key, sealed=sealed, store=store or fixture.store)


async def _counts(fixture, tenant):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        r = await conn.fetchval("SELECT count(*) FROM telephony_number_requests WHERE tenant_id=$1", tenant)
        d = await conn.fetchval(
            "SELECT count(*) FROM telephony_number_request_documents WHERE tenant_id=$1", tenant)
    return r, d


def _disk(root):
    return [os.path.join(b, n) for b, _d, names in os.walk(root) for n in names]


async def test_saved_together_one_request_two_documents_two_files(nr_db):
    out, replayed = await _submit(nr_db, nr_db.tenants[0], "pgkey-aaaaaaaa-1")
    assert replayed is False and out["status"] == "submitted"
    assert await _counts(nr_db, nr_db.tenants[0]) == (1, 2)
    assert len([f for f in _disk(nr_db.root) if f.endswith(".tnr")]) == 2
    assert [f for f in _disk(nr_db.root) if f.endswith(".part")] == []


async def test_store_failure_on_second_publish_leaves_no_row_and_no_file(nr_db):
    class Failing(DocumentStore):
        n = 0

        def publish(self, temp_path, key):
            self.n += 1
            if self.n == 2:
                raise OSError("disk full")
            super().publish(temp_path, key)

    with pytest.raises(errors.StorageUnavailable):
        await _submit(nr_db, nr_db.tenants[0], "pgkey-aaaaaaaa-2", store=Failing())
    assert await _counts(nr_db, nr_db.tenants[0]) == (0, 0)
    assert _disk(nr_db.root) == [] or all(not f.endswith((".tnr", ".part")) for f in _disk(nr_db.root))


async def test_same_key_same_files_replays_and_different_files_is_refused(nr_db):
    t = nr_db.tenants[0]
    first, _ = await _submit(nr_db, t, "pgkey-aaaaaaaa-3")
    again, replayed = await _submit(nr_db, t, "pgkey-aaaaaaaa-3")
    assert replayed is True and again["id"] == first["id"]
    with pytest.raises(errors.NumberRequestRefused) as info:
        await _submit(nr_db, t, "pgkey-aaaaaaaa-3", files={"a": PDF + b"x"})
    assert info.value.status == 400 and info.value.code == "idempotency_key_reused"
    assert await _counts(nr_db, t) == (1, 2)


async def test_concurrent_twin_submits_give_one_row(nr_db):
    t = nr_db.tenants[0]
    results = await asyncio.gather(
        _submit(nr_db, t, "pgkey-aaaaaaaa-4"), _submit(nr_db, t, "pgkey-aaaaaaaa-4"))
    assert sorted(r[1] for r in results) == [False, True]
    assert results[0][0]["id"] == results[1][0]["id"]
    assert await _counts(nr_db, t) == (1, 2)


async def test_other_account_reusing_a_key_gets_its_own_row(nr_db):
    a, _ = await _submit(nr_db, nr_db.tenants[0], "pgkey-aaaaaaaa-5")
    b, replayed = await _submit(nr_db, nr_db.tenants[1], "pgkey-aaaaaaaa-5")
    assert replayed is False and a["id"] != b["id"]


async def test_forced_row_security_hides_other_accounts(nr_db):
    await _submit(nr_db, nr_db.tenants[0], "pgkey-aaaaaaaa-6")
    await _submit(nr_db, nr_db.tenants[1], "pgkey-aaaaaaaa-6")
    async with acquire_with_tenant(nr_db.service_pool, nr_db.tenants[1]) as conn:
        assert await conn.fetchval("SELECT count(*) FROM telephony_number_requests") == 1
        assert await conn.fetchval("SELECT count(*) FROM telephony_number_request_documents") == 2


async def test_composite_foreign_key_rejects_a_document_across_accounts(nr_db):
    out, _ = await _submit(nr_db, nr_db.tenants[0], "pgkey-aaaaaaaa-7")
    doc = uuid4()
    async with acquire_with_tenant(nr_db.pool, None) as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO telephony_number_request_documents "
                "(id, tenant_id, request_id, kind, storage_key, file_name, content_type, size_bytes, sha256, "
                "wrapped_key, key_provider) VALUES ($1,$2,$3,'business_registration',$4,'x.pdf','application/pdf',"
                "1,$5,$6,'fake_kms')",
                doc, nr_db.tenants[1], out["id"], f"{nr_db.tenants[1]}/{doc}.tnr", "0" * 64, b"w")


async def test_account_b_sending_account_a_key_gets_its_own_new_request(nr_db):
    key = "pgkey-aaaaaaaa-8"
    a, a_replayed = await _submit(nr_db, nr_db.tenants[0], key)
    b, b_replayed = await _submit(nr_db, nr_db.tenants[1], key, files={"a": PDF + b"b-only"})
    assert a_replayed is False and b_replayed is False
    assert b["id"] != a["id"]
    assert b["documents"][0]["size_bytes"] == len(PDF) + len(b"b-only")  # B's own files, not A's answer
    assert await _counts(nr_db, nr_db.tenants[0]) == (1, 2)
    assert await _counts(nr_db, nr_db.tenants[1]) == (1, 2)
    again, replayed = await _submit(nr_db, nr_db.tenants[0], key)
    assert replayed is True and again["id"] == a["id"]


async def test_cap_on_waiting_requests_holds_on_real_rows(nr_db):
    from app.domain.services.number_requests import config
    t, other = nr_db.tenants[0], nr_db.tenants[1]
    for n in range(config.MAX_OPEN_REQUESTS_PER_ACCOUNT):
        await _submit(nr_db, t, f"pgkey-aaaaaaaa-c{n}")
    with pytest.raises(errors.NumberRequestRefused) as info:
        await _submit(nr_db, t, "pgkey-aaaaaaaa-c9")
    assert info.value.status == 429 and info.value.code == "too_many_open_requests"
    assert await _counts(nr_db, t) == (config.MAX_OPEN_REQUESTS_PER_ACCOUNT, 2 * config.MAX_OPEN_REQUESTS_PER_ACCOUNT)
    # a replay is still answered, and another account is not affected
    _, replayed = await _submit(nr_db, t, "pgkey-aaaaaaaa-c0")
    assert replayed is True
    await _submit(nr_db, other, "pgkey-aaaaaaaa-c9")
    # a decided request no longer counts
    async with acquire_with_tenant(nr_db.pool, None) as conn:
        await conn.execute(
            "UPDATE telephony_number_requests "
            "SET status='rejected', reviewed_at=NOW(), reviewed_by_user_id=$3 "
            "WHERE tenant_id=$1 AND idempotency_key=$2",
            t, "pgkey-aaaaaaaa-c0", uuid4())
    await _submit(nr_db, t, "pgkey-aaaaaaaa-c9")

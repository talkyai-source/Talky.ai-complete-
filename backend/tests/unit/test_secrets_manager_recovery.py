"""F8-09: restored secrets refuse invalid key material with a durable safe audit."""

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.domain.services.secrets_manager import SecretAccessType, SecretsManager


# Synthetic key material only; these tests never read configured secrets.
MASTER = b"r" * 32
WRONG_MASTER = b"w" * 32
VALUE = {"token": "synthetic-recovery-secret"}


class _Connection:
    def __init__(self):
        self.row = None
        self.committed = []
        self.pending = []
        self.audit_error = None
        self.transaction_active = False

    @asynccontextmanager
    async def transaction(self):
        self.transaction_active = True
        try:
            yield
        except BaseException:
            self.pending.clear()
            raise
        else:
            self.committed.extend(self.pending)
            self.pending.clear()
        finally:
            self.transaction_active = False

    async def fetchrow(self, query, secret_id):
        assert self.transaction_active
        assert "WHERE secret_id = $1" in query
        assert self.row is None or self.row["secret_id"] == secret_id
        return self.row

    async def execute(self, query, *args):
        assert self.transaction_active
        sql = " ".join(query.split())
        if sql.startswith("SET LOCAL "):
            assert not args
            return "SET"
        if sql.startswith("INSERT INTO secret_access_log") and self.audit_error:
            raise self.audit_error
        assert sql.startswith(("INSERT INTO secret_access_log", "UPDATE tenant_secrets"))
        self.pending.append((sql, args))
        return "OK"


class _Pool:
    def __init__(self, conn):
        self.conn = conn
        self.releases = 0

    @asynccontextmanager
    async def acquire(self):
        try:
            yield self.conn
        finally:
            self.releases += 1


def _fixture():
    conn = _Connection()
    pool = _Pool(conn)
    manager = SecretsManager(pool, master_key=MASTER)
    dek = manager._generate_dek()
    ciphertext, iv = manager._encrypt_value(VALUE, dek)
    conn.row = {
        "secret_id": uuid4(),
        "tenant_id": uuid4(),
        "is_active": True,
        "encrypted_dek": manager._encrypt_dek(dek),
        "encrypted_value": ciphertext,
        "iv": iv,
    }
    return manager, conn, pool


async def _assert_refused(manager, conn, pool, caplog):
    accessor = uuid4()
    assert await manager.get(
        str(conn.row["secret_id"]), str(accessor), "Recovery drill", SecretAccessType.VALIDATE
    ) is None
    assert pool.releases == 1
    assert not conn.pending
    # A committed failed audit, with no successful access update or second row.
    assert len(conn.committed) == 1
    sql, args = conn.committed[0]
    assert sql.startswith("INSERT INTO secret_access_log")
    assert "failure_reason" in sql
    assert args == (
        conn.row["secret_id"], conn.row["tenant_id"], accessor,
        "validate", "Recovery drill", False, "Decryption failed",
    )
    assert VALUE["token"] not in repr(conn.committed) + caplog.text
    assert MASTER.decode() not in repr(conn.committed) + caplog.text
    assert WRONG_MASTER.decode() not in repr(conn.committed) + caplog.text


@pytest.mark.asyncio
async def test_wrong_master_key_refuses_and_commits_exactly_one_safe_failure_audit(caplog):
    manager, conn, pool = _fixture()
    original_row = dict(conn.row)
    wrong_manager = SecretsManager(pool, master_key=WRONG_MASTER)

    await _assert_refused(wrong_manager, conn, pool, caplog)

    assert conn.row == original_row
    # A failed restore attempt does not destroy data: restoring the key works.
    assert await manager.get(conn.row["secret_id"]) == VALUE


@pytest.mark.asyncio
async def test_correct_key_preserves_success_audit_and_access_tracking():
    manager, conn, pool = _fixture()
    accessor = uuid4()

    assert await manager.get(conn.row["secret_id"], accessor, "Recovery drill") == VALUE

    assert pool.releases == 1
    assert len(conn.committed) == 2
    update, audit = conn.committed
    assert "access_count = access_count + 1" in update[0]
    # F2-03: the write names the tenant of the row just read, not the id alone.
    assert "tenant_id IS NOT DISTINCT FROM $3" in update[0]
    assert update[1] == (accessor, conn.row["secret_id"], conn.row["tenant_id"])
    assert audit[0].startswith("INSERT INTO secret_access_log")
    assert audit[1] == (
        conn.row["secret_id"], conn.row["tenant_id"], accessor, "read", "Recovery drill", True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["encrypted_dek", "encrypted_value", "iv"])
@pytest.mark.parametrize("damage", ["missing", "corrupt", "truncated"])
async def test_damaged_restored_envelope_is_refused_with_one_safe_audit(field, damage, caplog):
    manager, conn, pool = _fixture()
    original = conn.row[field]
    if damage == "missing":
        conn.row[field] = None
    elif damage == "corrupt":
        conn.row[field] = bytes([original[0] ^ 1]) + original[1:]
    else:
        conn.row[field] = original[:1]

    await _assert_refused(manager, conn, pool, caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["_decrypt_dek", "_decrypt_value"])
async def test_failure_audit_does_not_copy_exception_details(stage, monkeypatch, caplog):
    manager, conn, pool = _fixture()
    ciphertext_hex = conn.row["encrypted_value"].hex()

    def fail_with_sensitive_details(*args):
        raise ValueError(f"{VALUE['token']} {MASTER.decode()} {ciphertext_hex}")

    monkeypatch.setattr(manager, stage, fail_with_sensitive_details)

    await _assert_refused(manager, conn, pool, caplog)
    assert ciphertext_hex not in repr(conn.committed) + caplog.text


@pytest.mark.asyncio
async def test_missing_secret_preserves_no_audit_no_update_behavior():
    manager, conn, pool = _fixture()
    conn.row = None

    assert await manager.get(uuid4()) is None
    assert conn.committed == []
    assert pool.releases == 1


@pytest.mark.asyncio
async def test_inactive_secret_preserves_refusal_without_decryption():
    manager, conn, pool = _fixture()
    conn.row.update(is_active=False, encrypted_dek=None)

    assert await manager.get(conn.row["secret_id"]) is None
    assert len(conn.committed) == 1
    assert conn.committed[0][1][-2:] == (False, "Secret is not active")
    assert pool.releases == 1


@pytest.mark.asyncio
async def test_audit_write_failure_is_not_swallowed_or_replaced():
    _, conn, pool = _fixture()
    manager = SecretsManager(pool, master_key=WRONG_MASTER)
    conn.audit_error = RuntimeError("synthetic audit unavailable")

    with pytest.raises(RuntimeError) as raised:
        await manager.get(conn.row["secret_id"])

    assert raised.value is conn.audit_error
    assert conn.committed == []
    assert conn.pending == []
    assert pool.releases == 1


def test_missing_master_key_in_production_still_refuses_initialization(monkeypatch):
    for name in ("SECRETS_MASTER_KEY", "JWT_SECRET", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="SECRETS_MASTER_KEY must be configured"):
        SecretsManager(_Pool(_Connection()))

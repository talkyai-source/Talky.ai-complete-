"""bughunt billing-money-credentials-7 (proper fix) - validate_api_key no longer
unwraps and decrypts every tenant API key on every request.

The route is unauthenticated, so each request used to cost one DEK unwrap and
one AES-GCM decrypt PER ACTIVE KEY ROW. Migration 0068 adds
tenant_secrets.lookup_hash (hex SHA-256 of the issued key); create()/rotate()
store it; validate_api_key selects the row(s) with the presented key's hash and
decrypts only those. Rows created before the column existed (lookup_hash IS NULL)
are the fallback and are backfilled as they are read.

These tests use a stand-in tenant_secrets that answers the two lookups the way
Postgres does; the same behaviour on a real Postgres, the migration and the
query plan are in test_bh2_cred_secret_lookup_pg.py.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.services import secrets_manager as sm
from app.domain.services.secrets_manager import SecretsManager, SecretType

MASTER = b"k" * 32


def _sha256(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class _Ctx:
    def __init__(self, conn):
        self._c = conn

    async def __aenter__(self):
        return self._c

    async def __aexit__(self, *exc):
        return False


class _Conn:
    """tenant_secrets stand-in. fetch() filters on lookup_hash exactly as the SQL
    does; a query that does not mention lookup_hash gets every live key row (the
    pre-0068 behaviour)."""

    def __init__(self, rows):
        self.rows = rows
        self.fetches: list[tuple[str, tuple]] = []
        self.executed: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Ctx(self)

    async def fetch(self, query, *args):
        sql = " ".join(query.split())
        self.fetches.append((sql, args))
        live = [r for r in self.rows if r["is_active"] and r["secret_type"] == "TENANT_API_KEY"]
        if re.search(r"lookup_hash\s*=\s*\$1", sql):
            return [r for r in live if r["lookup_hash"] == args[0]]
        if "lookup_hash IS NULL" in sql:
            return [r for r in live if r["lookup_hash"] is None]
        return live

    async def fetchrow(self, query, *args):
        for r in self.rows:
            if str(r["secret_id"]) == str(args[0]):
                return r
        return None

    async def execute(self, query, *args):
        sql = " ".join(query.split())
        self.executed.append((sql, args))
        if sql.startswith("UPDATE tenant_secrets SET lookup_hash"):
            for r in self.rows:
                if r["secret_id"] == args[1] and r["lookup_hash"] is None:
                    r["lookup_hash"] = args[0]
        return "OK"

    def writes(self, prefix):
        return [(s, a) for s, a in self.executed if s.startswith(prefix)]


class _Pool:
    def __init__(self, conn):
        self._c = conn

    def acquire(self, *a, **k):
        return _Ctx(self._c)


def _manager(rows=None):
    conn = _Conn(rows if rows is not None else [])
    return SecretsManager(_Pool(conn), master_key=MASTER, redis_client=None), conn


def _row(mgr, secret_type, value, *, key=None, indexed=True, tenant=None, permissions=("calls:read",)):
    dek = mgr._generate_dek()
    ciphertext, iv = mgr._encrypt_value(value, dek)
    created = datetime.utcnow() - timedelta(days=1)
    return {
        "secret_id": uuid4(),
        "tenant_id": tenant or uuid4(),
        "secret_type": secret_type,
        "secret_name": "n",
        "description": "d",
        "version": 1,
        "created_at": created,
        "expires_at": created + timedelta(days=30),
        "permissions": {"permissions": list(permissions)},
        "encrypted_value": ciphertext,
        "encrypted_dek": mgr._encrypt_dek(dek),
        "iv": iv,
        "is_active": True,
        "lookup_hash": _sha256(key) if (key and indexed) else None,
    }


def _key_row(mgr, key, **kwargs):
    return _row(
        mgr, "TENANT_API_KEY", {"api_key_hash": _sha256(key), "prefix": key[:16]}, key=key, **kwargs
    )


def _count_unwraps(mgr):
    calls: list[int] = []
    real = mgr._decrypt_dek

    def counting(blob):
        calls.append(1)
        return real(blob)

    mgr._decrypt_dek = counting
    return calls


def _issue_keys(mgr, n, **kwargs):
    keys = [mgr._generate_api_key() for _ in range(n)]
    return keys, [_key_row(mgr, k, **kwargs) for k in keys]


# ----------------------------------------------------------- validate: indexed


@pytest.mark.asyncio
async def test_valid_key_unwraps_only_the_row_found_by_hash():
    mgr, conn = _manager()
    keys, rows = _issue_keys(mgr, 25)
    conn.rows.extend(rows)
    unwraps = _count_unwraps(mgr)

    result = await mgr.validate_api_key(keys[17])

    assert result is not None and result["tenant_id"] == rows[17]["tenant_id"]
    assert result["secret_id"] == rows[17]["secret_id"]
    # Was 18: every row ahead of the match was unwrapped and decrypted too.
    assert len(unwraps) == 1
    assert conn.fetches[0][1] == (_sha256(keys[17]),), "first lookup is by the presented key's hash"
    assert len(conn.fetches) == 1, "a hit never reaches the legacy fallback"


@pytest.mark.asyncio
async def test_unknown_key_unwraps_nothing_once_every_row_is_indexed(caplog):
    mgr, conn = _manager()
    _, rows = _issue_keys(mgr, 25)
    conn.rows.extend(rows)
    unwraps = _count_unwraps(mgr)

    with caplog.at_level(logging.WARNING, logger=sm.__name__):
        assert await mgr.validate_api_key(mgr._generate_api_key()) is None

    assert len(unwraps) == 0  # was 25 per request
    assert "api_key_validation_failed" in " ".join(r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_key_without_the_prefix_never_reaches_the_database():
    mgr, conn = _manager()
    assert await mgr.validate_api_key("sk_live_" + "A" * 48) is None
    assert conn.fetches == []


@pytest.mark.asyncio
async def test_inactive_rows_are_not_candidates():
    mgr, conn = _manager()
    keys, rows = _issue_keys(mgr, 2)
    rows[0]["is_active"] = False
    conn.rows.extend(rows)
    assert await mgr.validate_api_key(keys[0]) is None
    assert await mgr.validate_api_key(keys[1]) is not None


@pytest.mark.asyncio
async def test_lookup_hash_only_narrows_candidates_the_decrypted_hash_decides():
    """A row indexed under the hash of a DIFFERENT key must not authenticate it.

    lookup_hash is plaintext metadata used only to find the candidate row; the
    authority is still the hash inside the encrypted value.
    """
    mgr, conn = _manager()
    own_key = mgr._generate_api_key()
    other_key = mgr._generate_api_key()
    row = _key_row(mgr, own_key)  # the encrypted value carries sha256(own_key)
    row["lookup_hash"] = _sha256(other_key)  # ... but the index says it is other_key's row
    conn.rows.append(row)

    unwraps = _count_unwraps(mgr)
    assert await mgr.validate_api_key(other_key) is None
    assert len(unwraps) == 1, "the indexed candidate was decrypted and then rejected"
    assert conn.writes("INSERT INTO secret_access_log") == []
    assert conn.writes("UPDATE tenant_secrets SET last_accessed_at") == []


# ------------------------------------------------------------ validate: legacy


@pytest.mark.asyncio
async def test_legacy_key_is_found_by_the_fallback_and_backfilled():
    mgr, conn = _manager()
    indexed_keys, indexed = _issue_keys(mgr, 20)
    legacy_keys, legacy = _issue_keys(mgr, 3, indexed=False)
    conn.rows.extend(indexed + legacy)
    unwraps = _count_unwraps(mgr)

    first = await mgr.validate_api_key(legacy_keys[1])
    assert first is not None and first["secret_id"] == legacy[1]["secret_id"]
    assert len(unwraps) <= 3, "only lookup_hash IS NULL rows may be unwrapped, never the 20 indexed ones"
    assert legacy[1]["lookup_hash"] == _sha256(legacy_keys[1])
    backfill = conn.writes("UPDATE tenant_secrets SET lookup_hash")
    assert (_sha256(legacy_keys[1]), legacy[1]["secret_id"], legacy[1]["tenant_id"]) in [a for _, a in backfill]
    assert all(
        "secret_id = $2" in s and "tenant_id IS NOT DISTINCT FROM $3" in s and "lookup_hash IS NULL" in s
        for s, _ in backfill
    )

    unwraps.clear()
    conn.fetches.clear()
    again = await mgr.validate_api_key(legacy_keys[1])
    assert again is not None
    assert len(unwraps) == 1 and len(conn.fetches) == 1, "second use is an indexed hit"


@pytest.mark.asyncio
async def test_fallback_backfills_every_legacy_row_it_had_to_decrypt():
    """Probing with an unknown key must stop costing CPU after the first pass."""
    mgr, conn = _manager()
    keys, legacy = _issue_keys(mgr, 4, indexed=False)
    conn.rows.extend(legacy)
    unwraps = _count_unwraps(mgr)

    assert await mgr.validate_api_key(mgr._generate_api_key()) is None
    assert len(unwraps) == 4
    assert [r["lookup_hash"] for r in legacy] == [_sha256(k) for k in keys]

    unwraps.clear()
    assert await mgr.validate_api_key(mgr._generate_api_key()) is None
    assert len(unwraps) == 0
    for key, row in zip(keys, legacy):  # and every key still validates, by index
        assert (await mgr.validate_api_key(key))["secret_id"] == row["secret_id"]
    assert len(unwraps) == 4


@pytest.mark.asyncio
async def test_a_row_whose_stored_hash_is_not_a_sha256_is_never_given_a_lookup_hash():
    mgr, conn = _manager()
    odd = _row(mgr, "TENANT_API_KEY", {"api_key_hash": "nope"}, indexed=False)
    conn.rows.append(odd)
    assert await mgr.validate_api_key(mgr._generate_api_key()) is None
    assert odd["lookup_hash"] is None
    assert conn.writes("UPDATE tenant_secrets SET lookup_hash") == []


@pytest.mark.asyncio
async def test_unreadable_legacy_row_is_logged_and_does_not_hide_the_good_one(caplog):
    mgr, conn = _manager()
    good_key = mgr._generate_api_key()
    bad = _key_row(mgr, mgr._generate_api_key(), indexed=False)
    bad["encrypted_dek"] = b"\x00" * 40  # cannot be unwrapped
    good = _key_row(mgr, good_key, indexed=False)
    conn.rows.extend([bad, good])

    with caplog.at_level(logging.WARNING, logger=sm.__name__):
        result = await mgr.validate_api_key(good_key)

    assert result is not None and result["secret_id"] == good["secret_id"]
    assert any("dek" in r.getMessage().lower() for r in caplog.records)
    assert bad["lookup_hash"] is None  # unreadable rows stay in the fallback set


@pytest.mark.asyncio
async def test_failed_validation_log_carries_no_key_material(caplog):
    mgr, conn = _manager()
    _, rows = _issue_keys(mgr, 3, indexed=False)
    conn.rows.extend(rows)
    presented = mgr.API_KEY_PREFIX + "Z" * 48
    with caplog.at_level(logging.DEBUG, logger=sm.__name__):
        assert await mgr.validate_api_key(presented) is None
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "api_key_validation_failed" in messages
    assert "ZZZZ" not in messages and _sha256(presented) not in messages


# ---------------------------------------------------------- comparison + paths


@pytest.mark.asyncio
async def test_hash_comparison_is_constant_time(monkeypatch):
    mgr, conn = _manager()
    keys, rows = _issue_keys(mgr, 2)
    conn.rows.extend(rows)
    compared: list[tuple] = []
    real = sm.hmac.compare_digest

    def spy(a, b):
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(sm.hmac, "compare_digest", spy)
    assert await mgr.validate_api_key(keys[0]) is not None
    assert compared, "the stored hash must be compared with hmac.compare_digest"


@pytest.mark.asyncio
async def test_missing_permission_is_rejected_and_logged_but_a_granted_one_passes():
    mgr, conn = _manager()
    keys, rows = _issue_keys(mgr, 1, permissions=("calls:read",))
    conn.rows.extend(rows)

    assert await mgr.validate_api_key(keys[0], required_permission="calls:write") is None
    denied = conn.writes("INSERT INTO secret_access_log")
    assert len(denied) == 1 and denied[0][1][-2:] == (False, "Permission denied")
    assert conn.writes("UPDATE tenant_secrets SET last_accessed_at") == []

    ok = await mgr.validate_api_key(keys[0], required_permission="calls:read")
    assert ok == {
        "secret_id": rows[0]["secret_id"],
        "tenant_id": rows[0]["tenant_id"],
        "permissions": ["calls:read"],
        "is_test": False,
    }
    assert len(conn.writes("INSERT INTO secret_access_log")) == 2
    assert len(conn.writes("UPDATE tenant_secrets SET last_accessed_at")) == 1


@pytest.mark.asyncio
async def test_test_keys_are_reported_as_test_keys():
    mgr, conn = _manager()
    key = mgr._generate_api_key(test=True)
    conn.rows.append(_key_row(mgr, key))
    assert (await mgr.validate_api_key(key))["is_test"] is True


# ------------------------------------------------- writers store the lookup hash


def _insert_args(conn):
    (statement, args), = conn.writes("INSERT INTO tenant_secrets")
    return args


@pytest.mark.asyncio
async def test_create_stores_the_sha256_of_the_issued_key_as_lookup_hash():
    mgr, conn = _manager()
    secret_id, api_key = await mgr.create(
        SecretType.TENANT_API_KEY, "tenant", uuid4(), {}, secret_name="k", permissions={"permissions": []}
    )
    args = _insert_args(conn)
    assert isinstance(secret_id, UUID)
    assert args[14] == _sha256(api_key)
    # ... and it is the same value the encrypted blob carries.
    dek = mgr._decrypt_dek(args[7])
    assert mgr._decrypt_value(args[6], args[8], dek)["api_key_hash"] == args[14]


@pytest.mark.asyncio
async def test_create_leaves_lookup_hash_empty_for_other_secret_types():
    mgr, conn = _manager()
    await mgr.create(SecretType.WEBHOOK_HMAC, "tenant", uuid4(), {"secret": "s"}, secret_name="w")
    assert _insert_args(conn)[14] is None


@pytest.mark.asyncio
async def test_rotate_stores_the_hash_of_the_new_key_and_leaves_other_types_empty():
    mgr, conn = _manager()
    old_key = mgr._generate_api_key()
    old = _key_row(mgr, old_key)
    conn.rows.append(old)
    _, new_key = await mgr.rotate(old["secret_id"], rotated_by=uuid4(), tenant_id=old["tenant_id"])
    args = _insert_args(conn)
    assert args[15] == _sha256(new_key) != _sha256(old_key)
    assert args[12] == old["secret_id"]  # rotated_from: positions before lookup_hash are unchanged

    mgr, conn = _manager()
    hmac_row = _row(mgr, "WEBHOOK_HMAC", {"secret": "old"})
    conn.rows.append(hmac_row)
    await mgr.rotate(
        hmac_row["secret_id"], rotated_by=uuid4(), tenant_id=hmac_row["tenant_id"], new_value={"secret": "new"}
    )
    assert _insert_args(conn)[15] is None

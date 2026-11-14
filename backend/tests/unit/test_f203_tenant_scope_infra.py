"""F2-03 (platform-infrastructure slice): secret writes name the tenant of the secret they just read.

Production has run with an application role that bypasses row-level security, so a
statement that does not name its tenant is held back by nothing but its id.
``SecretsManager.get``, ``rotate``, ``revoke`` and ``mark_compromised`` each read a
secret row and then write that same row by ``secret_id`` alone. Every such write now
also names the tenant of the row just read, with ``IS NOT DISTINCT FROM`` because a
platform-owned secret has a NULL ``tenant_id`` (an equality would never match it, and
revoking it would silently do nothing).

Each test runs a real ``SecretsManager`` method on a connection that records
``(sql, args)`` and checks two things about the UPDATE that follows the read:

* the tenant predicate is in the SQL and it is bound to the tenant of the row the
  method has just read, not to the caller's ``tenant_id`` argument (a platform caller
  passes none) and not to a constant;
* executed on SQLite beside another tenant's value, the statement still matches the
  row it came from (a platform-owned row through a NULL bind) and matches nothing
  once the bound tenant is anyone else's.

The other baseline entries of this slice (token, sign-in, role, passkey, admin
directory and database-layer statements) are recorded as reviewed exceptions in
``tenant_sql_baseline.json`` without a code change. Section 2 pins the two claims
those reasons lean on most (a refresh family never spans tenants; role grants bind
the membership tenant) so they stay true if the code is rewritten.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.security.rbac import get_user_permissions
from app.core.security.refresh_tokens import _hash_token, revoke_all_user_refresh_tokens, rotate_refresh_token
from app.domain.services.secrets_manager import SecretsManager

TENANT_A = UUID("a1111111-1111-4111-8111-111111111111")
TENANT_B = UUID("b2222222-2222-4222-8222-222222222222")
SECRET_ID = UUID("c3333333-3333-4333-8333-333333333333")
MASTER = b"i" * 32  # synthetic key material; these tests never read a configured secret
VALUE = {"token": "synthetic-infra-secret"}


class RecordingConn:
    """Answers the one read each method makes and records every statement it is sent."""

    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return self.row

    async def execute(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return "UPDATE 1"


class RecordingPool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def build(owner):
    """A manager over one active secret owned by ``owner`` (None = platform-owned)."""
    conn = RecordingConn(None)
    manager = SecretsManager(RecordingPool(conn), master_key=MASTER)
    dek = manager._generate_dek()
    ciphertext, iv = manager._encrypt_value(VALUE, dek)
    created = datetime.utcnow() - timedelta(days=10)
    conn.row = {
        "secret_id": SECRET_ID, "tenant_id": owner, "secret_type": "WEBHOOK_HMAC",
        "secret_name": "hmac", "description": "d", "version": 1, "created_at": created,
        "expires_at": created + timedelta(days=90), "permissions": {}, "is_active": True,
        "encrypted_dek": manager._encrypt_dek(dek), "encrypted_value": ciphertext, "iv": iv,
    }
    return manager, conn


# Each flow is run by a caller that supplies no tenant (a platform caller): the only
# source of the row's tenant is the read the method makes itself.
async def run_get(manager, secret_id, tenant):
    assert await manager.get(secret_id, uuid4(), "unit test") == VALUE


async def run_rotate(manager, secret_id, tenant):
    await manager.rotate(secret_id, uuid4(), new_value={"token": "rotated"}, tenant_id=tenant)


async def run_revoke(manager, secret_id, tenant):
    assert await manager.revoke(secret_id, uuid4(), "unit test", tenant_id=tenant) is True


async def run_compromise(manager, secret_id, tenant):
    assert await manager.mark_compromised(secret_id, uuid4(), "unit test", tenant_id=tenant) is True


# label, flow, text found in the UPDATE it sends
CASES = [
    ("get: access tracking", run_get, "SET last_accessed_at = NOW()"),
    ("rotate: old version linked to its successor", run_rotate, "SET rotated_to = $1"),
    ("rotate: old version's grace-period expiry", run_rotate, "SET expires_at = LEAST(expires_at, $1)"),
    ("revoke: secret deactivated", run_revoke, "SET is_active = FALSE, revoked_at = NOW()"),
    ("mark_compromised: secret deactivated and flagged", run_compromise, "SET is_active = FALSE, is_compromised = TRUE"),
]
IDS = [case[0] for case in CASES]
OWNERS = pytest.mark.parametrize("owner", [TENANT_A, None], ids=["tenant-owned", "platform-owned"])


def written(conn, marker):
    """The one UPDATE of tenant_secrets the flow sent that contains ``marker``."""
    hits = [(sql, args) for sql, args in conn.calls
            if sql.startswith("UPDATE tenant_secrets") and marker in sql]
    assert len(hits) == 1, f"expected exactly one UPDATE containing {marker!r}, got {len(hits)}"
    return hits[0]


def slot(sql, column):
    """Zero-based position of the bind parameter ``column`` is compared with."""
    pattern = rf"\b{column}\s+IS\s+NOT\s+DISTINCT\s+FROM\s+\$(\d+)" if column == "tenant_id" else rf"\b{column}\s*=\s*\$(\d+)"
    match = re.search(pattern, sql, re.I)
    assert match, f"the statement does not constrain {column}: {sql}"
    return int(match.group(1)) - 1


@OWNERS
@pytest.mark.parametrize("label,flow,marker", CASES, ids=IDS)
async def test_write_names_the_tenant_of_the_row_it_read(label, flow, marker, owner):
    manager, conn = build(owner)
    await flow(manager, SECRET_ID, None)
    sql, args = written(conn, marker)
    assert args[slot(sql, "secret_id")] == SECRET_ID
    assert args[slot(sql, "tenant_id")] == owner, "the write must bind the tenant of the secret it read"


@pytest.mark.parametrize("label,flow,marker", CASES[1:], ids=IDS[1:])
async def test_tenant_scoped_call_binds_the_same_tenant(label, flow, marker):
    manager, conn = build(TENANT_A)
    await flow(manager, SECRET_ID, TENANT_A)
    sql, args = written(conn, marker)
    assert args[slot(sql, "tenant_id")] == TENANT_A


def rows_changed(sql, args, *, row_owner):
    """Run the recorded UPDATE on SQLite against one secret owned by ``row_owner``."""
    db = sqlite3.connect(":memory:")
    db.create_function("NOW", 0, lambda: "2026-11-13T00:00:00")
    db.create_function("LEAST", 2, min)
    db.execute(
        "CREATE TABLE tenant_secrets (secret_id TEXT, tenant_id TEXT, last_accessed_at TEXT,"
        " last_accessed_by TEXT, access_count INTEGER DEFAULT 0, rotated_to TEXT, expires_at TEXT,"
        " is_active INTEGER DEFAULT 1, is_compromised INTEGER DEFAULT 0, revoked_at TEXT,"
        " revoked_reason TEXT)"
    )
    db.execute(
        "INSERT INTO tenant_secrets (secret_id, tenant_id, expires_at) VALUES (?, ?, ?)",
        (str(SECRET_ID), None if row_owner is None else str(row_owner), "2099-01-01T00:00:00"),
    )
    bound = [a.isoformat() if isinstance(a, datetime) else str(a) if isinstance(a, UUID) else a for a in args]
    return db.execute(re.sub(r"\$(\d+)", r"?\1", sql), bound).rowcount


@OWNERS
@pytest.mark.parametrize("label,flow,marker", CASES, ids=IDS)
async def test_statement_matches_only_its_own_tenants_row(label, flow, marker, owner):
    manager, conn = build(owner)
    await flow(manager, SECRET_ID, None)
    sql, args = written(conn, marker)
    position = slot(sql, "tenant_id")
    assert rows_changed(sql, args, row_owner=owner) == 1, "the bound tenant must still match its own row"
    stranger = TENANT_B if owner is not None else TENANT_A
    other = list(args)
    other[position] = stranger
    assert rows_changed(sql, other, row_owner=owner) == 0, "another tenant's value must match nothing"
    if owner is not None:
        unowned = list(args)
        unowned[position] = None
        assert rows_changed(sql, unowned, row_owner=owner) == 0, "a NULL bind must not match a tenant-owned row"


# ---------------------------------------------------------------------------
# Section 2 - claims the baseline exceptions of this slice rest on. These pass before
# and after the change; they keep the reasons recorded in tenant_sql_baseline.json
# true if the code they describe is ever rewritten.
# ---------------------------------------------------------------------------
FAMILY = UUID("d4444444-4444-4444-8444-444444444444")
TOKEN_ID = UUID("e5555555-5555-4555-8555-555555555555")
USER = UUID("f6666666-6666-4666-8666-666666666666")


class TokenConn:
    """Answers the one refresh_tokens read and records every statement after it."""

    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return self.row

    async def fetchval(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return False

    async def execute(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return "UPDATE 1"


def token_row(tenant, **over):
    now = datetime.now(timezone.utc)
    row = {"id": TOKEN_ID, "family_id": FAMILY, "user_id": USER, "tenant_id": tenant,
           "expires_at": now + timedelta(days=3), "used_at": None, "revoked_at": None, "session_id": None}
    return {**row, **over}


@pytest.mark.parametrize("tenant", [TENANT_A, None], ids=["tenant-session", "platform-session"])
async def test_rotation_finds_the_row_by_hash_and_its_successor_copies_the_tenant(tenant):
    conn = TokenConn(token_row(tenant))
    assert await rotate_refresh_token(conn, presented_token="opaque-cookie") is not None
    assert conn.calls[0][1] == (_hash_token("opaque-cookie"),), "the row is found by the hash of the cookie"
    consumed = [args for sql, args in conn.calls if sql.startswith("UPDATE refresh_tokens SET used_at")]
    assert [args[0] for args in consumed] == [TOKEN_ID], "only the row just located is marked consumed"
    (insert,) = [args for sql, args in conn.calls if sql.startswith("INSERT INTO refresh_tokens")]
    # (family_id, user_id, tenant_id, token_hash, parent_id, ...): one family, one tenant
    assert (insert[0], insert[1], insert[2], insert[4]) == (FAMILY, USER, tenant, TOKEN_ID)


async def test_reuse_revokes_the_family_of_the_row_found_by_hash():
    replayed = datetime.now(timezone.utc) - timedelta(hours=1)
    conn = TokenConn(token_row(TENANT_A, used_at=replayed))
    assert await rotate_refresh_token(conn, presented_token="replayed-cookie") is None
    (revocation,) = [args for sql, args in conn.calls if "reuse_detected" in sql]
    assert revocation[0] == FAMILY


async def test_account_wide_revocation_is_keyed_by_the_account_and_spans_every_tenant():
    conn = TokenConn(None)
    await revoke_all_user_refresh_tokens(conn, str(USER), reason="password_change")
    ((sql, args),) = conn.calls
    assert "WHERE user_id = $1" in sql and "tenant" not in sql
    assert args[0] == str(USER)


class PermissionConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return []


async def test_permissions_in_a_tenant_bind_the_membership_and_the_direct_grants_to_it():
    conn = PermissionConn()
    await get_user_permissions(conn, str(USER), str(TENANT_A))
    (roles, role_args), (direct, direct_args) = conn.calls
    assert "tu.tenant_id = $2" in roles and role_args == (str(USER), str(TENANT_A))
    assert "up.tenant_id IS NULL OR up.tenant_id = $2" in direct
    assert direct_args == (str(USER), str(TENANT_A))


async def test_permissions_without_a_tenant_use_only_platform_roles_and_global_grants():
    conn = PermissionConn()
    await get_user_permissions(conn, str(USER), None)
    (roles, role_args), (_direct, direct_args) = conn.calls
    assert "r.tenant_scoped = FALSE" in roles and role_args == (str(USER),)
    assert direct_args == (str(USER), None), "a NULL tenant can only match grants that name no tenant"

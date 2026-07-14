"""Tests for the Phase 2.3 db pool config.

We don't spin up a real Postgres here; we patch asyncpg.create_pool so
the test verifies _what_ would be created and how the read-pool falls
back to the primary."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# Import order matters here: `app.core.security.tenant_isolation` and
# `app.api.v1.dependencies` import from each other, so importing
# `dependencies` first avoids resolving `tenant_isolation` while it's
# still mid-import (which raises ImportError under pytest's
# `monkeypatch.setattr("dotted.path", ...)` string-resolution). Importing
# the module object directly here — instead of monkeypatching via a
# dotted string — sidesteps that ordering trap entirely.
import app.api.v1.dependencies  # noqa: F401
import app.core.security.tenant_isolation as tenant_isolation


@pytest.fixture
def reset_db_module():
    from app.core import db
    db._pool = None
    db._read_pool = None
    yield db
    db._pool = None
    db._read_pool = None


@pytest.mark.asyncio
async def test_init_db_pool_uses_env_for_sizes(monkeypatch, reset_db_module):
    db = reset_db_module
    monkeypatch.setenv("PG_POOL_MIN_SIZE", "7")
    monkeypatch.setenv("PG_POOL_MAX_SIZE", "33")
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/db")
    monkeypatch.delenv("READ_DATABASE_URL", raising=False)

    fake_pool = SimpleNamespace(close=AsyncMock())
    create_mock = AsyncMock(return_value=fake_pool)

    with patch("app.core.db.asyncpg.create_pool", create_mock):
        await db.init_db_pool()

    # Verify the primary pool was built with the env-supplied sizes.
    assert create_mock.await_args.kwargs["min_size"] == 7
    assert create_mock.await_args.kwargs["max_size"] == 33

    # No replica configured → read pool aliases the primary.
    assert db.get_read_pool() is db.get_pool()


@pytest.mark.asyncio
async def test_init_db_pool_creates_replica_when_configured(
    monkeypatch, reset_db_module
):
    db = reset_db_module
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/primary")
    monkeypatch.setenv("READ_DATABASE_URL", "postgresql://stub/replica")
    monkeypatch.setenv("PG_READ_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("PG_READ_POOL_MAX_SIZE", "9")

    primary = SimpleNamespace(close=AsyncMock(), id="primary")
    replica = SimpleNamespace(close=AsyncMock(), id="replica")
    create_mock = AsyncMock(side_effect=[primary, replica])

    with patch("app.core.db.asyncpg.create_pool", create_mock):
        await db.init_db_pool()

    # Two distinct pools were created.
    assert create_mock.await_count == 2
    assert db.get_pool() is primary
    assert db.get_read_pool() is replica
    # Read-pool sizing came from the dedicated env vars.
    assert create_mock.await_args_list[1].kwargs["min_size"] == 1
    assert create_mock.await_args_list[1].kwargs["max_size"] == 9


@pytest.mark.asyncio
async def test_pgbouncer_compat_env_disables_statement_cache(
    monkeypatch, reset_db_module
):
    db = reset_db_module
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/db")
    monkeypatch.setenv("PG_STATEMENT_CACHE_SIZE", "0")
    monkeypatch.delenv("READ_DATABASE_URL", raising=False)

    create_mock = AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))
    with patch("app.core.db.asyncpg.create_pool", create_mock):
        await db.init_db_pool()

    assert create_mock.await_args.kwargs["statement_cache_size"] == 0


# ── jsonb/json codec wiring (root-cause fix for raw-string jsonb reads) ──

@pytest.mark.asyncio
async def test_init_db_pool_wires_jsonb_codec_hook(monkeypatch, reset_db_module):
    """Both primary and read pools must get the codec `init` hook — a pool
    created without it silently reverts to asyncpg's default behaviour
    (jsonb columns come back as raw strings), reintroducing the exact bug
    class this fix closes."""
    db = reset_db_module
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/primary")
    monkeypatch.setenv("READ_DATABASE_URL", "postgresql://stub/replica")

    create_mock = AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))
    with patch("app.core.db.asyncpg.create_pool", create_mock):
        await db.init_db_pool()

    assert create_mock.await_count == 2
    for call in create_mock.await_args_list:
        assert call.kwargs["init"] is db._register_jsonb_codecs


@pytest.mark.asyncio
async def test_register_jsonb_codecs_round_trips_dict_and_list():
    """Exercises the codec registration against a fake connection and
    proves the encoder/decoder pair it installs round-trips Python
    dict/list <-> JSON text exactly like the old blocking adapter did."""
    from app.core import db

    calls = []

    class _FakeConn:
        async def set_type_codec(self, typename, **kwargs):
            calls.append((typename, kwargs))

    await db._register_jsonb_codecs(_FakeConn())

    registered = {name: kwargs for name, kwargs in calls}
    assert set(registered) == {"jsonb", "json"}
    for typename, kwargs in registered.items():
        assert kwargs["schema"] == "pg_catalog"
        assert kwargs["format"] == "text"
        payload = {"a": 1, "b": [1, 2, "x"]}
        # decoder(encoder(x)) == x — the exact contract asyncpg relies on.
        assert kwargs["decoder"](kwargs["encoder"](payload)) == payload


@pytest.mark.asyncio
async def test_register_jsonb_codecs_idempotent_on_same_connection():
    """The pool's `init` hook is documented to run once per physical
    connection, never on every acquire — but re-invoking it (e.g. a
    defensive re-init) must not raise or corrupt the codec, since
    `set_type_codec` simply overwrites the prior registration."""
    from app.core import db

    class _FakeConn:
        def __init__(self):
            self.registrations = 0

        async def set_type_codec(self, typename, **kwargs):
            self.registrations += 1

    conn = _FakeConn()
    await db._register_jsonb_codecs(conn)
    await db._register_jsonb_codecs(conn)
    assert conn.registrations == 4  # 2 typenames x 2 calls, no exception


# ── RLS GUCs must be set inside an open transaction ──────────────────────
#
# `SET LOCAL` / `set_config(..., true)` are both transaction-local: outside
# an open transaction Postgres discards them immediately (with a warning),
# so the previous implementation's `SET LOCAL` calls never actually reached
# the caller's query. These tests use a fake connection that records the
# order of transaction begin/commit vs. `execute()` calls, proving the GUC
# statements now run strictly between BEGIN and COMMIT/ROLLBACK.

class _FakeTransaction:
    def __init__(self, conn, readonly=False):
        self._conn = conn
        self.readonly = readonly

    async def __aenter__(self):
        self._conn.log.append(("BEGIN", self.readonly))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._conn.log.append(("ROLLBACK" if exc_type else "COMMIT", None))
        return False


class _FakeRlsConn:
    def __init__(self):
        self.log = []  # ordered list of ("BEGIN"|"COMMIT"|"ROLLBACK"|"EXECUTE", payload)

    def transaction(self, *, readonly=False, **kwargs):
        return _FakeTransaction(self, readonly=readonly)

    async def execute(self, query, *args):
        self.log.append(("EXECUTE", query, args))


class _FakeRlsPool:
    def __init__(self, conn):
        self._conn = conn
        self.released = []

    async def acquire(self, timeout=None):
        return self._conn

    async def release(self, conn):
        self.released.append(conn)


def _guc_calls(log):
    return [entry for entry in log if entry[0] == "EXECUTE"]


@pytest.mark.asyncio
async def test_get_db_sets_rls_guc_inside_transaction(monkeypatch, reset_db_module):
    """The GUC-setting statements must appear strictly after BEGIN and
    before COMMIT — proving they now execute inside the open transaction
    (the actual bug fix), not as bare autocommit statements outside one."""
    db = reset_db_module
    conn = _FakeRlsConn()
    pool = _FakeRlsPool(conn)
    db._pool = pool

    tenant_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(tenant_isolation, "get_current_tenant_id", lambda: tenant_id)
    monkeypatch.setattr(tenant_isolation, "get_bypass_rls", lambda: False)

    async with db.get_db() as yielded_conn:
        assert yielded_conn is conn
        # The caller's own statement also runs inside the transaction.
        await conn.execute("SELECT 1")

    kinds = [entry[0] for entry in conn.log]
    assert kinds[0] == "BEGIN"
    assert kinds[-1] == "COMMIT"
    assert "EXECUTE" in kinds[1:-1]

    guc_calls = _guc_calls(conn.log)
    # set_config calls are parameterized (no interpolated tenant id in the
    # SQL text) and bind the validated UUID as a $1 argument.
    tenant_calls = [c for c in guc_calls if "current_tenant_id" in c[1]]
    assert tenant_calls, "expected a set_config call for app.current_tenant_id"
    assert tenant_id not in tenant_calls[0][1]  # not string-interpolated
    assert tenant_calls[0][2] == (tenant_id,)  # bound as a parameter instead
    assert pool.released == [conn]


@pytest.mark.asyncio
async def test_get_db_bypass_rls_uses_nil_uuid_not_empty_string(
    monkeypatch, reset_db_module
):
    """Bypass/no-tenant paths must bind the nil-UUID sentinel, never ''  —
    RLS policies cast the GUC to ::uuid, and '' would raise once a real
    RLS-enforcing role is ever put in front of this pool."""
    db = reset_db_module
    conn = _FakeRlsConn()
    db._pool = _FakeRlsPool(conn)

    monkeypatch.setattr(tenant_isolation, "get_current_tenant_id", lambda: None)
    monkeypatch.setattr(tenant_isolation, "get_bypass_rls", lambda: True)

    async with db.get_db():
        pass

    tenant_calls = [
        c for c in _guc_calls(conn.log) if "current_tenant_id" in c[1]
    ]
    assert tenant_calls[0][2] == ("00000000-0000-0000-0000-000000000000",)


@pytest.mark.asyncio
async def test_get_db_rejects_non_uuid_tenant_id(monkeypatch, reset_db_module):
    db = reset_db_module
    conn = _FakeRlsConn()
    db._pool = _FakeRlsPool(conn)

    monkeypatch.setattr(
        tenant_isolation, "get_current_tenant_id", lambda: "not-a-uuid"
    )
    monkeypatch.setattr(tenant_isolation, "get_bypass_rls", lambda: False)

    with pytest.raises(ValueError):
        async with db.get_db():
            pass


@pytest.mark.asyncio
async def test_get_read_db_opens_readonly_transaction(monkeypatch, reset_db_module):
    """get_read_db() should request a readonly transaction — documents (and,
    against a non-superuser role, enforces) the read-only contract."""
    db = reset_db_module
    conn = _FakeRlsConn()
    db._read_pool = _FakeRlsPool(conn)

    monkeypatch.setattr(tenant_isolation, "get_current_tenant_id", lambda: None)
    monkeypatch.setattr(tenant_isolation, "get_bypass_rls", lambda: False)

    async with db.get_read_db():
        pass

    begin_entries = [e for e in conn.log if e[0] == "BEGIN"]
    assert begin_entries == [("BEGIN", True)]  # readonly=True

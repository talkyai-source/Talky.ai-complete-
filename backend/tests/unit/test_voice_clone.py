"""Unit tests for voice cloning: ownership service (tenant scoping + cap)
and the ElevenLabs error extractor."""
from datetime import datetime, timezone

import pytest

from app.domain.services import voice_clone_service as vcs
from app.infrastructure.tts.elevenlabs_clone import _extract_error


# ── EL error extraction ───────────────────────────────────────────
def test_extract_error_shapes():
    assert _extract_error({"detail": {"message": "voice limit reached"}}) == "voice limit reached"
    assert _extract_error({"detail": "bad request"}) == "bad request"
    assert _extract_error({"message": "nope"}) == "nope"
    assert _extract_error({}) is None
    assert _extract_error("not a dict") is None


# ── ownership service with a fake asyncpg pool ────────────────────
class _FakeConn:
    def __init__(self, *, fetch=None, fetchval=None, fetchrow=None, execute=None):
        self._fetch = fetch if fetch is not None else []
        self._fetchval = fetchval
        self._fetchrow = fetchrow
        self._execute = execute or "DELETE 1"
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetch

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetchval

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetchrow

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self._execute

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn
    def acquire(self):
        return self._conn


@pytest.mark.asyncio
async def test_count_for_tenant_filters_by_tenant():
    conn = _FakeConn(fetchval=3)
    n = await vcs.count_for_tenant(_FakePool(conn), "t1")
    assert n == 3
    sql, args = conn.calls[0]
    assert "WHERE tenant_id = $1" in sql and args == ("t1",)


@pytest.mark.asyncio
async def test_owned_voice_ids_set():
    conn = _FakeConn(fetch=[{"voice_id": "v1"}, {"voice_id": "v2"}])
    ids = await vcs.owned_voice_ids(_FakePool(conn), "t1")
    assert ids == {"v1", "v2"}
    assert conn.calls[0][1] == ("t1",)


@pytest.mark.asyncio
async def test_all_platform_voice_ids_no_tenant_filter():
    conn = _FakeConn(fetch=[{"voice_id": "v1"}, {"voice_id": "v9"}])
    ids = await vcs.all_platform_voice_ids(_FakePool(conn))
    assert ids == {"v1", "v9"}
    # no tenant arg — it spans all tenants on purpose
    assert conn.calls[0][1] == ()


@pytest.mark.asyncio
async def test_record_clone_returns_dict():
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": "abc", "voice_id": "vX", "name": "My voice", "provider": "elevenlabs",
        "created_by": "u@e.com", "consent_at": now, "status": "ready", "created_at": now,
    }
    conn = _FakeConn(fetchrow=row)
    out = await vcs.record_clone(
        _FakePool(conn), tenant_id="t1", voice_id="vX", name="My voice",
        created_by="u@e.com", consent_at=now,
    )
    assert out["voice_id"] == "vX" and out["name"] == "My voice"
    assert out["consent_at"] == now.isoformat()


@pytest.mark.asyncio
async def test_get_owned_is_tenant_scoped():
    conn = _FakeConn(fetchrow=None)  # not this tenant's row
    out = await vcs.get_owned(_FakePool(conn), "t1", "clone-9")
    assert out is None
    sql, args = conn.calls[0]
    assert "tenant_id = $1 AND id = $2" in sql and args == ("t1", "clone-9")


@pytest.mark.asyncio
async def test_delete_owned_reports_rowcount():
    conn = _FakeConn(execute="DELETE 1")
    assert await vcs.delete_owned(_FakePool(conn), "t1", "c1") is True
    conn2 = _FakeConn(execute="DELETE 0")
    assert await vcs.delete_owned(_FakePool(conn2), "t1", "c1") is False


# ── catalog filter logic (the isolation rule) ─────────────────────
def test_catalog_hide_set_logic():
    # Reproduces the endpoint's filter: hide = all_platform_clones - owned.
    owned = {"v1"}
    all_clones = {"v1", "v2", "v3"}
    hidden = all_clones - owned
    assert hidden == {"v2", "v3"}            # other tenants' clones hidden
    # a library voice (not a clone) is never in all_clones → never hidden
    assert "library-voice" not in hidden

"""T2.1 — DNC list service.

Covers:
  - E.164 normalisation (libphonenumber preferred; fallback digit-strip)
  - add() is idempotent and returns a row whether freshly inserted or refreshed
  - add_caller_opt_out() sets the right source + reason
  - bulk_import() splits accepted / skipped / invalid
  - is_on_dnc() matches both tenant-scoped and global rows
  - remove() returns True/False correctly
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import pytest

from app.domain.services.dnc_service import (
    SOURCE_CALLER_OPT_OUT,
    SOURCE_MANUAL_ADMIN,
    DNCService,
    normalize_e164,
)


# ──────────────────────────────────────────────────────────────────────────
# Normalisation
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+14155551234", "+14155551234"),
        ("+1 415-555-1234", "+14155551234"),
        ("  +1 (415) 555-1234  ", "+14155551234"),
        ("+442079460000", "+442079460000"),
        ("14155551234", "+14155551234"),  # fallback: add +
    ],
)
def test_normalize_e164_variants(raw: str, expected: str):
    assert normalize_e164(raw) == expected


def test_normalize_e164_empty_returns_empty():
    assert normalize_e164("") == ""
    assert normalize_e164(None) == ""  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────
# Fake DB pool
# ──────────────────────────────────────────────────────────────────────────

class _FakeRow(dict):
    """Behaves like asyncpg's Record — subscript access + .get()."""


class _FakeConn:
    def __init__(self, store: dict):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def fetchrow(self, sql: str, *args):
        sql_norm = " ".join(sql.split())

        if "INSERT INTO dnc_entries" in sql_norm and "DO NOTHING" in sql_norm:
            tenant_id, number, source, reason, added_by, expires_at = args
            key = (tenant_id, number, source)
            if key in self.store["by_tuple"]:
                return None
            row_id = uuid.uuid4()
            row = _FakeRow(
                id=row_id, tenant_id=tenant_id, normalized_number=number,
                source=source, reason=reason, expires_at=expires_at,
                created_at=datetime.utcnow(),
            )
            self.store["by_id"][str(row_id)] = row
            self.store["by_tuple"][key] = row
            return row

        if sql_norm.startswith("UPDATE dnc_entries"):
            tenant_id, number, source, reason, expires_at = args
            key = (tenant_id, number, source)
            existing = self.store["by_tuple"].get(key)
            if existing is None:
                return None
            if reason is not None:
                existing["reason"] = reason
            if expires_at is not None:
                existing["expires_at"] = expires_at
            return existing

        if "SELECT 1 FROM dnc_entries" in sql_norm:
            tenant_id, number = args
            for (tid, num, _src), row in self.store["by_tuple"].items():
                if num == number and (tid == tenant_id or tid is None):
                    return _FakeRow(exists=True)
            return None

        raise AssertionError(f"unexpected fetchrow SQL: {sql_norm!r}")

    async def fetch(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if "SELECT id, tenant_id" in sql_norm and "FROM dnc_entries" in sql_norm:
            tenant_id = args[0]
            limit = args[1]
            include_global = "OR tenant_id IS NULL" in sql_norm
            out = []
            for row in self.store["by_id"].values():
                if row["tenant_id"] == tenant_id:
                    out.append(row)
                elif include_global and row["tenant_id"] is None:
                    out.append(row)
            return out[:limit]
        raise AssertionError(f"unexpected fetch SQL: {sql_norm!r}")

    async def execute(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("INSERT INTO dnc_entries"):
            tenant_id, number, source, reason = args
            key = (tenant_id, number, source)
            if key in self.store["by_tuple"]:
                return "INSERT 0 0"
            row_id = uuid.uuid4()
            row = _FakeRow(
                id=row_id, tenant_id=tenant_id, normalized_number=number,
                source=source, reason=reason, expires_at=None,
                created_at=datetime.utcnow(),
            )
            self.store["by_id"][str(row_id)] = row
            self.store["by_tuple"][key] = row
            return "INSERT 0 1"
        if sql_norm.startswith("DELETE FROM dnc_entries"):
            tenant_id, entry_id = args
            row = self.store["by_id"].get(str(entry_id))
            if row and row["tenant_id"] == tenant_id:
                del self.store["by_id"][str(entry_id)]
                for k, v in list(self.store["by_tuple"].items()):
                    if v is row:
                        del self.store["by_tuple"][k]
                return "DELETE 1"
            return "DELETE 0"
        raise AssertionError(f"unexpected execute SQL: {sql_norm!r}")


class _FakePool:
    def __init__(self):
        self.store = {"by_id": {}, "by_tuple": {}}

    def acquire(self):
        return _FakeConn(self.store)


# ──────────────────────────────────────────────────────────────────────────
# Service tests
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_returns_entry_with_normalised_number():
    svc = DNCService(_FakePool())
    entry = await svc.add(
        tenant_id="t1",
        e164="+1 415-555-1234",
        source=SOURCE_MANUAL_ADMIN,
    )
    assert entry.normalized_number == "+14155551234"
    assert entry.source == SOURCE_MANUAL_ADMIN
    assert entry.tenant_id == "t1"


@pytest.mark.asyncio
async def test_add_is_idempotent():
    pool = _FakePool()
    svc = DNCService(pool)
    first = await svc.add(tenant_id="t1", e164="+14155551234", source=SOURCE_MANUAL_ADMIN)
    second = await svc.add(
        tenant_id="t1", e164="+14155551234",
        source=SOURCE_MANUAL_ADMIN, reason="updated",
    )
    assert first.id == second.id
    assert second.reason == "updated"
    assert len(pool.store["by_id"]) == 1


@pytest.mark.asyncio
async def test_add_rejects_empty_number():
    svc = DNCService(_FakePool())
    with pytest.raises(ValueError, match="valid phone number"):
        await svc.add(tenant_id="t1", e164="", source=SOURCE_MANUAL_ADMIN)


@pytest.mark.asyncio
async def test_add_caller_opt_out_sets_source_and_call_id():
    svc = DNCService(_FakePool())
    entry = await svc.add_caller_opt_out(
        tenant_id="t1", e164="+14155551234", call_id="call-abc-123",
    )
    assert entry.source == SOURCE_CALLER_OPT_OUT
    assert "call-abc-123" in (entry.reason or "")


@pytest.mark.asyncio
async def test_bulk_import_splits_accepted_and_invalid():
    svc = DNCService(_FakePool())
    result = await svc.bulk_import(
        tenant_id="t1",
        numbers=[
            "+14155551234",
            "+1 415 555 1235",
            "not-a-number",
            "",
        ],
        source="bulk_import",
    )
    assert result["accepted_count"] >= 2
    assert result["invalid_count"] >= 1
    assert "+14155551234" in result["accepted"]


@pytest.mark.asyncio
async def test_is_on_dnc_matches_tenant_and_global():
    pool = _FakePool()
    svc = DNCService(pool)
    # Tenant-scoped entry
    await svc.add(tenant_id="t1", e164="+14155551234", source=SOURCE_MANUAL_ADMIN)
    # Global entry (tenant_id=None) — FTC-style
    await svc.add(tenant_id=None, e164="+19995551234", source="ftc_national")

    # Tenant sees its own entry
    assert await svc.is_on_dnc(tenant_id="t1", e164="+1 415 555 1234") is True
    # Tenant sees the global entry
    assert await svc.is_on_dnc(tenant_id="t1", e164="+1 999 555 1234") is True
    # Clean number not on the list
    assert await svc.is_on_dnc(tenant_id="t1", e164="+14155559999") is False
    # Normalisation applied to the query side too
    assert await svc.is_on_dnc(tenant_id="t1", e164="1-415-555-1234") is True


@pytest.mark.asyncio
async def test_is_on_dnc_empty_number_returns_false():
    svc = DNCService(_FakePool())
    assert await svc.is_on_dnc(tenant_id="t1", e164="") is False


@pytest.mark.asyncio
async def test_remove_only_affects_own_tenant():
    pool = _FakePool()
    svc = DNCService(pool)
    mine = await svc.add(tenant_id="t1", e164="+14155551234", source=SOURCE_MANUAL_ADMIN)
    other = await svc.add(tenant_id="t2", e164="+14155551235", source=SOURCE_MANUAL_ADMIN)

    # Can't delete the other tenant's entry.
    removed = await svc.remove(tenant_id="t1", entry_id=other.id)
    assert removed is False
    assert other.id in pool.store["by_id"]

    # Can delete your own.
    removed = await svc.remove(tenant_id="t1", entry_id=mine.id)
    assert removed is True
    assert mine.id not in pool.store["by_id"]


@pytest.mark.asyncio
async def test_list_for_tenant_excludes_global_by_default():
    pool = _FakePool()
    svc = DNCService(pool)
    await svc.add(tenant_id="t1", e164="+14155551234", source=SOURCE_MANUAL_ADMIN)
    await svc.add(tenant_id=None, e164="+19995551234", source="ftc_national")

    own = await svc.list_for_tenant("t1")
    assert len(own) == 1
    assert own[0].tenant_id == "t1"

    with_global = await svc.list_for_tenant("t1", include_global=True)
    assert len(with_global) == 2

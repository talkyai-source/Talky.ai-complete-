"""A deleted PBX line (``tenant_sip_trunks.deleted_at``) is invisible and inert.

The behaviour is proven against real PostgreSQL in
tests/integration/test_op04_trunk_lifecycle.py::test_a_deleted_line_is_invisible_and_cannot_be_used.
These tests pin, per statement, that every path that lists, selects or updates a
line by id carries the ``deleted_at IS NULL`` predicate (so a retired row can never
be listed, edited, switched on, tested, allotted to a campaign or to the pool), and
that the live-status updater and the SIP-readiness gate ignore retired rows.
"""
from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1.endpoints.telephony_sip import trunks

TENANT = str(uuid4())


class RecordingConn:
    """Answers every query with ``rows`` and remembers the SQL it was given."""

    def __init__(self, row=None):
        self.row, self.sql = row, []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql, *args):
        return "SELECT 1"

    async def fetch(self, sql, *args):
        self.sql.append(" ".join(sql.split()))
        return []

    async def fetchrow(self, sql, *args):
        self.sql.append(" ".join(sql.split()))
        return self.row

    async def fetchval(self, sql, *args):
        self.sql.append(" ".join(sql.split()))
        return None


def install(monkeypatch, conn):
    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(trunks, "acquire_with_tenant", acquire)
    monkeypatch.setattr(trunks, "_claim_idempotency", AsyncMock(return_value=("new", None, None)))
    monkeypatch.setattr(trunks, "_enforce_ws_i_quota", AsyncMock(return_value=None))
    monkeypatch.setattr(trunks, "_store_error_idempotency_result", AsyncMock())


def request(path="/trunks"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


USER = SimpleNamespace(tenant_id=TENANT, id=str(uuid4()))


def live_only(statements):
    assert statements, "no SQL was issued"
    for sql in statements:
        if "tenant_sip_trunks" in sql and not sql.startswith(("INSERT", "SET")):
            assert "deleted_at IS NULL" in sql, sql


@pytest.mark.asyncio
async def test_the_trunk_list_hides_deleted_lines(monkeypatch):
    conn = RecordingConn()
    install(monkeypatch, conn)
    assert await trunks.list_sip_trunks(request(), USER, object()) == []
    live_only(conn.sql)


@pytest.mark.asyncio
async def test_edit_switch_on_switch_off_and_test_all_treat_a_deleted_line_as_missing(monkeypatch):
    conn = RecordingConn(row=None)
    install(monkeypatch, conn)
    tid = uuid4()
    patch = trunks.SIPTrunkUpdateRequest(metadata={"region": "eu"})
    answers = [
        await trunks.update_sip_trunk(tid, patch, request(), "k1", None, USER, object()),
        await trunks.activate_sip_trunk(tid, request(), "k2", None, USER, object()),
        await trunks.deactivate_sip_trunk(tid, request(), "k3", None, USER, object()),
        await trunks.test_sip_trunk(tid, request(), USER, object()),
    ]
    assert [a.status_code for a in answers] == [404] * 4
    live_only(conn.sql)


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["update", "activate"])
async def test_a_line_deleted_between_the_read_and_the_write_is_404_not_a_crash(monkeypatch, call):
    row = {
        "id": uuid4(), "tenant_id": TENANT, "trunk_name": "x-line", "sip_domain": "sip.example.com",
        "port": 5060, "transport": "udp", "direction": "both", "is_active": False,
        "auth_username": None, "auth_password_encrypted": None, "metadata": {},
        "last_tested_at": None, "last_test_result": None,
    }

    class Conn(RecordingConn):
        async def fetchrow(self, sql, *args):
            normalized = " ".join(sql.split())
            self.sql.append(normalized)
            return row if normalized.startswith("SELECT") else None  # the UPDATE finds nothing

    conn = Conn()
    install(monkeypatch, conn)
    tid = uuid4()
    if call == "update":
        answer = await trunks.update_sip_trunk(
            tid, trunks.SIPTrunkUpdateRequest(metadata={"region": "eu"}), request(), "k", None, USER, object()
        )
    else:
        answer = await trunks.activate_sip_trunk(tid, request(), "k", None, USER, object())
    assert answer.status_code == 404
    updates = [s for s in conn.sql if s.startswith("UPDATE tenant_sip_trunks")]
    assert updates and all("deleted_at IS NULL" in s for s in updates)


@pytest.mark.asyncio
async def test_campaign_and_pool_assignment_lookups_ignore_deleted_lines():
    conn = RecordingConn()
    await trunks._fetch_assignable_trunk(conn, str(uuid4()), TENANT)
    await trunks._fetch_pool_trunk(conn, str(uuid4()), TENANT)
    assert len(conn.sql) == 2
    live_only(conn.sql)


@pytest.mark.asyncio
async def test_the_assignment_label_lookup_ignores_deleted_lines(monkeypatch):
    conn = RecordingConn()
    install(monkeypatch, conn)
    await trunks._current_assignment_projection(object(), TENANT, str(uuid4()))
    live_only(conn.sql)


@pytest.mark.asyncio
async def test_a_tenant_with_only_deleted_lines_does_not_need_sip_readiness(monkeypatch):
    from app.core import db_utils
    from app.domain.services.telephony.trunk_resolver import requires_sip_readiness
    from app.infrastructure.telephony.provider_factory import TelephonyProviderFactory

    monkeypatch.setenv("TELEPHONY_ADAPTER", "auto")
    monkeypatch.delenv("TELEPHONY_PROVIDER", raising=False)
    monkeypatch.setattr(TelephonyProviderFactory, "_read_config_active", lambda: None)
    conn = RecordingConn(row={"active_telephony_provider": "none", "has_trunks": False, "pool_trunk": None})

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    assert await requires_sip_readiness(object(), tenant_id=TENANT, campaign={}) is False
    (sql,) = conn.sql
    assert "FROM tenant_sip_trunks WHERE tenant_id=$1::uuid AND deleted_at IS NULL" in sql


def load_updater():
    script = Path(__file__).parents[2] / "scripts" / "trunk_live_status_updater.py"
    spec = importlib.util.spec_from_file_location("trunk_live_status_updater_deleted", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_the_status_updater_writes_nothing_for_a_deleted_line(monkeypatch):
    updater = load_updater()
    live_id, gone_id = uuid4(), uuid4()
    rows = [
        {"id": live_id, "trunk_name": "live-line", "is_active": True, "metadata": {"register": True},
         "auth_username": "150001", "deleted_at": None},
        {"id": gone_id, "trunk_name": "gone-line", "is_active": False, "metadata": {},
         "auth_username": "150002", "deleted_at": "2026-10-14T00:00:00+00:00"},
    ]
    executed, cleaned = [], []

    class Conn:
        async def execute(self, sql, *args):
            executed.append((" ".join(sql.split()), args))

        async def fetch(self, sql, *args):
            assert "deleted_at" in sql, "the updater must read deleted_at to know which rows to skip"
            return rows

        async def close(self):
            pass

    async def connect(_url):
        return Conn()

    monkeypatch.setattr(updater, "load_database_url", lambda: "postgresql://unused")
    monkeypatch.setattr(updater.asyncpg, "connect", connect)
    monkeypatch.setattr(updater, "read_registrations", lambda: ({}, True))
    monkeypatch.setattr(updater, "read_endpoints", lambda: (set(), True))
    monkeypatch.setattr(updater, "read_endpoint_contacts", lambda: ({}, True))
    monkeypatch.setattr(updater, "read_reg_failures", lambda: {})
    monkeypatch.setattr(updater, "reconcile_inactive_config_files", lambda r, e: cleaned.append([x["id"] for x in r]) or 0)
    await updater.main()
    stamped = [args[-1] for sql, args in executed if sql.startswith("UPDATE tenant_sip_trunks")]
    assert stamped == [live_id], "only the live line gets a status heartbeat"
    # A stale config file of a deleted (inactive) line is still cleaned up.
    assert cleaned == [[live_id, gone_id]]

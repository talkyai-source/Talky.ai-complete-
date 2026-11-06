"""F2-03: tenant identity must survive campaign stop/pause into bypass SQL.

SQLite executes the real SELECT/UPDATE predicates after narrow PostgreSQL
syntax adaptation. These cases do not exercise PostgreSQL RLS, locking or
schema constraints. A foreign call carrying the owned campaign ID models
legacy inconsistent data; fresh composite FKs can prevent that relationship.
"""
from contextlib import asynccontextmanager
import inspect
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import telephony_bridge
from app.core import container as container_module
from app.domain.services.campaign_service import CampaignService
from app.domain.services.telephony.termination import HangupProof


OWNER = "a1111111-bbbb-4111-8111-ccccccccccc1"
OTHER = "a2222222-bbbb-4222-8222-ccccccccccc2"
CAMPAIGN = "d4444444-bbbb-4444-8444-eeeeeeeeeee4"
OWN_CALL = "c3333333-bbbb-4333-8333-ddddddddddd3"
OTHER_CALL = "f6666666-bbbb-4666-8666-aaaaaaaaaaa6"


class Connection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.create_function("BTRIM", 1, lambda value: value.strip() if value is not None else None)
        self.statements = []
        self.bypass = False
        self.db.executescript("""
            CREATE TABLE calls (
                id TEXT PRIMARY KEY, tenant_id TEXT, campaign_id TEXT,
                provider TEXT, provider_call_id TEXT, external_call_uuid TEXT,
                route_snapshot TEXT, status TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE call_legs (
                call_id TEXT, provider_leg_id TEXT, provider TEXT,
                status TEXT, created_at TEXT, id TEXT
            );
        """)
        for call_id, tenant, created in ((OWN_CALL, OWNER, "2026-01-01"), (OTHER_CALL, OTHER, "2026-02-01")):
            self.db.execute("INSERT INTO calls VALUES (?, ?, ?, 'asterisk', ?, ?, '{}', 'in_call', ?, ?)",
                            (call_id, tenant, CAMPAIGN, "pbx-" + call_id, "shared-channel", created, created))
            self.db.execute("INSERT INTO call_legs VALUES (?, ?, 'asterisk', 'active', ?, ?)",
                            (call_id, "leg-" + call_id, created, "child-" + call_id))

    @asynccontextmanager
    async def transaction(self):
        try:
            yield
        finally:
            self.bypass = False

    def _query(self, query, args):
        assert self.bypass, "Exercise bypassed SQL, not an RLS-filtered fake"
        bindings = {}

        def array(match):
            index = int(match.group(1))
            values = args[index - 1]
            names = []
            for position, value in enumerate(values):
                key = f"p{index}_{position}"
                bindings[key] = str(value)
                names.append(":" + key)
            return " IN (" + (",".join(names) or "NULL") + ")"

        query = re.sub(r"=\s*ANY\(\$(\d+)::text\[\]\)", array, query, flags=re.I)
        query = re.sub(r"::(?:uuid|text)\b", "", query, flags=re.I)
        query = re.sub(r"\bFOR UPDATE(?: OF \w+)?", "", query, flags=re.I)
        query = re.sub(r"\bNOW\(\)", "CURRENT_TIMESTAMP", query, flags=re.I)

        def scalar(match):
            index = int(match.group(1))
            bindings[f"p{index}"] = None if args[index - 1] is None else str(args[index - 1])
            return f":p{index}"

        return self.db.execute(re.sub(r"\$(\d+)", scalar, query), bindings)

    async def fetch(self, query, *args):
        self.statements.append((query, args))
        rows = [dict(row) for row in self._query(query, args).fetchall()]
        for row in rows:
            if "route_snapshot" in row:
                row["route_snapshot"] = {}
        return rows

    async def fetchrow(self, query, *args):
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def execute(self, query, *args):
        self.statements.append((query, args))
        if query.strip() == "SET LOCAL app.bypass_rls = 'on'":
            self.bypass = True
            return "SET"
        result = self._query(query, args)
        return f"UPDATE {result.rowcount}"


class Pool:
    def __init__(self, conn):
        self.conn = conn
        self.entries = self.releases = 0

    @asynccontextmanager
    async def acquire(self, **_kwargs):
        self.entries += 1
        try:
            yield self.conn
        finally:
            self.releases += 1


@pytest.fixture
def runtime(monkeypatch):
    conn = Connection()
    pool = Pool(conn)
    proof = AsyncMock(return_value=HangupProof(True, True, "confirmed"))
    monkeypatch.setattr(container_module, "get_container", lambda: SimpleNamespace(db_pool=pool, is_initialized=True))
    monkeypatch.setattr(telephony_bridge, "request_confirmed_hangup", proof)
    try:
        yield conn, pool, proof
    finally:
        conn.db.close()


async def sweep_with_tenant():
    # Exercise the old real behavior for RED without making an import/signature
    # failure stand in for evidence that a foreign call was selected.
    if "tenant_id" in inspect.signature(telephony_bridge.hangup_calls_for_campaign).parameters:
        return await telephony_bridge.hangup_calls_for_campaign(CAMPAIGN, tenant_id=OWNER)
    return await telephony_bridge.hangup_calls_for_campaign(CAMPAIGN)


@pytest.mark.asyncio
async def test_campaign_sweep_selects_marks_and_controls_only_owned_calls(runtime):
    conn, pool, proof = runtime
    result = await sweep_with_tenant()
    assert result["total_selected"] == 1
    assert result["confirmed"] == 1
    assert [call.args[1] for call in proof.await_args_list] == ["pbx-" + OWN_CALL]
    assert proof.await_args.kwargs["provider_leg_ids"] == ("leg-" + OWN_CALL,)
    states = dict(conn.db.execute("SELECT id, status FROM calls").fetchall())
    assert states == {OWN_CALL: "termination_pending", OTHER_CALL: "in_call"}
    assert pool.entries == pool.releases == 1


@pytest.mark.asyncio
async def test_campaign_sweep_without_tenant_refuses_before_database_or_provider(runtime):
    _conn, pool, proof = runtime
    result = await telephony_bridge.hangup_calls_for_campaign(CAMPAIGN)
    assert result["status"] == "lookup_failed"
    assert result["lookup_error"] == "tenant_context_required"
    assert pool.entries == pool.releases == 0
    proof.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_ownership_query_does_not_choose_a_foreign_tenants_newer_row(runtime):
    _conn, pool, proof = runtime
    await telephony_bridge._verify_call_ownership(SimpleNamespace(is_internal=False, tenant_id=OWNER), "shared-channel")
    assert pool.entries == pool.releases == 1
    proof.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_ownership_still_refuses_a_foreign_only_call(runtime):
    _conn, pool, proof = runtime
    with pytest.raises(HTTPException) as error:
        await telephony_bridge._verify_call_ownership(SimpleNamespace(is_internal=False, tenant_id=OWNER), "pbx-" + OTHER_CALL)
    assert error.value.status_code == 403
    assert pool.entries == pool.releases == 1
    proof.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pause", "stop"])
@pytest.mark.parametrize("explicit_tenant", [OWNER, None])
async def test_campaign_service_passes_owned_tenant_to_termination(monkeypatch, operation, explicit_tenant):
    campaign = {"id": CAMPAIGN, "tenant_id": OWNER, "direction": "outbound", "status": "running"}

    class Query:
        def update(self, _values):
            return self

        def eq(self, _column, _value):
            return self

        def execute(self):
            return SimpleNamespace(data=[campaign], error=None)

    service = CampaignService(SimpleNamespace(table=lambda _name: Query()),
                              queue_service=SimpleNamespace(clear_campaign_jobs=AsyncMock()))
    monkeypatch.setattr(service, "get_campaign", AsyncMock(return_value=campaign))
    monkeypatch.setattr(service, "_resolve_tenant_id", lambda value: value)
    monkeypatch.setattr(service, "_leads_awaiting_retry", lambda *_args: [])
    monkeypatch.setattr(service, "_release_leads_to_pending", lambda *_args: None)
    from app.domain.services.dialer import job_lifecycle
    monkeypatch.setattr(job_lifecycle, "cancel_active_jobs_for_campaign", lambda *_args, **_kwargs: 0)
    terminate = AsyncMock(return_value={"status": "confirmed", "total_selected": 0})
    monkeypatch.setattr(telephony_bridge, "hangup_calls_for_campaign", terminate)

    await getattr(service, operation + "_campaign")(CAMPAIGN, tenant_id=explicit_tenant)
    terminate.assert_awaited_once_with(CAMPAIGN, tenant_id=OWNER)

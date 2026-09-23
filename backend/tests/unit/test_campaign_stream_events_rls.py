"""Reproduces stream-events-rls-bare-acquire (day0923 forensics).

campaigns.py start/pause/stop/delete emitted their 'Campaign started' /
'Campaign paused' / 'Campaign stopped' / 'Campaign deleted' events over a
bare ``db_client.pool.acquire()`` connection that never sets the
``app.current_tenant_id`` GUC. Since migration 0013 put every tenant_id
table (including stream_events) under FORCE ROW LEVEL SECURITY, that bare
connection has no tenant context and the policy's WITH CHECK rejects the
INSERT — 15 rejections logged in day0923/DAY.talky-api.errorish.log,
7 started + 7 stopped on 790ca2db and 1 stopped on 1845a165, all
'emit_event.failed ... row-level security policy for table "stream_events"'.

This test does NOT mock emit_event/emit_event_via_pool (the units whose
behaviour is under test). Instead it fakes only the asyncpg pool/connection,
modelling the same RLS contract the real migration 0013 policy enforces:
an INSERT into stream_events succeeds only on a connection that has run
``SET LOCAL app.current_tenant_id = '<tenant>'`` in the current transaction
(what ``acquire_with_tenant``/``emit_event_via_pool`` does), and is rejected
on a bare, GUC-less connection (what the four buggy call sites did) — so
the real endpoint handlers exercise the real fix.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import campaigns as campaigns_ep

TENANT_ID = "790ca2db-0000-4000-8000-000000000001"


class _RLSRejected(Exception):
    """Stands in for the real asyncpg error the prod logs recorded."""


class _FakeAcquireCM:
    def __init__(self, conn: "_FakeConn"):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeTxnCM:
    def __init__(self, conn: "_FakeConn"):
        self._conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # SET LOCAL is transaction-scoped: the GUC drops when the txn ends,
        # exactly as db_utils.acquire_with_tenant's docstring describes.
        self._conn.guc = None
        return False


class _FakeConn:
    """Models the one RLS invariant that matters here: a bare `.acquire()`
    connection (no SET LOCAL) cannot satisfy the WITH CHECK on stream_events;
    a connection that ran `SET LOCAL app.current_tenant_id = '<tenant>'`
    (via `acquire_with_tenant`) can, for that tenant only.
    """

    def __init__(self, store: list[dict[str, Any]]):
        self.store = store
        self.guc: Optional[str] = None

    def transaction(self):
        return _FakeTxnCM(self)

    async def execute(self, sql: str, *args: Any):
        stripped = sql.strip()
        if stripped.startswith("SET LOCAL app.current_tenant_id"):
            self.guc = stripped.split("'")[1]
            return "SET"
        if stripped.startswith("SET LOCAL app.bypass_rls"):
            self.guc = "__bypass__"
            return "SET"
        if "INSERT INTO stream_events" in stripped:
            tenant_id = args[0]
            if self.guc != "__bypass__" and self.guc != str(tenant_id):
                # Same failure text the real policy raises, reproduced in
                # day0923/DAY.talky-api.errorish.log via emit_event's own
                # "emit_event.failed ... error=%s" wrapper.
                raise _RLSRejected(
                    'new row violates row-level security policy for '
                    'table "stream_events"'
                )
            self.store.append(
                {
                    "tenant_id": tenant_id,
                    "category": args[1],
                    "title": args[2],
                    "description": args[3],
                }
            )
            return "INSERT 0 1"
        raise AssertionError(f"unexpected SQL against fake conn: {sql!r}")


class _FakePool:
    def __init__(self):
        self.store: list[dict[str, Any]] = []
        self._conn = _FakeConn(self.store)

    def acquire(self, timeout=None):
        return _FakeAcquireCM(self._conn)


class _Resp:
    def __init__(self, data):
        self.data = data
        self.error = None


class _FakeQuery:
    """One row, direction=outbound — enough for every `.table("campaigns")`
    call the four handlers make (direction guard, delete's existence check,
    the compare-and-set update); none of them are the thing under test.
    """

    def __init__(self):
        self._row = {"id": "campaign-1", "direction": "outbound", "status": "running"}

    def select(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def neq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def execute(self):
        return _Resp([dict(self._row)])


class _FakeDB:
    def __init__(self, pool: _FakePool):
        self.pool = pool

    def table(self, _name):
        return _FakeQuery()


@dataclass
class _StartResult:
    message: str = "Campaign started"
    jobs_enqueued: int = 3
    queue_stats: dict = field(default_factory=dict)
    campaign_id: str = "campaign-1"


class _FakeCampaignService:
    """Stands in for CampaignService — the business logic it owns is
    covered elsewhere (test_campaign_start_dispatch_truth.py etc); this
    test is only about what happens to the event AFTER that logic returns.
    """

    async def get_campaign(self, _campaign_id):
        return None  # skips the guidance-budget / pacing-config side paths

    async def start_campaign(self, *, campaign_id, tenant_id, priority_override=None, first_speaker="agent"):
        return _StartResult(campaign_id=campaign_id)

    async def pause_campaign(self, campaign_id, *, tenant_id):
        return {"termination_summary": {"status": "confirmed", "deferred": 0}}

    async def stop_campaign(self, campaign_id, *, clear_queue=False, tenant_id):
        return {"termination_summary": {"status": "confirmed", "deferred": 0}}


async def _fake_minutes_status(_tenant_id):
    class _Minutes:
        exhausted = False

    return _Minutes()


@pytest.fixture
def rig(monkeypatch):
    pool = _FakePool()
    db = _FakeDB(pool)
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: _FakeCampaignService())
    monkeypatch.setattr(
        "app.domain.services.minutes_quota.tenant_minutes_status", _fake_minutes_status,
    )
    user = CurrentUser(id="user-1", email="u@t.example", tenant_id=TENANT_ID)
    return pool, db, user


@pytest.mark.asyncio
async def test_start_campaign_emits_event_through_tenant_scoped_pool(rig):
    pool, db, user = rig
    await campaigns_ep.start_campaign(
        campaign_id="campaign-1",
        request=None,
        start_request=None,
        current_user=user,
        idempotency_key=None,
        db_client=db,
    )
    assert len(pool.store) == 1, "the campaign-started row never reached stream_events"
    assert pool.store[0]["tenant_id"] == TENANT_ID
    assert pool.store[0]["title"] == "Campaign started"


@pytest.mark.asyncio
async def test_pause_campaign_emits_event_through_tenant_scoped_pool(rig):
    pool, db, user = rig
    await campaigns_ep.pause_campaign(
        campaign_id="campaign-1",
        request=None,
        current_user=user,
        db_client=db,
    )
    assert len(pool.store) == 1, "the campaign-paused row never reached stream_events"
    assert pool.store[0]["tenant_id"] == TENANT_ID
    assert pool.store[0]["title"] == "Campaign paused"


@pytest.mark.asyncio
async def test_stop_campaign_emits_event_through_tenant_scoped_pool(rig):
    pool, db, user = rig
    await campaigns_ep.stop_campaign(
        campaign_id="campaign-1",
        clear_queue=False,
        current_user=user,
        db_client=db,
    )
    assert len(pool.store) == 1, "the campaign-stopped row never reached stream_events"
    assert pool.store[0]["tenant_id"] == TENANT_ID
    assert pool.store[0]["title"] == "Campaign stopped"


@pytest.mark.asyncio
async def test_delete_campaign_emits_event_through_tenant_scoped_pool(rig):
    pool, db, user = rig
    await campaigns_ep.delete_campaign(
        campaign_id="campaign-1",
        current_user=user,
        db_client=db,
    )
    assert len(pool.store) == 1, "the campaign-deleted row never reached stream_events"
    assert pool.store[0]["tenant_id"] == TENANT_ID
    assert pool.store[0]["title"] == "Campaign deleted"


def test_no_emit_event_call_site_uses_a_bare_pool_acquire():
    """Guard, not proof (the four tests above are the proof): once fixed,
    campaigns.py must have no `db_client.pool.acquire()` left at all — every
    stream_events emission goes through emit_event_via_pool's
    acquire_with_tenant, never a bare, GUC-less connection.
    """
    import inspect

    source = inspect.getsource(campaigns_ep)
    assert "pool.acquire()" not in source

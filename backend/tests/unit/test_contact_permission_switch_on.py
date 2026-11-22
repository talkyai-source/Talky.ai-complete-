"""Making CONTACT_PERMISSION_DIAL_GATE safe to switch on: the ``warn`` position,
the per-decision log lines, confirming contacts that have no list, queueing on
confirmation, and refusing redial / assistant calls up front.

Switch positions: unset/other = off (no lookup, nothing blocked), ``warn`` = the
real lookup is logged and nothing is blocked, ``on`` = contacts without a
recorded permission are not queued and not dialled.

Imports of the new modules are inside each test so that, against code that lacks
them, each test fails on its own.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.workers.dialer_worker import DialerWorker
from tests.unit.test_contact_permission_dial_gate import (
    CONFIRMED_AT,
    _FakeDB,
    _row,
    _seed,
    _service,
    _start,
    _start_db,
)
from tests.unit.test_contact_permission_on_import import (
    GOOD,
    FakeDB as _ListDB,
    _list_row,
    _User,
)
from tests.unit.test_w1_dialer_job_lifecycle import _conn_pool, _job

GATE = "CONTACT_PERMISSION_DIAL_GATE"
CAMPAIGN = "44444444-4444-4444-4444-444444444444"
TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
LOG = "app.domain.services.dialer.permission_gate"


@pytest.fixture(autouse=True)
def _gate_unset(monkeypatch):
    monkeypatch.delenv(GATE, raising=False)


# ═══ 1. the switch and its log lines ═════════════════════════════════════


def test_mode_words(monkeypatch):
    from app.domain.services.dialer.block_reasons import contact_permission_dial_gate_enabled
    from app.domain.services.dialer.permission_gate import contact_permission_dial_gate_mode

    for value, mode in [
        ("1", "on"), ("true", "on"), ("YES", "on"), ("On", "on"),
        ("warn", "warn"), ("WARN", "warn"), (" Warn ", "warn"),
        ("", "off"), ("0", "off"), ("off", "off"), ("enabled", "off"), ("warning", "off"),
    ]:
        monkeypatch.setenv(GATE, value)
        assert contact_permission_dial_gate_mode() == mode, value
        # every caller that predates ``warn`` sees True only for ``on``
        assert contact_permission_dial_gate_enabled() is (mode == "on"), value
    monkeypatch.delenv(GATE)
    assert contact_permission_dial_gate_mode() == "off"


def test_dial_time_logs_every_decision(monkeypatch, caplog):
    from app.domain.services.dialer.permission_gate import dial_time_decision

    caplog.set_level(logging.INFO, logger=LOG)
    job = _job()
    seen = []
    for mode, has_list, recorded, decision in [
        (None, True, False, "off"),
        ("on", True, True, "allow"),
        ("warn", False, False, "would_block"),
        ("on", False, False, "block"),
    ]:
        if mode:
            monkeypatch.setenv(GATE, mode)
        else:
            monkeypatch.delenv(GATE, raising=False)
        caplog.clear()
        assert dial_time_decision(job, has_list=has_list, recorded=recorded) == decision
        lines = [r.getMessage() for r in caplog.records if "contact_permission_gate " in r.getMessage()]
        assert len(lines) == 1
        seen.append(lines[0])
        assert f"decision={decision}" in lines[0]
        assert f"has_list={has_list}" in lines[0] and f"recorded={recorded}" in lines[0]
        # tenant and campaign are shortened to 8 characters
        assert f"tenant={job.tenant_id[:8]} " in lines[0]
        assert f"campaign={job.campaign_id[:8]} " in lines[0]
    assert len(set(seen)) == 4


# ═══ 2. warn never blocks ════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("row,expected", [
    (_row(), None),
    (_row(confirmed=None), None),
    (_row(list_id=None, confirmed=None), None),
])
async def test_warn_never_blocks_at_dial(monkeypatch, caplog, row, expected):
    monkeypatch.setenv(GATE, "warn")
    caplog.set_level(logging.INFO, logger=LOG)
    w = DialerWorker()
    _conn_pool(w, fetchrow=row)
    assert await w._stale_payload_reason(_job()) == expected
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "contact_permission_gate " in text and "mode=warn" in text
    assert ("decision=would_block" in text) is (row["list_confirmed_at"] is None)


@pytest.mark.asyncio
async def test_off_dial_time_logs_and_blocks_nothing(caplog):
    caplog.set_level(logging.INFO, logger=LOG)
    w = DialerWorker()
    _conn_pool(w, fetchrow=_row(list_id=None, confirmed=None))
    assert await w._stale_payload_reason(_job()) is None
    assert "mode=off" in caplog.text and "decision=off" in caplog.text


@pytest.mark.asyncio
async def test_warn_start_selects_all_and_counts(monkeypatch, caplog):
    monkeypatch.setenv(GATE, "warn")
    caplog.set_level(logging.INFO, logger=LOG)
    db = _FakeDB()
    _seed(db)
    out: list = []
    leads = await _service(db)._get_pending_leads("c1", withheld_out=out)
    assert {l["id"] for l in leads} == {"CONF", "UNCONF", "NOLIST", "XTENANT"}
    assert out == []  # nothing is withheld, so nothing is reported as withheld
    assert (
        "contact_permission_gate_start campaign=c1 tenant=None mode=warn "
        "candidates=4 without_permission=3 lookup=ok"
    ) in caplog.text or (
        "mode=warn candidates=4 without_permission=3 lookup=ok" in caplog.text
    )


@pytest.mark.asyncio
async def test_warn_start_message_and_jobs_match_off(monkeypatch):
    monkeypatch.setenv(GATE, "warn")
    result, _ = await _start(_start_db(pending_ok=2, pending_no=3), monkeypatch)
    assert result.jobs_enqueued == 5 and result.jobs_withheld == 0
    assert result.message == "Campaign campaign-1 started"


@pytest.mark.asyncio
async def test_warn_lookup_failure_selects_all(monkeypatch, caplog):
    monkeypatch.setenv(GATE, "warn")
    caplog.set_level(logging.INFO, logger=LOG)
    db = _FakeDB(fail_tables={"contact_lists"})
    _seed(db)
    out: list = []
    leads = await _service(db)._get_pending_leads("c1", withheld_out=out)
    assert len(leads) == 4 and out == []
    assert "lookup=failed" in caplog.text


@pytest.mark.asyncio
async def test_on_lookup_failure_still_fails_closed_and_logs(monkeypatch, caplog):
    monkeypatch.setenv(GATE, "on")
    caplog.set_level(logging.INFO, logger=LOG)
    db = _FakeDB(fail_tables={"contact_lists"})
    _seed(db)
    out: list = []
    assert await _service(db)._get_pending_leads("c1", withheld_out=out) == []
    assert out == [4]
    assert "mode=on" in caplog.text and "lookup=failed" in caplog.text


@pytest.mark.asyncio
async def test_on_start_logs_counts(monkeypatch, caplog):
    monkeypatch.setenv(GATE, "on")
    caplog.set_level(logging.INFO, logger=LOG)
    db = _FakeDB()
    _seed(db)
    out: list = []
    leads = await _service(db)._get_pending_leads("c1", withheld_out=out)
    assert {l["id"] for l in leads} == {"CONF"} and out == [3]
    assert "mode=on candidates=4 without_permission=3 lookup=ok" in caplog.text


@pytest.mark.asyncio
async def test_off_start_logs_nothing(caplog):
    caplog.set_level(logging.INFO, logger=LOG)
    db = _FakeDB(fail_tables={"contact_lists"})
    _seed(db)
    await _service(db)._get_pending_leads("c1")
    assert "contact_permission_gate_start" not in caplog.text


# ═══ 3. fakes for the routes ═════════════════════════════════════════════


class _Conn:
    """Answers the new statements. ``listless`` are the campaign's leads with no list."""

    def __init__(self, *, listless=3, status="running", pending=("p1", "p2"), waiting=(5, 3)):
        self.listless, self.status, self.pending, self.waiting = listless, status, list(pending), waiting
        self.calls: list = []
        self.lists: list = []
        self.moved = 0

    async def fetchrow(self, sql, *args):
        from app.api.v1.endpoints import contact_permission_confirm as cpc
        from app.domain.services.dialer import permission_gate as pg

        self.calls.append((sql, args))
        if sql == cpc._INSERT_LIST_SQL:
            self.lists.append(args)
            return {
                "id": args[2], "name": args[3], "source": "manual", "is_active": True,
                "created_at": datetime(2026, 11, 23, tzinfo=timezone.utc),
                "consent_confirmed_at": args[9],
                "consent_basis": args[5], "consent_source": args[6],
                "consent_permission_date": args[7],
            }
        if sql == pg._WAITING_SQL:
            return {"waiting": self.waiting[0], "ungrouped": self.waiting[1]}
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        from app.api.v1.endpoints import contact_permission_confirm as cpc

        self.calls.append((sql, args))
        if sql == cpc._MOVE_SQL:
            self.moved = self.listless
            return [{"id": f"m{i}"} for i in range(self.listless)]
        if sql == cpc._PENDING_IDS_SQL:
            return [{"id": i} for i in self.pending]
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        from app.api.v1.endpoints import contact_permission_confirm as cpc

        self.calls.append((sql, args))
        if sql == cpc._CAMPAIGN_STATUS_SQL:
            return self.status
        raise AssertionError(sql)


class _Tx:
    """Stands in for acquire_with_tenant: one transaction per ``async with``."""

    def __init__(self, conn):
        self.conn, self.scopes, self.committed, self.rolled_back = conn, [], 0, 0

    def __call__(self, pool, tenant, **kwargs):
        @asynccontextmanager
        async def cm():
            self.scopes.append((tenant, kwargs))
            try:
                yield self.conn
            except BaseException:
                self.rolled_back += 1
                raise
            self.committed += 1

        return cm()


def _wire(monkeypatch, conn, *, campaign_status="running"):
    from app.api.v1.endpoints import contact_permission_confirm as cpc

    tx = _Tx(conn)
    monkeypatch.setattr(cpc, "acquire_with_tenant", tx)
    monkeypatch.setattr(
        cpc, "require_owned_outbound_campaign",
        lambda *_a, **_k: {"id": CAMPAIGN, "direction": "outbound", "status": campaign_status},
    )
    return cpc, tx


class _User2(_User):
    tenant_id = TENANT
    id = "33333333-3333-3333-3333-333333333333"


def _db():
    return SimpleNamespace(pool=object())


class _Service:
    def __init__(self, jobs=2, dispatching=0, error=None):
        self.calls, self.jobs, self.dispatching, self.error = [], jobs, dispatching, error

    async def start_campaign(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(jobs_enqueued=self.jobs, jobs_dispatching=self.dispatching)


def _service_patch(monkeypatch, svc):
    from app.api.v1.endpoints import campaigns as campaigns_ep

    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: svc)


BODY = {**GOOD, "expected_count": 3}


# ═══ 4. the summary route ════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,status,queues", [
    (None, "running", False), ("warn", "running", False),
    ("on", "paused", False), ("on", "running", True),
])
async def test_summary_counts_and_mode(monkeypatch, mode, status, queues):
    if mode:
        monkeypatch.setenv(GATE, mode)
    conn = _Conn(waiting=(312, 40))
    cpc, tx = _wire(monkeypatch, conn, campaign_status=status)
    out = await cpc.get_contact_permission_summary(CAMPAIGN, _User2(), _db())
    assert out == {
        "dial_gate": mode or "off", "statement_version": "2026-11-v1",
        "waiting_without_permission": 312, "ungrouped_without_permission": 40,
        "queues_on_confirm": queues,
    }
    assert conn.calls[0][1] == (TENANT, CAMPAIGN) and tx.scopes[0][0] == TENANT


def _camp_db(rows):
    """Tenant-scoped campaigns table for the REAL ownership guard."""
    class Q:
        def __init__(self):
            self.f = {}

        def select(self, *_a, **_k):
            return self

        def eq(self, col, val):
            self.f[col] = val
            return self

        def execute(self):
            data = [r for r in rows if all(r.get(k) == v for k, v in self.f.items())]
            return SimpleNamespace(data=data, error=None)

    return SimpleNamespace(table=lambda _n: Q(), pool=object())


@pytest.mark.asyncio
async def test_summary_404_foreign_campaign(monkeypatch):
    from app.api.v1.endpoints import contact_permission_confirm as cpc

    conn = _Conn()
    monkeypatch.setattr(cpc, "acquire_with_tenant", _Tx(conn))
    db = _camp_db([{"id": CAMPAIGN, "tenant_id": OTHER_TENANT, "direction": "outbound", "status": "running"}])
    with pytest.raises(HTTPException) as exc:
        await cpc.get_contact_permission_summary(CAMPAIGN, _User2(), db)
    assert exc.value.status_code == 404 and conn.calls == []


@pytest.mark.asyncio
async def test_summary_409_inbound(monkeypatch):
    from app.api.v1.endpoints import contact_permission_confirm as cpc

    conn = _Conn()
    monkeypatch.setattr(cpc, "acquire_with_tenant", _Tx(conn))
    db = _camp_db([{"id": CAMPAIGN, "tenant_id": TENANT, "direction": "inbound", "status": "running"}])
    with pytest.raises(HTTPException) as exc:
        await cpc.get_contact_permission_summary(CAMPAIGN, _User2(), db)
    assert exc.value.status_code == 409 and conn.calls == []


# ═══ 5. confirming the contacts that have no list ════════════════════════


@pytest.mark.asyncio
async def test_ungrouped_records_six_columns_and_moves_exact_contacts(monkeypatch):
    conn = _Conn(listless=3)
    cpc, tx = _wire(monkeypatch, conn)
    out = await cpc.confirm_ungrouped_permission(CAMPAIGN, dict(BODY), _User2(), _db())

    assert conn.calls[0][0] == cpc._CAMPAIGN_STATUS_SQL
    insert_sql, a = conn.calls[1]
    assert insert_sql == cpc._INSERT_LIST_SQL
    (tenant, campaign, list_id, name, user, basis, source, perm_date, version, at) = a
    assert at.tzinfo is not None and abs((datetime.now(timezone.utc) - at).total_seconds()) < 60
    assert (tenant, campaign, user) == (TENANT, CAMPAIGN, _User2.id)
    assert (basis, source, version) == (GOOD["basis"], GOOD["source"], "2026-11-v1")
    assert perm_date.isoformat() == GOOD["permission_date"]
    assert name.startswith("Earlier contacts, confirmed ") and name.endswith(" UTC")
    move_sql, m = conn.calls[2]
    assert move_sql == cpc._MOVE_SQL and m == (TENANT, CAMPAIGN, list_id)
    # one transaction, scoped to the tenant, carrying the user for audit
    assert tx.committed == 1 and tx.rolled_back == 0
    assert tx.scopes[0] == (TENANT, {"user_id": _User2.id})
    assert out["contacts_confirmed"] == 3 and out["list"].contact_count == 3
    assert out["list"].permission.confirmed is True
    assert out["list"].permission.basis == "express_consent"
    assert out["list"].id == list_id
    assert out["message"].startswith("Permission recorded for 3 contacts. They are now in the list ")
    assert out["queued_for_calling"] is None  # switch off: not attempted


@pytest.mark.asyncio
async def test_ungrouped_count_changed_is_409_and_rolled_back(monkeypatch):
    conn = _Conn(listless=5)  # the person saw 3
    cpc, tx = _wire(monkeypatch, conn)
    svc = _Service()
    _service_patch(monkeypatch, svc)
    monkeypatch.setenv(GATE, "on")
    with pytest.raises(HTTPException) as exc:
        await cpc.confirm_ungrouped_permission(CAMPAIGN, dict(BODY), _User2(), _db())
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "contact_count_changed",
        "message": "The number of contacts without a list changed from 3 to 5. Check them and confirm again.",
    }
    # both statements ran INSIDE the one transaction block, which was exited by the
    # exception (a rollback): the list insert and the move are both undone
    assert [c[0] for c in conn.calls] == [
        cpc._CAMPAIGN_STATUS_SQL, cpc._INSERT_LIST_SQL, cpc._MOVE_SQL,
    ]
    assert tx.rolled_back == 1 and tx.committed == 0 and len(tx.scopes) == 1
    assert svc.calls == []  # nothing queued for a refused confirmation


@pytest.mark.asyncio
async def test_ungrouped_campaign_changed_is_409_before_any_write(monkeypatch):
    conn = _Conn(status="deleted")
    cpc, tx = _wire(monkeypatch, conn)
    with pytest.raises(HTTPException) as exc:
        await cpc.confirm_ungrouped_permission(CAMPAIGN, dict(BODY), _User2(), _db())
    assert exc.value.status_code == 409
    assert [c[0] for c in conn.calls] == [cpc._CAMPAIGN_STATUS_SQL] and tx.rolled_back == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"basis": None}, {"basis": "because"}, {"source": "abc"}, {"permission_date": "2999-01-01"},
    {"permission_date": "yesterday"}, {"statement_version": "old"},
])
async def test_ungrouped_422_missing_or_bad_fields(monkeypatch, override):
    conn = _Conn()
    cpc, tx = _wire(monkeypatch, conn)
    with pytest.raises(HTTPException) as exc:
        await cpc.confirm_ungrouped_permission(CAMPAIGN, {**BODY, **override}, _User2(), _db())
    assert exc.value.status_code == 422 and conn.calls == [] and tx.scopes == []


@pytest.mark.asyncio
async def test_ungrouped_422_no_body(monkeypatch):
    cpc, tx = _wire(monkeypatch, _Conn())
    with pytest.raises(HTTPException) as exc:
        await cpc.confirm_ungrouped_permission(CAMPAIGN, None, _User2(), _db())
    assert exc.value.status_code == 422 and exc.value.detail["code"] == "contact_permission_required"
    assert tx.scopes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, 0, -1, 1_000_001, "3", 3.0, True, [3]])
async def test_ungrouped_422_bad_expected_count(monkeypatch, bad):
    conn = _Conn()
    cpc, tx = _wire(monkeypatch, conn)
    body = {**GOOD}
    if bad is not None:
        body["expected_count"] = bad
    with pytest.raises(HTTPException) as exc:
        await cpc.confirm_ungrouped_permission(CAMPAIGN, body, _User2(), _db())
    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "code": "contact_permission_invalid", "field": "expected_count",
        "message": "Reload the page and confirm again.",
    }
    assert conn.calls == [] and tx.scopes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,status,queues", [
    (None, "running", False), ("warn", "running", False),
    ("on", "running", True), ("on", "paused", False), ("on", "stopped", False),
    ("on", "draft", False),
])
async def test_ungrouped_queues_only_when_on_and_running(monkeypatch, mode, status, queues):
    if mode:
        monkeypatch.setenv(GATE, mode)
    conn = _Conn(status=status)
    cpc, tx = _wire(monkeypatch, conn)
    svc = _Service(jobs=2)
    _service_patch(monkeypatch, svc)
    out = await cpc.confirm_ungrouped_permission(CAMPAIGN, dict(BODY), _User2(), _db())
    if queues:
        assert len(svc.calls) == 1
        assert out["queued_for_calling"] == 2 and "2 were queued for calling." in out["message"]
    else:
        assert svc.calls == [] and out["queued_for_calling"] is None
        assert "queued" not in out["message"]


@pytest.mark.asyncio
async def test_ungrouped_queue_call_is_narrow_and_after_the_commit(monkeypatch):
    monkeypatch.setenv(GATE, "on")
    conn = _Conn()
    cpc, tx = _wire(monkeypatch, conn)
    seen = {}

    class Svc(_Service):
        async def start_campaign(self, **kwargs):
            seen["committed_before"], seen["rolled_back"] = tx.committed, tx.rolled_back
            return await super().start_campaign(**kwargs)

    svc = Svc()
    _service_patch(monkeypatch, svc)
    await cpc.confirm_ungrouped_permission(CAMPAIGN, dict(BODY), _User2(), _db())
    # the confirmation transaction (1) and the helper's own read (2) are both closed
    assert seen["committed_before"] == 2 and seen["rolled_back"] == 0
    kwargs = svc.calls[0]
    assert kwargs["allow_running"] is True and kwargs["only_lead_ids"] == ["p1", "p2"]
    assert kwargs["campaign_id"] == CAMPAIGN and kwargs["tenant_id"] == TENANT
    assert "list_id" not in kwargs  # a switched-off list stays out


@pytest.mark.asyncio
async def test_ungrouped_never_restarts_stopped_campaign(monkeypatch):
    """The helper does not call the start path for a non-running campaign, and the
    start path itself refuses one (the real service, not a stub)."""
    from app.domain.services.campaign_service import CampaignService, CampaignStateError
    from tests.unit.test_campaign_start_dispatch_truth import _DB as StartDB
    from tests.unit.test_campaign_start_dispatch_truth import _Queue as StartQueue

    monkeypatch.setenv(GATE, "on")
    conn = _Conn(status="stopped")
    cpc, _tx = _wire(monkeypatch, conn)
    svc = _Service()
    _service_patch(monkeypatch, svc)
    out = await cpc.confirm_ungrouped_permission(CAMPAIGN, dict(BODY), _User2(), _db())
    assert svc.calls == [] and out["queued_for_calling"] is None

    db = StartDB(lead_count=1)
    db.tables["campaigns"][0]["status"] = "stopped"
    service = CampaignService(db, queue_service=StartQueue([True] * 5))
    with pytest.raises(CampaignStateError):
        await service.start_campaign(
            "campaign-1", tenant_id="tenant-1", allow_running=True, only_lead_ids=["lead-1"],
        )
    assert db.tables["dialer_jobs"] == [] and db.tables["campaigns"][0]["status"] == "stopped"


@pytest.mark.asyncio
async def test_queue_failure_keeps_the_permission_and_says_so(monkeypatch, caplog):
    monkeypatch.setenv(GATE, "on")
    conn = _Conn()
    cpc, tx = _wire(monkeypatch, conn)
    _service_patch(monkeypatch, _Service(error=RuntimeError("redis down")))
    caplog.set_level(logging.WARNING)
    out = await cpc.confirm_ungrouped_permission(CAMPAIGN, dict(BODY), _User2(), _db())
    assert tx.committed >= 1 and tx.rolled_back == 0 and out["queued_for_calling"] is None
    assert out["message"].endswith(" They could not be queued just now; use Call this list on that list.")
    assert "contact_permission_queue_failed" in caplog.text


# ═══ 6. confirming an older list queues its contacts ═════════════════════


def _confirm_list(monkeypatch, *, active=True, status="running"):
    db = _ListDB()
    db.lists.append(_list_row(is_active=active))
    db.pool = object()
    conn = _Conn(status=status)
    from app.api.v1.endpoints import contact_permission_confirm as cpc

    tx = _Tx(conn)
    monkeypatch.setattr(cpc, "acquire_with_tenant", tx)
    return db, conn


@pytest.mark.asyncio
async def test_list_confirm_queues_when_on_and_running(monkeypatch):
    from app.api.v1.endpoints import contact_lists as cl_ep

    monkeypatch.setenv(GATE, "on")
    db, conn = _confirm_list(monkeypatch)
    svc = _Service(jobs=4)
    _service_patch(monkeypatch, svc)
    out = await cl_ep.record_contact_list_permission("L1", dict(GOOD), _User(), db)
    assert out.permission.confirmed is True
    assert out.queued_for_calling == 4
    assert out.message == "Permission recorded. 4 contacts were queued for calling."
    assert svc.calls[0]["only_lead_ids"] == ["p1", "p2"] and svc.calls[0]["allow_running"] is True
    # the pending-ids statement was asked for THIS list in THIS tenant
    ids_call = [c for c in conn.calls if "FROM leads" in c[0]][0]
    assert ids_call[1] == ("t1", "c1", "L1")


@pytest.mark.asyncio
async def test_list_confirm_does_not_queue_inactive_list(monkeypatch):
    from app.api.v1.endpoints import contact_lists as cl_ep

    monkeypatch.setenv(GATE, "on")
    db, conn = _confirm_list(monkeypatch, active=False)
    svc = _Service()
    _service_patch(monkeypatch, svc)
    out = await cl_ep.record_contact_list_permission("L1", dict(GOOD), _User(), db)
    assert out.permission.confirmed is True
    assert svc.calls == [] and conn.calls == []
    assert out.queued_for_calling is None and out.message is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "warn"])
async def test_list_confirm_unchanged_when_off(monkeypatch, mode):
    from app.api.v1.endpoints import contact_lists as cl_ep

    if mode:
        monkeypatch.setenv(GATE, mode)
    db, conn = _confirm_list(monkeypatch)
    svc = _Service()
    _service_patch(monkeypatch, svc)
    out = await cl_ep.record_contact_list_permission("L1", dict(GOOD), _User(), db)
    assert out.permission.confirmed is True
    assert svc.calls == [] and conn.calls == []
    assert out.queued_for_calling is None and out.message is None


@pytest.mark.asyncio
async def test_list_confirm_not_running_queues_nothing(monkeypatch):
    from app.api.v1.endpoints import contact_lists as cl_ep

    monkeypatch.setenv(GATE, "on")
    db, conn = _confirm_list(monkeypatch, status="paused")
    svc = _Service()
    _service_patch(monkeypatch, svc)
    out = await cl_ep.record_contact_list_permission("L1", dict(GOOD), _User(), db)
    assert svc.calls == [] and out.queued_for_calling is None and out.message is None


# ═══ 7. redial, assistant call, Start answer ═════════════════════════════


def _redial(monkeypatch, *, missing):
    from app.domain.services import call_redial_service as svc

    row = dict(
        campaign_id="c1", lead_id="l1", phone_number="+14165550123", status="ended",
        direction="outbound", campaign_direction="outbound", campaign_status="running",
        outcome="answered", original_phone="+14165550123", lead_status="completed",
        do_not_call=False, calling_config={}, calling_rules={},
    )
    looked_up = []

    @asynccontextmanager
    async def scoped(pool, tenant_id, **_k):
        yield object()

    async def missing_fn(conn, tenant_id, lead_id):
        looked_up.append((tenant_id, lead_id))
        return missing

    monkeypatch.setattr(svc, "acquire_with_tenant", scoped)
    monkeypatch.setattr(svc, "_context", AsyncMock(return_value=row))
    monkeypatch.setattr(svc, "_job_state", AsyncMock(return_value=(None, False)))
    monkeypatch.setattr(svc, "permission_missing_for_lead", missing_fn)
    dnc = AsyncMock(return_value=False)
    monkeypatch.setattr(svc, "DNCService", lambda _p: SimpleNamespace(is_on_dnc=dnc))
    ready = AsyncMock(return_value={"ready": True, "caller_id": "+1", "trunk_id": "t",
                                    "reason_code": None, "reason": None})
    monkeypatch.setattr(svc, "evaluate_outbound_readiness", ready)
    return svc, looked_up, dnc


@pytest.mark.asyncio
async def test_redial_refused_when_on_and_missing(monkeypatch):
    monkeypatch.setenv(GATE, "on")
    svc, looked_up, dnc = _redial(monkeypatch, missing=True)
    out = await svc.preview_redial(object(), tenant_id=TENANT, call_id="9" * 8 + "-0000-0000-0000-000000000000")
    assert out["eligible"] is False and out["reason_code"] == "contact_permission_missing"
    assert out["reason"] == (
        "No permission to call this contact is recorded. Confirm permission on the "
        "contact's list in the campaign first."
    )
    assert looked_up == [(TENANT, "l1")]
    dnc.assert_not_awaited()  # refused before the DNC check, as planned


@pytest.mark.asyncio
async def test_redial_allowed_when_on_and_recorded(monkeypatch):
    monkeypatch.setenv(GATE, "on")
    svc, looked_up, _dnc = _redial(monkeypatch, missing=False)
    out = await svc.preview_redial(object(), tenant_id=TENANT, call_id="9" * 8 + "-0000-0000-0000-000000000000")
    assert out["eligible"] is True and looked_up == [(TENANT, "l1")]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "warn"])
async def test_redial_unchanged_when_off(monkeypatch, mode):
    if mode:
        monkeypatch.setenv(GATE, mode)
    svc, looked_up, _dnc = _redial(monkeypatch, missing=True)
    out = await svc.preview_redial(object(), tenant_id=TENANT, call_id="9" * 8 + "-0000-0000-0000-000000000000")
    assert out["eligible"] is True and looked_up == []  # off/warn never look it up


class _CallConn:
    def __init__(self, missing):
        self.missing, self.sqls = missing, []

    async def fetchrow(self, sql, *args):
        self.sqls.append(sql)
        if "FROM campaigns" in sql:
            return {"direction": "outbound", "status": "running"}
        if "FROM leads AS l" in sql:  # the permission lookup
            return {"missing": self.missing}
        return {"id": "55555555-5555-5555-5555-555555555555", "phone_number": "+14155550123",
                "do_not_call": False, "status": "pending"}

    async def fetchval(self, sql, *args):
        self.sqls.append(sql)
        return False

    async def execute(self, sql, *args):
        self.sqls.append(sql)


async def _assistant_call(monkeypatch, *, missing, confirm):
    from app.infrastructure.assistant.tools import calls

    conn = _CallConn(missing)

    @asynccontextmanager
    async def scoped(pool, tenant_id, **_k):
        yield conn

    import app.core.db_utils as db_utils

    monkeypatch.setattr(db_utils, "acquire_with_tenant", scoped)
    out = await calls.initiate_call(
        TENANT, SimpleNamespace(pool=object()), "+14155550123", campaign_id=CAMPAIGN, confirm=confirm,
    )
    return out, conn


@pytest.mark.asyncio
async def test_assistant_call_refused_when_on_and_missing(monkeypatch):
    monkeypatch.setenv(GATE, "on")
    for confirm in (False, True):
        out, conn = await _assistant_call(monkeypatch, missing=True, confirm=confirm)
        assert out == {"success": False, "error": (
            "No permission to call this contact is recorded. Confirm permission on the "
            "contact's list in the campaign first."
        )}
        assert not any("INSERT INTO dialer_jobs" in s for s in conn.sqls)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "warn"])
async def test_assistant_call_unchanged_when_off(monkeypatch, mode):
    if mode:
        monkeypatch.setenv(GATE, mode)
    out, conn = await _assistant_call(monkeypatch, missing=True, confirm=False)
    assert out["success"] is True and out["status"] == "preview"
    assert not any("FROM leads AS l" in s for s in conn.sqls)


@pytest.mark.asyncio
async def test_start_response_has_jobs_withheld(monkeypatch):
    """The Start endpoint's answer carries the withheld count the service reports."""
    import inspect

    from app.api.v1.endpoints import campaigns as campaigns_ep

    # behavioural half: the service really reports the count when the switch is on
    monkeypatch.setenv(GATE, "on")
    result, _ = await _start(_start_db(pending_ok=1, pending_no=2), monkeypatch)
    assert result.jobs_withheld == 2
    # wiring half: the endpoint copies it into the answer (a guard, not a behaviour test)
    src = inspect.getsource(campaigns_ep.start_campaign)
    assert '"jobs_withheld": int(getattr(result, "jobs_withheld", 0) or 0)' in src


# ═══ 8. the new SQL ══════════════════════════════════════════════════════


def test_new_sql_carries_tenant_predicate():
    from app.api.v1.endpoints import contact_permission_confirm as cpc
    from app.domain.services.dialer import permission_gate as pg

    for name, sql in [
        ("_MOVE_SQL", cpc._MOVE_SQL),
        ("_CAMPAIGN_STATUS_SQL", cpc._CAMPAIGN_STATUS_SQL), ("_PENDING_IDS_SQL", cpc._PENDING_IDS_SQL),
        ("_WAITING_SQL", pg._WAITING_SQL), ("_LEAD_PERMISSION_SQL", pg._LEAD_PERMISSION_SQL),
    ]:
        assert "tenant_id = $1::uuid" in sql, name
    # the INSERT binds the tenant ($1) into the tenant_id column of its VALUES row
    cols, values = cpc._INSERT_LIST_SQL.split("VALUES")
    names = [c.strip() for c in cols.split("(", 1)[1].rsplit(")", 1)[0].split(",")]
    bound = [v.strip() for v in values.split("(", 1)[1].split(")", 1)[0].split(",")]
    assert bound[names.index("tenant_id")] == "$1::uuid"
    # the list is read through a tenant-scoped subquery
    assert "cl.tenant_id = $1::uuid" in pg._WAITING_SQL and "cl.tenant_id = $1::uuid" in pg._LEAD_PERMISSION_SQL


@pytest.mark.asyncio
async def test_summary_and_lead_helpers_pass_the_tenant_first():
    from app.domain.services.dialer import permission_gate as pg

    conn = SimpleNamespace(fetchrow=AsyncMock(return_value=None))
    assert await pg.permission_missing_for_lead(conn, TENANT, "l1") is True  # no row = missing
    assert conn.fetchrow.call_args.args == (pg._LEAD_PERMISSION_SQL, TENANT, "l1")
    assert await pg.count_waiting_without_permission(conn, TENANT, CAMPAIGN) == (0, 0)
    assert conn.fetchrow.call_args.args == (pg._WAITING_SQL, TENANT, CAMPAIGN)
    conn.fetchrow = AsyncMock(return_value={"missing": False})
    assert await pg.permission_missing_for_lead(conn, TENANT, "l1") is False

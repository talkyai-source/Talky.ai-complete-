"""A contact whose call is still live is never queued again by the mid-run path (F1),
plus F4 (a do-not-call contact is not counted), F5 (an invalid-number skip is dialled
once the number is corrected) and G (the lead-status resets are best effort)."""
from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.endpoints.telephony_bridge import CAMPAIGN_LIVE_CALL_STATUSES
from app.api.v1.schemas.campaigns import ContactCreate, ContactUpdate
from app.domain.services.campaign_service import CampaignService
from tests.unit.test_bh_campaigns_contacts_contact_writes import _User
from tests.unit.test_bh_campaigns_contacts_contact_writes import _DB as _ContactDB
from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue
from tests.unit.test_live_risk_contacts import _JobsDB, _JobsQ
from tests.unit.test_running_campaign_new_contacts_20261121 import PERMISSION

TENANT = "tenant-1"


class _CallsConn:
    """Evaluates the live-call statement against db.tables['calls'], honouring each
    predicate only when its text is in the statement."""

    def __init__(self, db, log, fail=False):
        self.db, self.log, self.fail = db, log, fail

    async def fetchval(self, sql, *args):
        sql = " ".join(sql.split())
        self.log.append((sql, args))
        if self.fail:
            raise RuntimeError("calls unreadable")
        tenant, lead, statuses = args
        for call in self.db.tables.get("calls", []):
            if "tenant_id = $1::uuid" in sql and call["tenant_id"] != tenant:
                continue
            if "lead_id = $2::uuid" in sql and call["lead_id"] != lead:
                continue
            if "status = ANY($3::text[])" in sql and call["status"] not in statuses:
                continue
            if "is_test IS NOT TRUE" in sql and call.get("is_test") is True:
                continue
            return 1  # as the driver does for SELECT 1 ... LIMIT 1
        return None  # no row


def _pool(monkeypatch, db, *, fail=False):
    log = []

    @contextlib.asynccontextmanager
    async def acquire(_pool, _tenant):
        yield _CallsConn(db, log, fail)

    monkeypatch.setattr(campaigns_ep, "acquire_with_tenant", acquire)
    return log


def _running(lead_status="pending"):
    db = _DB(lead_count=0)
    db.tables["campaigns"][0].update(status="running", started_at="2026-11-20T09:00:00", total_leads=1)
    db.tables["leads"].append({
        "id": "lead-1", "tenant_id": TENANT, "campaign_id": "campaign-1", "priority": 5,
        "status": lead_status, "do_not_call": False, "list_id": None,
        "phone_number": "+14155550101", "created_at": "2026-09-03T00:00:00+00:00"})
    db.tables["calls"] = []
    return db


def _real_service(monkeypatch, db, queue):
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service",
                        lambda _db: CampaignService(db, queue_service=queue))


def _call(status="in_call", *, tenant=TENANT, lead="lead-1", is_test=False):
    return {"tenant_id": tenant, "lead_id": lead, "status": status, "is_test": is_test}


class _WithPool:
    """The endpoint's db client (has a pool); the service is given the plain fake."""
    pool = object()


async def _queue(db):
    campaign = {"status": "running"}
    await campaigns_ep._queue_new_contacts_if_running(_WithPool(), campaign, "campaign-1", TENANT, "lead-1")


# --- path (a): removed during a live call, added back ----------------------------
async def test_a_contact_added_back_during_its_live_call_is_not_queued_again(monkeypatch, caplog):
    db, queue = _running(), _Queue([True])
    db.tables["calls"].append(_call("in_call"))      # job was cancelled by the removal: none active
    log = _pool(monkeypatch, db)
    _real_service(monkeypatch, db, queue)

    with caplog.at_level("WARNING"):
        await _queue(db)

    assert db.tables["dialer_jobs"] == [] and queue.enqueued == []
    assert any("contact_queue_skipped_live_call campaign=campaign" in r.getMessage() for r in caplog.records)
    sql, args = log[0]
    assert "tenant_id = $1::uuid" in sql and "lead_id = $2::uuid" in sql and "is_test IS NOT TRUE" in sql
    assert args == (TENANT, "lead-1", list(CAMPAIGN_LIVE_CALL_STATUSES))


# --- path (b): renumbered while the first call may still be up -------------------
@pytest.mark.parametrize("lead_status,call_status", [("calling", "ringing"), ("calling", "initiated"),
                                                      ("pending", "answered")])
async def test_a_renumbered_contact_with_a_live_call_is_not_queued_again(
        monkeypatch, caplog, lead_status, call_status):
    db, queue = _running(lead_status), _Queue([True])
    db.tables["calls"].append(_call(call_status))
    _pool(monkeypatch, db)
    _real_service(monkeypatch, db, queue)

    with caplog.at_level("WARNING"):
        await _queue(db)

    assert db.tables["dialer_jobs"] == [] and queue.enqueued == []
    assert any("contact_queue_skipped_live_call" in r.getMessage() for r in caplog.records)


async def test_no_live_call_queues_the_contact_as_before(monkeypatch):
    db, queue = _running(), _Queue([True])
    db.tables["calls"].append(_call("completed"))     # finished calls do not block
    _pool(monkeypatch, db)
    _real_service(monkeypatch, db, queue)

    await _queue(db)

    assert [j.lead_id for j in queue.enqueued] == ["lead-1"]


async def test_a_live_test_agent_call_does_not_block(monkeypatch):
    db, queue = _running(), _Queue([True])
    db.tables["calls"].append(_call("in_progress", is_test=True))
    _pool(monkeypatch, db)
    _real_service(monkeypatch, db, queue)

    await _queue(db)

    assert [j.lead_id for j in queue.enqueued] == ["lead-1"]


async def test_another_tenants_live_call_with_the_same_lead_id_does_not_block(monkeypatch):
    db, queue = _running(), _Queue([True])
    db.tables["calls"].append(_call("in_call", tenant="tenant-other"))
    _pool(monkeypatch, db)
    _real_service(monkeypatch, db, queue)

    await _queue(db)

    assert [j.lead_id for j in queue.enqueued] == ["lead-1"]


async def test_an_unreadable_live_call_check_queues_nothing(monkeypatch, caplog):
    db, queue = _running(), _Queue([True])
    _pool(monkeypatch, db, fail=True)
    _real_service(monkeypatch, db, queue)

    with caplog.at_level("WARNING"):
        await _queue(db)

    assert db.tables["dialer_jobs"] == [] and queue.enqueued == []
    assert any("contact_queue_skipped_live_call" in r.getMessage() for r in caplog.records)


def test_the_contact_write_still_succeeds_when_the_live_call_read_fails(monkeypatch):
    service = SimpleNamespace(start_campaign=AsyncMock())
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: service)
    db = _ContactDB()
    db.tables["campaigns"][0]["status"] = "running"      # fake has no pool: the read raises
    db.tables["leads"].append({"id": "l1", "tenant_id": "t1", "campaign_id": "c1",
                               "phone_number": "+14155550199", "status": "deleted", "is_lead": False})

    out = asyncio.run(campaigns_ep.add_contact_to_campaign(
        campaign_id="c1", contact=ContactCreate(phone_number="+14155550199", permission=PERMISSION),
        current_user=_User(), supabase=db))

    assert out["message"] == "Contact added successfully"
    assert db.tables["leads"][0]["status"] == "pending"
    service.start_campaign.assert_not_awaited()


def test_the_live_call_statuses_are_the_products_own_set():
    assert "in_call" in CAMPAIGN_LIVE_CALL_STATUSES and "termination_pending" in CAMPAIGN_LIVE_CALL_STATUSES


# --- F4: a do-not-call contact is not counted ------------------------------------
class _CountConn:
    def __init__(self, log):
        self.log = log

    async def execute(self, sql, *args):
        self.log.append((sql, args))


@pytest.mark.parametrize("dnc,expected_writes", [(True, 0), (False, 1)])
def test_a_do_not_call_contact_is_not_counted_in_the_total(monkeypatch, dnc, expected_writes):
    monkeypatch.setattr(campaigns_ep, "_contact_has_live_call", AsyncMock(return_value=False))
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service",
                        lambda _db: SimpleNamespace(start_campaign=AsyncMock()))
    log = []

    @contextlib.asynccontextmanager
    async def acquire(_pool, _tenant):
        yield _CountConn(log)

    monkeypatch.setattr(campaigns_ep, "acquire_with_tenant", acquire)
    db = _ContactDB()
    db.tables["campaigns"][0]["status"] = "running"
    db.pool = object()

    asyncio.run(campaigns_ep.add_contact_to_campaign(
        campaign_id="c1",
        contact=ContactCreate(phone_number="+14155550177", do_not_call=dnc, permission=PERMISSION),
        current_user=_User(), supabase=db))

    assert len(log) == expected_writes


# --- F5 and G: the update endpoint ------------------------------------------------
def _edit(db, **kw):
    return asyncio.run(campaigns_ep.update_contact_in_campaign(
        campaign_id="c1", contact_id="l1", contact=ContactUpdate(**kw),
        current_user=_User(), supabase=db))


def _running_jobs_db(**lead):
    db = _JobsDB()
    db.tables["campaigns"][0]["status"] = "running"
    db.tables["dialer_jobs"].clear()
    db.tables["leads"][0].update(lead)
    return db


def _spy(monkeypatch):
    service = SimpleNamespace(start_campaign=AsyncMock())
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: service)
    monkeypatch.setattr(campaigns_ep, "_contact_has_live_call", AsyncMock(return_value=False))
    return service


def test_a_corrected_number_on_a_contact_skipped_for_an_invalid_number_is_queued(monkeypatch):
    service = _spy(monkeypatch)
    db = _running_jobs_db(status="skipped", last_call_result="invalid_phone_number")

    _edit(db, phone_number="+14155550999")

    assert db.tables["leads"][0]["status"] == "pending"
    assert db.tables["leads"][0]["phone_number"] == "+14155550999"
    service.start_campaign.assert_awaited_once_with(
        campaign_id="c1", tenant_id="t1", allow_running=True, only_lead_ids=["l1"])


@pytest.mark.parametrize("reason", ["no_answer", "dnc", "invalid_phone_number_other", None])
def test_a_contact_skipped_for_any_other_reason_is_left_alone(monkeypatch, reason):
    _spy(monkeypatch)
    db = _running_jobs_db(status="skipped", last_call_result=reason)

    _edit(db, phone_number="+14155550999")

    assert db.tables["leads"][0]["status"] == "skipped"


def test_the_invalid_number_reset_is_scoped_to_the_tenant_and_campaign(monkeypatch):
    _spy(monkeypatch)
    db = _running_jobs_db(status="skipped", last_call_result="invalid_phone_number", tenant_id="t2")
    db.tables["campaigns"][0]["tenant_id"] = "t1"

    with pytest.raises(Exception):      # the lead belongs to another tenant: edit is a 404
        _edit(db, phone_number="+14155550999")

    assert db.tables["leads"][0]["status"] == "skipped"


class _FailingResetQ(_JobsQ):
    def execute(self):
        if self._table == "leads" and self._op == "update" and self._payload == {"status": "pending"}:
            raise RuntimeError("reset failed")
        return super().execute()


class _FailingResetDB(_JobsDB):
    def table(self, name):
        return _FailingResetQ(self, name)


def test_a_failed_lead_status_reset_never_fails_the_edit(monkeypatch, caplog):
    _spy(monkeypatch)
    db = _FailingResetDB()
    db.tables["campaigns"][0]["status"] = "running"
    db.tables["leads"][0].update(status="skipped", last_call_result="invalid_phone_number")

    with caplog.at_level("WARNING"):
        out = _edit(db, phone_number="+14155550999")

    assert out["message"] == "Contact updated successfully"
    assert db.tables["leads"][0]["phone_number"] == "+14155550999"
    assert any("contact_requeue_status_not_reset" in r.getMessage() for r in caplog.records)

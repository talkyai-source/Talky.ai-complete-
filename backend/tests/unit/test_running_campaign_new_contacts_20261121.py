"""Dialler wave 2 (2026-11-21), campaigns.py + campaign_service.py items.

G-F8   a contact added, or given a new number, while a campaign runs is queued;
       the extra guard keeps a mid-run add from reopening finished contacts.
G-F7b  a Test-agent call row never keeps a campaign "running".
G-F12a Test-agent calls are not counted in the numbers a customer sees.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.schemas.campaigns import ContactCreate, ContactUpdate
from app.domain.services import campaign_service as svc
from app.domain.services.campaign_service import (
    CampaignDispatchError,
    CampaignError,
    CampaignService,
    CampaignStateError,
)
from tests.unit.test_campaign_start_compare_and_set import _change_after_reading, _set_campaign
from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue
from tests.unit.test_live_risk_contacts import _JobsDB, _status
from tests.unit.test_bh_campaigns_contacts_contact_writes import _DB as _ContactDB, _User

PERMISSION = {
    "basis": "express_consent",
    "source": "Customer sign-up form, March 2026",
    "permission_date": "2026-03-14",
    "statement_version": "2026-11-v1",
}


def _running_db(*, failed=0, new=1):
    """A running campaign: `failed` finished-failed contacts, `new` fresh pending ones."""
    db = _DB(lead_count=0)
    db.tables["campaigns"][0].update(
        status="running", started_at="2026-11-20T09:00:00", calls_completed=4, calls_failed=failed,
        total_leads=4 + failed)
    base = {"tenant_id": "tenant-1", "campaign_id": "campaign-1", "priority": 5,
            "do_not_call": False, "list_id": None, "created_at": "2026-09-03T00:00:00+00:00"}
    for i in range(failed):
        db.tables["leads"].append({**base, "id": f"failed-{i}", "status": "failed",
                                   "phone_number": f"+1415555020{i}"})
    for i in range(new):
        db.tables["leads"].append({**base, "id": f"new-{i}", "status": "pending",
                                   "phone_number": f"+1415555030{i}"})
    return db


# --- G-F8: the guard at the reset condition ---------------------------------
async def test_running_campaign_with_failed_contacts_and_one_new_contact_queues_only_the_new_one():
    db, queue = _running_db(failed=3, new=1), _Queue([True])
    service = CampaignService(db, queue_service=queue)

    result = await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert result.jobs_enqueued == 1
    assert [j.lead_id for j in queue.enqueued] == ["new-0"]
    assert [r["lead_id"] for r in db.tables["dialer_jobs"]] == ["new-0"]
    by_id = {r["id"]: r["status"] for r in db.tables["leads"]}
    assert by_id == {"failed-0": "failed", "failed-1": "failed", "failed-2": "failed", "new-0": "pending"}
    assert db.tables["campaigns"][0]["status"] == "running"


async def test_allow_running_with_nothing_selected_never_resets_failed_contacts():
    # e.g. the added contact is flagged do-not-call, so nothing is selected
    db, queue = _running_db(failed=3, new=0), _Queue([True])
    service = CampaignService(db, queue_service=queue)

    result = await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert result.jobs_enqueued == 0 and queue.enqueued == [] and db.tables["dialer_jobs"] == []
    assert {r["status"] for r in db.tables["leads"]} == {"failed"}
    campaign = db.tables["campaigns"][0]
    assert campaign["status"] == "running"
    assert campaign["started_at"] == "2026-11-20T09:00:00"  # a no-op must not re-stamp it


async def test_a_plain_restart_of_a_stopped_campaign_still_reopens_failed_contacts():
    db, queue = _running_db(failed=2, new=0), _Queue([True, True])
    db.tables["campaigns"][0]["status"] = "stopped"
    service = CampaignService(db, queue_service=queue)

    result = await service.start_campaign("campaign-1", tenant_id="tenant-1")

    assert result.jobs_enqueued == 2
    assert {r["status"] for r in db.tables["leads"]} == {"pending"}


async def test_a_second_contact_added_at_once_is_not_double_queued():
    db, queue = _running_db(new=2), _Queue([True, True, True])
    service = CampaignService(db, queue_service=queue)

    await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)
    await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert sorted(r["lead_id"] for r in db.tables["dialer_jobs"]) == ["new-0", "new-1"]
    assert len(queue.enqueued) == 2


async def test_a_pause_landing_after_the_read_is_not_turned_back_into_running():
    db, queue = _running_db(new=1), _Queue([True])
    service = CampaignService(db, queue_service=queue)
    _change_after_reading(service, _set_campaign(db, status="paused"))

    with pytest.raises(CampaignStateError):
        await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert db.tables["campaigns"][0]["status"] == "paused"
    assert queue.enqueued == []


async def test_a_stop_landing_after_the_read_is_not_turned_back_into_running():
    db, queue = _running_db(new=1), _Queue([True])
    service = CampaignService(db, queue_service=queue)
    _change_after_reading(service, _set_campaign(db, status="stopped"))

    with pytest.raises(CampaignStateError):
        await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert db.tables["campaigns"][0]["status"] == "stopped"
    assert queue.enqueued == []


async def test_a_contact_queued_mid_run_does_not_reset_the_campaign_pacing_gap():
    db, queue = _running_db(new=1), _Queue([True])
    service = CampaignService(db, queue_service=queue)
    service._reset_gap_clock = AsyncMock()

    await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    service._reset_gap_clock.assert_not_awaited()


async def test_a_plain_start_still_resets_the_pacing_gap():
    db, queue = _running_db(new=1), _Queue([True])
    db.tables["campaigns"][0]["status"] = "draft"
    service = CampaignService(db, queue_service=queue)
    service._reset_gap_clock = AsyncMock()

    await service.start_campaign("campaign-1", tenant_id="tenant-1")

    service._reset_gap_clock.assert_awaited_once()


# --- G-F8: the endpoints ----------------------------------------------------
def _spy_service(monkeypatch, start=None):
    service = SimpleNamespace(start_campaign=start or AsyncMock())
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: service)
    return service


def _add(db, phone="+14155550199", **kw):
    return asyncio.run(campaigns_ep.add_contact_to_campaign(
        campaign_id="c1", contact=ContactCreate(phone_number=phone, first_name="Ann", permission=PERMISSION, **kw),
        current_user=_User(), supabase=db))


def _contact_db(status):
    db = _ContactDB()
    db.tables["campaigns"][0]["status"] = status
    return db


def test_contact_added_to_a_running_campaign_is_queued(monkeypatch):
    service = _spy_service(monkeypatch)
    db = _contact_db("running")

    out = _add(db)

    assert out["message"] == "Contact added successfully"
    assert [r["phone_number"] for r in db.tables["leads"]] == ["+14155550199"]
    service.start_campaign.assert_awaited_once_with(
        campaign_id="c1", tenant_id="t1", allow_running=True, only_lead_ids=[db.tables["leads"][0]["id"]])


def test_a_revived_contact_on_a_running_campaign_is_queued(monkeypatch):
    service = _spy_service(monkeypatch)
    db = _contact_db("running")
    db.tables["leads"].append({"id": "l1", "tenant_id": "t1", "campaign_id": "c1",
                               "phone_number": "+14155550199", "status": "deleted", "is_lead": False})

    _add(db)

    assert db.tables["leads"][0]["status"] == "pending"
    service.start_campaign.assert_awaited_once()


@pytest.mark.parametrize("status", ["draft", "paused", "stopped", "completed", "scheduled"])
def test_contact_added_to_a_campaign_that_is_not_running_is_not_queued(monkeypatch, status):
    service = _spy_service(monkeypatch)

    _add(_contact_db(status))

    service.start_campaign.assert_not_awaited()


def test_contact_add_succeeds_when_queueing_fails(monkeypatch, caplog):
    service = _spy_service(monkeypatch, AsyncMock(side_effect=RuntimeError("redis down")))
    db = _contact_db("running")

    with caplog.at_level("WARNING"):
        out = _add(db)

    assert out["message"] == "Contact added successfully" and len(db.tables["leads"]) == 1
    assert service.start_campaign.await_count == 1
    assert any("contact_queue_failed" in r.getMessage() for r in caplog.records)


def test_a_lost_job_insert_race_is_retried_once_then_given_up(monkeypatch, caplog):
    service = _spy_service(
        monkeypatch, AsyncMock(side_effect=CampaignDispatchError("Dialer jobs could not be stored")))

    with caplog.at_level("WARNING"):
        out = _add(_contact_db("running"))

    assert out["message"] == "Contact added successfully"
    assert service.start_campaign.await_count == 2  # one retry, never a loop
    assert sum("contact_queue_failed" in r.getMessage() for r in caplog.records) == 1


def test_a_lost_job_insert_race_that_clears_on_the_retry_queues_the_contact(monkeypatch):
    service = _spy_service(monkeypatch, AsyncMock(side_effect=[CampaignDispatchError("x"), None]))

    _add(_contact_db("running"))

    assert service.start_campaign.await_count == 2


def _edit(db, **kw):
    return asyncio.run(campaigns_ep.update_contact_in_campaign(
        campaign_id="c1", contact_id="l1", contact=ContactUpdate(**kw),
        current_user=_User(), supabase=db))


def test_phone_change_on_a_running_campaign_requeues_with_the_new_number(monkeypatch):
    service = _spy_service(monkeypatch)
    db = _JobsDB()
    db.tables["campaigns"][0]["status"] = "running"
    db.tables["leads"][0]["status"] = "called"          # a retry was due
    db.tables["dialer_jobs"][:] = [
        {"id": "j2", "tenant_id": "t1", "lead_id": "l1", "status": "retry_scheduled"}]

    _edit(db, phone_number="+14155550999")

    assert _status(db) == {"j2": "cancelled"}
    assert db.tables["leads"][0]["status"] == "pending"
    assert db.tables["leads"][0]["phone_number"] == "+14155550999"
    service.start_campaign.assert_awaited_once_with(
        campaign_id="c1", tenant_id="t1", allow_running=True, only_lead_ids=["l1"])


def test_phone_change_during_a_live_call_adds_no_second_job_and_keeps_the_lead(monkeypatch):
    service = _spy_service(monkeypatch)
    db = _JobsDB()
    db.tables["campaigns"][0]["status"] = "running"
    db.tables["leads"][0]["status"] = "calling"
    db.tables["dialer_jobs"][:] = [
        {"id": "j3", "tenant_id": "t1", "lead_id": "l1", "status": "calling"}]

    _edit(db, phone_number="+14155550999")

    assert _status(db) == {"j3": "calling"}              # the live call keeps its job
    assert db.tables["leads"][0]["status"] == "calling"  # nothing moved it back
    # the start path then skips a lead that already has an active job (dedupe test below)
    service.start_campaign.assert_awaited_once()


async def test_a_lead_with_a_live_calling_job_is_skipped_by_the_start_path():
    db, queue = _running_db(new=1), _Queue([True])
    db.tables["dialer_jobs"].append({"id": "j3", "tenant_id": "tenant-1", "campaign_id": "campaign-1",
                                     "lead_id": "new-0", "status": "calling"})
    service = CampaignService(db, queue_service=queue)

    with pytest.raises(CampaignStateError):  # nothing to add: plain "already running"
        await service.start_campaign("campaign-1", tenant_id="tenant-1")
    result = await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert result.jobs_enqueued == 0 and queue.enqueued == []
    assert len(db.tables["dialer_jobs"]) == 1


async def test_a_corrected_number_is_dialled_through_the_start_path():
    db, queue = _running_db(new=1), _Queue([True])
    db.tables["leads"][0]["phone_number"] = "+14155550999"   # the corrected number
    service = CampaignService(db, queue_service=queue)

    await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert [j.phone_number for j in queue.enqueued] == ["+14155550999"]


def test_phone_change_on_a_campaign_that_is_not_running_queues_nothing(monkeypatch):
    service = _spy_service(monkeypatch)
    db = _JobsDB()                                       # campaign status 'draft'

    _edit(db, phone_number="+14155550999")

    service.start_campaign.assert_not_awaited()


def test_a_non_phone_edit_on_a_running_campaign_queues_nothing(monkeypatch):
    service = _spy_service(monkeypatch)
    db = _JobsDB()
    db.tables["campaigns"][0]["status"] = "running"

    _edit(db, first_name="Ann")

    service.start_campaign.assert_not_awaited()
    assert db.tables["leads"][0]["status"] == "pending"


# --- G-F7b: the completion statement ----------------------------------------
def test_completion_sql_ignores_test_agent_calls():
    sql = " ".join(svc._COMPLETE_IF_FINISHED_SQL.split())
    assert ("FROM calls WHERE campaign_id = $1::uuid AND tenant_id = $2::uuid "
            "AND status = ANY($4::text[]) AND is_test IS NOT TRUE)") in sql


class _CompletionConn:
    """Evaluates the calls segment of the completion statement, honouring is_test only
    when the statement text says so (dropping the predicate fails the test)."""

    def __init__(self, calls):
        self.calls = calls

    async def fetchval(self, query, *args):
        sql = " ".join(query.split())
        segment = sql.split("NOT EXISTS")[2]
        assert "FROM calls" in segment
        for call in self.calls:
            if call["tenant_id"] != args[1] or call["status"] not in args[3]:
                continue
            if "is_test IS NOT TRUE" in segment and call.get("is_test") is True:
                continue
            return None
        return args[0]


async def test_a_crashed_test_agent_session_does_not_keep_a_campaign_running():
    conn = _CompletionConn([{"tenant_id": "t1", "status": "in_progress", "is_test": True}])
    assert await svc.complete_campaign_if_finished(conn, "c1", "t1") is True


async def test_a_real_live_call_still_keeps_it_running():
    conn = _CompletionConn([{"tenant_id": "t1", "status": "in_call", "is_test": False},
                            {"tenant_id": "t1", "status": "in_progress", "is_test": True}])
    assert await svc.complete_campaign_if_finished(conn, "c1", "t1") is False


# --- G-F12a: the four statements a customer's numbers come from -------------
def test_the_four_customer_facing_call_counts_exclude_test_agent_calls():
    stats = " ".join(campaigns_ep._STATS_CALLS_SQL.split())
    summary = " ".join(campaigns_ep._STATS_STRING_SUMMARY_SQL.split())
    live_list = " ".join(svc._LIST_LIVE_CALLS_SQL.split())
    for sql in (stats, summary, live_list):
        assert "is_test IS NOT TRUE" in sql
        assert "tenant_id = $" in sql and "FROM calls" in sql


async def test_the_detail_live_call_count_excludes_test_agent_calls(monkeypatch):
    import inspect
    source = inspect.getsource(campaigns_ep)
    needle = ('"SELECT count(*) FROM calls WHERE tenant_id = $1::uuid AND campaign_id = $2::uuid"\n'
              '                " AND status = ANY($3::text[]) AND is_test IS NOT TRUE"')
    assert needle in source


# =============================================================================
# Scope (coordinator decision 2026-11-21): the mid-run queueing names exactly one
# contact. It never queues "every pending lead without a job".
# =============================================================================
def _lead(lead_id, status="pending", *, list_id=None, tenant="tenant-1", campaign="campaign-1", phone=None):
    return {"id": lead_id, "tenant_id": tenant, "campaign_id": campaign, "priority": 5,
            "status": status, "do_not_call": False, "list_id": list_id,
            "phone_number": phone or "+14155559%03d" % (sum(map(ord, lead_id)) % 1000),
            "created_at": "2026-09-03T00:00:00+00:00"}


def _after_call_this_list():
    """A campaign made running by "call this list" for list A: A's contacts have jobs,
    list B's pending contacts have none."""
    db = _DB(lead_count=0)
    db.tables["campaigns"][0].update(status="running", started_at="2026-11-20T09:00:00",
                                     total_leads=4, calls_completed=0, calls_failed=0)
    db.tables["leads"] += [_lead("a1", list_id="list-a", phone="+14155550101"),
                           _lead("a2", list_id="list-a", phone="+14155550102"),
                           _lead("b1", list_id="list-b", phone="+14155550201"),
                           _lead("b2", list_id="list-b", phone="+14155550202")]
    for i, lead in enumerate(("a1", "a2")):
        db.tables["dialer_jobs"].append({"id": f"ja{i}", "tenant_id": "tenant-1", "campaign_id": "campaign-1",
                                         "lead_id": lead, "status": "queued"})
    return db


@pytest.mark.parametrize("list_id", ["list-a", "list-b", None])
async def test_one_added_contact_queues_only_itself_never_another_lists_pending_contacts(list_id):
    db, queue = _after_call_this_list(), _Queue([True, True, True])
    db.tables["leads"].append(_lead("new", list_id=list_id, phone="+14155550301"))
    before = {r["id"]: dict(r) for r in db.tables["leads"] if r["id"] != "new"}
    service = CampaignService(db, queue_service=queue)

    result = await service.start_campaign(
        "campaign-1", tenant_id="tenant-1", allow_running=True, only_lead_ids=["new"])

    assert result.jobs_enqueued == 1
    assert [j.lead_id for j in queue.enqueued] == ["new"]
    new_jobs = [r for r in db.tables["dialer_jobs"] if r["lead_id"] not in ("a1", "a2")]
    assert [r["lead_id"] for r in new_jobs] == ["new"]
    assert len(db.tables["dialer_jobs"]) == 3
    assert {r["id"]: r for r in db.tables["leads"] if r["id"] != "new"} == before  # no status change


async def test_a_phone_change_requeues_only_that_contact():
    db, queue = _after_call_this_list(), _Queue([True])
    db.tables["leads"][2]["phone_number"] = "+14155550999"     # b1 renumbered, its job cancelled
    service = CampaignService(db, queue_service=queue)

    await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True,
                                 only_lead_ids=["b1"])

    assert [(j.lead_id, j.phone_number) for j in queue.enqueued] == [("b1", "+14155550999")]
    assert not [r for r in db.tables["dialer_jobs"] if r["lead_id"] == "b2"]


@pytest.mark.parametrize("foreign", [
    _lead("x1", campaign="campaign-other"),
    _lead("x2", tenant="tenant-other"),
    _lead("x3", tenant="tenant-other", campaign="campaign-other"),
])
async def test_naming_another_campaigns_or_tenants_contact_queues_nothing(foreign):
    db, queue = _running_db(failed=2, new=0), _Queue([True])
    db.tables["leads"].append(dict(foreign))
    before = [dict(r) for r in db.tables["leads"]]
    service = CampaignService(db, queue_service=queue)

    result = await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True,
                                          only_lead_ids=[foreign["id"]])

    assert result.jobs_enqueued == 0 and queue.enqueued == [] and db.tables["dialer_jobs"] == []
    assert db.tables["leads"] == before


async def test_a_scoped_start_without_allow_running_still_never_reopens_failed_contacts():
    db, queue = _running_db(failed=2, new=0), _Queue([True])
    service = CampaignService(db, queue_service=queue)

    with pytest.raises(CampaignStateError):
        await service.start_campaign("campaign-1", tenant_id="tenant-1", only_lead_ids=["nobody"])

    assert {r["status"] for r in db.tables["leads"]} == {"failed"}


async def test_only_lead_ids_none_builds_the_same_selection_as_before(monkeypatch):
    """Start, "call this list" and resume select leads with exactly the filters they used
    before this change (literal expectation, also run against the unchanged code)."""
    from tests.unit.test_campaign_start_dispatch_truth import _Query
    recorded = []
    original = _Query.execute

    def spy(self):
        if self.table == "leads" and self.operation == "select":
            recorded.append(list(self.filters))
        return original(self)

    monkeypatch.setattr(_Query, "execute", spy)
    main = [("eq", "campaign_id", "campaign-1"), ("in", "status", ("pending", "calling")),
            ("is", "do_not_call", "NOT TRUE"), ("eq", "tenant_id", "tenant-1")]
    suppressed = [("eq", "campaign_id", "campaign-1"), ("in", "status", ("pending", "calling")),
                  ("is", "do_not_call", "TRUE"), ("eq", "tenant_id", "tenant-1")]
    for before_status, kwargs, extra in (
        ("draft", {}, []),                                        # Start
        ("stopped", {"list_id": "list-1", "allow_running": True}, [("eq", "list_id", "list-1")]),  # call this list
        ("paused", {}, []),                                       # resume
    ):
        recorded.clear()
        db, queue = _DB(lead_count=1), _Queue([True])
        db.tables["campaigns"][0]["status"] = before_status
        db.tables["leads"][0]["list_id"] = "list-1"
        await CampaignService(db, queue_service=queue).start_campaign(
            "campaign-1", tenant_id="tenant-1", **kwargs)
        assert recorded[0] == main + extra
        assert recorded[1] == suppressed + extra


# --- counters ----------------------------------------------------------------
def _ten_contact_campaign():
    """10 contacts: 3 completed, 2 failed, 2 waiting for a retry, 3 pending (with jobs)."""
    db = _DB(lead_count=0)
    db.tables["campaigns"][0].update(status="running", started_at="2026-11-20T09:00:00",
                                     total_leads=10, calls_completed=3, calls_failed=2)
    for status, n in (("completed", 3), ("failed", 2), ("called", 2), ("pending", 3)):
        for i in range(n):
            db.tables["leads"].append(_lead(f"{status}-{i}", status))
    for lead in db.tables["leads"]:
        if lead["status"] in ("called", "pending"):
            db.tables["dialer_jobs"].append({"id": f"j-{lead['id']}", "tenant_id": "tenant-1",
                                             "campaign_id": "campaign-1", "lead_id": lead["id"],
                                             "status": "retry_scheduled" if lead["status"] == "called" else "queued"})
    return db


async def test_a_scoped_start_leaves_every_counter_and_the_start_time_alone():
    db, queue = _ten_contact_campaign(), _Queue([True])
    db.tables["leads"].append(_lead("new", phone="+14155550301"))
    service = CampaignService(db, queue_service=queue)

    await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True, only_lead_ids=["new"])

    campaign = db.tables["campaigns"][0]
    assert (campaign["total_leads"], campaign["calls_completed"], campaign["calls_failed"]) == (10, 3, 2)
    assert campaign["started_at"] == "2026-11-20T09:00:00" and campaign["status"] == "running"
    assert [j.lead_id for j in queue.enqueued] == ["new"]


class _CountConn:
    def __init__(self, db, log):
        self.db, self.log = db, log

    async def execute(self, sql, *args):
        sql = " ".join(sql.split())
        self.log.append((sql, args))
        row = self.db.tables["campaigns"][0]
        if row["id"] == args[0] and row["tenant_id"] == args[1] and row["status"] == "running":
            row["total_leads"] = (row.get("total_leads") or 0) + 1


def _count_pool(monkeypatch, db):
    import contextlib
    log = []

    @contextlib.asynccontextmanager
    async def acquire(_pool, _tenant):
        yield _CountConn(db, log)

    monkeypatch.setattr(campaigns_ep, "acquire_with_tenant", acquire)
    db.pool = object()
    return log


def _contact_db_with_counters(status="running"):
    db = _contact_db(status)
    db.tables["campaigns"][0].update(total_leads=10, calls_completed=3, calls_failed=2)
    return db


def test_a_new_contact_on_a_running_campaign_counts_once_in_the_total(monkeypatch):
    _spy_service(monkeypatch)
    db = _contact_db_with_counters()
    log = _count_pool(monkeypatch, db)

    _add(db)

    campaign = db.tables["campaigns"][0]
    assert (campaign["total_leads"], campaign["calls_completed"], campaign["calls_failed"]) == (11, 3, 2)
    assert len(log) == 1 and "tenant_id = $2::uuid" in log[0][0] and "status = 'running'" in log[0][0]
    assert log[0][1] == ("c1", "t1")


def test_a_revived_contact_is_not_counted_a_second_time(monkeypatch):
    _spy_service(monkeypatch)
    db = _contact_db_with_counters()
    db.tables["leads"].append({"id": "l1", "tenant_id": "t1", "campaign_id": "c1",
                               "phone_number": "+14155550199", "status": "deleted", "is_lead": False})
    log = _count_pool(monkeypatch, db)

    _add(db)

    assert db.tables["campaigns"][0]["total_leads"] == 10 and log == []


@pytest.mark.parametrize("status", ["draft", "paused", "stopped"])
def test_a_contact_added_while_not_running_leaves_the_total_to_the_next_start(monkeypatch, status):
    _spy_service(monkeypatch)
    db = _contact_db_with_counters(status)
    log = _count_pool(monkeypatch, db)

    _add(db)

    assert db.tables["campaigns"][0]["total_leads"] == 10 and log == []


def test_a_phone_change_does_not_touch_the_total(monkeypatch):
    _spy_service(monkeypatch)
    db = _JobsDB()
    db.tables["campaigns"][0].update(status="running", total_leads=10)
    log = _count_pool(monkeypatch, db)

    _edit(db, phone_number="+14155550999")

    assert db.tables["campaigns"][0]["total_leads"] == 10 and log == []


def test_a_failed_total_write_never_fails_the_add(monkeypatch):
    _spy_service(monkeypatch)
    db = _contact_db_with_counters()          # no pool on this fake: the write raises

    out = _add(db)

    assert out["message"] == "Contact added successfully"


async def test_a_scoped_start_does_not_restamp_started_at_when_it_creates_a_job():
    db, queue = _running_db(new=1), _Queue([True])
    service = CampaignService(db, queue_service=queue)

    await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True,
                                 only_lead_ids=["new-0"])

    assert db.tables["campaigns"][0]["started_at"] == "2026-11-20T09:00:00"
    assert len(db.tables["dialer_jobs"]) == 1


# --- the service itself refuses a scoped start on a campaign that is not running ----
@pytest.mark.parametrize("stored", ["stopped", "completed", "draft", "paused", "deleted"])
async def test_a_scoped_start_on_a_campaign_that_is_not_running_writes_and_queues_nothing(stored):
    db, queue = _running_db(new=1), _Queue([True])
    db.tables["campaigns"][0]["status"] = stored
    campaign_before = dict(db.tables["campaigns"][0])
    leads_before = [dict(r) for r in db.tables["leads"]]
    service = CampaignService(db, queue_service=queue)

    with pytest.raises((CampaignStateError, CampaignError)):
        await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True,
                                     only_lead_ids=["new-0"])

    assert db.tables["campaigns"][0] == campaign_before          # status and every column unchanged
    assert db.tables["dialer_jobs"] == [] and queue.enqueued == []
    assert db.tables["leads"] == leads_before                    # the contact is untouched


async def test_a_scoped_start_on_a_running_campaign_still_queues_the_one_contact():
    db, queue = _running_db(failed=2, new=1), _Queue([True])
    service = CampaignService(db, queue_service=queue)

    result = await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True,
                                          only_lead_ids=["new-0"])

    assert result.jobs_enqueued == 1 and [j.lead_id for j in queue.enqueued] == ["new-0"]
    assert db.tables["campaigns"][0]["status"] == "running"


async def test_a_stop_between_the_services_read_and_its_write_leaves_it_stopped_and_pushes_nothing():
    db, queue = _running_db(new=1), _Queue([True])
    service = CampaignService(db, queue_service=queue)
    _change_after_reading(service, _set_campaign(db, status="stopped"))

    with pytest.raises(CampaignStateError):
        await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True,
                                     only_lead_ids=["new-0"])

    assert db.tables["campaigns"][0]["status"] == "stopped"
    assert queue.enqueued == []                                   # no Redis payload
    # the one job row inserted before the lost write stays 'pending' and inert
    assert [r["status"] for r in db.tables["dialer_jobs"]] == ["pending"]


async def test_the_scoped_status_write_is_always_from_running(monkeypatch):
    db, queue = _running_db(new=1), _Queue([True])
    service = CampaignService(db, queue_service=queue)
    seen = []
    original = service._update_campaign_status

    async def spy(campaign_id, status, **kw):
        seen.append(kw.get("from_statuses"))
        return await original(campaign_id, status, **kw)

    service._update_campaign_status = spy
    await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True,
                                 only_lead_ids=["new-0"])

    assert seen == [("running",)]

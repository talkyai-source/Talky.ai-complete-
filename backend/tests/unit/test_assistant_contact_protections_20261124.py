"""The assistant's manage_lead gets the protections the website's contact routes have:
cancel queued calls on a phone change, refuse a duplicate number, revive a removed row
(keeping its do-not-call flag), hand the contact back to the queue, queue it on a running
campaign, and treat a failed read as a failure.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.domain.services.dialer.job_lifecycle import cancel_not_originated_jobs_for_lead
from app.infrastructure.assistant.tools.campaign_admin import manage_lead
from app.infrastructure.assistant.tools.lead_write_safety import N1, S3, S4, S5, S6, S7, W1
from tests.unit import test_bh_campaigns_contacts_contact_writes as _w
from tests.unit.test_failed_cancel_is_not_nothing_queued_20261124 import (
    MARKER,
    _ErrDB,
    _JobsErrDB,
    _LeadsSelectErrDB,
)
from tests.unit.test_live_risk_contacts import _JobsDB, _JobsQ

OUTER = "The contact change could not be confirmed. Preview the change again."
OLD = "+14155550124"
NEW = "+14155550999"


@pytest.fixture(autouse=True)
def _permission_off(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "off")


def _run(db, **kw):
    return asyncio.run(manage_lead(tenant_id="t1", db_client=db, campaign_id="c1", **kw))


def _jobs(db):
    return {j["id"]: j["status"] for j in db.tables["dialer_jobs"]}


def _spy(monkeypatch, *, live_call=False):
    service = SimpleNamespace(start_campaign=AsyncMock())
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: service)
    monkeypatch.setattr(campaigns_ep, "_contact_has_live_call", AsyncMock(return_value=live_call))
    monkeypatch.setattr(campaigns_ep, "_count_new_contact_if_running", AsyncMock())
    return service


def _deleted(lid="l1", *, dnc=False, is_lead=False, phone=OLD):
    return {"id": lid, "tenant_id": "t1", "campaign_id": "c1", "phone_number": phone,
            "status": "deleted", "is_lead": is_lead, "do_not_call": dnc}


# --- T1 ----------------------------------------------------------------------------
def test_t1_a_phone_edit_cancels_the_contacts_queued_calls():
    db = _JobsDB()
    out = _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    assert out["applied"] is True
    assert db.tables["leads"][0]["phone_number"] == NEW
    assert _jobs(db) == {"j1": "cancelled", "j2": "cancelled", "j3": "calling",
                         "j4": "queued", "j5": "queued"}
    j1 = [j for j in db.tables["dialer_jobs"] if j["id"] == "j1"][0]
    assert j1["last_error"] == "contact_phone_changed"


def test_t1_a_name_edit_leaves_the_calls_alone():
    db = _JobsDB()
    _run(db, action="update", lead_id="l1", first_name="Ann", confirm=True)
    assert _jobs(db)["j1"] == "queued"


# --- T2 ----------------------------------------------------------------------------
def test_t2_a_failed_cancel_saves_nothing_and_shows_no_database_text():
    db = _JobsErrDB()
    out = _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    assert out == {"applied": False, "error": S7}
    assert db.tables["leads"][0]["phone_number"] == OLD
    assert MARKER not in repr(out)


# --- T3 ----------------------------------------------------------------------------
@pytest.mark.parametrize("confirm", [False, True])
def test_t3_another_live_contact_with_the_new_number_refuses_the_edit(confirm):
    db = _JobsDB()
    db.tables["leads"].append({"id": "l2", "tenant_id": "t1", "campaign_id": "c1",
                               "phone_number": NEW, "status": "pending"})
    before = _jobs(db)
    out = _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=confirm)
    assert out == {"error": S5}
    assert _jobs(db) == before
    assert db.tables["leads"][0]["phone_number"] == OLD


def test_t3_a_removed_row_with_the_new_number_does_not_block_the_edit():
    db = _JobsDB()
    db.tables["leads"].append(_deleted("l2", phone=NEW))
    out = _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    assert out["applied"] is True


# --- T4 ----------------------------------------------------------------------------
def test_t4_a_called_contact_goes_back_to_pending_after_its_calls_are_cancelled():
    db = _JobsDB()
    db.tables["leads"][0]["status"] = "called"
    _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    assert db.tables["leads"][0]["status"] == "pending"


def test_t4_a_contact_skipped_for_an_invalid_number_goes_back_to_pending():
    db = _JobsDB()
    db.tables["dialer_jobs"].clear()
    db.tables["leads"][0].update(status="skipped", last_call_result="invalid_phone_number")
    _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    assert db.tables["leads"][0]["status"] == "pending"


def test_t4_a_contact_skipped_for_another_reason_stays_skipped():
    db = _JobsDB()
    db.tables["dialer_jobs"].clear()
    db.tables["leads"][0].update(status="skipped", last_call_result="no_answer")
    _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    assert db.tables["leads"][0]["status"] == "skipped"


class _FailingResetQ(_JobsQ):
    def execute(self):
        if self._table == "leads" and self._op == "update" and self._payload == {"status": "pending"}:
            raise RuntimeError("reset failed")
        return super().execute()


class _FailingResetDB(_JobsDB):
    def table(self, name):
        return _FailingResetQ(self, name)


def test_t4_a_failed_status_reset_never_fails_the_edit(caplog):
    db = _FailingResetDB()
    db.tables["leads"][0]["status"] = "called"
    with caplog.at_level("WARNING"):
        out = _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    assert out["applied"] is True
    assert db.tables["leads"][0]["phone_number"] == NEW
    assert "manage_lead_requeue_status_not_reset" in caplog.text


# --- T5 ----------------------------------------------------------------------------
def test_t5_a_renumbered_contact_on_a_running_campaign_is_queued(monkeypatch):
    service = _spy(monkeypatch)
    db = _JobsDB()
    db.tables["campaigns"][0]["status"] = "running"
    _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    service.start_campaign.assert_awaited_once_with(
        campaign_id="c1", tenant_id="t1", allow_running=True, only_lead_ids=["l1"])


def test_t5_a_contact_with_a_live_call_is_not_queued(monkeypatch):
    service = _spy(monkeypatch, live_call=True)
    db = _JobsDB()
    db.tables["campaigns"][0]["status"] = "running"
    out = _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    assert out["applied"] is True
    service.start_campaign.assert_not_awaited()


def test_t5_a_draft_campaign_queues_nothing(monkeypatch):
    service = _spy(monkeypatch)
    db = _JobsDB()
    _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    service.start_campaign.assert_not_awaited()


# --- T6 ----------------------------------------------------------------------------
def test_t6_adding_over_a_removed_do_not_call_row_revives_it_marked_do_not_call():
    db = _w._DB(leads=[_deleted("l1", dnc=True)])
    snapshot = [dict(r) for r in db.tables["leads"]]
    preview = _run(db, action="add", phone_number=OLD, name="Ann", confirm=False)
    assert preview["preview"] is True
    assert preview["warnings"] == [W1]
    assert preview["changes"][0]["before"]["id"] == "l1"
    assert preview["changes"][0]["after"]["do_not_call"] is True
    assert db.tables["leads"] == snapshot

    out = _run(db, action="add", phone_number=OLD, name="Ann", confirm=True)
    assert out["applied"] is True
    assert len(db.tables["leads"]) == 1
    row = db.tables["leads"][0]
    assert row["status"] == "pending" and row["do_not_call"] is True
    assert row["first_name"] == "Ann" and "last_name" not in row


def test_t6_a_removed_row_without_the_flag_has_no_warning():
    db = _w._DB(leads=[_deleted("l1", dnc=False)])
    preview = _run(db, action="add", phone_number=OLD, confirm=False)
    assert "warnings" not in preview
    assert preview["changes"][0]["after"]["do_not_call"] is False


def test_t6_two_removed_rows_revive_the_lead_row_and_keep_the_flag_of_either():
    db = _w._DB(leads=[_deleted("l1", is_lead=True, dnc=False), _deleted("l2", dnc=True)])
    out = _run(db, action="add", phone_number=OLD, confirm=True)
    assert out["applied"] is True
    revived = [r for r in db.tables["leads"] if r["status"] == "pending"]
    assert len(revived) == 1 and revived[0]["id"] == "l1" and revived[0]["do_not_call"] is True


# --- T7 ----------------------------------------------------------------------------
@pytest.mark.parametrize("confirm", [False, True])
def test_t7_a_failed_lookup_adds_nothing(confirm):
    db = _LeadsSelectErrDB(leads=[_deleted("l1", dnc=True)])
    out = _run(db, action="add", phone_number=OLD, confirm=confirm)
    assert out == {"error": S3}
    assert MARKER not in repr(out)
    assert len(db.tables["leads"]) == 1 and db.tables["leads"][0]["status"] == "deleted"


def test_t7_a_failed_lookup_on_a_phone_edit_changes_nothing():
    db = _JobsDB()
    seen = {"n": 0}

    class _Q(_JobsQ):
        def execute(self):
            if self._table == "leads" and self._op == "select":
                seen["n"] += 1
                if seen["n"] == 2:      # 1: the contact itself, 2: the number check
                    return SimpleNamespace(data=None, count=0, error=MARKER)
            return super().execute()

    db.table = lambda name: _Q(db, name)
    before = _jobs(db)
    out = _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    assert out == {"error": S6}
    assert _jobs(db) == before and db.tables["leads"][0]["phone_number"] == OLD


# --- T8 ----------------------------------------------------------------------------
def test_t8_adding_a_number_that_is_already_a_live_contact_is_refused():
    db = _w._DB(leads=[{"id": "l1", "tenant_id": "t1", "campaign_id": "c1",
                        "phone_number": OLD, "status": "pending"}])
    out = _run(db, action="add", phone_number=OLD, confirm=True)
    assert out == {"error": S4}
    assert len(db.tables["leads"]) == 1


def test_t8_a_new_contact_on_a_running_campaign_is_queued_and_counted(monkeypatch):
    service = _spy(monkeypatch)
    db = _w._DB()
    db.tables["campaigns"][0]["status"] = "running"
    out = _run(db, action="add", phone_number=NEW, confirm=True)
    assert out["applied"] is True
    new_id = db.tables["leads"][0]["id"]
    service.start_campaign.assert_awaited_once_with(
        campaign_id="c1", tenant_id="t1", allow_running=True, only_lead_ids=[new_id])
    campaigns_ep._count_new_contact_if_running.assert_awaited_once()


def test_t8_a_queue_failure_never_fails_the_add(monkeypatch):
    service = _spy(monkeypatch)
    service.start_campaign.side_effect = RuntimeError("boom")
    db = _w._DB()
    db.tables["campaigns"][0]["status"] = "running"
    out = _run(db, action="add", phone_number=NEW, confirm=True)
    assert out["applied"] is True and len(db.tables["leads"]) == 1


# --- T9 ----------------------------------------------------------------------------
def test_t9_removing_a_contact_cancels_its_active_calls():
    db = _JobsDB()
    out = _run(db, action="remove", lead_id="l1", confirm=True)
    assert out["applied"] is True and "notice" not in out
    assert _jobs(db) == {"j1": "cancelled", "j2": "cancelled", "j3": "cancelled",
                         "j4": "queued", "j5": "queued"}
    assert db.tables["leads"][0]["status"] == "deleted"


def test_t9_a_failed_cancel_on_remove_still_removes_and_says_so():
    db = _JobsErrDB()
    out = _run(db, action="remove", lead_id="l1", confirm=True)
    assert out["applied"] is True and out["notice"] == N1
    assert MARKER not in repr(out)
    assert db.tables["leads"][0]["status"] == "deleted"


def test_t9_a_remove_preview_cancels_nothing():
    db = _JobsDB()
    before = _jobs(db)
    out = _run(db, action="remove", lead_id="l1", confirm=False)
    assert out["preview"] is True and _jobs(db) == before


# --- T10 ---------------------------------------------------------------------------
def test_t10_a_failed_campaign_read_is_not_campaign_not_found():
    class _Q(_w._Q):
        def execute(self):
            if self._table == "campaigns":
                return SimpleNamespace(data=None, count=0, error=MARKER)
            return super().execute()

    db = _w._DB(leads=[_deleted("l1")])
    db.table = lambda name: _Q(db, name)
    out = _run(db, action="add", phone_number=OLD, confirm=True)
    assert out == {"error": OUTER}
    assert MARKER not in repr(out)
    assert db.tables["leads"][0]["status"] == "deleted"


@pytest.mark.parametrize("action, kw", [
    ("remove", {}),
    ("update", {"phone_number": NEW}),
])
def test_t10_a_failed_contact_read_is_not_lead_not_found(action, kw):
    class _Q(_JobsQ):
        def execute(self):
            if self._table == "leads" and self._op == "select":
                return SimpleNamespace(data=None, count=0, error=MARKER)
            return super().execute()

    db = _JobsDB()
    db.table = lambda name: _Q(db, name)
    before = _jobs(db)
    out = _run(db, action=action, lead_id="l1", confirm=True, **kw)
    assert out == {"error": OUTER}
    assert _jobs(db) == before and db.tables["leads"][0]["status"] == "pending"


# --- T11 ---------------------------------------------------------------------------
def test_t11_cancel_not_originated_raises_on_a_returned_error():
    with pytest.raises(RuntimeError):
        cancel_not_originated_jobs_for_lead(_ErrDB(), "l1", tenant_id="t1", last_error="x")


def test_t11_cancel_not_originated_returns_zero_without_rows():
    class _Q:
        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        def execute(self):
            return SimpleNamespace(data=[], error=None)

    class _D:
        def table(self, _n):
            return _Q()

    assert cancel_not_originated_jobs_for_lead(_D(), "l1", tenant_id="t1", last_error="x") == 0


@pytest.mark.parametrize("tenant", ["", None])
def test_t11_cancel_not_originated_needs_a_tenant(tenant):
    with pytest.raises(RuntimeError, match="tenant_id is required"):
        cancel_not_originated_jobs_for_lead(_ErrDB(), "l1", tenant_id=tenant, last_error="x")


def test_t11_cancel_not_originated_touches_only_not_originated_jobs_of_the_tenant():
    db = _JobsDB()
    n = cancel_not_originated_jobs_for_lead(db, "l1", tenant_id="t1", last_error="why")
    assert n == 2
    assert _jobs(db) == {"j1": "cancelled", "j2": "cancelled", "j3": "calling",
                         "j4": "queued", "j5": "queued"}
    j1 = [j for j in db.tables["dialer_jobs"] if j["id"] == "j1"][0]
    assert j1["failure_reason"] == "removed_from_campaign" and j1["last_error"] == "why"

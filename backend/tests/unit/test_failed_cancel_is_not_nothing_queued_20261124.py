"""A cancel the older database layer RETURNED as an error is not "nothing queued".

T3 (job_lifecycle), T4 (stop_campaign), T5 (remove contact), T6 (phone edit),
T7 (add contact). The fake error text is a marker that must never reach an answer.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.schemas.campaigns import ContactCreate, ContactUpdate
from app.core.db import DatabasePoolTimeoutError
from app.domain.services.dialer.job_lifecycle import (
    cancel_active_jobs_for_campaign,
    cancel_active_jobs_for_lead,
)
from tests.unit import test_bh_campaigns_contacts_contact_writes as _w
from tests.unit import test_contact_direction_boundaries as _b
from tests.unit.test_campaign_pause_stop_state import (  # noqa: F401  (fixture is used by name)
    CID,
    TENANT,
    _DB as _CampaignDB,
    _service,
    side_effects,
)
from tests.unit.test_live_risk_contacts import _JobsDB, _JobsQ

MARKER = "SECRET-DRIVER-TEXT"


# --- T3 ---------------------------------------------------------------------------
class _ErrQuery:
    def update(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def execute(self):
        return SimpleNamespace(data=None, error="x")


class _ErrDB:
    def table(self, _name):
        return _ErrQuery()


def test_t3_a_failed_cancel_raises_in_both_functions():
    with pytest.raises(RuntimeError):
        cancel_active_jobs_for_campaign(_ErrDB(), "camp-1", reason="campaign_stopped")
    with pytest.raises(RuntimeError):
        cancel_active_jobs_for_lead(_ErrDB(), "lead-1", tenant_id="t1")


def test_t3_zero_rows_without_error_is_still_zero():
    class _Q(_ErrQuery):
        def execute(self):
            return SimpleNamespace(data=[], error=None)

    class _D:
        def table(self, _n):
            return _Q()

    assert cancel_active_jobs_for_campaign(_D(), "c", reason="r") == 0
    assert cancel_active_jobs_for_lead(_D(), "l", tenant_id="t1") == 0


# --- T4 ---------------------------------------------------------------------------
@pytest.mark.parametrize("exc", [RuntimeError("boom"), DatabasePoolTimeoutError("slow")])
async def test_t4_a_failed_cancel_does_not_abort_the_stop(exc, side_effects, monkeypatch):
    def _raise(*_a, **_k):
        raise exc

    monkeypatch.setattr(
        "app.domain.services.dialer.job_lifecycle.cancel_active_jobs_for_campaign", _raise
    )
    db = _CampaignDB("running")
    service = _service(db)
    released = []
    service._release_leads_to_pending = lambda *a, **k: released.append(a)

    result = await service.stop_campaign(CID, tenant_id=TENANT)

    assert result["status"] == "stopped"
    side_effects.hangup.assert_awaited_once()
    assert released, "the release of retry leads must still run"


# --- T5 ---------------------------------------------------------------------------
class _JobsFailQuery(_b._Query):
    def execute(self):
        if self.table == "dialer_jobs" and self.operation == "update":
            self.db.writes.append((self.table, self.operation, self.payload))
            return _b._Response([], error=MARKER)
        return super().execute()


class _JobsFailDB(_b._DB):
    def table(self, name):
        return _JobsFailQuery(self, name)


async def test_t5_remove_contact_says_so_when_its_calls_could_not_be_cancelled():
    db = _JobsFailDB(direction="outbound")
    body = await campaigns_ep.remove_contact_from_campaign("c1", "lead1", _b.USER, db)
    assert body["cancelled_jobs"] is None
    assert body["warning"] == (
        "Queued calls for this contact could not be cancelled now. "
        "The contact is removed, so the dialler will not call it."
    )
    assert "SECRET" not in repr(body)
    assert db.tables["leads"][0]["status"] == "deleted"


async def test_t5_remove_contact_success_body_has_no_warning():
    db = _b._DB(direction="outbound")
    db.tables["dialer_jobs"].append(
        {"id": "j1", "tenant_id": "t1", "lead_id": "lead1", "status": "queued"}
    )
    body = await campaigns_ep.remove_contact_from_campaign("c1", "lead1", _b.USER, db)
    assert body == {"message": "Contact removed successfully", "cancelled_jobs": 1}


# --- T6 ---------------------------------------------------------------------------
class _JobsErrQ(_JobsQ):
    def execute(self):
        if self._table == "dialer_jobs" and self._op == "update":
            return SimpleNamespace(data=[], count=0, error=MARKER)
        return super().execute()


class _JobsErrDB(_JobsDB):
    def table(self, name):
        return _JobsErrQ(self, name)


def test_t6_a_phone_edit_fails_when_the_old_calls_could_not_be_cancelled():
    db = _JobsErrDB()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(campaigns_ep.update_contact_in_campaign(
            campaign_id="c1", contact_id="l1",
            contact=ContactUpdate(phone_number="+14155550999"),
            current_user=_w._User(), supabase=db))
    assert caught.value.status_code == 500
    assert caught.value.detail == "Failed to update contact"
    assert db.tables["leads"][0]["phone_number"] == "+14155550124"


# --- T7 ---------------------------------------------------------------------------
class _LeadsSelectErrQ(_w._Q):
    def execute(self):
        if self._table == "leads" and self._op == "select":
            return SimpleNamespace(data=None, count=0, error=MARKER)
        return super().execute()


class _LeadsSelectErrDB(_w._DB):
    def table(self, name):
        return _LeadsSelectErrQ(self, name)


def test_t7_add_contact_fails_when_the_existing_row_lookup_failed():
    db = _LeadsSelectErrDB(leads=[{
        "id": "l1", "tenant_id": "t1", "campaign_id": "c1",
        "phone_number": "+14155550124", "status": "deleted",
        "is_lead": False, "do_not_call": True,
    }])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(campaigns_ep.add_contact_to_campaign(
            campaign_id="c1",
            contact=ContactCreate(phone_number="+14155550124", permission=_w.PERMISSION),
            current_user=_w._User(), supabase=db))
    assert caught.value.status_code == 500
    assert caught.value.detail == "Failed to add contact"
    assert len(db.tables["leads"]) == 1
    assert db.tables["leads"][0]["status"] == "deleted"


# --- review of 24 November: the phone edit's two reads before its cancel -----------
def _phone_edit_db_whose_leads_read_fails(nth):
    seen = {"n": 0}

    class _Q(_JobsQ):
        def execute(self):
            if self._table == "leads" and self._op == "select":
                seen["n"] += 1
                if seen["n"] == nth:
                    return SimpleNamespace(data=None, count=0, error=MARKER)
            return super().execute()

    class _DBx(_JobsDB):
        def table(self, name):
            return _Q(self, name)

    return _DBx()


@pytest.mark.parametrize("nth, which", [(1, "the contact itself"), (2, "the duplicate check")])
def test_a_phone_edit_fails_when_a_read_before_the_cancel_failed(nth, which):
    # A failed read of the contact answered "not found"; a failed duplicate check let the
    # edit go on, cancel the queued calls and then fail on the save.
    db = _phone_edit_db_whose_leads_read_fails(nth)
    before = {j["id"]: j["status"] for j in db.tables["dialer_jobs"]}
    with pytest.raises(HTTPException) as caught:
        asyncio.run(campaigns_ep.update_contact_in_campaign(
            campaign_id="c1", contact_id="l1",
            contact=ContactUpdate(phone_number="+14155550999"),
            current_user=_w._User(), supabase=db))
    assert caught.value.status_code == 500, which
    assert caught.value.detail == "Failed to update contact"
    assert db.tables["leads"][0]["phone_number"] == "+14155550124"
    assert {j["id"]: j["status"] for j in db.tables["dialer_jobs"]} == before      # nothing cancelled

"""The website's contact edit and remove carry the rules the assistant's tool has.

G1 a removed number's do-not-call mark comes with the number, G2 a removed contact is
not found, G3 the status resets run after the save, G4 a failed contact read on remove
is a failure. The fake error text is a marker that must never reach an answer.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.endpoints._contact_phone_edit import DNC_MARK_TAKEN, release_called_contact
from app.api.v1.schemas.campaigns import ContactUpdate
from app.infrastructure.assistant.tools import lead_write_safety
from tests.unit import test_bh_campaigns_contacts_contact_writes as _w
from tests.unit import test_contact_direction_boundaries as _b
from tests.unit.test_live_risk_contacts import _JobsDB, _JobsQ

MARKER = "SECRET-DRIVER-TEXT"
OLD = "+14155550124"
NEW = "+14155550999"


def _spy(monkeypatch):
    service = SimpleNamespace(start_campaign=AsyncMock())
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: service)
    monkeypatch.setattr(campaigns_ep, "_contact_has_live_call", AsyncMock(return_value=False))
    return service


def _edit(db, **kw):
    return asyncio.run(campaigns_ep.update_contact_in_campaign(
        campaign_id="c1", contact_id="l1", contact=ContactUpdate(**kw),
        current_user=_w._User(), supabase=db))


def _running_db(keep_jobs=True, **lead):
    db = _JobsDB()
    db.tables["campaigns"][0]["status"] = "running"
    if not keep_jobs:
        db.tables["dialer_jobs"].clear()
    db.tables["leads"][0].update(lead)
    return db


def _other(status, number, dnc):
    return {"id": "l2", "tenant_id": "t1", "campaign_id": "c1",
            "phone_number": number, "status": status, "do_not_call": dnc}


def _jobs(db):
    return {j["id"]: j["status"] for j in db.tables["dialer_jobs"]}


# 1 --------------------------------------------------------------------------------
def test_1_a_removed_numbers_do_not_call_mark_is_taken_with_the_number(monkeypatch):
    service = _spy(monkeypatch)
    db = _running_db()
    db.tables["leads"].append(_other("deleted", NEW, True))
    snapshot = dict(db.tables["leads"][1])

    out = _edit(db, phone_number=NEW, do_not_call=False)

    assert db.tables["leads"][0]["phone_number"] == NEW
    assert db.tables["leads"][0]["do_not_call"] is True
    assert out["warning"] == DNC_MARK_TAKEN
    assert out["contact"]["do_not_call"] is True
    service.start_campaign.assert_not_awaited()
    assert _jobs(db)["j1"] == "cancelled" and _jobs(db)["j2"] == "cancelled"
    assert db.tables["leads"][1] == snapshot


# 2 --------------------------------------------------------------------------------
def test_2_a_removed_number_without_a_mark_gives_no_warning_and_is_queued(monkeypatch):
    service = _spy(monkeypatch)
    db = _running_db()
    db.tables["leads"].append(_other("deleted", NEW, False))

    out = _edit(db, phone_number=NEW, do_not_call=False)

    assert "warning" not in out
    assert db.tables["leads"][0].get("do_not_call") is not True
    service.start_campaign.assert_awaited_once()
    assert service.start_campaign.await_args.kwargs["only_lead_ids"] == ["l1"]


# 3 --------------------------------------------------------------------------------
def test_3_an_unchanged_number_takes_no_mark(monkeypatch):
    _spy(monkeypatch)
    db = _running_db()
    db.tables["leads"].append(_other("deleted", OLD, True))

    out = _edit(db, phone_number=OLD, first_name="Ann")

    assert "warning" not in out
    assert db.tables["leads"][0].get("do_not_call") is not True


# 4 --------------------------------------------------------------------------------
def test_4_a_live_duplicate_is_still_refused(monkeypatch):
    _spy(monkeypatch)
    db = _running_db()
    db.tables["leads"].append(_other("pending", NEW, False))
    before = _jobs(db)

    with pytest.raises(HTTPException) as caught:
        _edit(db, phone_number=NEW)

    assert caught.value.status_code == 409
    assert caught.value.detail == f"Phone number {NEW} already exists in this campaign"
    assert _jobs(db) == before
    assert db.tables["leads"][0]["phone_number"] == OLD


# 5 --------------------------------------------------------------------------------
def test_5_the_sentence_is_the_assistants_sentence():
    assert DNC_MARK_TAKEN == lead_write_safety.W2


# 6 --------------------------------------------------------------------------------
@pytest.mark.parametrize("change", [
    {"do_not_call": False}, {"phone_number": NEW}, {"first_name": "Ann"},
])
def test_6_a_removed_contact_is_not_found(monkeypatch, change):
    _spy(monkeypatch)
    db = _running_db(status="deleted", do_not_call=True)
    snapshot = dict(db.tables["leads"][0])
    before = _jobs(db)

    with pytest.raises(HTTPException) as caught:
        _edit(db, **change)

    assert caught.value.status_code == 404
    assert caught.value.detail == "Contact not found in this campaign"
    assert db.tables["leads"][0] == snapshot
    assert _jobs(db) == before


# 7 --------------------------------------------------------------------------------
def test_7_a_contact_removed_between_the_read_and_the_save_is_a_conflict(monkeypatch):
    _spy(monkeypatch)

    class _Q(_JobsQ):
        def execute(self):
            if self._table == "leads" and self._op == "update" and "first_name" in (self._payload or {}):
                self._db.tables["leads"][0]["status"] = "deleted"
            return super().execute()

    class _DBx(_JobsDB):
        def table(self, name):
            return _Q(self, name)

    db = _DBx()
    with pytest.raises(HTTPException) as caught:
        _edit(db, first_name="Ann")

    assert caught.value.status_code == 409
    assert caught.value.detail["error"] == "contact_write_conflict"
    assert "first_name" not in db.tables["leads"][0]


# 8 --------------------------------------------------------------------------------
class _SaveFailQ(_JobsQ):
    def execute(self):
        if self._table == "leads" and self._op == "update" and "phone_number" in (self._payload or {}):
            return SimpleNamespace(data=[], count=0, error=MARKER)
        return super().execute()


class _SaveFailDB(_JobsDB):
    def table(self, name):
        return _SaveFailQ(self, name)


def _failing_save_db(**lead):
    db = _SaveFailDB()
    db.tables["campaigns"][0]["status"] = "running"
    db.tables["leads"][0].update(lead)
    return db


def test_8_a_failed_save_leaves_an_invalid_number_skip_as_it_was(monkeypatch):
    service = _spy(monkeypatch)
    db = _failing_save_db(status="skipped", last_call_result="invalid_phone_number")
    db.tables["dialer_jobs"].clear()

    with pytest.raises(HTTPException) as caught:
        _edit(db, phone_number=NEW)

    assert caught.value.status_code == 500
    assert caught.value.detail == "Failed to update contact"
    assert MARKER not in repr(caught.value.detail)
    assert db.tables["leads"][0]["status"] == "skipped"
    assert db.tables["leads"][0]["phone_number"] == OLD
    service.start_campaign.assert_not_awaited()


# 9 --------------------------------------------------------------------------------
def test_9_a_failed_save_still_makes_a_called_contact_selectable(monkeypatch):
    service = _spy(monkeypatch)
    db = _failing_save_db(status="called")

    with pytest.raises(HTTPException):
        _edit(db, phone_number=NEW)

    assert _jobs(db)["j1"] == "cancelled" and _jobs(db)["j2"] == "cancelled"
    assert db.tables["leads"][0]["status"] == "pending"
    assert db.tables["leads"][0]["phone_number"] == OLD
    service.start_campaign.assert_not_awaited()


# 10 -------------------------------------------------------------------------------
def test_10_the_save_comes_before_the_invalid_number_reset(monkeypatch):
    _spy(monkeypatch)
    seen = []

    class _Q(_JobsQ):
        def execute(self):
            if self._table == "leads" and self._op == "update":
                seen.append((dict(self._payload), list(self._f)))
            return super().execute()

    class _DBx(_JobsDB):
        def table(self, name):
            return _Q(self, name)

    db = _DBx()
    db.tables["campaigns"][0]["status"] = "running"
    db.tables["leads"][0].update(status="skipped", last_call_result="invalid_phone_number")
    db.tables["dialer_jobs"].clear()

    _edit(db, phone_number=NEW)

    save = next(i for i, (p, _f) in enumerate(seen) if "phone_number" in p)
    reset = next(i for i, (p, f) in enumerate(seen)
                 if p == {"status": "pending"} and ("eq", "status", "skipped") in f)
    assert save < reset


# 11 -------------------------------------------------------------------------------
def test_11_a_failed_contact_read_on_remove_is_a_failure():
    class _Q(_b._Query):
        def execute(self):
            if self.table == "leads" and self.operation == "select":
                return _b._Response([], error=MARKER)
            return super().execute()

    class _DBx(_b._DB):
        def table(self, name):
            return _Q(self, name)

    db = _DBx(direction="outbound")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(campaigns_ep.remove_contact_from_campaign("c1", "lead1", _b.USER, db))

    assert caught.value.status_code == 500
    assert caught.value.detail == "Failed to remove contact"
    assert MARKER not in repr(caught.value.detail)
    assert db.writes == []
    assert db.tables["leads"][0]["status"] == "pending"


# 12 -------------------------------------------------------------------------------
def test_12_a_returned_error_is_logged_and_never_raised(caplog):
    class _Q:
        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            return SimpleNamespace(data=None, error=MARKER)

    class _DBx:
        def table(self, _n):
            return _Q()

    with caplog.at_level(logging.WARNING):
        release_called_contact(_DBx(), tenant_id="t1", campaign_id="c1", contact_id="l1")

    assert "contact_requeue_status_not_reset" in caplog.text
    assert MARKER not in caplog.text

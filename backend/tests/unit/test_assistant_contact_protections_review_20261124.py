"""What the review of the assistant's contact protections changed (24 November 2026).

1. A save that fails AFTER the contact's queued calls were cancelled makes the contact
   selectable again: it was left ``called`` with nothing queued, which no Start
   selects. It is NOT queued at that point (a second check's finding): that would
   ring, at once, the number the customer is replacing. Applying again queues it
   with the new number.
2. A status reset the database layer RETURNS as an error is logged, as a raised one is.
3. Order: cancel the queued calls, save, and only then hand the contact back.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.unit import test_bh_campaigns_contacts_contact_writes as _w
from tests.unit.test_assistant_contact_protections_20261124 import (
    NEW,
    OLD,
    OUTER,
    _deleted,
    _jobs,
    _run,
    _spy,
)
from tests.unit.test_live_risk_contacts import _JobsDB, _JobsQ

DB_TEXT = 'duplicate key value violates unique constraint "uq_leads_campaign_phone_active"'
QUEUED_L1 = dict(campaign_id="c1", tenant_id="t1", allow_running=True, only_lead_ids=["l1"])


@pytest.fixture(autouse=True)
def _permission_off(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "off")


class _SaveQ(_JobsQ):
    """Records every write; can fail the SAVE (the leads write that carries the number)."""

    def execute(self):
        db = self._db
        payload = dict(self._payload) if isinstance(self._payload, dict) else None
        if self._op != "select":
            db.calls.append((self._table, self._op, payload))
        is_save = self._table == "leads" and self._op == "update" and "phone_number" in (payload or {})
        if is_save and db.fail_saves > 0:
            db.fail_saves -= 1
            if db.save_raises:
                raise RuntimeError(DB_TEXT)
            if db.save_matches_nothing:
                return SimpleNamespace(data=[], count=0, error=None)
            # what the database layer returns for the unique index (it does not raise)
            return SimpleNamespace(data=None, count=0, error=DB_TEXT)
        return super().execute()


class _SaveDB(_JobsDB):
    def __init__(self, *, fail_saves=0, save_raises=False, save_matches_nothing=False):
        super().__init__()
        self.fail_saves = fail_saves
        self.save_raises = save_raises
        self.save_matches_nothing = save_matches_nothing
        self.calls = []

    def table(self, name):
        return _SaveQ(self, name)


def _waiting_for_a_retry(db):
    """A no-answer left the contact ``called`` with its retry booked."""
    db.tables["leads"][0]["status"] = "called"
    db.tables["dialer_jobs"] = [
        {"id": "j2", "tenant_id": "t1", "lead_id": "l1", "status": "retry_scheduled"},
    ]
    return db


def _edit(db, *, confirm=True):
    return _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=confirm)


# --- 1. a failed save after the cancel ------------------------------------------------
def test_a_refused_save_after_the_cancel_puts_the_contact_back(monkeypatch):
    service = _spy(monkeypatch)
    db = _waiting_for_a_retry(_SaveDB(fail_saves=1))
    db.tables["campaigns"][0]["status"] = "running"
    lead = db.tables["leads"][0]

    first = _edit(db)
    assert first["applied"] is False and first["error"] == "lead_write_conflict"
    assert DB_TEXT not in repr(first)
    assert _jobs(db) == {"j2": "cancelled"}
    # its calls are gone and its number is unchanged: selectable again, but NOT
    # queued: nothing may ring the old number because an edit failed
    assert (lead["phone_number"], lead["status"]) == (OLD, "pending")
    service.start_campaign.assert_not_awaited()

    # the answer says "preview the change again": the customer does, and applies;
    # now it is queued, once, with the new number saved
    second = _edit(db)
    assert second["applied"] is True
    assert (lead["phone_number"], lead["status"]) == (NEW, "pending")
    service.start_campaign.assert_awaited_once_with(**QUEUED_L1)


def test_a_save_that_raises_after_the_cancel_puts_the_contact_back(monkeypatch):
    service = _spy(monkeypatch)
    db = _waiting_for_a_retry(_SaveDB(fail_saves=1, save_raises=True))
    db.tables["campaigns"][0]["status"] = "running"
    lead = db.tables["leads"][0]

    out = _edit(db)
    assert out == {"error": OUTER}
    assert (lead["phone_number"], lead["status"]) == (OLD, "pending")
    service.start_campaign.assert_not_awaited()


def test_a_save_that_matches_no_row_after_the_cancel_puts_the_contact_back(monkeypatch):
    """The contact or campaign changed in between: the layer returns no row and no error."""
    service = _spy(monkeypatch)
    db = _waiting_for_a_retry(_SaveDB(fail_saves=1, save_matches_nothing=True))
    db.tables["campaigns"][0]["status"] = "running"
    lead = db.tables["leads"][0]

    out = _edit(db)
    assert out["applied"] is False and out["error"] == "lead_write_conflict"
    assert (lead["phone_number"], lead["status"]) == (OLD, "pending")
    service.start_campaign.assert_not_awaited()


def test_a_refused_save_with_nothing_cancelled_touches_no_status(monkeypatch):
    service = _spy(monkeypatch)
    db = _SaveDB(fail_saves=1)
    db.tables["campaigns"][0]["status"] = "running"
    db.tables["dialer_jobs"].clear()
    db.tables["leads"][0]["status"] = "called"

    out = _edit(db)
    assert out["applied"] is False
    assert db.tables["leads"][0]["status"] == "called"
    assert [c for c in db.calls if c[2] == {"status": "pending"}] == []
    service.start_campaign.assert_not_awaited()


def test_a_called_contact_with_nothing_queued_stays_called():
    """It has used its attempts: a corrected number alone does not put it back in the
    queue (the website's rule: only a contact whose calls this change cancelled)."""
    db = _SaveDB()
    db.tables["dialer_jobs"].clear()
    db.tables["leads"][0]["status"] = "called"
    out = _edit(db)
    assert out["applied"] is True
    assert db.tables["leads"][0]["status"] == "called"


# --- 2. a returned error on the status reset is logged ---------------------------------
class _ResetReturnsErrorQ(_JobsQ):
    def execute(self):
        if self._table == "leads" and self._op == "update" and self._payload == {"status": "pending"}:
            return SimpleNamespace(data=None, count=0, error=DB_TEXT)
        return super().execute()


class _ResetReturnsErrorDB(_JobsDB):
    def table(self, name):
        return _ResetReturnsErrorQ(self, name)


def test_a_status_reset_returned_as_an_error_is_logged_and_the_edit_stands(caplog):
    db = _ResetReturnsErrorDB()
    db.tables["leads"][0]["status"] = "called"
    with caplog.at_level("WARNING"):
        out = _edit(db)
    assert out["applied"] is True
    assert db.tables["leads"][0]["phone_number"] == NEW
    assert caplog.text.count("manage_lead_requeue_status_not_reset") == 2  # both resets
    assert DB_TEXT not in caplog.text and "uq_leads" not in caplog.text


# --- 3. order ---------------------------------------------------------------------------
def test_the_calls_are_cancelled_before_the_save_and_the_hand_back_comes_after():
    db = _SaveDB()
    db.tables["leads"][0]["status"] = "called"
    out = _edit(db)
    assert out["applied"] is True

    def first(pred):
        return next(i for i, c in enumerate(db.calls) if pred(c))

    cancel = first(lambda c: c[0] == "dialer_jobs" and c[2].get("status") == "cancelled")
    save = first(lambda c: c[0] == "leads" and "phone_number" in c[2])
    hand_back = first(lambda c: c[0] == "leads" and c[2] == {"status": "pending"})
    assert cancel < save < hand_back
    # nothing else is written: one cancel, one save, the two status hand-backs
    assert len(db.calls) == 4


# --- a revived contact on a running campaign is queued ----------------------------------
def test_a_revived_contact_on_a_running_campaign_is_queued(monkeypatch):
    service = _spy(monkeypatch)
    db = _w._DB(leads=[_deleted("l1")])
    db.tables["campaigns"][0]["status"] = "running"
    out = _run(db, action="add", phone_number=OLD, confirm=True)
    assert out["applied"] is True
    assert db.tables["leads"][0]["status"] == "pending"
    service.start_campaign.assert_awaited_once_with(**QUEUED_L1)

"""A do-not-call mark belongs to the number, on an edit as on an add (24 November 2026).

The assistant's add revives a removed row with its do-not-call mark. Its phone EDIT
could still move a live contact onto such a number and leave it callable: the dial
guard reads only the contact's own mark and the tenant's do-not-call list, and the
contact form's mark is never written to that list. The edit now gives the contact the
mark, shows it on the preview, and says so when applied.
"""
from __future__ import annotations

import pytest

from app.infrastructure.assistant.tools.lead_write_safety import W2
from tests.unit.test_assistant_contact_protections_20261124 import (
    NEW,
    _deleted,
    _jobs,
    _run,
    _spy,
)
from tests.unit.test_live_risk_contacts import _JobsDB


@pytest.fixture(autouse=True)
def _permission_off(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "off")


def _edit(db, *, confirm=True):
    return _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=confirm)


def test_an_edit_onto_a_removed_do_not_call_number_shows_the_mark_on_the_preview():
    db = _JobsDB()
    db.tables["leads"].append(_deleted("l2", dnc=True, phone=NEW))
    leads_before = [dict(r) for r in db.tables["leads"]]
    jobs_before = _jobs(db)

    out = _edit(db, confirm=False)
    assert out["preview"] is True
    assert out["warnings"] == [W2]
    assert {"field": "do_not_call", "before": None, "after": True} in out["changes"]
    assert db.tables["leads"] == leads_before and _jobs(db) == jobs_before


def test_an_edit_onto_a_removed_do_not_call_number_marks_the_contact(monkeypatch):
    service = _spy(monkeypatch)
    db = _JobsDB()
    db.tables["campaigns"][0]["status"] = "running"
    db.tables["leads"].append(_deleted("l2", dnc=True, phone=NEW))

    out = _edit(db)
    lead = db.tables["leads"][0]
    assert out["applied"] is True and out["notice"] == W2
    assert lead["phone_number"] == NEW and lead["do_not_call"] is True
    # the calls queued for the old number are still taken off, and nothing new is queued
    assert _jobs(db)["j1"] == "cancelled" and _jobs(db)["j2"] == "cancelled"
    service.start_campaign.assert_not_awaited()


def test_a_removed_row_without_the_mark_changes_nothing_about_do_not_call():
    db = _JobsDB()
    db.tables["leads"].append(_deleted("l2", dnc=False, phone=NEW))
    preview = _edit(db, confirm=False)
    assert "warnings" not in preview
    assert all(c["field"] != "do_not_call" for c in preview["changes"])
    out = _edit(db)
    assert out["applied"] is True and "notice" not in out
    assert db.tables["leads"][0].get("do_not_call") is None


def test_a_contact_already_marked_keeps_its_mark_without_a_second_warning():
    db = _JobsDB()
    db.tables["leads"][0]["do_not_call"] = True
    db.tables["leads"].append(_deleted("l2", dnc=True, phone=NEW))
    preview = _edit(db, confirm=False)
    assert "warnings" not in preview
    out = _edit(db)
    assert out["applied"] is True and "notice" not in out
    assert db.tables["leads"][0]["do_not_call"] is True

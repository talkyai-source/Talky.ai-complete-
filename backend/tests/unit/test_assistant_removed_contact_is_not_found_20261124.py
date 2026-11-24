"""The assistant treats a removed contact as not found, for an update and for a remove.

A lead id kept from an earlier conversation must not work on a removed row: it could carry a
do-not-call mark that an edit would move off its number.
"""
from __future__ import annotations

import asyncio

import pytest

from app.infrastructure.assistant.tools.campaign_admin import manage_lead
from tests.unit.test_live_risk_contacts import _JobsDB

NEW = "+14155550999"
NOT_FOUND = {"error": "lead not found"}


@pytest.fixture(autouse=True)
def _permission_off(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "off")


def _run(db, **kw):
    return asyncio.run(manage_lead(tenant_id="t1", db_client=db, campaign_id="c1", **kw))


def _jobs(db):
    return [dict(j) for j in db.tables["dialer_jobs"]]


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("kw", [
    {"action": "update", "phone_number": NEW},
    {"action": "update", "first_name": "Ann"},
    {"action": "remove"},
], ids=["phone-change", "name-change", "remove"])
def test_a_removed_row_is_not_found(kw, confirm):
    db = _JobsDB()
    db.tables["leads"][0].update(status="deleted", do_not_call=True)
    row_before = dict(db.tables["leads"][0])
    jobs_before = _jobs(db)

    out = _run(db, lead_id="l1", confirm=confirm, **kw)

    assert out == NOT_FOUND
    assert db.tables["leads"][0] == row_before
    assert _jobs(db) == jobs_before


def test_a_row_removed_between_the_read_and_the_save_is_not_written(monkeypatch):
    from tests.unit.test_live_risk_contacts import _JobsQ

    class _Q(_JobsQ):
        def execute(self):
            if self._table == "leads" and self._op == "update" and "phone_number" in (self._payload or {}):
                self._db.tables["leads"][0].update(status="deleted", do_not_call=True)
            return super().execute()

    class _DBx(_JobsDB):
        def table(self, name):
            return _Q(self, name)

    db = _DBx()
    out = _run(db, action="update", lead_id="l1", phone_number=NEW, confirm=True)
    row = db.tables["leads"][0]
    assert row["phone_number"] == "+14155550124", (out, row)
    assert out.get("applied") is not True, out


def test_a_live_row_is_still_updated_and_removed():
    db = _JobsDB()
    out = _run(db, action="update", lead_id="l1", first_name="Ann", confirm=True)
    assert out["applied"] is True
    assert db.tables["leads"][0]["first_name"] == "Ann"

    out = _run(db, action="remove", lead_id="l1", confirm=True)
    assert out["applied"] is True
    assert db.tables["leads"][0]["status"] == "deleted"

"""An import that brings back a removed contact keeps its do-not-call mark.

A removed contact keeps its row, and that row can carry the mark. The dial guard reads
only the contact's own mark, so a number that comes back unmarked is callable. The
website's add and edit already keep the mark; the CSV, paste and older bulk imports must too.
"""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api.v1.endpoints import contacts as contacts_ep
from app.domain.services.dialer.bulk_ingest import LeadRecord, ingest_lead_records
from app.domain.services.dialer.removed_dnc_marks import removed_marked_phones
from tests.unit.test_bulk_ingest import _id_normalize
from tests.unit.test_contact_import_validation import (
    FORM_PERMISSION, FakeDB, _Query, _Resp, _User,
)

PHONE = "+14155551234"


class _Q(_Query):
    def select(self, *a, **k):
        self._db.selects.append((self._table, self._filters))
        return super().select(*a, **k)

    def execute(self):
        if self._table == "leads" and self._op == "select":
            cols = {c for _, c, _v in self._filters}
            if "do_not_call" in cols and self._db.dnc_error is not None:
                return type("R", (), {"data": None, "error": self._db.dnc_error})()
        if self._op == "update" and self._table == "leads":
            self._db.updates.append(dict(self._payload))
        return super().execute()


class _DB(FakeDB):
    def __init__(self, rows=()):
        super().__init__()
        self.tables["leads"] = [dict(r) for r in rows]
        self.selects: list = []
        self.updates: list = []
        self.dnc_error = None

    def table(self, name):
        return _Q(self, name)


def _row(id_, *, dnc=None, status="deleted", is_lead=False, campaign="c1", tenant="t1",
         phone=PHONE):
    r = {"id": id_, "phone_number": phone, "status": status, "is_lead": is_lead,
         "campaign_id": campaign, "tenant_id": tenant}
    if dnc is not None:
        r["do_not_call"] = dnc
    return r


def _ingest(db, **rec_kw):
    return ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", source_row=1, **rec_kw)],
        normalize=_id_normalize,
    )


def _revive(db):
    return [u for u in db.updates if u.get("status") == "pending"][0]


# 1 (scratch a1)
def test_sheet_saying_no_keeps_the_removed_rows_mark():
    db = _DB([_row("DEL1", dnc=True)])
    _ingest(db, do_not_call=False)
    assert _revive(db)["do_not_call"] is True


# 2 (scratch a2)
def test_a_mark_held_by_a_sibling_removed_row_is_kept():
    db = _DB([_row("DEL_MARKED", dnc=True), _row("DEL_LEAD", dnc=False, is_lead=True)])
    _ingest(db)
    assert _revive(db)["do_not_call"] is True
    assert db.tables["leads"][1]["status"] == "pending"  # the is_lead row is the one revived


# 3
def test_no_do_not_call_column_still_keeps_the_mark():
    db = _DB([_row("DEL1", dnc=True)])
    _ingest(db)
    assert _revive(db)["do_not_call"] is True


# 4 (guards)
@pytest.mark.parametrize("cell, expected", [(False, False), (True, True), (None, "absent")])
def test_unmarked_removed_row_follows_the_sheet_as_before(cell, expected):
    db = _DB([_row("DEL1", dnc=False)])
    _ingest(db, do_not_call=cell)
    payload = _revive(db)
    if expected == "absent":
        assert "do_not_call" not in payload
    else:
        assert payload["do_not_call"] is expected


# 5
def test_the_lookup_is_scoped_and_other_campaigns_and_tenants_give_no_mark():
    db = _DB([
        _row("OTHER_C", dnc=True, campaign="c2"),
        _row("OTHER_T", dnc=True, tenant="t2"),
        _row("DEL1", dnc=False),
    ])
    assert removed_marked_phones(db, campaign_id="c1", tenant_id="t1") == set()
    filters = [f for t, f in db.selects if t == "leads"][-1]
    assert set(filters) == {
        ("eq", "campaign_id", "c1"), ("eq", "tenant_id", "t1"),
        ("eq", "status", "deleted"), ("eq", "do_not_call", True),
    }
    _ingest(db)
    assert "do_not_call" not in _revive(db)


# 6
def test_a_failed_lookup_fails_the_import_and_writes_nothing():
    db = _DB([_row("DEL1", dnc=True)])
    db.dnc_error = {"message": "boom"}
    with pytest.raises(RuntimeError, match="removed do-not-call lookup failed"):
        ingest_lead_records(
            db, campaign_id="c1", tenant_id="t1",
            records=[LeadRecord("+1 415 555 1234", source_row=1),
                     LeadRecord("+1 415 555 9999", source_row=2)],
            normalize=_id_normalize,
        )
    assert db.updates == []
    assert db.inserted == []


# 7 (guards)
def test_a_fresh_number_is_inserted_as_before():
    db = _DB([])
    res = _ingest(db)
    assert res.imported == 1
    assert "do_not_call" not in db.inserted[0]


def test_a_live_duplicate_is_skipped_as_before():
    db = _DB([_row("LIVE", status="pending"), _row("DEL1", dnc=True)])
    res = _ingest(db)
    assert res.duplicates_skipped == 1
    assert db.updates == [] and db.inserted == []


# 8 the older bulk route
def _legacy(db, csv_text):
    return asyncio.run(contacts_ep.bulk_import_contacts(
        file=UploadFile(file=io.BytesIO(csv_text.encode()), filename="c.csv"),
        campaign_id="c1", current_user=_User(), db_client=db,
        default_country=None, **FORM_PERMISSION,
    ))


CSV = "phone_number,first_name\n+442079460123,Alex\n+442079460124,Sam\n"


def test_older_bulk_route_marks_a_number_whose_removed_row_is_marked():
    db = _DB([_row("DEL1", dnc=True, phone="+442079460123")])
    res = _legacy(db, CSV)
    assert res.imported == 2
    by_phone = {r["phone_number"]: r for r in db.inserted}
    assert by_phone["+442079460123"]["do_not_call"] is True
    assert "do_not_call" not in by_phone["+442079460124"]


def test_older_bulk_route_failed_lookup_answers_fixed_sentence_and_inserts_nothing():
    db = _DB([_row("DEL1", dnc=True, phone="+442079460123")])
    db.dnc_error = {"message": "secret database text"}
    with pytest.raises(HTTPException) as exc:
        _legacy(db, CSV)
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to import contacts."
    assert db.inserted == []
    assert db.tables["contact_lists"] == []

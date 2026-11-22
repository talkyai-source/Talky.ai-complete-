"""Re-importing a file fills the missing name of a contact already in the campaign.

The tester's contacts were imported without names and the only way to add them
was to delete every contact. A row whose number is already a LIVE contact of the
campaign, and whose contact has no first AND no last name, gives that contact
its name: first_name / last_name only, nothing else.
"""
from __future__ import annotations

import asyncio
import copy
import io

from starlette.datastructures import UploadFile

from app.api.v1.endpoints import contacts as contacts_ep
from tests.unit.test_contact_import_validation import FORM_PERMISSION, FakeDB, _User

PHONE = "+442079460123"


def _lead(**over):
    row = {
        "id": "L1", "tenant_id": "t1", "campaign_id": "c1", "phone_number": PHONE,
        "first_name": None, "last_name": None, "email": "old@example.com",
        "status": "pending", "last_call_result": "pending", "call_attempts": 2,
        "do_not_call": False, "list_id": "list-old", "is_lead": False,
        "custom_fields": {"company": "Old Co"}, "permission_basis": "express_consent",
    }
    row.update(over)
    return row


def _run(csv_text: str, leads: list[dict]):
    db = FakeDB()
    db.tables["leads"] = [dict(r) for r in leads]
    tables_before = set(db.tables)
    resp = asyncio.run(contacts_ep.upload_campaign_contacts(
        campaign_id="c1",
        file=UploadFile(file=io.BytesIO(csv_text.encode()), filename="c.csv"),
        skip_duplicates=True, current_user=_User(), db_client=db, **FORM_PERMISSION,
    ))
    return resp, db, tables_before


def _by_id(db, lead_id):
    return next(r for r in db.tables["leads"] if r["id"] == lead_id)


NAMED = f"Name,Phone\nAlex Example,{PHONE}\n"


def test_nameless_contact_gets_the_name_and_nothing_else_changes():
    before = _lead()
    resp, db, _ = _run(NAMED, [before])
    after = _by_id(db, "L1")
    assert (after["first_name"], after["last_name"]) == ("Alex", "Example")
    assert resp.names_added == 1 and resp.duplicates_skipped == 1 and resp.imported == 0
    expected = copy.deepcopy(before)
    expected.update(first_name="Alex", last_name="Example")
    assert after == expected, "every other column must be untouched"
    assert len(db.tables["leads"]) == 1, "no second contact"
    assert not [t for t in db.tables if "job" in t or "queue" in t], "no job is created"


def test_a_contact_with_a_name_is_never_overwritten():
    resp, db, _ = _run(NAMED, [_lead(first_name="Kept", last_name=None)])
    assert _by_id(db, "L1")["first_name"] == "Kept" and _by_id(db, "L1")["last_name"] is None
    assert resp.names_added == 0 and resp.duplicates_skipped == 1


def test_other_campaign_or_tenant_with_the_same_number_is_untouched():
    other_campaign = _lead(id="L2", campaign_id="c2")
    other_tenant = _lead(id="L3", tenant_id="t2")
    resp, db, _ = _run(NAMED, [other_campaign, other_tenant])
    assert _by_id(db, "L2") == other_campaign and _by_id(db, "L3") == other_tenant
    assert resp.names_added == 0 and resp.imported == 1, "the number is new to THIS campaign"


def test_a_soft_deleted_contact_is_still_revived_as_before():
    resp, db, _ = _run(NAMED, [_lead(status="deleted")])
    row = _by_id(db, "L1")
    assert row["status"] == "pending" and (row["first_name"], row["last_name"]) == ("Alex", "Example")
    assert resp.imported == 1 and resp.names_added == 0 and resp.duplicates_skipped == 0


def test_a_do_not_call_contact_may_get_a_name_but_stays_do_not_call():
    resp, db, _ = _run(NAMED, [_lead(do_not_call=True)])
    row = _by_id(db, "L1")
    assert row["first_name"] == "Alex" and row["do_not_call"] is True
    assert resp.names_added == 1


def test_two_rows_for_the_same_number_first_name_wins_and_counts_once():
    resp, db, _ = _run(f"Name,Phone\nAlex Example,{PHONE}\nZed Other,{PHONE}\n", [_lead()])
    row = _by_id(db, "L1")
    assert (row["first_name"], row["last_name"]) == ("Alex", "Example")
    assert resp.names_added == 1 and resp.duplicates_skipped == 2


def test_a_row_without_a_name_adds_nothing():
    resp, db, _ = _run(f"Phone\n{PHONE}\n", [_lead()])
    assert _by_id(db, "L1")["first_name"] is None and resp.names_added == 0

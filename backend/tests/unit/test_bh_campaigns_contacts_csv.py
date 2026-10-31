"""CSV import robustness (bughunt campaigns-contacts-crm-2 and crm-3)."""
from __future__ import annotations

import asyncio
import io

from starlette.datastructures import UploadFile

from app.api.v1.endpoints import contacts as contacts_ep
from tests.unit.test_contact_import_validation import FakeDB, _User


def _run_bytes(raw: bytes, db=None):
    db = db or FakeDB()
    resp = asyncio.run(
        contacts_ep.upload_campaign_contacts(
            campaign_id="c1",
            file=UploadFile(file=io.BytesIO(raw), filename="contacts.csv"),
            skip_duplicates=True,
            current_user=_User(),
            db_client=db,
        )
    )
    return resp, db


BOM = b"\xef\xbb\xbf"


def test_bom_csv_with_phone_first_column_imports():
    resp, db = _run_bytes(BOM + b"phone_number,first_name\n+447700900123,Sian\n")
    assert resp.imported == 1
    assert db.inserted[0]["phone_number"] == "+447700900123"


def test_bom_csv_with_name_first_column_keeps_the_name():
    resp, db = _run_bytes(BOM + b"first_name,phone_number\nSian,+447700900123\n")
    assert resp.imported == 1
    row = db.inserted[0]
    assert row["first_name"] == "Sian"
    assert not any("\ufeff" in k for k in (row.get("custom_fields") or {}))


def test_bom_csv_with_email_first_column_keeps_the_email():
    _, db = _run_bytes(
        BOM + b"email,phone_number\nsian@buildwright.co.uk,+447700900123\n"
    )
    assert db.inserted[0]["email"] == "sian@buildwright.co.uk"


def test_a_row_with_extra_cells_is_reported_and_the_rest_import():
    resp, db = _run_bytes(
        b"phone_number,first_name\n"
        b"+447700900123,Sian\n"
        b"+447700900124,Smith, John\n"
        b"+447700900125,Rhys\n"
    )
    assert resp.imported == 2
    assert resp.failed == 1
    bad = [e for e in resp.errors if e.row == 3]
    assert bad and "more cells" in bad[0].error
    assert {r["phone_number"] for r in db.inserted} == {
        "+447700900123", "+447700900125"
    }

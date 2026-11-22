"""A CSV's names must reach the place the call reads them (20 Nov 2026).

A customer-side tester: "the names that were in the CSV list were not
imported". The production log agreed: every one of 213 calls warned
``telephony_prompt_unfilled_slot ... slots=[Name]``.

The call greets the person from the lead's ``first_name`` / ``last_name``
(``telephony_session_config._sanitize_lead_field``). So the rule pinned here is:
for every ordinary name heading, the imported lead row has the name in
``first_name`` / ``last_name`` and the call's name slot would be filled.

Two real import paths take a CSV on the server: the campaign upload
(``upload_campaign_contacts``, which the website sends its cleaned file to) and
the legacy ``bulk_import_contacts``. The paste import carries no names. The
website's own reader is covered by ``contact-csv.test.ts``.
"""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api.v1.endpoints import contacts as contacts_ep
from app.domain.services.telephony_session_config import _sanitize_lead_field
from tests.unit.test_contact_import_validation import FORM_PERMISSION, FakeDB, _User

PEOPLE = [
    ("Alex", "Example", "+442079460123"),
    ("Sam", "Sample", "+442079460124"),
    ("Jo", "Test", "+442079460125"),
]


def _upload(raw: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(raw), filename="contacts.csv")


def _via_upload(raw: bytes, db: FakeDB):
    return asyncio.run(contacts_ep.upload_campaign_contacts(
        campaign_id="c1", file=_upload(raw), skip_duplicates=True,
        current_user=_User(), db_client=db, **FORM_PERMISSION,
    ))


def _via_legacy(raw: bytes, db: FakeDB):
    return asyncio.run(contacts_ep.bulk_import_contacts(
        file=_upload(raw), campaign_id="c1", current_user=_User(),
        db_client=db, default_country=None, **FORM_PERMISSION,
    ))


PATHS = [pytest.param(_via_upload, id="campaign_upload"),
         pytest.param(_via_legacy, id="legacy_bulk")]


def _csv(header: str, cells, sep: str = ",", bom: bool = False) -> bytes:
    """Rows are (first, last, phone) people; ``cells`` says how a row is written."""
    lines = [header] + [sep.join(cells(f, l, p)) for f, l, p in PEOPLE]
    text = "\n".join(lines) + "\n"
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def _full(f, l, p):
    return [f"{f} {l}", p]


def _split(f, l, p):
    return [f, l, p]


# (id, raw file bytes)
HEADER_FILES = [
    pytest.param(_csv("Name,Phone", _full), id="Name,Phone"),
    pytest.param(_csv("name,phone", _full), id="name,phone"),
    pytest.param(_csv("Full Name,Phone Number", _full), id="Full Name,Phone Number"),
    pytest.param(_csv("First Name,Last Name,Phone", _split), id="First Name,Last Name,Phone"),
    pytest.param(_csv("first_name,last_name,phone_number", _split),
                 id="first_name,last_name,phone_number"),
    pytest.param(_csv("Contact Name,Mobile", _full), id="Contact Name,Mobile"),
    pytest.param(_csv("Customer,Number", _full), id="Customer,Number"),
    pytest.param(_csv("Name,Phone", _full, bom=True), id="byte-order mark"),
    pytest.param(_csv(" Name , Phone ", lambda f, l, p: [f" {f} {l} ", f" {p} "]),
                 id="spaces around headers"),
]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("raw", HEADER_FILES)
def test_every_ordinary_name_heading_reaches_the_call(path, raw):
    db = FakeDB()
    path(raw, db)
    by_phone = {r["phone_number"]: r for r in db.inserted}
    assert set(by_phone) == {p for _, _, p in PEOPLE}, "every person must import"
    for first, last, phone in PEOPLE:
        row = by_phone[phone]
        assert row["first_name"] == first
        assert row["last_name"] == last
        # What the call does with it: the opening's name slot is filled.
        assert _sanitize_lead_field(row["first_name"], field="first_name") == first


@pytest.mark.parametrize("path", PATHS)
def test_a_single_name_column_uses_the_products_split_rule(path):
    db = FakeDB()
    path(b"Name,Phone\nMary Ann Smith,+442079460123\nMadonna,+442079460124\n", db)
    by_phone = {r["phone_number"]: r for r in db.inserted}
    assert (by_phone["+442079460123"]["first_name"], by_phone["+442079460123"]["last_name"]) == ("Mary", "Ann Smith")
    assert (by_phone["+442079460124"]["first_name"], by_phone["+442079460124"]["last_name"]) == ("Madonna", None)


@pytest.mark.parametrize("path", PATHS)
def test_semicolon_separated_file_is_refused_clearly_not_imported_nameless(path):
    """Excel in some locales saves ``Name;Phone``. We read commas only, so the
    customer must be told what to do; a silent import with no names is the bug."""
    db = FakeDB()
    raw = _csv("Name;Phone", _full, sep=";")
    with pytest.raises(HTTPException) as exc:
        path(raw, db)
    assert exc.value.status_code == 400
    assert "semicolon" in str(exc.value.detail).lower()
    assert db.inserted == []


def test_preview_refuses_a_semicolon_file_and_maps_name_to_the_name_field():
    from app.api.v1.endpoints.lead_details import preview_import

    def _preview(raw):
        return asyncio.run(preview_import(
            file=_upload(raw), default_country="GB", campaign_id=None,
            current_user=_User(), db_client=FakeDB(),
        ))

    with pytest.raises(HTTPException) as exc:
        _preview(_csv("Name;Phone", _full, sep=";"))
    assert exc.value.status_code == 400 and "semicolon" in str(exc.value.detail).lower()
    assert _preview(_csv("Name,Phone", _full)).headers == {"Name": "full_name", "Phone": "phone_number"}


def test_preview_names_the_column_it_will_dial_phone_first_then_mobile():
    from app.api.v1.endpoints.lead_details import preview_import

    def _dial(raw):
        return asyncio.run(preview_import(
            file=_upload(raw), default_country="GB", campaign_id=None,
            current_user=_User(), db_client=FakeDB(),
        )).dial_column

    assert _dial(_csv("Name,Mobile", _full)) == "Mobile"
    assert _dial(_csv("Name,Phone", _full)) == "Phone"
    assert _dial(b"Name\nAlex\n") is None

"""What a person sees when a contact import goes wrong (goals.md §11).

§11 asks for "row-level validation failures" and a downloadable template. Both
have to hold on the path that actually WRITES — the preview endpoint is
optional and a user can upload straight past it.

The gap these tests pin: the CSV upload endpoint mapped headers through the
canonical registry but never ran the registry validators, so an invalid
timezone, a malformed email or a "maybe" in the do-not-call column were written
to the database with nobody told. Only the phone number was ever checked.
"""
from __future__ import annotations

import asyncio
import io
from typing import Optional

from starlette.datastructures import UploadFile

from app.api.v1.endpoints import contacts as contacts_ep


# ── in-memory fake DB (same shape as test_contact_lists.py) ────────────────
class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else (
            len(data) if isinstance(data, list) else 0
        )


class _Query:
    def __init__(self, db, table):
        self._db, self._table = db, table
        self._filters: list = []
        self._op = "select"
        self._payload = None

    def select(self, *_a, count=None, **_k):
        self._op = "select"
        return self

    def insert(self, data):
        self._op, self._payload = "insert", data
        return self

    def update(self, data):
        self._op, self._payload = "update", data
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def _match(self, row):
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and rv != val:
                return False
            if kind == "neq" and rv == val:
                return False
            if kind == "in" and rv not in val:
                return False
            if kind == "is" and val is None and rv is not None:
                return False
        return True

    def execute(self):
        rows = self._db.tables.setdefault(self._table, [])
        if self._op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            return _Resp(matched, count=len(matched))
        if self._op == "insert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            for it in items:
                rows.append(dict(it))
            if self._table == "leads":
                self._db.inserted.extend(items)
            return _Resp([dict(i) for i in items])
        if self._op == "update":
            changed = []
            for r in rows:
                if self._match(r):
                    r.update(self._payload)
                    changed.append(dict(r))
            return _Resp(changed)
        return _Resp([])


class FakeDB:
    def __init__(self):
        self.tables = {
            "leads": [],
            "contact_lists": [],
            "campaigns": [
                {"id": "c1", "name": "Roofing Q3", "tenant_id": "t1",
                 "script_config": {}}
            ],
        }
        self.inserted: list = []

    def table(self, name):
        return _Query(self, name)


class _User:
    tenant_id = "t1"
    id = "u1"
    role = "owner"


def _upload(text: str, name: str = "contacts.csv") -> UploadFile:
    return UploadFile(file=io.BytesIO(text.encode("utf-8")), filename=name)


def _run(csv_text: str, db: Optional[FakeDB] = None):
    db = db or FakeDB()
    resp = asyncio.run(
        contacts_ep.upload_campaign_contacts(
            campaign_id="c1",
            file=_upload(csv_text),
            skip_duplicates=True,
            current_user=_User(),
            db_client=db,
        )
    )
    return resp, db


GOOD = (
    "phone_number,first_name,last_name,email,company_name,timezone\n"
    "+447700900123,Sian,Roberts,sian@buildwright.co.uk,BuildWright,Europe/London\n"
)


# ── a valid import still works end to end ──────────────────────────────────

def test_a_valid_import_still_works_end_to_end():
    resp, db = _run(GOOD)
    assert resp.total_rows == 1 and resp.imported == 1 and resp.failed == 0
    row = db.inserted[0]
    assert row["phone_number"] == "+447700900123"
    assert row["first_name"] == "Sian" and row["last_name"] == "Roberts"
    assert row["company_name"] == "BuildWright"
    assert row["timezone"] == "Europe/London"


# ── row-level validation failures name the row AND the field ───────────────

def test_a_bad_row_produces_an_error_naming_the_row_and_the_field():
    resp, _ = _run(
        "phone_number,email,timezone\n"
        "+447700900123,sian@buildwright.co.uk,Europe/London\n"
        "+447700900124,not-an-email,Europe/London\n"
    )
    bad = [e for e in resp.field_errors if e.field == "email"]
    assert bad, f"expected an email issue, got {resp.field_errors}"
    assert bad[0].row == 3, "row 1 is the header, so the second data row is 3"
    assert "email" in bad[0].error


def test_an_invalid_timezone_is_reported_and_not_written():
    resp, db = _run(
        "phone_number,first_name,timezone\n+447700900123,Sian,Mars/Base\n"
    )
    assert resp.imported == 1, "one bad cell must not lose the contact"
    assert [e.field for e in resp.field_errors] == ["timezone"]
    assert "timezone" not in db.inserted[0], "a rejected value must not be stored"
    assert db.inserted[0]["first_name"] == "Sian"


def test_an_unparseable_do_not_call_is_reported_rather_than_read_as_no():
    """A "maybe" silently coercing to False is the worst outcome here: it turns
    an ambiguous cell into permission to dial."""
    resp, db = _run("phone_number,do_not_call\n+447700900123,maybe\n")
    assert [e.field for e in resp.field_errors] == ["do_not_call"]
    assert "do_not_call" not in db.inserted[0]


def test_a_recognised_do_not_call_still_lands():
    _, db = _run("phone_number,do_not_call\n+447700900123,yes\n")
    assert db.inserted[0]["do_not_call"] is True


def test_a_missing_phone_is_a_row_failure_naming_the_row():
    resp, _ = _run("phone_number,first_name\n,Sian\n+447700900123,Rhys\n")
    assert resp.imported == 1
    assert resp.failed == 1
    assert resp.errors[0].row == 2
    assert resp.errors[0].field == "phone_number"


# ── unknown columns are preserved, never discarded ─────────────────────────

def test_an_unknown_column_is_preserved_into_custom_fields():
    """The registry docstring promises this: losing a column silently is worse
    than not understanding it."""
    _, db = _run(
        "phone_number,Lead Score,Postcode\n+447700900123,88,SW1A 1AA\n"
    )
    cf = db.inserted[0]["custom_fields"]
    assert cf["Lead Score"] == "88"
    assert cf["Postcode"] == "SW1A 1AA"


# ── the downloadable template comes from the registry ──────────────────────

def test_the_template_endpoint_serves_the_registry_csv():
    from app.domain.services.contact_fields import CONTACT_FIELDS

    resp = asyncio.run(contacts_ep.download_import_template(current_user=_User()))
    assert resp.media_type == "text/csv"
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.body.decode("utf-8")
    assert body.splitlines()[0].split(",") == [f.key for f in CONTACT_FIELDS]


def test_the_downloaded_template_imports_cleanly_through_this_endpoint():
    """End to end: download the template, upload it back. Zero errors, or the
    template is lying about what we accept."""
    from app.domain.services.contact_fields import csv_template_csv

    resp, db = _run(csv_template_csv())
    assert resp.total_rows == 1
    assert resp.imported == 1, (
        f"errors={resp.errors} field_errors={resp.field_errors}"
    )
    assert resp.field_errors == []
    assert db.inserted[0]["phone_number"].startswith("+")

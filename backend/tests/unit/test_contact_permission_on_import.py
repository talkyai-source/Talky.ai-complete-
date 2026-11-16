"""Customer permission confirmation on every route that adds contacts.

The readiness plan: store consent source, basis and date per import, and only
let outbound campaigns hold lists the customer confirms they may contact.
These tests pin behaviour (status codes, stored columns, what was NOT written),
not source text.
"""
from __future__ import annotations

import asyncio
import io
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api.v1.endpoints import contact_lists as cl_ep
from app.api.v1.endpoints import contacts as contacts_ep
from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.endpoints import salesforce as sf
from app.api.v1.schemas.campaigns import ContactCreate
from app.domain.services import contact_permission as cp
from app.domain.services.contact_permission import (
    CONTACT_PERMISSION_STATEMENT_VERSION,
    ContactPermissionError,
    validate_contact_permission,
)

TODAY = datetime.now(timezone.utc).date()

GOOD = {
    "basis": "express_consent",
    "source": "Website sign-up form, March 2026",
    "permission_date": "2026-03-14",
    "statement_version": CONTACT_PERMISSION_STATEMENT_VERSION,
}
OTHER = {**GOOD, "source": "Trade show badge scans, May 2026"}
REQUIRED_DETAIL = {
    "code": "contact_permission_required",
    "message": "Confirm you have permission to call these contacts before adding them.",
}
FORM_GOOD = dict(
    permission_basis=GOOD["basis"],
    permission_source=GOOD["source"],
    permission_date=GOOD["permission_date"],
    permission_statement_version=GOOD["statement_version"],
)
CSV = "phone_number,first_name\n+442079460123,Sian\n"


@pytest.fixture(autouse=True)
def _requirement_on(monkeypatch):
    monkeypatch.delenv("CONTACT_PERMISSION_REQUIRED", raising=False)


# ── in-memory fake DB ──────────────────────────────────────────────────────
class _Resp:
    def __init__(self, data, count=None, error=None):
        self.data = data
        self.error = error
        self.count = count if count is not None else (len(data) if isinstance(data, list) else 0)


class _Query:
    def __init__(self, db, table):
        self._db, self._table = db, table
        self._filters: list = []
        self._op = "select"
        self._payload = None

    def select(self, *_a, **_k):
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
        fail = self._db.fail.get((self._table, self._op))
        if fail:
            return _Resp(None, error=fail)
        rows = self._db.tables.setdefault(self._table, [])
        if self._op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            return _Resp(matched, count=len(matched))
        if self._op == "insert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            for it in items:
                rows.append(dict(it))
            self._db.writes.append((self._table, "insert", items))
            return _Resp([dict(i) for i in items])
        changed = []
        for r in rows:
            if self._match(r):
                r.update(self._payload)
                changed.append(dict(r))
        self._db.writes.append((self._table, "update", self._payload))
        return _Resp(changed)


class FakeDB:
    def __init__(self, *, user_profile_id="u1"):
        self.tables = {
            "leads": [],
            "contact_lists": [],
            "campaigns": [
                {"id": "c1", "name": "Roofing", "tenant_id": "t1", "script_config": {},
                 "direction": "outbound", "status": "draft"},
            ],
        }
        self.writes: list = []
        self.fail: dict = {}

    def table(self, name):
        return _Query(self, name)

    @property
    def leads(self):
        return self.tables["leads"]

    @property
    def lists(self):
        return self.tables["contact_lists"]

    def table_writes(self, table):
        return [w for w in self.writes if w[0] == table]


class _User:
    tenant_id = "t1"
    id = "u1"
    role = "owner"


def _upload(text=CSV, name="contacts.csv"):
    return UploadFile(file=io.BytesIO(text.encode("utf-8")), filename=name)


class _NeverRead:
    filename = "contacts.csv"

    async def read(self):
        raise AssertionError("the file must not be read before permission is checked")


def _detail(exc: pytest.ExceptionInfo):
    return exc.value.detail


def _run(coro):
    return asyncio.run(coro)


def _upload_call(db, *, file=None, **perm):
    return contacts_ep.upload_campaign_contacts(
        campaign_id="c1", file=file or _upload(), skip_duplicates=True,
        current_user=_User(), db_client=db, default_country=None, **perm,
    )


def _paste_call(db, permission=None, text="+442079460123"):
    return contacts_ep.paste_campaign_contacts(
        campaign_id="c1",
        body=contacts_ep.BulkPasteRequest(text=text, permission=permission),
        current_user=_User(), db_client=db,
    )


def _bulk_call(db, *, file=None, campaign_id="c1", **perm):
    return contacts_ep.bulk_import_contacts(
        file=file or _upload(), campaign_id=campaign_id, current_user=_User(),
        db_client=db, default_country=None, **perm,
    )


def _add_call(db, permission=None, phone="+442079460123"):
    return campaigns_ep.add_contact_to_campaign(
        campaign_id="c1",
        contact=ContactCreate(phone_number=phone, permission=permission),
        current_user=_User(), supabase=db,
    )


def _assert_consent_columns(row, expected=GOOD, user_id="u1"):
    assert row["consent_basis"] == expected["basis"]
    assert row["consent_source"] == expected["source"].strip()
    assert str(row["consent_permission_date"]) == expected["permission_date"]
    assert row["consent_statement_version"] == CONTACT_PERMISSION_STATEMENT_VERSION
    assert row["consent_confirmed_by"] == user_id
    assert row["consent_confirmed_at"]


# ── the validator ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("basis", ["express_consent", "existing_customer",
                                   "made_an_enquiry", "other_documented"])
def test_each_customer_basis_is_accepted(basis):
    out = validate_contact_permission({**GOOD, "basis": basis})
    assert out.basis == basis
    assert out.permission_date == date(2026, 3, 14)
    assert out.statement_version == CONTACT_PERMISSION_STATEMENT_VERSION


def test_pre_existing_unverified_is_rejected_for_a_customer():
    with pytest.raises(ContactPermissionError) as exc:
        validate_contact_permission({**GOOD, "basis": "pre_existing_unverified"})
    assert exc.value.code == "contact_permission_invalid" and exc.value.field == "basis"


def test_unknown_or_missing_basis_names_the_basis_field():
    for bad in ({**GOOD, "basis": "because"}, {k: v for k, v in GOOD.items() if k != "basis"}):
        with pytest.raises(ContactPermissionError) as exc:
            validate_contact_permission(bad)
        assert exc.value.field == "basis"


def test_source_length_limits_after_trimming_and_stored_trimmed():
    for bad in ("abcd", "  abcd  ", "x" * 501, "", "     ", None, 12345):
        with pytest.raises(ContactPermissionError) as exc:
            validate_contact_permission({**GOOD, "source": bad})
        assert exc.value.field == "source", bad
    assert validate_contact_permission({**GOOD, "source": "abcde"}).source == "abcde"
    assert len(validate_contact_permission({**GOOD, "source": "x" * 500}).source) == 500
    assert validate_contact_permission({**GOOD, "source": "  Website form  "}).source == "Website form"


def test_permission_date_today_ok_future_rejected_garbage_rejected():
    today = date(2026, 11, 16)
    assert validate_contact_permission({**GOOD, "permission_date": "2026-11-16"}, today=today)
    for bad in ("2026-11-17", "not a date", "", None, "16/11/2026", "20261116", 5):
        with pytest.raises(ContactPermissionError) as exc:
            validate_contact_permission({**GOOD, "permission_date": bad}, today=today)
        assert exc.value.field == "permission_date", bad


def test_default_today_is_utc_today():
    ok = (datetime.now(timezone.utc).date()).isoformat()
    assert validate_contact_permission({**GOOD, "permission_date": ok})
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    with pytest.raises(ContactPermissionError):
        validate_contact_permission({**GOOD, "permission_date": tomorrow})


def test_wrong_statement_version_is_rejected():
    for bad in ("2026-11-v2", "", None):
        with pytest.raises(ContactPermissionError) as exc:
            validate_contact_permission({**GOOD, "statement_version": bad})
        assert exc.value.field == "statement_version"


def test_non_object_is_invalid_not_a_crash():
    with pytest.raises(ContactPermissionError) as exc:
        validate_contact_permission("express_consent")
    assert exc.value.code == "contact_permission_invalid"


def test_switch_values():
    import os
    for off in ("0", "false", "No", "OFF", "False"):
        os.environ["CONTACT_PERMISSION_REQUIRED"] = off
        assert cp.contact_permission_required() is False
    for on in ("1", "true", "yes", "", "maybe"):
        os.environ["CONTACT_PERMISSION_REQUIRED"] = on
        assert cp.contact_permission_required() is True
    del os.environ["CONTACT_PERMISSION_REQUIRED"]
    assert cp.contact_permission_required() is True


# ── CSV upload ─────────────────────────────────────────────────────────────
def test_upload_without_confirmation_is_422_and_nothing_written_or_read():
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_upload_call(db, file=_NeverRead()))
    assert exc.value.status_code == 422 and _detail(exc) == REQUIRED_DETAIL
    assert db.writes == []


@pytest.mark.parametrize("field,form", [
    ("basis", {"permission_basis": "pre_existing_unverified"}),
    ("source", {"permission_source": "abc"}),
    ("permission_date", {"permission_date": "2999-01-01"}),
    ("statement_version", {"permission_statement_version": "old"}),
])
def test_upload_invalid_confirmation_names_the_field(field, form):
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_upload_call(db, file=_NeverRead(), **{**FORM_GOOD, **form}))
    d = _detail(exc)
    assert exc.value.status_code == 422
    assert d["code"] == "contact_permission_invalid" and d["field"] == field and d["message"]
    assert db.writes == []


def test_upload_partial_form_is_invalid_not_missing():
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_upload_call(db, file=_NeverRead(), permission_basis="express_consent"))
    assert _detail(exc)["code"] == "contact_permission_invalid"


def test_upload_valid_confirmation_stores_six_columns_and_tags_leads():
    db = FakeDB()
    resp = _run(_upload_call(db, **FORM_GOOD))
    assert resp.imported == 1
    assert len(db.lists) == 1
    _assert_consent_columns(db.lists[0])
    assert db.leads[0]["list_id"] == db.lists[0]["id"] == resp.list_id


def test_upload_switch_off_without_confirmation_behaves_as_before(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "off")
    db = FakeDB()
    resp = _run(_upload_call(db))
    assert resp.imported == 1
    assert db.lists[0].get("consent_confirmed_at") is None
    assert db.lists[0].get("consent_basis") is None


def test_upload_switch_off_still_stores_a_confirmation_that_is_sent(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "0")
    db = FakeDB()
    _run(_upload_call(db, **FORM_GOOD))
    _assert_consent_columns(db.lists[0])


def test_upload_switch_off_still_validates_a_bad_confirmation(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "0")
    with pytest.raises(HTTPException) as exc:
        _run(_upload_call(FakeDB(), **{**FORM_GOOD, "permission_source": "x"}))
    assert _detail(exc)["field"] == "source"


def test_upload_list_failure_with_confirmation_fails_import_and_writes_no_lead():
    db = FakeDB()
    db.fail[("contact_lists", "insert")] = "boom"
    with pytest.raises(HTTPException) as exc:
        _run(_upload_call(db, **FORM_GOOD))
    assert exc.value.status_code == 503
    assert db.leads == [] and db.table_writes("leads") == []


def test_upload_list_lookup_failure_with_confirmation_fails_import():
    db = FakeDB()
    db.fail[("contact_lists", "select")] = "boom"
    with pytest.raises(HTTPException) as exc:
        _run(_upload_call(db, **FORM_GOOD))
    assert exc.value.status_code == 503 and db.leads == []


def test_upload_list_failure_without_confirmation_still_falls_back_to_ungrouped(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "false")
    db = FakeDB()
    db.fail[("contact_lists", "insert")] = "boom"
    resp = _run(_upload_call(db))
    assert resp.imported == 1 and resp.list_id is None
    assert "list_id" not in db.leads[0]


# ── paste ──────────────────────────────────────────────────────────────────
def test_paste_without_confirmation_is_422_nothing_written_not_parsed(monkeypatch):
    def _boom(_text):
        raise AssertionError("paste must not be parsed before permission is checked")
    monkeypatch.setattr("app.domain.services.dialer.bulk_ingest.parse_pasted_numbers", _boom)
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_paste_call(db))
    assert exc.value.status_code == 422 and _detail(exc) == REQUIRED_DETAIL
    assert db.writes == []


def test_paste_invalid_confirmation_names_the_field():
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_paste_call(db, {**GOOD, "permission_date": "2999-01-01"}))
    d = _detail(exc)
    assert d["code"] == "contact_permission_invalid" and d["field"] == "permission_date"
    assert db.writes == []


def test_paste_valid_confirmation_stores_columns_and_tags_leads():
    db = FakeDB()
    resp = _run(_paste_call(db, GOOD))
    assert resp.imported == 1
    _assert_consent_columns(db.lists[0])
    assert db.leads[0]["list_id"] == db.lists[0]["id"]


def test_paste_switch_off_no_confirmation_as_before(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "no")
    db = FakeDB()
    resp = _run(_paste_call(db))
    assert resp.imported == 1 and db.lists[0].get("consent_basis") is None


def test_paste_switch_off_with_confirmation_still_stores_it(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "no")
    db = FakeDB()
    _run(_paste_call(db, GOOD))
    _assert_consent_columns(db.lists[0])


def test_paste_list_failure_with_confirmation_fails_and_writes_no_lead():
    db = FakeDB()
    db.fail[("contact_lists", "insert")] = "boom"
    with pytest.raises(HTTPException) as exc:
        _run(_paste_call(db, GOOD))
    assert exc.value.status_code == 503 and db.leads == []


# ── legacy /contacts/bulk ──────────────────────────────────────────────────
def test_bulk_with_campaign_without_confirmation_is_422_nothing_written():
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_bulk_call(db, file=_NeverRead()))
    assert exc.value.status_code == 422 and _detail(exc) == REQUIRED_DETAIL
    assert db.writes == []


def test_bulk_invalid_confirmation_names_the_field():
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_bulk_call(db, file=_NeverRead(), **{**FORM_GOOD, "permission_basis": "nope"}))
    assert _detail(exc)["field"] == "basis" and db.writes == []


def test_bulk_valid_confirmation_stores_columns_and_tags_leads():
    db = FakeDB()
    resp = _run(_bulk_call(db, **FORM_GOOD))
    assert resp.imported == 1
    _assert_consent_columns(db.lists[0])
    assert db.leads[0]["list_id"] == db.lists[0]["id"]


def test_bulk_switch_off_no_confirmation_as_before(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "off")
    db = FakeDB()
    resp = _run(_bulk_call(db))
    assert resp.imported == 1
    assert db.lists == [] and "list_id" not in db.leads[0]


def test_bulk_list_failure_with_confirmation_fails_and_writes_no_lead():
    db = FakeDB()
    db.fail[("contact_lists", "insert")] = "boom"
    with pytest.raises(HTTPException) as exc:
        _run(_bulk_call(db, **FORM_GOOD))
    assert exc.value.status_code == 503 and db.leads == []


# ── single add (campaigns.py) ──────────────────────────────────────────────
def test_single_add_without_confirmation_is_422_nothing_written():
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_add_call(db))
    assert exc.value.status_code == 422 and _detail(exc) == REQUIRED_DETAIL
    assert db.writes == []


def test_single_add_invalid_confirmation_names_the_field():
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_add_call(db, {**GOOD, "source": "no"}))
    assert _detail(exc)["field"] == "source" and db.writes == []


def test_single_add_valid_goes_into_added_one_at_a_time_list():
    db = FakeDB()
    out = _run(_add_call(db, GOOD))
    assert out["message"] == "Contact added successfully"
    assert len(db.lists) == 1
    assert db.lists[0]["name"] == "Added one at a time" and db.lists[0]["source"] == "manual"
    _assert_consent_columns(db.lists[0])
    assert db.leads[0]["list_id"] == db.lists[0]["id"]


def test_single_add_same_confirmation_reuses_the_list():
    db = FakeDB()
    _run(_add_call(db, GOOD))
    _run(_add_call(db, GOOD, phone="+442079460124"))
    assert len(db.lists) == 1
    assert {l["list_id"] for l in db.leads} == {db.lists[0]["id"]}


def test_single_add_switch_off_no_confirmation_as_before(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "off")
    db = FakeDB()
    _run(_add_call(db))
    assert db.lists == [] and "list_id" not in db.leads[0]


def test_single_add_list_failure_with_confirmation_fails_and_writes_no_lead():
    db = FakeDB()
    db.fail[("contact_lists", "insert")] = "boom"
    with pytest.raises(HTTPException) as exc:
        _run(_add_call(db, GOOD))
    assert exc.value.status_code == 503 and db.leads == []


# ── reuse rule in create_contact_list ──────────────────────────────────────
def _perm(raw=GOOD):
    return validate_contact_permission(raw)


def _make(db, name="Leads.csv", raw=GOOD):
    return cl_ep.create_contact_list(
        db, campaign_id="c1", tenant_id="t1", name=name, source="csv",
        permission=_perm(raw) if raw else None, confirmed_by="u1",
    )


def test_same_name_same_confirmation_reuses_the_list():
    db = FakeDB()
    first = _make(db)
    second = _make(db)
    assert first == second and len(db.lists) == 1


def test_same_name_different_confirmation_creates_name_2_and_leaves_old_untouched():
    db = FakeDB()
    first = _make(db)
    before = dict(db.lists[0])
    second = _make(db, raw=OTHER)
    assert second != first and len(db.lists) == 2
    assert db.lists[1]["name"] == "Leads.csv (2)"
    assert db.lists[0] == before
    _assert_consent_columns(db.lists[1], OTHER)


def test_same_name_different_date_is_a_different_confirmation():
    db = FakeDB()
    _make(db)
    _make(db, raw={**GOOD, "permission_date": "2026-03-15"})
    assert [l["name"] for l in db.lists] == ["Leads.csv", "Leads.csv (2)"]


def test_different_basis_is_a_different_confirmation():
    db = FakeDB()
    _make(db)
    _make(db, raw={**GOOD, "basis": "existing_customer"})
    assert len(db.lists) == 2


def test_existing_unconfirmed_list_is_not_reused_or_overwritten():
    db = FakeDB()
    db.lists.append({"id": "old", "campaign_id": "c1", "tenant_id": "t1",
                     "name": "Leads.csv", "source": "csv", "is_active": True})
    new_id = _make(db)
    assert new_id != "old" and len(db.lists) == 2
    assert db.lists[0] == {"id": "old", "campaign_id": "c1", "tenant_id": "t1",
                           "name": "Leads.csv", "source": "csv", "is_active": True}
    assert db.lists[1]["name"] == "Leads.csv (2)"


def test_first_free_number_is_used_and_a_matching_numbered_list_is_reused():
    db = FakeDB()
    _make(db)                      # Leads.csv   (GOOD)
    second = _make(db, raw=OTHER)  # Leads.csv (2)
    third = _make(db, raw={**GOOD, "source": "A third documented source"})  # (3)
    assert db.lists[2]["name"] == "Leads.csv (3)"
    assert _make(db, raw=OTHER) == second
    assert _make(db, raw={**GOOD, "source": "A third documented source"}) == third
    assert len(db.lists) == 3


def test_other_tenant_list_with_same_name_is_ignored():
    db = FakeDB()
    db.lists.append({"id": "x", "campaign_id": "c1", "tenant_id": "t2",
                     "name": "Leads.csv", "source": "csv", "is_active": True,
                     "consent_basis": GOOD["basis"], "consent_source": GOOD["source"],
                     "consent_permission_date": GOOD["permission_date"]})
    assert _make(db) != "x"
    assert db.lists[1]["name"] == "Leads.csv"


def test_without_confirmation_reuse_is_unchanged():
    db = FakeDB()
    a = _make(db, raw=None)
    b = _make(db, raw=None)
    assert a == b and len(db.lists) == 1
    assert db.lists[0].get("consent_confirmed_at") is None


def test_list_failure_without_confirmation_returns_none_with_confirmation_raises():
    db = FakeDB()
    db.fail[("contact_lists", "insert")] = "boom"
    assert _make(db, raw=None) is None
    with pytest.raises(HTTPException) as exc:
        _make(db)
    assert exc.value.status_code == 503


# ── Salesforce ─────────────────────────────────────────────────────────────
def _sf_setup(monkeypatch, people=None):
    people = people if people is not None else [
        {"id": "00Q1", "phone": "+442079460123", "first_name": "Pat", "last_name": "Lee"}
    ]

    class _Conn:
        async def list_people(self, *_a, **_k):
            return people

    async def _resolve(*_a, **_k):
        return _Conn(), "cid", "salesforce"

    monkeypatch.setattr(sf, "resolve_active_connector", _resolve)


def _sf_call(db, permission=None):
    return sf.import_salesforce_people(
        body=sf.ImportRequest(campaign_id="c1", permission=permission),
        current_user=_User(), db_client=db,
    )


def test_salesforce_import_without_confirmation_is_422_nothing_written(monkeypatch):
    _sf_setup(monkeypatch)
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_sf_call(db))
    assert exc.value.status_code == 422 and _detail(exc) == REQUIRED_DETAIL
    assert db.writes == []


def test_salesforce_import_invalid_confirmation_names_the_field(monkeypatch):
    _sf_setup(monkeypatch)
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        _run(_sf_call(db, {**GOOD, "statement_version": "x"}))
    assert _detail(exc)["field"] == "statement_version" and db.writes == []


def test_salesforce_import_valid_confirmation_stores_columns_and_tags_leads(monkeypatch):
    _sf_setup(monkeypatch)
    db = FakeDB()
    out = _run(_sf_call(db, GOOD))
    assert out.imported == 1
    _assert_consent_columns(db.lists[0])
    assert db.leads[0]["list_id"] == db.lists[0]["id"] == out.list_id


def test_salesforce_import_switch_off_no_confirmation_as_before(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "off")
    _sf_setup(monkeypatch)
    db = FakeDB()
    out = _run(_sf_call(db))
    assert out.imported == 1 and db.lists[0].get("consent_basis") is None


def test_salesforce_import_list_failure_with_confirmation_fails_no_lead(monkeypatch):
    _sf_setup(monkeypatch)
    db = FakeDB()
    db.fail[("contact_lists", "insert")] = "boom"
    with pytest.raises(HTTPException) as exc:
        _run(_sf_call(db, GOOD))
    assert exc.value.status_code == 503 and db.leads == []


@pytest.mark.parametrize("switch", [None, "off"])
def test_salesforce_callback_records_the_system_confirmation(monkeypatch, switch):
    if switch:
        monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", switch)
    db = FakeDB()
    monkeypatch.setattr(
        sf, "require_owned_outbound_campaign",
        lambda db_, cid, *, tenant_id, extra_columns=(): {
            "id": cid, "tenant_id": tenant_id, "direction": "outbound",
            "status": "paused", "name": "Callbacks", "script_config": {}},
    )
    out = _run(sf._enqueue_callback(
        db, tenant_id="t1", config={"callback_campaign_id": "c1"},
        payload=sf.CallbackRequest(phone="+442079460123"),
    ))
    assert out.accepted
    row = db.lists[0]
    assert row["name"] == sf.CALLBACK_LIST_NAME
    assert row["consent_basis"] == "made_an_enquiry"
    assert row["consent_source"] == "Callback request received through the Salesforce callback endpoint"
    assert str(row["consent_permission_date"]) == TODAY.isoformat()
    assert row["consent_statement_version"] == "system-callback-v1"
    assert row["consent_confirmed_by"] is None
    assert row["consent_confirmed_at"]
    assert db.leads[0]["list_id"] == row["id"]


# ── assistant tool ─────────────────────────────────────────────────────────
def _assistant_add(db, confirm=True):
    from app.infrastructure.assistant.tools import campaign_admin
    return asyncio.run(campaign_admin.manage_lead(
        tenant_id="t1", db_client=db, campaign_id="c1", action="add",
        phone_number="+442079460123", name="Pat Lee", confirm=confirm,
    ))


@pytest.mark.parametrize("confirm", [True, False])
def test_assistant_adds_nothing_while_the_requirement_is_on(confirm):
    db = FakeDB()
    out = _assistant_add(db, confirm=confirm)
    assert out == {"error": "Add contacts on the Contacts page, where you confirm you have permission to call them."}
    assert db.writes == []


def test_assistant_add_works_as_before_with_the_requirement_off(monkeypatch):
    monkeypatch.setenv("CONTACT_PERMISSION_REQUIRED", "off")
    db = FakeDB()
    out = _assistant_add(db)
    assert out.get("applied") is True
    assert len(db.leads) == 1 and db.lists == []


# ── list responses and the new permission route ────────────────────────────
def _list_row(**extra):
    row = {"id": "L1", "campaign_id": "c1", "tenant_id": "t1", "name": "A", "source": "csv",
           "is_active": True, "created_at": "2026-11-01T00:00:00+00:00"}
    row.update(extra)
    return row


def test_list_endpoint_returns_permission_object():
    db = FakeDB()
    db.lists.append(_list_row(
        consent_confirmed_at="2026-11-02T10:00:00+00:00", consent_basis="existing_customer",
        consent_source="CRM export", consent_permission_date="2026-10-01"))
    db.lists.append(_list_row(id="L2", name="B"))
    out = _run(cl_ep.list_contact_lists("c1", current_user=_User(), db_client=db))
    by_id = {o.id: o for o in out}
    assert by_id["L1"].permission.model_dump() == {
        "confirmed": True, "basis": "existing_customer", "source": "CRM export",
        "permission_date": "2026-10-01", "confirmed_at": "2026-11-02T10:00:00+00:00"}
    assert by_id["L2"].permission.model_dump() == {
        "confirmed": False, "basis": None, "source": None,
        "permission_date": None, "confirmed_at": None}


def _post_permission(db, body, list_id="L1", user=None):
    return cl_ep.record_contact_list_permission(
        list_id=list_id, body=body, current_user=user or _User(), db_client=db)


def test_permission_route_records_on_an_unconfirmed_list():
    db = FakeDB()
    db.lists.append(_list_row())
    out = _run(_post_permission(db, GOOD))
    _assert_consent_columns(db.lists[0])
    assert out.permission.confirmed is True and out.permission.basis == "express_consent"
    assert out.id == "L1"


def test_permission_route_409_when_already_recorded_and_row_untouched():
    db = FakeDB()
    db.lists.append(_list_row())
    _run(_post_permission(db, GOOD))
    before = dict(db.lists[0])
    with pytest.raises(HTTPException) as exc:
        _run(_post_permission(db, OTHER))
    assert exc.value.status_code == 409
    assert _detail(exc) == {"code": "contact_permission_already_recorded",
                            "message": "Permission is already recorded for this list."}
    assert db.lists[0] == before


def test_permission_route_404_for_another_tenants_list_and_unknown_list():
    db = FakeDB()
    db.lists.append(_list_row(tenant_id="t2"))
    for lid in ("L1", "nope"):
        with pytest.raises(HTTPException) as exc:
            _run(_post_permission(db, GOOD, list_id=lid))
        assert exc.value.status_code == 404
    assert "consent_basis" not in db.lists[0]


def test_permission_route_422_for_missing_or_invalid_body():
    db = FakeDB()
    db.lists.append(_list_row())
    with pytest.raises(HTTPException) as exc:
        _run(_post_permission(db, None))
    assert exc.value.status_code == 422 and _detail(exc)["code"] == "contact_permission_required"
    with pytest.raises(HTTPException) as exc:
        _run(_post_permission(db, {**GOOD, "basis": "pre_existing_unverified"}))
    assert _detail(exc)["field"] == "basis"
    assert "consent_basis" not in db.lists[0]


def test_permission_route_is_registered_with_the_campaign_update_permission():
    routes = [r for r in cl_ep.router.routes
              if getattr(r, "path", "") == "/contact-lists/{list_id}/permission"]
    assert routes and "POST" in routes[0].methods

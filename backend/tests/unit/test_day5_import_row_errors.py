"""D5-13: import row errors are plain sentences with a stable code, and the
list says plainly when it is partial.

Covers the three contact import routes (CSV upload, paste, legacy bulk) and the
shared ingest core. Behaviour only: every assertion is on what a route returns
or logs.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api.v1.endpoints import contacts as contacts_ep
from app.domain.services.dialer import bulk_ingest
from app.domain.services.dialer.bulk_ingest import (
    IMPORT_ERRORS_CAP,
    LeadRecord,
    ingest_lead_records,
)
from tests.unit.test_contact_import_validation import (
    FORM_PERMISSION,
    FakeDB,
    _Query,
    _User,
)

PHONE = "+442079460123"

# Nothing a customer reads may carry a code-style field name, an underscore, or
# exception vocabulary.
FORBIDDEN = re.compile(
    r"_|phone_number|Exception|Error\b|Error:|Traceback|\b[A-Z][A-Za-z]+Error\b|None\b"
)


def _assert_plain(text: str) -> None:
    assert text and not FORBIDDEN.search(text), f"not plain: {text!r}"
    assert text.endswith("."), f"not a sentence: {text!r}"


class _FailQuery(_Query):
    def execute(self):
        if self._op == "insert" and self._table == "leads":
            raise RuntimeError(self._db.fail_text)
        return super().execute()


class FailingDB(FakeDB):
    """Every lead insert fails with an exception that names a full phone number."""

    fail_text = f"duplicate key value violates unique constraint (phone_number)=({PHONE})"

    def table(self, name):
        return _FailQuery(self, name)


def _upload(text: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(text.encode("utf-8")), filename="contacts.csv")


def _csv_call(text, db=None):
    db = db or FakeDB()
    return asyncio.run(contacts_ep.upload_campaign_contacts(
        campaign_id="c1", file=_upload(text), skip_duplicates=True,
        current_user=_User(), db_client=db, **FORM_PERMISSION,
    ))


def _paste_call(text, db=None, country="GB"):
    db = db or FakeDB()
    return asyncio.run(contacts_ep.paste_campaign_contacts(
        campaign_id="c1",
        body=contacts_ep.BulkPasteRequest(
            text=text, default_country=country,
            permission={
                "basis": "express_consent",
                "source": "Customer sign-up form, March 2026",
                "permission_date": "2026-03-14",
                "statement_version": "2026-11-v1",
            },
        ),
        current_user=_User(), db_client=db,
    ))


def _bulk_call(text, db=None):
    db = db or FakeDB()
    return asyncio.run(contacts_ep.bulk_import_contacts(
        file=_upload(text), campaign_id="c1", current_user=_User(),
        db_client=db, default_country=None, **FORM_PERMISSION,
    ))


def _numbers(n):
    return [f"+4420794{60000 + i:05d}" for i in range(n)]


def _paste_text(n):
    return "\n".join(_numbers(n))


def _bulk_text(n):
    return "phone_number,first_name\n" + "".join(f"{p},A\n" for p in _numbers(n))


def _ragged_csv(n_bad, n_good=0):
    rows = "".join(f"{p},A,extra,extra2\n" for p in _numbers(n_bad))
    rows += "".join(f"{p},B\n" for p in _numbers(n_good + 1000)[1000:])
    return "phone_number,first_name\n" + rows


# ── the shared ingest core ─────────────────────────────────────────────────

def _normalize(raw):
    if raw == "country":
        raise ValueError("Country or region is required for a phone number without a '+' country code")
    if raw == "bad":
        raise ValueError("Invalid phone number")
    return raw


def test_ingest_gives_each_phone_problem_a_plain_sentence_and_a_code():
    res = ingest_lead_records(
        FakeDB(), campaign_id="c1", tenant_id="t1", normalize=_normalize,
        records=[
            LeadRecord(phone_raw="", source_row=2),
            LeadRecord(phone_raw="bad", source_row=3),
            LeadRecord(phone_raw="country", source_row=4),
        ],
    )
    by_row = {e.row: e for e in res.errors}
    assert by_row[2].code == "phone_missing"
    assert by_row[2].error == "This row has no phone number."
    assert by_row[3].code == "phone_invalid"
    assert by_row[3].error.startswith("This phone number is not valid for the country chosen.")
    assert "+442071234567" in by_row[3].error
    assert by_row[4].code == "phone_country_required"
    assert "country code" in by_row[4].error
    for e in res.errors:
        _assert_plain(e.error)
    assert res.invalid == 3


def test_ingest_save_failure_is_plain_and_keeps_the_exception_out_of_the_text():
    res = ingest_lead_records(
        FailingDB(), campaign_id="c1", tenant_id="t1", normalize=_normalize,
        records=[LeadRecord(phone_raw=PHONE, source_row=2)],
    )
    assert [e.code for e in res.errors] == ["save_failed"]
    _assert_plain(res.errors[0].error)
    assert "duplicate key" not in res.errors[0].error
    assert PHONE not in res.errors[0].error


def test_ingest_revive_failure_is_plain():
    class _ReviveFail(FakeDB):
        def table(self, name):
            q = super().table(name)
            orig = q.execute

            def run():
                if q._op == "update" and name == "leads":
                    raise RuntimeError("secret revive detail")
                return orig()
            q.execute = run
            return q

    db = _ReviveFail()
    db.tables["leads"].append({"id": "L1", "campaign_id": "c1", "tenant_id": "t1",
                               "phone_number": PHONE, "status": "deleted"})
    res = ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1", normalize=_normalize,
        records=[LeadRecord(phone_raw=PHONE, source_row=2)],
    )
    assert [e.code for e in res.errors] == ["update_failed"]
    _assert_plain(res.errors[0].error)
    assert "secret" not in res.errors[0].error


def test_duplicates_are_still_counted_not_failed():
    res = ingest_lead_records(
        FakeDB(), campaign_id="c1", tenant_id="t1", normalize=_normalize,
        records=[LeadRecord(phone_raw=PHONE, source_row=2),
                 LeadRecord(phone_raw=PHONE, source_row=3)],
    )
    assert (res.imported, res.duplicates_skipped, res.errors) == (1, 1, [])


# ── CSV upload: row errors, field errors, 422 list ─────────────────────────

def test_csv_every_kind_of_bad_cell_is_plain_and_coded():
    text = (
        "phone_number,email,timezone,do_not_call,preferred_contact_method,mobile_number,business_number,job_title\n"
        f"{PHONE},not-an-email,Mars/Base,maybe,carrier pigeon,12,xx,{'x' * 300}\n"
        f"{PHONE},a@b.co,Europe/London,no,phone,+442079460124,+442079460125,Boss,EXTRA\n"
    )
    resp = _csv_call(text)
    codes = {(e.field, e.code) for e in resp.field_errors}
    assert ("email", "email_invalid") in codes
    assert ("timezone", "timezone_invalid") in codes
    assert ("do_not_call", "do_not_call_invalid") in codes
    assert ("preferred_contact_method", "contact_method_invalid") in codes
    assert ("mobile_number", "mobile_number_invalid") in codes
    assert ("business_number", "business_number_invalid") in codes
    assert ("job_title", "value_too_long") in codes
    ragged = [e for e in resp.errors if e.code == "row_too_many_cells"]
    assert [e.row for e in ragged] == [3]
    for e in resp.field_errors + resp.errors:
        _assert_plain(e.error)
    assert resp.errors_total == resp.failed == 1


@pytest.mark.parametrize("text,code,needle", [
    (f"phone_number,first_name\n,Sian\n{PHONE},Rhys\n", "phone_missing", "no phone number"),
    ("phone_number,first_name\n+12345,Sian\n", "phone_invalid", "not valid for the country chosen"),
    ("phone_number,first_name\n12345,Sian\n", "phone_country_required", "country code"),
    ("phone_number,first_name\n07400 123456,Sian\n", "phone_country_required", "country code"),
])
def test_csv_phone_problems_are_refused_with_a_plain_sentence_and_code(text, code, needle):
    with pytest.raises(HTTPException) as caught:
        _csv_call(text)
    assert caught.value.status_code == 422
    first = caught.value.detail[0]
    assert first["field"] == "phone_number"
    assert first["code"] == code
    assert needle in first["message"]
    _assert_plain(first["message"])


def test_csv_422_list_is_capped_and_says_so():
    text = "phone_number\n" + "+12345\n" * 120
    with pytest.raises(HTTPException) as caught:
        _csv_call(text)
    detail = caught.value.detail
    assert len(detail) == IMPORT_ERRORS_CAP + 1
    assert all(d["code"] == "phone_invalid" for d in detail[:-1])
    tail = detail[-1]
    assert tail["code"] == "more_errors" and tail["row"] is None
    assert "20 more" in tail["message"]
    _assert_plain(tail["message"])


def test_csv_422_with_few_errors_has_no_summary_entry():
    with pytest.raises(HTTPException) as caught:
        _csv_call("phone_number\n+12345\n+12346\n+12347\n")
    assert [d["code"] for d in caught.value.detail] == ["phone_invalid"] * 3


def test_csv_unsupported_country_is_a_plain_sentence():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(contacts_ep.upload_campaign_contacts(
            campaign_id="c1", file=_upload(f"phone_number\n{PHONE}\n"), skip_duplicates=True,
            current_user=_User(), db_client=FakeDB(), default_country="ZZ", **FORM_PERMISSION,
        ))
    d = caught.value.detail[0]
    assert d["code"] == "country_invalid"
    _assert_plain(d["message"])


# ── truncation honesty, all three routes ───────────────────────────────────

def test_csv_120_bad_rows_report_total_and_truncation():
    resp = _csv_call(_ragged_csv(120))
    assert resp.failed == 120
    assert resp.errors_total == 120
    assert resp.errors_truncated is True
    assert len(resp.errors) == IMPORT_ERRORS_CAP == 100


def test_csv_3_bad_rows_are_not_truncated():
    resp = _csv_call(_ragged_csv(3, n_good=2))
    assert (resp.failed, resp.errors_total, resp.errors_truncated, len(resp.errors)) == (3, 3, False, 3)
    assert resp.imported == 2


def test_csv_clean_file_has_zero_errors():
    resp = _csv_call(_ragged_csv(0, n_good=4))
    assert (resp.imported, resp.failed, resp.errors_total, resp.errors_truncated) == (4, 0, 0, False)
    assert resp.errors == []


def test_csv_field_errors_are_honest_about_their_own_cap():
    text = "phone_number,email\n" + "".join(f"{p},bad\n" for p in _numbers(210))
    resp = _csv_call(text)
    assert resp.imported == 210
    assert resp.field_errors_total == 210 and resp.field_errors_truncated is True
    assert len(resp.field_errors) == 200


def test_paste_120_failed_saves_report_total_and_truncation():
    resp = _paste_call(_paste_text(120), FailingDB())
    assert resp.failed == 120 and resp.imported == 0
    assert resp.errors_total == 120 and resp.errors_truncated is True
    assert len(resp.errors) == IMPORT_ERRORS_CAP
    assert {e.code for e in resp.errors} == {"save_failed"}
    for e in resp.errors:
        _assert_plain(e.error)


def test_paste_3_failures_not_truncated_and_clean_paste_is_clean():
    resp = _paste_call(_paste_text(3), FailingDB())
    assert (resp.failed, resp.errors_total, resp.errors_truncated, len(resp.errors)) == (3, 3, False, 3)
    clean = _paste_call(_paste_text(3))
    assert (clean.imported, clean.failed, clean.errors_total, clean.errors_truncated) == (3, 0, 0, False)


def test_paste_bad_numbers_get_plain_422_entries():
    with pytest.raises(HTTPException) as caught:
        _paste_call(f"{PHONE}\n+12345\n")
    assert caught.value.detail == [{
        "row": 2, "field": "phone_number", "code": "phone_invalid",
        "message": caught.value.detail[0]["message"],
    }]
    _assert_plain(caught.value.detail[0]["message"])


def test_bulk_120_failed_rows_report_total_and_truncation_with_the_shared_cap():
    resp = _bulk_call(_bulk_text(120), FailingDB())
    assert resp.failed == 120 and resp.imported == 0
    assert resp.errors_total == 120 and resp.errors_truncated is True
    assert len(resp.errors) == IMPORT_ERRORS_CAP
    assert {e.code for e in resp.errors} == {"save_failed"}
    for e in resp.errors:
        _assert_plain(e.error)
        assert "duplicate key" not in e.error


def test_bulk_3_failures_and_clean_file():
    resp = _bulk_call(_bulk_text(3), FailingDB())
    assert (resp.failed, resp.errors_total, resp.errors_truncated, len(resp.errors)) == (3, 3, False, 3)
    clean = _bulk_call(_bulk_text(3))
    assert (clean.imported, clean.failed, clean.errors_total, clean.errors_truncated) == (3, 0, 0, False)


@pytest.mark.parametrize("text,code", [
    ("phone_number,first_name\n,Sian\n", "phone_missing"),
    ("phone_number,first_name\n+12345,Sian\n", "phone_invalid"),
])
def test_bulk_phone_problems_are_plain_422_entries(text, code):
    with pytest.raises(HTTPException) as caught:
        _bulk_call(text)
    assert caught.value.detail[0]["code"] == code
    _assert_plain(caught.value.detail[0]["message"])


# ── the log carries the detail, once, masked ───────────────────────────────

@pytest.mark.parametrize("route", ["paste", "bulk"])
def test_exception_text_goes_to_one_warning_with_no_full_phone(route, caplog):
    caplog.set_level(logging.DEBUG)
    if route == "paste":
        _paste_call(_paste_text(5) + "\n" + PHONE, FailingDB())
    else:
        _bulk_call(_bulk_text(5) + f"{PHONE},A\n", FailingDB())
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "import" in r.getMessage().lower()
                and "row" in r.getMessage().lower()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "duplicate key" in warnings[0].getMessage()
    for r in caplog.records:
        msg = r.getMessage()
        assert PHONE not in msg and PHONE[1:] not in msg, msg
        assert not re.search(r"\+\d{7,}|\b\d{10,}\b", msg), msg
    assert not any(r.levelno == logging.INFO and "duplicate key" in r.getMessage()
                   for r in caplog.records)


def test_csv_ingest_failure_is_logged_once_masked(caplog):
    caplog.set_level(logging.DEBUG)
    resp = _csv_call(f"phone_number,first_name\n{PHONE},A\n", FailingDB())
    assert resp.failed == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and "duplicate key" in r.getMessage()]
    assert len(warnings) == 1
    assert all(PHONE[1:] not in r.getMessage() for r in caplog.records)


def test_masking_keeps_only_the_last_four_digits():
    assert bulk_ingest.mask_phone(PHONE) == "***0123"
    assert bulk_ingest.mask_phone(None) == "-"

"""D2-8: the import preview judges numbers by the same rule as the import.

POST /contacts/import/preview checked numbers with a loose 6-16 digit rule,
took no country and returned no normalised value, so its "valid rows" count
could be higher than what the import accepts. The preview now uses the strict
normaliser with the import's country (request -> campaign -> account, never a
US guess), applies the same rule to mobile/business numbers, and returns each
row's dial number as the import will store it.
"""
import asyncio
import io
from types import SimpleNamespace

from starlette.datastructures import UploadFile

from app.api.v1.endpoints.lead_details import preview_import
from tests.unit.test_contact_import_validation import FakeDB

USER = SimpleNamespace(id="u1", tenant_id="t1", email="owner@example.com")


def _preview(csv_text, db=None, **kwargs):
    upload = UploadFile(file=io.BytesIO(csv_text.encode("utf-8")), filename="contacts.csv")
    return asyncio.run(preview_import(file=upload, current_user=USER, db_client=db or FakeDB(), **kwargs))


def _row_issues(preview, row):
    return [(i["field"], i["reason"]) for i in preview.issues if i["row"] == row]


def test_a_national_number_with_a_country_is_returned_in_e164():
    preview = _preview("phone_number\n07400 123456\n", default_country="GB")
    assert preview.default_country == "GB"
    assert preview.normalized_rows == [{"row": 2, "phone_number": "+447400123456"}]
    assert (preview.valid_rows, preview.issues) == (1, [])


def test_numbers_the_import_refuses_are_issues_not_valid_rows():
    preview = _preview("phone_number\n12345\n1234567\n+442079460958\n", default_country="GB")
    assert preview.valid_rows == 1
    assert [f for f, _ in _row_issues(preview, 2)] == ["phone_number"]
    assert [f for f, _ in _row_issues(preview, 3)] == ["phone_number"], "7 digits passed the old 6-16 rule"
    assert preview.normalized_rows[0] == {"row": 2, "phone_number": None}
    assert preview.normalized_rows[2] == {"row": 4, "phone_number": "+442079460958"}


def test_no_country_anywhere_asks_for_the_country_code_instead_of_guessing_us():
    preview = _preview("phone_number\n07400 123456\n+447400123456\n")
    assert preview.default_country is None
    assert "country code" in _row_issues(preview, 2)[0][1]
    assert preview.normalized_rows[1] == {"row": 3, "phone_number": "+447400123456"}


def test_the_account_country_applies_when_none_is_requested():
    db = FakeDB()
    db.tables["tenants"] = [{"id": "t1", "calling_rules": {"default_country": "GB"}}]
    preview = _preview("phone_number\n020 7946 0958\n", db=db)
    assert preview.default_country == "GB"
    assert preview.normalized_rows == [{"row": 2, "phone_number": "+442079460958"}]


def test_the_campaign_country_applies_as_on_the_campaign_import():
    db = FakeDB()
    db.tables["campaigns"][0]["script_config"] = {"default_country_code": "GB"}
    preview = _preview("phone_number\n020 7946 0958\n", db=db, campaign_id="c1")
    assert preview.default_country == "GB"
    assert preview.valid_rows == 1


def test_optional_numbers_follow_the_same_rule_and_come_back_normalised():
    preview = _preview(
        "phone_number,mobile,business phone\n"
        "+442079460958,12345678,020 7946 0959\n",
        default_country="GB",
    )
    assert [f for f, _ in _row_issues(preview, 2)] == ["mobile_number"]
    assert preview.normalized_rows == [
        {"row": 2, "phone_number": "+442079460958", "business_number": "+442079460959"}]
    assert preview.valid_rows == 0


def test_a_mobile_column_is_the_dial_number_when_there_is_no_phone_column():
    preview = _preview("mobile\n07400 123456\n", default_country="GB")
    assert preview.normalized_rows == [{"row": 2, "phone_number": "+447400123456"}]
    assert preview.valid_rows == 1
    assert [i for i in preview.issues if i["row"] == 0] == []


def test_a_row_without_a_number_cannot_be_imported():
    preview = _preview("phone_number,email\n,a@example.com\n+442079460958,\n", default_country="GB")
    assert [f for f, _ in _row_issues(preview, 2)] == ["phone_number"]
    assert preview.valid_rows == 1


def test_a_file_without_any_number_column_has_no_valid_rows():
    preview = _preview("email\na@example.com\n")
    assert preview.valid_rows == 0
    assert preview.issues[0]["row"] == 0

"""D2-9: mobile_number and business_number follow the same rule as the dial number.

The optional numbers were checked separately: CSV with a loose 6-16 digit
rule, Add/Update contact only by length (max 32), and stored as typed. An
invalid mobile number in an upload was accepted silently. Every phone field
now goes through the same strict normaliser and country: add/update answer a
field-level 422, and CSV drops the bad cell with a row error (the contact
still imports, as for every other bad cell).
"""
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.campaigns import _normalized_optional_numbers
from app.api.v1.endpoints.contacts import _check_optional_numbers
from app.api.v1.schemas.campaigns import ContactCreate, ContactUpdate


def test_valid_optional_numbers_are_stored_in_e164():
    contact = ContactCreate(phone_number="+442079460958", mobile_number="07400 123456",
                            business_number="+1 415 555 2671")
    assert _normalized_optional_numbers(contact, "GB") == {
        "mobile_number": "+447400123456", "business_number": "+14155552671"}


@pytest.mark.parametrize("field", ["mobile_number", "business_number"])
def test_an_invalid_optional_number_is_a_field_error(field):
    contact = ContactUpdate(**{field: "12345"})
    with pytest.raises(HTTPException) as refused:
        _normalized_optional_numbers(contact, "GB")
    assert refused.value.status_code == 422
    assert [e["field"] for e in refused.value.detail] == [field]


def test_a_national_optional_number_without_country_asks_for_the_country_code():
    contact = ContactUpdate(mobile_number="07400 123456")
    with pytest.raises(HTTPException) as refused:
        _normalized_optional_numbers(contact, None)
    assert "country code" in refused.value.detail[0]["message"]


def test_unsupplied_or_blank_optional_numbers_are_left_alone():
    assert _normalized_optional_numbers(ContactUpdate(email="a@example.com"), "GB") == {}
    assert _normalized_optional_numbers(ContactUpdate(mobile_number="  "), "GB") == {}


def test_csv_drops_a_bad_optional_cell_reports_it_and_keeps_the_contact():
    values = {"phone_number": "+442079460958", "mobile_number": "12345", "business_number": "020 7946 0959"}
    errors = _check_optional_numbers(values, 7, "GB")
    assert [(e.row, e.field) for e in errors] == [(7, "mobile_number")]
    assert "mobile_number" not in values
    assert values["business_number"] == "+442079460959"
    assert values["phone_number"] == "+442079460958", "the dial number has its own stricter path"


def test_csv_leaves_a_mobile_column_that_is_the_dial_number_to_the_dial_check():
    values = {"mobile_number": "12345"}
    assert _check_optional_numbers(values, 3, "GB") == []
    assert values == {"mobile_number": "12345"}

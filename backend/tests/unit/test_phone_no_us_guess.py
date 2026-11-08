"""D2-5: a national-format number with no country anywhere is refused, not read as US.

Contact intake resolved the country as request -> campaign -> account -> "US".
A UK national number such as "07400 123456" with nothing configured was
parsed as a US number (refused as "invalid for the selected country", or,
worse, stored wrongly when it happened to fit a US pattern). Intake now has no
US fallback: such a number is refused with a message that asks for the
country code. DNC keeps its explicit US fallback so its key still matches the
call guard's US-default lookup, and the legacy repair tool keeps it for its
reviewed historical population.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.domain.services.phone_number_normalizer import (
    campaign_country,
    normalize_phone_number,
    phone_number_error,
)


def test_no_country_anywhere_resolves_to_none_unless_a_fallback_is_named():
    assert campaign_country({}, {}) is None
    assert campaign_country({}, {}, fallback="US") == "US"
    assert campaign_country({"script_config": {"default_country_code": "gb"}}, {}) == "GB"
    assert campaign_country({}, {"default_country": "DE"}) == "DE"


@pytest.mark.parametrize("raw,country,expected", [
    ("+447400123456", None, "+447400123456"),
    ("07400 123456", "GB", "+447400123456"),
])
def test_self_describing_or_country_backed_numbers_still_normalise(raw, country, expected):
    assert normalize_phone_number(raw, default_country=country) == expected


def test_a_national_number_with_no_country_is_refused_not_read_as_us():
    with pytest.raises(ValueError, match="(?i)country"):
        normalize_phone_number("07400 123456", default_country=None)


@pytest.mark.parametrize("raw,country,needle", [
    ("07400 123456", None, "country code"),
    ("12345", "GB", "selected country"),
    ("+12345", None, "selected country"),
])
def test_the_row_message_says_what_to_fix(raw, country, needle):
    assert needle in phone_number_error(raw, country)


def test_csv_and_paste_preflight_reports_the_missing_country_per_row():
    from app.api.v1.endpoints.contacts import _preflight_phones

    with pytest.raises(HTTPException) as refused:
        _preflight_phones([SimpleNamespace(phone_raw="07400 123456", source_row=2)], None)
    assert refused.value.status_code == 422
    assert refused.value.detail == [{"row": 2, "field": "phone_number",
                                     "message": phone_number_error("07400 123456", None)}]


async def test_dnc_writes_keep_the_us_fallback_that_matches_the_call_guard():
    from app.domain.services.dnc_service import _write_country

    assert await _write_country(None, None, None) == "US"


async def test_the_assistant_refuses_a_national_number_with_no_country_and_says_why():
    from app.infrastructure.assistant.tools.campaign_admin import manage_lead
    from tests.unit.test_assistant_campaign_admin import FakeDbClient

    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[{"id": "c1", "direction": "outbound"}])
    result = await manage_lead(tenant_id="t1", db_client=db, campaign_id="c1", action="add",
                               phone_number="07400 123456", confirm=False)
    assert "country" in result["error"].lower()
    assert db._insert_calls == []

"""The canonical contact model (goals.md §11).

One definition, read by CSV import, validation, the form, the table and the
agent. These tests pin the properties that break quietly if it drifts.
"""
from __future__ import annotations

import pytest

from app.domain.services.contact_fields import (
    BY_KEY,
    CONTACT_FIELDS,
    agent_context_fields,
    coerce_bool,
    csv_template_headers,
    dedupe_key,
    map_column,
    map_headers,
    validate_row,
)


# ── every field §11 asks for exists ─────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "first_name", "last_name", "mobile_number", "business_number", "email",
    "company_name", "job_title", "best_time_to_call", "timezone",
    "calling_notes", "preferred_contact_method", "do_not_call",
])
def test_the_canonical_field_exists(key):
    assert key in BY_KEY, f"goals.md §11 lists {key} and it is missing"


def test_full_name_is_not_a_stored_field():
    """§11 wants it "as display/derived field where possible" — it is a
    GENERATED column in migration 0020. Adding it here would invite something
    to try to write it."""
    assert "full_name" not in BY_KEY


# ── alias mapping, the part that meets real spreadsheets ────────────────────

@pytest.mark.parametrize("header,expected", [
    ("Mobile", "mobile_number"),
    ("Cell Phone", "mobile_number"),
    ("Job Title", "job_title"),
    ("Position", "job_title"),
    ("Organisation", "company_name"),
    ("Organization", "company_name"),
    ("Company Name", "company_name"),
    ("E-Mail", "email"),
    ("Surname", "last_name"),
    ("  PHONE_NUMBER  ", "phone_number"),
    ("Work Phone", "business_number"),
    ("DNC", "do_not_call"),
])
def test_real_spreadsheet_headers_map(header, expected):
    assert map_column(header) == expected


def test_an_unrecognised_column_maps_to_none_rather_than_guessing():
    """None means "we could not tell", which lets the importer keep the column
    in custom_fields and the UI offer a manual mapping. A wrong guess would
    silently put postcodes in a phone field."""
    assert map_column("Postcode") is None
    assert map_column("Lead Score") is None


def test_map_headers_reports_every_column_including_unknowns():
    got = map_headers(["Phone", "Postcode", "Job Title"])
    assert got == {"Phone": "phone_number", "Postcode": None, "Job Title": "job_title"}


# ── row-level validation ────────────────────────────────────────────────────

def test_a_bad_email_is_reported_not_raised():
    """§11 wants row-level failures. A 4,000-row import with nine bad rows must
    load 3,991 contacts, which is only possible if validation returns."""
    issues = validate_row({"email": "not-an-email"}, 7)
    assert len(issues) == 1
    assert issues[0].row == 7 and issues[0].field == "email"


def test_a_real_email_passes():
    assert validate_row({"email": "r.oconnell42@buildwright-uk.co.uk"}, 2) == []


def test_an_unknown_timezone_is_caught():
    assert validate_row({"timezone": "Mars/Base"}, 2)[0].reason.startswith("unknown timezone")


def test_a_real_iana_timezone_passes():
    assert validate_row({"timezone": "Europe/London"}, 2) == []


@pytest.mark.parametrize("number", [
    "+447700900123", "07700900123", "+1 (415) 555-0142", "0044 7700 900123",
])
def test_real_world_phone_formats_are_accepted(number):
    """Rejecting a real number is worse than accepting an odd one — the dialler
    surfaces a bad number on the first attempt anyway."""
    assert validate_row({"phone_number": number}, 2) == []


def test_a_phone_field_containing_words_is_rejected():
    assert validate_row({"phone_number": "call the office"}, 2)


def test_an_overlong_value_is_reported_with_its_field():
    issues = validate_row({"first_name": "x" * 300}, 4)
    assert issues and "longer than" in issues[0].reason


def test_empty_values_are_not_errors():
    """A blank cell is a normal spreadsheet, not a validation failure."""
    assert validate_row({"email": "", "job_title": "   "}, 2) == []


# ── duplicate detection ─────────────────────────────────────────────────────

def test_the_same_number_in_two_formats_is_one_person():
    assert dedupe_key({"phone_number": "+447700900123"}) == \
           dedupe_key({"phone_number": "07700900123"})


def test_email_is_the_fallback_key_when_there_is_no_number():
    assert dedupe_key({"email": "A.Person@Example.com"}) == "mail:a.person@example.com"


def test_a_name_alone_is_NOT_a_duplicate_key():
    """Two different Michael Smiths at two companies are two contacts. Merging
    on name would silently destroy one of them."""
    assert dedupe_key({"first_name": "Michael", "last_name": "Smith"}) is None


def test_phone_beats_email_when_both_are_present():
    key = dedupe_key({"phone_number": "07700900123", "email": "a@b.co"})
    assert key.startswith("tel:")


# ── what the agent is allowed to see ────────────────────────────────────────

def test_do_not_call_never_reaches_the_agent():
    """A do-not-call contact should not be dialled at all. Putting the flag in
    the prompt would invite the model to mention it, which is the worst
    possible handling."""
    assert BY_KEY["do_not_call"].agent_usable is False
    assert "do_not_call" not in agent_context_fields({"do_not_call": True})


def test_timezone_and_contact_preference_are_operational_not_conversational():
    assert BY_KEY["timezone"].agent_usable is False
    assert BY_KEY["preferred_contact_method"].agent_usable is False
    ctx = agent_context_fields({"timezone": "Europe/London",
                                "preferred_contact_method": "email"})
    assert ctx == {}


def test_the_agent_does_get_the_useful_context():
    ctx = agent_context_fields({
        "first_name": "Sian", "job_title": "Quantity Surveyor",
        "company_name": "BuildWright", "do_not_call": False,
    })
    assert ctx["first_name"] == "Sian"
    assert ctx["job_title"] == "Quantity Surveyor"
    assert ctx["company_name"] == "BuildWright"


def test_blank_values_are_dropped_from_agent_context():
    """Sending empty fields spends prompt tokens telling the model nothing —
    and the prompt is already ~9,500 tokens."""
    assert agent_context_fields({"first_name": "  ", "job_title": None}) == {}


# ── the template ────────────────────────────────────────────────────────────

def test_the_csv_template_maps_cleanly_onto_itself():
    """An import using our own downloaded template must need no guessing."""
    for header in csv_template_headers():
        assert map_column(header) == header


def test_coerce_bool_understands_how_people_write_dnc():
    assert coerce_bool("yes") and coerce_bool("TRUE") and coerce_bool("DNC")
    assert not coerce_bool("no") and not coerce_bool("") and not coerce_bool("0")

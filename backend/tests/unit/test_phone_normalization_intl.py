"""T2.5 — internationalized E.164 normalization.

Before: `normalize_phone_number` hard-defaulted to +1 for any
unprefixed number. A UK number typed without a leading + was
mis-routed as a US area code.

After: libphonenumber is tried first with a per-campaign
`default_country` (ISO-3166 alpha-2). US default preserved for
back-compat. The legacy heuristic remains as a fallback for
environments without libphonenumber installed.
"""
from __future__ import annotations

import pytest

from app.api.v1.endpoints.campaigns import normalize_phone_number


# ──────────────────────────────────────────────────────────────────────────
# US default (back-compat)
# ──────────────────────────────────────────────────────────────────────────

def test_us_default_10_digits():
    assert normalize_phone_number("4155551234") == "+14155551234"


def test_us_default_with_formatting():
    assert normalize_phone_number("(415) 555-1234") == "+14155551234"


def test_us_default_with_plus_passthrough():
    assert normalize_phone_number("+14155551234") == "+14155551234"


def test_us_default_11_digits_starting_with_1():
    assert normalize_phone_number("14155551234") == "+14155551234"


# ──────────────────────────────────────────────────────────────────────────
# International numbers — the T2.5 win
# ──────────────────────────────────────────────────────────────────────────

def test_uk_number_with_default_country_gb():
    """020 7946 0958 is Ofcom's reserved London test number. With
    default_country="GB" it must normalise to +44…, not +1…"""
    out = normalize_phone_number("020 7946 0958", default_country="GB")
    assert out.startswith("+44"), f"expected +44 prefix, got {out}"


def test_international_number_with_plus_ignores_default():
    out = normalize_phone_number("+442079460958", default_country="US")
    assert out == "+442079460958"


def test_german_number_with_default_de():
    out = normalize_phone_number("030 2345 6789", default_country="DE")
    assert out.startswith("+49"), f"expected +49 prefix, got {out}"


def test_australian_number_with_default_au():
    out = normalize_phone_number("02 9374 4000", default_country="AU")
    assert out.startswith("+61"), f"expected +61 prefix, got {out}"


# ──────────────────────────────────────────────────────────────────────────
# Edge cases
# ──────────────────────────────────────────────────────────────────────────

def test_sip_extension_passes_through():
    assert normalize_phone_number("1001") == "1001"


def test_empty_raises():
    with pytest.raises(ValueError):
        normalize_phone_number("")


def test_too_short_raises():
    with pytest.raises(ValueError):
        normalize_phone_number("12")


def test_too_long_raises():
    with pytest.raises(ValueError):
        normalize_phone_number("1234567890123456")


def test_invalid_country_code_falls_back_to_legacy():
    """Garbage country code → libphonenumber rejects → legacy
    fallback path (US-centric) kicks in and the number is still
    normalised (perhaps imperfectly) rather than blowing up."""
    out = normalize_phone_number("4155551234", default_country="XX")
    assert out.startswith("+")  # some sane E.164 shape


# ──────────────────────────────────────────────────────────────────────────
# The campaign country must reach BOTH import paths, not just manual add.
#
# `campaigns.add_contact_to_campaign` has always resolved the campaign's
# default country and passed it to the normalizer. `contacts._normalize_for_user`
# — the CSV upload and the pasted-numbers import — dropped the parameter, so the
# same UK number became +447700900123 when typed in and the un-dialable
# +07700900123 when uploaded in a spreadsheet.
# ──────────────────────────────────────────────────────────────────────────

class _PlainUser:
    """A user with no relaxed-validation override."""
    id = "user-1"
    email = "someone@example.com"
    tenant_id = "tenant-1"


@pytest.mark.parametrize(
    "raw,country,expected",
    [
        ("07700 900123", "GB", "+447700900123"),
        ("02079460958", "GB", "+442079460958"),
        ("03001234567", "PK", "+923001234567"),
        ("4155551234", "US", "+14155551234"),
    ],
)
def test_csv_import_honours_the_campaign_country(raw, country, expected):
    from app.api.v1.endpoints.contacts import _normalize_for_user

    assert _normalize_for_user(raw, _PlainUser(), country) == expected


def test_csv_import_defaults_to_us_when_no_country_given():
    from app.api.v1.endpoints.contacts import _normalize_for_user

    assert _normalize_for_user("4155551234", _PlainUser()) == "+14155551234"


@pytest.mark.parametrize(
    "raw,country",
    [
        ("07700 900123", "GB"),
        ("02079460958", "GB"),
        ("03001234567", "PK"),
        ("4155551234", "US"),
    ],
)
def test_csv_import_and_manual_add_agree(raw, country):
    """The two paths must produce byte-identical phone_number values."""
    from app.api.v1.endpoints.contacts import _normalize_for_user

    assert _normalize_for_user(raw, _PlainUser(), country) == normalize_phone_number(
        raw, default_country=country,
    )


@pytest.mark.parametrize(
    "campaign,expected",
    [
        ({"script_config": {"default_country_code": "gb"}}, "GB"),
        ({"script_config": {"campaign_slots": {"default_country_code": "PK"}}}, "PK"),
        ({"script_config": {}}, "US"),
        ({"script_config": None}, "US"),
        ({}, "US"),
        (None, "US"),
    ],
)
def test_campaign_default_country_resolution(campaign, expected):
    from app.api.v1.endpoints.campaigns import campaign_default_country

    assert campaign_default_country(campaign) == expected


def test_contacts_resolves_the_country_the_same_way_campaigns_does():
    from app.api.v1.endpoints.campaigns import campaign_default_country
    from app.api.v1.endpoints.contacts import _campaign_default_country

    campaign = {"script_config": {"default_country_code": "GB"}}
    assert _campaign_default_country(campaign) == campaign_default_country(campaign) == "GB"

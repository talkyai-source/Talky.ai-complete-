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

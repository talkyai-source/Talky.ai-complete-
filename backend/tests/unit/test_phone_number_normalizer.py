"""Unit tests for phone number normalization.

Focus on the lenient normalizer used by accounts with relaxed phone validation:
normal numbers must still come out as proper E.164, while numbers the strict
normalizer rejects (e.g. 6-digit, >15) fall back to a digits passthrough. Only
an empty/no-digit input is rejected.
"""
from __future__ import annotations

import pytest

from app.domain.services.phone_number_normalizer import (
    normalize_phone_number,
    normalize_phone_number_lenient,
)


class TestStrictNormalizer:
    def test_six_digits_rejected(self):
        with pytest.raises(ValueError):
            normalize_phone_number("123456")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError):
            normalize_phone_number("1234567890123456789")


class TestLenientNormalizer:
    def test_normal_us_number_still_e164(self):
        assert normalize_phone_number_lenient("5551234567") == "+15551234567"

    def test_keeps_existing_plus_country_code(self):
        # International number — strict path via libphonenumber.
        assert normalize_phone_number_lenient("+44 7911 123456") == "+447911123456"

    def test_six_digit_passthrough(self):
        # Strict rejects "minimum 7 digits"; lenient passes it through as digits.
        assert normalize_phone_number_lenient("123456") == "123456"

    def test_six_digit_with_plus_keeps_plus(self):
        assert normalize_phone_number_lenient("+123456") == "+123456"

    def test_over_15_digits_passthrough(self):
        assert normalize_phone_number_lenient("1234567890123456789") == "1234567890123456789"

    def test_formatting_is_stripped_on_passthrough(self):
        # 6 digits → strict rejects → passthrough strips the formatting.
        assert normalize_phone_number_lenient("(123) 456") == "123456"

    def test_no_digits_rejected(self):
        with pytest.raises(ValueError):
            normalize_phone_number_lenient("----")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            normalize_phone_number_lenient("")

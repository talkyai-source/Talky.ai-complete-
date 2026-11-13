"""Unit tests for phone number normalization.

The lenient normalizer these tests also covered was deleted (D2-6): every
intake path refuses a number that is not valid.
"""
from __future__ import annotations

import pytest

from app.domain.services.phone_number_normalizer import normalize_phone_number


class TestStrictNormalizer:
    def test_six_digits_rejected(self):
        with pytest.raises(ValueError):
            normalize_phone_number("123456")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError):
            normalize_phone_number("1234567890123456789")

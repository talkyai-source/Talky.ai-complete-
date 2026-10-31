"""Behavioural baseline for EVERY phone/E.164 normaliser in the codebase.

Why this file exists
--------------------
The repo grew six independent phone normalisers.  They disagree -- and a
disagreement between the DNC *write* path and the CallGuard *read* path has
already produced a real incident.  Before any of them can be consolidated we
need a frozen, executable record of what each one does *today*.

That is all this file is: a table of ~44 inputs x 6 implementations recording
the CURRENT output (or the current exception type).  The consolidation work
makes the four endpoint/service-level normalisers delegate to
``app.domain.services.phone_number_normalizer``; this test passing UNCHANGED
before and after that delegation is the proof the refactor is behaviour
preserving.

Do NOT "fix" a surprising value here.  If a row looks like a bug, it *is*
recorded as one in the consolidation report -- changing it is a separate,
deliberate behaviour change with its own test.

ONE column has been deliberately re-recorded since: ``dnc_service``.  On
2026-08-27 the DNC write path was moved off ``normalize_e164_libphonenumber``
onto ``normalize_e164_digits`` -- the exact function ``CallGuard`` uses to look
``dnc_entries`` up -- because the two disagreed on a bare 10-digit US number
and the row therefore never matched, leaving a number the customer had put on
Do-Not-Call dialable.  The ``dnc_service`` and ``call_guard`` columns are now
identical BY DESIGN, and
``test_dnc_and_call_guard_agree_on_every_baseline_input`` at the bottom of this
file is what keeps them that way.  Every other column is untouched.
"""
from __future__ import annotations

import pytest

from app.api.v1.endpoints.contacts import (
    normalize_phone_number as contacts_legacy_normalize,
)
from app.api.v1.endpoints.tenant_phone_numbers import PhoneNumberRegisterRequest
from app.domain.services.call_guard import CallGuard
from app.domain.services.dnc_service import normalize_e164 as dnc_normalize_e164
from app.domain.services.phone_number_normalizer import (
    normalize_phone_number as canonical_strict,
    normalize_phone_number_lenient as canonical_lenient,
)

from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Adapters -- call every implementation through one uniform signature.
# ---------------------------------------------------------------------------


def _call_guard(value):
    """CallGuard._normalize_phone_number does not touch ``self``."""
    return CallGuard._normalize_phone_number(None, value)


def _tenant_phone_numbers(value):
    """The ``e164`` field validator on PhoneNumberRegisterRequest.

    Pydantic re-wraps the validator's ValueError as a ValidationError; unwrap
    it so the baseline records the validator's own contract (ValueError on
    reject) rather than a pydantic implementation detail.
    """
    try:
        return PhoneNumberRegisterRequest(e164=value).e164
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


IMPLEMENTATIONS = {
    "canonical_strict": canonical_strict,
    "canonical_lenient": canonical_lenient,
    "contacts_legacy": contacts_legacy_normalize,
    "call_guard": _call_guard,
    "dnc_service": dnc_normalize_e164,
    "tenant_phone_numbers": _tenant_phone_numbers,
}

# Column order of the BASELINE rows below.
COLUMNS = (
    "canonical_strict",
    "canonical_lenient",
    "contacts_legacy",
    "call_guard",
    "dnc_service",
    "tenant_phone_numbers",
)


def _outcome(fn, value) -> str:
    """Return ``repr(result)`` or ``"ERR_<ExceptionClassName>"``."""
    try:
        return repr(fn(value))
    except Exception as exc:  # noqa: BLE001 - recording behaviour, not asserting
        return "ERR_" + type(exc).__name__


# ---------------------------------------------------------------------------
# THE BASELINE.  Generated from the code as it stood before consolidation.
# (input, canonical_strict, canonical_lenient, contacts_legacy,
#  call_guard, dnc_service, tenant_phone_numbers)
# ---------------------------------------------------------------------------

BASELINE = (
    ('+14155551234', "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'"),
    ('14155551234', "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "ERR_ValueError"),
    ('4155551234', "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "ERR_ValueError"),
    ('(415) 555-1234', "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "ERR_ValueError"),
    ('415-555-1234', "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "ERR_ValueError"),
    ('415.555.1234', "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "ERR_ValueError"),
    (' +1 415 555 1234 ', "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "ERR_ValueError"),
    ('001 415 555 1234', "'+0014155551234'", "'+0014155551234'", "'+0014155551234'", "'+0014155551234'", "'+0014155551234'", "ERR_ValueError"),
    ('+1 (415) 555-1234 ext 22', "'+14155551234'", "'+14155551234'", "'+1415555123422'", "'+1415555123422'", "'+1415555123422'", "ERR_ValueError"),
    ('+447700900123', "'+447700900123'", "'+447700900123'", "'+447700900123'", "'+447700900123'", "'+447700900123'", "'+447700900123'"),
    ('447700900123', "'+447700900123'", "'+447700900123'", "'+447700900123'", "'+447700900123'", "'+447700900123'", "ERR_ValueError"),
    ('07700 900123', "'+07700900123'", "'+07700900123'", "'+07700900123'", "'+07700900123'", "'+07700900123'", "ERR_ValueError"),
    ('07700900123', "'+07700900123'", "'+07700900123'", "'+07700900123'", "'+07700900123'", "'+07700900123'", "ERR_ValueError"),
    ('+44 7700 900 123', "'+447700900123'", "'+447700900123'", "'+447700900123'", "'+447700900123'", "'+447700900123'", "ERR_ValueError"),
    ('0044 7700 900123', "'+00447700900123'", "'+00447700900123'", "'+00447700900123'", "'+00447700900123'", "'+00447700900123'", "ERR_ValueError"),
    ('+44 20 7946 0958', "'+442079460958'", "'+442079460958'", "'+442079460958'", "'+442079460958'", "'+442079460958'", "ERR_ValueError"),
    ('02079460958', "'+02079460958'", "'+02079460958'", "'+02079460958'", "'+02079460958'", "'+02079460958'", "ERR_ValueError"),
    ('+923001234567', "'+923001234567'", "'+923001234567'", "'+923001234567'", "'+923001234567'", "'+923001234567'", "'+923001234567'"),
    ('923001234567', "'+923001234567'", "'+923001234567'", "'+923001234567'", "'+923001234567'", "'+923001234567'", "ERR_ValueError"),
    ('03001234567', "'+03001234567'", "'+03001234567'", "'+03001234567'", "'+03001234567'", "'+03001234567'", "ERR_ValueError"),
    ('0300 123 4567', "'+03001234567'", "'+03001234567'", "'+03001234567'", "'+03001234567'", "'+03001234567'", "ERR_ValueError"),
    ('+92 300 1234567', "'+923001234567'", "'+923001234567'", "'+923001234567'", "'+923001234567'", "'+923001234567'", "ERR_ValueError"),
    ('1234', "'1234'", "'1234'", "'1234'", "'+1234'", "'+1234'", "ERR_ValueError"),
    ('123', "ERR_ValueError", "'123'", "'123'", "'+123'", "'+123'", "ERR_ValueError"),
    ('12345', "'12345'", "'12345'", "'12345'", "'+12345'", "'+12345'", "ERR_ValueError"),
    ('123456', "ERR_ValueError", "'123456'", "'123456'", "'+123456'", "'+123456'", "ERR_ValueError"),
    ('1234567', "'+1234567'", "'+1234567'", "'+1234567'", "'+1234567'", "'+1234567'", "ERR_ValueError"),
    ('+123456789012345', "'+123456789012345'", "'+123456789012345'", "'+123456789012345'", "'+123456789012345'", "'+123456789012345'", "'+123456789012345'"),
    ('+1234567890123456', "ERR_ValueError", "'+1234567890123456'", "ERR_ValueError", "'+1234567890123456'", "'+1234567890123456'", "ERR_ValueError"),
    ('', "ERR_ValueError", "ERR_ValueError", "ERR_ValueError", "''", "''", "ERR_ValueError"),
    ('   ', "ERR_ValueError", "ERR_ValueError", "ERR_ValueError", "'+'", "'+'", "ERR_ValueError"),
    ('abc', "ERR_ValueError", "ERR_ValueError", "ERR_ValueError", "'+'", "'+'", "ERR_ValueError"),
    ('phone', "ERR_ValueError", "ERR_ValueError", "ERR_ValueError", "'+'", "'+'", "ERR_ValueError"),
    ('+', "ERR_ValueError", "ERR_ValueError", "ERR_ValueError", "'+'", "'+'", "ERR_ValueError"),
    ('++14155551234', "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "'+14155551234'", "ERR_ValueError"),
    ('-', "ERR_ValueError", "ERR_ValueError", "ERR_ValueError", "'+'", "'+'", "ERR_ValueError"),
    ('()', "ERR_ValueError", "ERR_ValueError", "ERR_ValueError", "'+'", "'+'", "ERR_ValueError"),
    ('555-CALL-NOW', "ERR_ValueError", "'555'", "'555'", "'+555'", "'+555'", "ERR_ValueError"),
    ('+1-800-FLOWERS', "'1800'", "'1800'", "'1800'", "'+1800'", "'+1800'", "ERR_ValueError"),
    ('0', "ERR_ValueError", "'0'", "ERR_ValueError", "'+0'", "'+0'", "ERR_ValueError"),
    ('00', "ERR_ValueError", "'00'", "ERR_ValueError", "'+00'", "'+00'", "ERR_ValueError"),
    ('0000000000', "'+10000000000'", "'+10000000000'", "'+10000000000'", "'+10000000000'", "'+10000000000'", "ERR_ValueError"),
    ('+4915112345678', "'+4915112345678'", "'+4915112345678'", "'+4915112345678'", "'+4915112345678'", "'+4915112345678'", "'+4915112345678'"),
    ('+61412345678', "'+61412345678'", "'+61412345678'", "'+61412345678'", "'+61412345678'", "'+61412345678'", "'+61412345678'"),
)


def _cases():
    for row in BASELINE:
        raw, expectations = row[0], row[1:]
        for name, expected in zip(COLUMNS, expectations):
            yield pytest.param(name, raw, expected, id=f"{name}::{raw!r}")


@pytest.mark.parametrize("impl_name,raw,expected", list(_cases()))
def test_normaliser_baseline(impl_name: str, raw: str, expected: str) -> None:
    """Each implementation still produces exactly what it produced before."""
    assert _outcome(IMPLEMENTATIONS[impl_name], raw) == expected, (
        f"{impl_name}({raw!r}) changed behaviour"
    )


# ---------------------------------------------------------------------------
# None handling -- deliberately separate: the six disagree here too, and the
# AttributeError raised by the canonical pair is itself part of the contract.
# ---------------------------------------------------------------------------

NONE_BASELINE = {
    "canonical_strict": "ERR_AttributeError",
    "canonical_lenient": "ERR_AttributeError",
    "contacts_legacy": "ERR_ValueError",
    "call_guard": "''",
    "dnc_service": "''",
    "tenant_phone_numbers": "ERR_ValueError",
}


@pytest.mark.parametrize("impl_name,expected", sorted(NONE_BASELINE.items()))
def test_normaliser_none_baseline(impl_name: str, expected: str) -> None:
    assert _outcome(IMPLEMENTATIONS[impl_name], None) == expected


# ---------------------------------------------------------------------------
# default_country is a real behavioural lever on the canonical strict
# normaliser -- and only ``campaigns.py`` passes it.  Freeze it so the
# consolidation cannot quietly drop the parameter.
# ---------------------------------------------------------------------------

DEFAULT_COUNTRY_BASELINE = (
    ("07700900123", "US", "+07700900123"),
    ("07700900123", "GB", "+447700900123"),
    ("07700900123", "PK", "+07700900123"),
    ("07700900123", "DE", "+497700900123"),
    ("02079460958", "US", "+02079460958"),
    ("02079460958", "GB", "+442079460958"),
    ("03001234567", "PK", "+923001234567"),
    ("03001234567", "GB", "+443001234567"),
    ("4155551234", "US", "+14155551234"),
    ("4155551234", "GB", "+14155551234"),
    ("4155551234", "PK", "+924155551234"),
    ("0300 123 4567", "PK", "+923001234567"),
)


@pytest.mark.parametrize("raw,country,expected", DEFAULT_COUNTRY_BASELINE)
def test_canonical_strict_default_country(raw: str, country: str, expected: str) -> None:
    assert canonical_strict(raw, default_country=country) == expected


# ---------------------------------------------------------------------------
# The DNC write path and the CallGuard read path MUST agree.
# ---------------------------------------------------------------------------


def test_dnc_write_and_call_guard_read_agree_on_bare_us_ten_digits() -> None:
    """The fixed compliance bug, pinned the other way round.

    ``DNCService.add`` stores ``normalize_e164(raw)`` and ``CallGuard`` queries
    ``dnc_entries.normalized_number`` with its own normalisation.  If the two
    ever disagree the row silently never matches and a number the customer put
    on Do-Not-Call stays dialable.  They are now the SAME function; this test
    is the guard against anyone re-splitting them.
    """
    raw = "(415) 555-1234"
    assert dnc_normalize_e164(raw) == "+14155551234"
    assert _call_guard(raw) == "+14155551234"
    assert dnc_normalize_e164(raw) == _call_guard(raw)


@pytest.mark.parametrize("raw", [row[0] for row in BASELINE])
def test_dnc_and_call_guard_agree_on_every_baseline_input(raw: str) -> None:
    """Not just the 10-digit case -- agreement across the whole input table."""
    assert dnc_normalize_e164(raw) == _call_guard(raw), (
        f"DNC write and CallGuard read disagree on {raw!r}"
    )

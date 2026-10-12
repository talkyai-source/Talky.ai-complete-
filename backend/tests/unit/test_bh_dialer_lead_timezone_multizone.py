"""outbound-telephony-dialer-9: a phone number that spans several time zones
must not resolve to the FIRST zone of the list.

`phonenumbers.time_zones_for_number` returns every zone a number can be in.
The old code returned ``zones[0]`` (sorted alphabetically), so a Russian
mobile was judged in Asia/Almaty, an Australian mobile in Australia/Adelaide
and a toll-free +1 800 in America/Adak. The module's own contract is "fail
safe = None" for zones it cannot disambiguate: the caller then falls back to
the campaign/tenant timezone.
"""
from __future__ import annotations

import phonenumbers
import pytest
from phonenumbers import timezone as pn_timezone

from app.domain.services.dialer import lead_timezone as lt


@pytest.fixture(autouse=True)
def _clear_caches():
    lt._resolve_cached.cache_clear()
    yield
    lt._resolve_cached.cache_clear()


def _zones(e164: str):
    return pn_timezone.time_zones_for_number(phonenumbers.parse(e164, None))


@pytest.mark.parametrize(
    "number",
    [
        "+61412345678",  # Australia mobile: 8 zones
        "+79161234567",  # Russia mobile: many zones
        "+18005551234",  # NANP toll-free: every NANP zone
    ],
)
def test_multi_zone_number_resolves_to_none(number):
    assert len(_zones(number)) > 1, "fixture number must really be multi-zone"
    assert lt.resolve_lead_timezone(number) is None
    assert lt.resolve_effective_lead_timezone(None, number) is None


@pytest.mark.parametrize(
    "number,expected",
    [
        ("+14165551234", "America/Toronto"),
        ("+442071234567", "Europe/London"),
        ("+12125551234", "America/New_York"),
    ],
)
def test_single_zone_numbers_are_unchanged(number, expected):
    assert len(_zones(number)) == 1
    assert lt.resolve_lead_timezone(number) == expected


def test_explicit_lead_timezone_still_outranks_a_multi_zone_number():
    assert (
        lt.resolve_effective_lead_timezone("Australia/Perth", "+61412345678")
        == "Australia/Perth"
    )


# ── reviewer additions: do not throw away a zone that is not actually ambiguous ──
#
# ``phonenumbers`` lists several IANA names for numbers whose candidate zones all
# keep the SAME clock (UK mobiles: London + Jersey + Guernsey + Isle of Man; Italy:
# Rome + Vatican). Returning None for those silently dropped the per-lead window
# for the most common destinations: a non-UK tenant then judged UK numbers on its
# own clock. Only zones whose clocks genuinely differ are ambiguous.


def _clock(zone: str):
    """The zone's UTC offset on the 1st and 15th of every month of 2026-27."""
    import pytz
    from datetime import datetime, timezone

    tz = pytz.timezone(zone)
    out = []
    for year in (2026, 2027):
        for month in range(1, 13):
            for day in (1, 15):
                moment = datetime(year, month, day, 12, tzinfo=timezone.utc)
                out.append(moment.astimezone(tz).utcoffset())
    return out


@pytest.mark.parametrize(
    "number",
    [
        "+447911123456",  # UK mobile: four zones, one clock
        "+393312345678",  # Italy mobile: Rome + Vatican
    ],
)
def test_multi_zone_number_whose_zones_share_one_clock_keeps_a_zone(number):
    zones = _zones(number)
    assert len(zones) > 1, "fixture number must really list several zones"
    assert len({tuple(_clock(z)) for z in zones}) == 1, "fixture zones must share one clock"
    resolved = lt.resolve_lead_timezone(number)
    assert resolved in zones
    assert _clock(resolved) == _clock(zones[0])


@pytest.mark.parametrize(
    "number",
    [
        "+34612345678",   # Spain: Madrid + Canary Islands (one hour apart)
        "+64211234567",   # New Zealand: Auckland + Chatham (45 min apart)
    ],
)
def test_multi_zone_number_whose_clocks_differ_still_resolves_to_none(number):
    zones = _zones(number)
    assert len({tuple(_clock(z)) for z in zones}) > 1, "fixture zones must differ"
    assert lt.resolve_lead_timezone(number) is None

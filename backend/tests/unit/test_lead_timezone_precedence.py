"""The calling window must be evaluated in the LEAD's timezone.

Regression cover for a compliance bug: ``SchedulingRuleEngine.can_make_call``
has always accepted a ``lead_timezone`` argument and ``lead_timezone.py`` has
always been able to derive one, but **no caller ever passed it**. Every
window check therefore ran in the campaign/tenant timezone, so a London
account could legally-on-paper dial a California lead at 06:30 local.

These tests pin three things:

1. the precedence rule — explicit ``leads.timezone`` (customer-supplied) >
   phone-derived zone (feature-flagged) > None (campaign/tenant tz);
2. that the resolved zone actually **varies** per lead at the call site —
   this repo's recurring trap is a guard wired to a signal that is constant
   in production, so a wiring test that only proves "an argument was passed"
   is not enough;
3. that every failure path (invalid customer IANA string, unresolvable
   number, ``phonenumbers`` blowing up) falls back instead of dropping a dial.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from app.domain.models import calling_rules as calling_rules_module
from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob
from app.domain.services.dialer import lead_timezone as lt
from app.workers.dialer_worker import DialerWorker


# A Monday. 15:30 UTC is 11:30 New York, 08:30 Los Angeles, 16:30 London —
# one instant that lands INSIDE a 09:00-19:00 window in two of those zones
# and OUTSIDE it in the third.
FIXED_UTC = datetime(2026, 6, 15, 15, 30, tzinfo=dt_timezone.utc)

NY_NUMBER = "+12125551234"   # America/New_York
LA_NUMBER = "+13105551234"   # America/Los_Angeles


class _FrozenDatetime(datetime):
    """datetime whose .now() is pinned to FIXED_UTC, so window decisions are
    a pure function of the timezone under test."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FIXED_UTC.replace(tzinfo=None)
        return FIXED_UTC.astimezone(tz)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(calling_rules_module, "datetime", _FrozenDatetime)
    return FIXED_UTC


@pytest.fixture(autouse=True)
def _clear_caches():
    lt._resolve_cached.cache_clear()
    if hasattr(lt, "_normalize_cached"):
        lt._normalize_cached.cache_clear()
    yield
    lt._resolve_cached.cache_clear()
    if hasattr(lt, "_normalize_cached"):
        lt._normalize_cached.cache_clear()


def _rules_9_to_19_in(tz: str) -> CallingRules:
    return CallingRules(
        time_window_start="09:00",
        time_window_end="19:00",
        timezone=tz,
        allowed_days=[0, 1, 2, 3, 4],
    )


# ── 1. precedence ────────────────────────────────────────────────────────
def test_explicit_lead_timezone_beats_phone_derived():
    """The customer told us the zone; a NANP area-code guess must not win."""
    assert lt.resolve_effective_lead_timezone(
        explicit_timezone="America/Los_Angeles",
        phone_number=NY_NUMBER,
    ) == "America/Los_Angeles"


def test_phone_derived_used_when_no_explicit_timezone():
    assert lt.resolve_effective_lead_timezone(
        explicit_timezone=None, phone_number=LA_NUMBER,
    ) == "America/Los_Angeles"
    assert lt.resolve_effective_lead_timezone(
        explicit_timezone="   ", phone_number=NY_NUMBER,
    ) == "America/New_York"


def test_none_when_neither_so_caller_falls_back_to_campaign_tz():
    assert lt.resolve_effective_lead_timezone(None, None) is None
    assert lt.resolve_effective_lead_timezone(None, "not-a-number") is None


def test_explicit_timezone_wins_even_when_feature_flag_is_off(monkeypatch):
    """``leads.timezone`` is customer data, not a guess — the flag only gates
    the phone-derived fallback."""
    monkeypatch.setenv("DIALER_PER_LEAD_TIMEZONE", "0")
    assert lt.resolve_effective_lead_timezone(
        "America/Los_Angeles", NY_NUMBER,
    ) == "America/Los_Angeles"
    # ...and with the flag off the derived path yields nothing.
    assert lt.resolve_effective_lead_timezone(None, LA_NUMBER) is None


def test_feature_flag_default_is_unchanged(monkeypatch):
    """Guard against silently flipping the kill-switch's default."""
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    assert lt.per_lead_timezone_enabled() is True
    for off in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv("DIALER_PER_LEAD_TIMEZONE", off)
        assert lt.per_lead_timezone_enabled() is False


# ── 2. failure paths must never raise ────────────────────────────────────
def test_invalid_iana_string_falls_back_to_derived_without_raising():
    assert lt.resolve_effective_lead_timezone(
        explicit_timezone="Mars/Olympus_Mons", phone_number=LA_NUMBER,
    ) == "America/Los_Angeles"


def test_invalid_iana_and_unresolvable_number_falls_back_to_none():
    assert lt.resolve_effective_lead_timezone(
        explicit_timezone="America/Nowhere", phone_number="banana",
    ) is None


@pytest.mark.parametrize("bad", ["", "   ", None, "US/Not/A/Zone", "12345"])
def test_normalize_rejects_garbage_without_raising(bad):
    assert lt.normalize_lead_timezone(bad) is None


def test_phonenumbers_exploding_does_not_propagate(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("libphonenumber exploded")

    monkeypatch.setattr(lt, "_resolve_cached", _boom)
    # Must fall back, not blow up the dialer.
    assert lt.resolve_effective_lead_timezone(None, LA_NUMBER) is None
    assert lt.resolve_effective_lead_timezone("Europe/London", LA_NUMBER) == "Europe/London"


# ── 3. the decision genuinely differs for ONE fixed instant ──────────────
def test_same_instant_different_zones_different_decision(frozen_clock):
    """The anti-"constant signal" test: same UTC moment, same rules, three
    zones, three outcomes that are not all identical."""
    rules = _rules_9_to_19_in("America/New_York")
    ny, _ = rules.is_within_time_window(tz_override="America/New_York")
    la, la_reason = rules.is_within_time_window(tz_override="America/Los_Angeles")
    ldn, _ = rules.is_within_time_window(tz_override="Europe/London")
    assert ny is True      # 11:30 local
    assert la is False     # 08:30 local — before the window
    assert ldn is True     # 16:30 local
    assert "outside_time_window" in la_reason


# ── 4. the worker actually passes a VARYING timezone ─────────────────────
def _job(lead_id: str, phone: str) -> DialerJob:
    return DialerJob(
        job_id=f"job-{lead_id}",
        campaign_id="campaign-1",
        lead_id=lead_id,
        tenant_id="tenant-1",
        phone_number=phone,
    )


def _worker(rules: CallingRules, lead_tz_value):
    """DialerWorker with every process_job collaborator stubbed except the
    scheduling gate itself."""
    w = DialerWorker()
    w.queue_service = AsyncMock()
    w._redis = None

    w._get_campaign_status = AsyncMock(return_value="running")
    w._tenant_minutes_exhausted = AsyncMock(return_value=False)
    w._get_tenant_rules = AsyncMock(return_value=rules)
    w._get_campaign_calling_config = AsyncMock(return_value={})
    w._get_lead_last_called = AsyncMock(return_value=None)
    w._get_lead_attempts_today = AsyncMock(return_value=0)
    w._get_lead_timezone = AsyncMock(return_value=lead_tz_value)

    w._resolve_batch_size = MagicMock(return_value=0)
    w._campaign_inflight_calls = AsyncMock(return_value=0)
    w._resolve_call_gap = MagicMock(return_value=0)
    w._campaign_seconds_since_last_dial = AsyncMock(return_value=None)
    w._evaluate_call_guard = AsyncMock(return_value="allow")
    w._publish_reason = AsyncMock()
    w._publish_block = AsyncMock()
    w._update_job_status = AsyncMock()
    w._make_call = AsyncMock(return_value="provider-call-1")
    w._create_call_record = AsyncMock(return_value=("call-1", "tk-1", "leg-1"))
    w._update_lead_status = AsyncMock()
    w._mark_campaign_dialed = AsyncMock()
    w._emit_progress_event_throttled = AsyncMock()
    return w


async def _tz_passed_to_gate(worker, job) -> object:
    """Run process_job with the scheduling gate spied on; return the
    lead_timezone it was called with."""
    spy = AsyncMock(return_value=(True, "all_rules_passed"))
    worker.rules_engine.can_make_call = spy
    await worker.process_job(job)
    assert spy.await_count == 1, "scheduling gate was not reached"
    return spy.await_args.kwargs.get("lead_timezone")


@pytest.mark.asyncio
async def test_worker_passes_explicit_lead_timezone(monkeypatch):
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    w = _worker(_rules_9_to_19_in("Europe/London"), "America/Los_Angeles")
    # Explicit column beats the New-York area code on the number.
    assert await _tz_passed_to_gate(w, _job("lead-a", NY_NUMBER)) == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_worker_passes_derived_timezone_when_column_empty(monkeypatch):
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    w = _worker(_rules_9_to_19_in("Europe/London"), None)
    assert await _tz_passed_to_gate(w, _job("lead-b", LA_NUMBER)) == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_worker_passes_none_when_nothing_resolvable(monkeypatch):
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    w = _worker(_rules_9_to_19_in("Europe/London"), None)
    assert await _tz_passed_to_gate(w, _job("lead-c", "+9999999")) is None


@pytest.mark.asyncio
async def test_worker_ignores_invalid_column_value_and_derives(monkeypatch):
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    w = _worker(_rules_9_to_19_in("Europe/London"), "Middle/Earth")
    assert await _tz_passed_to_gate(w, _job("lead-d", LA_NUMBER)) == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_worker_honours_flag_for_derived_path_only(monkeypatch):
    monkeypatch.setenv("DIALER_PER_LEAD_TIMEZONE", "0")
    w_derived = _worker(_rules_9_to_19_in("Europe/London"), None)
    assert await _tz_passed_to_gate(w_derived, _job("lead-e", LA_NUMBER)) is None

    w_explicit = _worker(_rules_9_to_19_in("Europe/London"), "America/Los_Angeles")
    assert await _tz_passed_to_gate(
        w_explicit, _job("lead-f", LA_NUMBER)
    ) == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_lead_timezone_lookup_failure_never_drops_the_dial(monkeypatch):
    """A DB hiccup reading leads.timezone must degrade to the derived/campaign
    zone, not raise out of process_job."""
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    w = _worker(_rules_9_to_19_in("Europe/London"), None)
    w._db_pool = None  # real _get_lead_timezone hits a None pool
    del w._get_lead_timezone  # use the real implementation
    assert await _tz_passed_to_gate(w, _job("lead-g", LA_NUMBER)) == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_window_decision_differs_per_lead_at_the_call_site(
    frozen_clock, monkeypatch,
):
    """END OF THE TRAP: same campaign, same instant, real scheduling engine —
    the New York lead is dialled and the California lead is held. Before the
    wiring both were dialled, because the window ran in the campaign's tz.
    """
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    rules = _rules_9_to_19_in("America/New_York")  # campaign tz: 11:30 => open

    w_ny = _worker(rules, None)
    await w_ny.process_job(_job("lead-ny", NY_NUMBER))
    assert w_ny._make_call.await_count == 1, "New York lead should have been dialled"

    w_la = _worker(rules, None)
    await w_la.process_job(_job("lead-la", LA_NUMBER))
    assert w_la._make_call.await_count == 0, (
        "California lead was dialled at 08:30 their local time — the window "
        "is still being evaluated in the campaign's timezone"
    )
    w_la.queue_service.schedule_retry.assert_awaited()


@pytest.mark.asyncio
async def test_retry_delay_also_uses_the_lead_timezone(frozen_clock, monkeypatch):
    """If the block is computed in the lead's tz but the sleep is computed in
    the campaign's, the job re-wakes at the wrong moment."""
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    rules = _rules_9_to_19_in("America/New_York")
    w = _worker(rules, None)
    spy = MagicMock(return_value=1800)
    w.rules_engine.get_delay_until_next_window = spy

    await w.process_job(_job("lead-la2", LA_NUMBER))

    assert spy.call_count == 1
    assert spy.call_args.kwargs.get("lead_timezone") == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_uk_campaign_uk_lead_behaviour_is_unchanged(frozen_clock, monkeypatch):
    """Sanity: the wiring must not stop calls that are legal today. A London
    campaign dialling a London number resolves to the same zone it already
    used, so the decision is identical."""
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    rules = _rules_9_to_19_in("Europe/London")
    w = _worker(rules, None)
    await w.process_job(_job("lead-uk", "+442079460000"))
    assert w._make_call.await_count == 1

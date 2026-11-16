"""An outbound call ends no later than the moment its tenant's balance runs out.

Readiness plan: "Fix prepaid-minute overrun so active/reserved call usage cannot
exceed the customer balance." Outbound minutes were checked once, before
dialling, for one free minute; a live call then ran to the 300/600 s platform
limits whatever the balance. The balance is now shared in real time by the
tenant's live answered outbound calls and enforced through the same deadline
the watchdog and the wrap-up nudge already use. Inbound calls are admitted
with their own cap and are never touched here.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.domain.services import minutes_quota
from app.domain.services.telephony import lifecycle

TENANT = "20000000-0000-4000-8000-0000000000b1"
OTHER_TENANT = "20000000-0000-4000-8000-0000000000b2"
NOW = 5000.0  # the fake monotonic clock every session is measured against
MAX_S, SOFT_S = 600, 300
TICK = lifecycle._DURATION_WATCHDOG_TICK_S  # 30 s: deadlines are set one tick early
LIVE: dict = {"items": []}  # what the fake state backend currently holds


class _CallSession:
    """Duration is measured from session creation, i.e. it includes ringing."""

    def __init__(self, duration: float) -> None:
        self.duration = duration
        self.conversation_history: list = []

    def get_duration_seconds(self) -> float:
        return self.duration

    def is_stale(self, _timeout) -> bool:
        return False


def _session(*, ring_s=0.0, used_s=0.0, tenant=TENANT, inbound=False, test=False):
    """A live answered call: ``ring_s`` of ringing, then ``used_s`` of talk."""
    cs = _CallSession(ring_s + used_s)
    if test:
        cs._lead_capture_is_test = True
    return SimpleNamespace(
        call_id="call-b1",
        call_session=cs,
        _dialer_tenant_id=tenant,
        _answered_at_monotonic=NOW - used_s,
        _is_true_inbound=inbound,
    )


def _deadline(vs) -> float:
    return lifecycle._effective_deadline_seconds(vs, max_duration_s=MAX_S, soft_cap_s=SOFT_S)


def _talk_limit(vs) -> float:
    """Seconds of talk time (from answer) the call may still reach."""
    used = NOW - vs._answered_at_monotonic
    return used + (_deadline(vs) - vs.call_session.get_duration_seconds())


@pytest.fixture(autouse=True)
def _clock_and_outage_state(monkeypatch):
    monkeypatch.setattr(lifecycle, "_loop_now", lambda: NOW)
    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(iter_voice_session_items=lambda: iter(list(LIVE["items"]))),
    )
    LIVE["items"] = []
    lifecycle._balance_outage_tenants.clear()
    lifecycle._balance_tenant_locks.clear()
    yield
    lifecycle._balance_outage_tenants.clear()
    lifecycle._balance_tenant_locks.clear()


def _balance(monkeypatch, remaining=None, *, error=None, per_tenant=None):
    """Fake the database boundary; returns the list of tenants that were read."""
    reads: list[str] = []

    async def fake_remaining(conn, tenant_id):
        reads.append(tenant_id)
        if error is not None:
            if isinstance(error, float):
                await asyncio.sleep(error)
            else:
                raise error
        if per_tenant is not None:
            return per_tenant[tenant_id]
        return remaining

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kwargs):
        yield object()

    monkeypatch.setattr(minutes_quota, "outbound_remaining_seconds", fake_remaining)
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", scoped)
    monkeypatch.setattr(
        "app.core.container.get_container", lambda: SimpleNamespace(db_pool=object())
    )
    return reads


def _items(*sessions):
    return [(f"call-{i}", vs) for i, vs in enumerate(sessions)]


async def _refresh(*sessions):
    LIVE["items"] = _items(*sessions)
    await lifecycle._refresh_outbound_balance_caps(LIVE["items"])


# --------------------------------------------------------------------------- #
# The share of the balance each live call may use
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_two_minutes_left_ends_the_call_one_tick_before_the_balance_runs_out(monkeypatch):
    _balance(monkeypatch, 120)
    vs = _session(used_s=0)
    await _refresh(vs)
    assert _deadline(vs) == 120 - TICK  # 90 s of talk time from answer
    assert lifecycle._collect_expired_sessions(
        _items(vs), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
    ) == ([], [])
    vs.call_session.duration = 91
    stale, overlong = lifecycle._collect_expired_sessions(
        _items(vs), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
    )
    assert overlong == ["call-0"] and stale == []


@pytest.mark.asyncio
async def test_the_watchdog_ends_the_call_at_the_first_tick_and_never_after_the_balance(monkeypatch):
    _balance(monkeypatch, 120)
    vs = _session(used_s=0)
    await _refresh(vs)
    ended_at = None
    for tick in range(1, 10):
        vs.call_session.duration = tick * TICK
        _, overlong = lifecycle._collect_expired_sessions(
            _items(vs), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
        )
        if overlong:
            ended_at = tick * TICK
            break
    assert ended_at == 120 and ended_at <= 120  # the balance (120 s) is not overrun


@pytest.mark.asyncio
async def test_a_share_smaller_than_one_tick_gives_a_deadline_of_now(monkeypatch):
    _balance(monkeypatch, 20)
    vs = _session(ring_s=5, used_s=0)
    await _refresh(vs)
    assert _deadline(vs) == 5  # the session's duration right now


@pytest.mark.asyncio
async def test_wrap_up_nudge_is_due_inside_its_window_before_the_balance_ends(monkeypatch):
    _balance(monkeypatch, 120)
    vs = _session(used_s=50)  # 70 s of balance left; deadline is 90 s of talk
    await _refresh(vs)
    candidates = lifecycle._collect_wrap_up_candidates(
        _items(vs), max_duration_s=MAX_S, soft_cap_s=SOFT_S, lead_s=45
    )
    assert [(c[0], round(c[2])) for c in candidates] == [("call-0", 40)]
    # Not yet due with the whole balance ahead of it.
    fresh = _session(used_s=10)
    await _refresh(fresh)
    assert _deadline(fresh) == 90
    assert lifecycle._collect_wrap_up_candidates(
        _items(fresh), max_duration_s=MAX_S, soft_cap_s=SOFT_S, lead_s=45
    ) == []


@pytest.mark.asyncio
async def test_wrap_up_is_due_on_a_watchdog_tick_before_the_earlier_deadline(monkeypatch):
    _balance(monkeypatch, 120)
    vs = _session(used_s=0)
    await _refresh(vs)
    deadline = _deadline(vs)
    first_nudge = None
    for tick in range(1, 10):
        vs.call_session.duration = tick * TICK
        if lifecycle._collect_wrap_up_candidates(
            _items(vs), max_duration_s=MAX_S, soft_cap_s=SOFT_S, lead_s=45
        ):
            first_nudge = tick * TICK
            break
    assert first_nudge is not None and first_nudge < deadline


@pytest.mark.asyncio
async def test_live_calls_of_one_tenant_share_the_balance(monkeypatch):
    _balance(monkeypatch, 120)
    a, b = _session(used_s=0), _session(used_s=0)
    await _refresh(a, b)
    assert _talk_limit(a) == 60 - TICK and _talk_limit(b) == 60 - TICK
    assert _talk_limit(a) + _talk_limit(b) <= 120

    # When one ends the next refresh gives the other the rest (a has used 30 s).
    a2 = _session(used_s=30)
    await _refresh(a2)
    assert _talk_limit(a2) == 120 - TICK


@pytest.mark.asyncio
async def test_a_late_second_call_gets_a_fair_share_not_zero(monkeypatch):
    _balance(monkeypatch, 300)
    a, b = _session(used_s=100), _session(used_s=0)
    await _refresh(a, b)
    # 300 - 100 used = 200 s left, split two ways: 100 s each, less one tick.
    assert _talk_limit(b) == 100 - TICK
    assert _talk_limit(a) == 100 + 100 - TICK
    assert _talk_limit(a) + _talk_limit(b) <= 300


@pytest.mark.asyncio
async def test_a_late_second_call_with_less_than_a_tick_each_ends_at_once(monkeypatch):
    _balance(monkeypatch, 120)
    a, b = _session(used_s=100), _session(used_s=0)
    await _refresh(a, b)
    # 20 s left, 10 s each: smaller than a tick, so the deadline is "now".
    assert _talk_limit(b) == 0
    assert _talk_limit(a) == 100


@pytest.mark.asyncio
async def test_nothing_left_gives_a_new_call_zero(monkeypatch):
    _balance(monkeypatch, 100)
    a, b = _session(used_s=100), _session(used_s=0)
    await _refresh(a, b)
    assert _talk_limit(b) == 0
    assert _talk_limit(a) == 100
    b.call_session.duration = 1
    _, overlong = lifecycle._collect_expired_sessions(
        _items(b), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
    )
    assert overlong == ["call-0"]


@pytest.mark.asyncio
async def test_each_tenant_has_its_own_balance_read_once(monkeypatch):
    reads = _balance(monkeypatch, per_tenant={TENANT: 120, OTHER_TENANT: 40})
    a1, a2 = _session(), _session()
    b = _session(tenant=OTHER_TENANT)
    await _refresh(a1, a2, b)
    assert sorted(reads) == sorted([TENANT, OTHER_TENANT])
    assert _talk_limit(a1) == 60 - TICK and _talk_limit(b) == 40 - TICK


# --------------------------------------------------------------------------- #
# Who is capped
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_an_account_with_no_limit_is_not_capped_and_a_cap_is_lifted(monkeypatch):
    _balance(monkeypatch, None)
    vs = _session(used_s=50)
    vs._balance_deadline_seconds = 70.0  # left from an earlier read
    await _refresh(vs)
    assert _deadline(vs) == SOFT_S
    assert not getattr(vs, "_balance_deadline_seconds", None)


@pytest.mark.asyncio
async def test_a_test_call_is_not_capped_and_reads_nothing(monkeypatch):
    reads = _balance(monkeypatch, 0)
    vs = _session(test=True)
    await _refresh(vs)
    assert reads == []
    assert _deadline(vs) == SOFT_S


@pytest.mark.asyncio
async def test_a_test_call_does_not_take_a_share_of_the_balance(monkeypatch):
    _balance(monkeypatch, 120)
    real, test = _session(), _session(test=True)
    await _refresh(real, test)
    assert _talk_limit(real) == 120 - TICK
    assert _deadline(test) == SOFT_S


@pytest.mark.asyncio
async def test_a_call_still_ringing_is_not_capped(monkeypatch):
    reads = _balance(monkeypatch, 0)
    vs = _session()
    del vs._answered_at_monotonic
    await _refresh(vs)
    assert reads == [] and _deadline(vs) == SOFT_S


# --------------------------------------------------------------------------- #
# The balance read itself
# --------------------------------------------------------------------------- #

def _row(**over):
    base = {
        "minutes_allocated": 0, "plan_minutes": 0, "billing_model": "subscription",
        "no_plan": False, "has_ledger": False, "window_start": None,
    }
    base.update(over)
    return base


class _Conn:
    def __init__(self, row, accounted=0):
        self.row, self.accounted, self.queries = row, accounted, []

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        return self.row

    async def fetchval(self, query, *args):
        self.queries.append(query)
        return self.accounted


@pytest.mark.asyncio
async def test_helper_returns_none_for_a_verified_unlimited_plan():
    conn = _Conn(_row(minutes_allocated=0, plan_minutes=0, billing_model="subscription"))
    assert await minutes_quota.outbound_remaining_seconds(conn, TENANT) is None


@pytest.mark.asyncio
async def test_helper_reports_seconds_left_and_never_goes_negative():
    row = _row(minutes_allocated=10, plan_minutes=10)
    assert await minutes_quota.outbound_remaining_seconds(_Conn(row, accounted=450), TENANT) == 150
    assert await minutes_quota.outbound_remaining_seconds(_Conn(row, accounted=9999), TENANT) == 0


@pytest.mark.asyncio
async def test_prepaid_account_with_zero_plan_minutes_is_capped_by_its_topups(monkeypatch):
    async def topup_balance(conn, tenant_id, base):
        return 5  # five bought minutes still unused

    monkeypatch.setattr("app.domain.services.topup_usage.topup_balance", topup_balance)
    conn = _Conn(
        _row(minutes_allocated=0, plan_minutes=0, billing_model="prepaid", has_ledger=True),
        accounted=60,
    )
    assert await minutes_quota.outbound_remaining_seconds(conn, TENANT) == 5 * 60 - 60


@pytest.mark.asyncio
async def test_prepaid_account_with_no_topups_has_nothing_left():
    conn = _Conn(_row(minutes_allocated=0, plan_minutes=0, billing_model="prepaid"))
    assert await minutes_quota.outbound_remaining_seconds(conn, TENANT) == 0


@pytest.mark.asyncio
async def test_helper_is_tenant_scoped_and_takes_no_row_lock():
    conn = _Conn(_row(minutes_allocated=1, plan_minutes=1))
    await minutes_quota.outbound_remaining_seconds(conn, TENANT)
    text = " ".join(conn.queries).upper()
    assert "FOR UPDATE" not in text
    assert not any(w in text for w in ("INSERT ", "UPDATE ", "DELETE "))


@pytest.mark.asyncio
async def test_helper_raises_when_the_allowance_cannot_be_established():
    with pytest.raises(minutes_quota.MeteringUnavailable):
        await minutes_quota.outbound_remaining_seconds(_Conn(None), TENANT)


# --------------------------------------------------------------------------- #
# Balance unreadable
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unreadable_balance_gives_the_provisional_cap_and_keeps_the_call(monkeypatch, caplog):
    _balance(monkeypatch, error=RuntimeError("database unavailable"))
    vs = _session(ring_s=20, used_s=60)
    with caplog.at_level(logging.WARNING, logger=lifecycle.logger.name):
        await _refresh(vs)  # never raises
    assert lifecycle._OUTBOUND_BALANCE_UNKNOWN_CAP_S == 180
    assert _talk_limit(vs) == 180
    assert _deadline(vs) == 80 + (180 - 60)
    stale, overlong = lifecycle._collect_expired_sessions(
        _items(vs), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
    )
    assert (stale, overlong) == ([], [])
    # The provisional limit is a guess: it gets no one-tick margin.
    assert vs._balance_deadline_seconds == 80 + (180 - 60)


@pytest.mark.asyncio
async def test_a_timed_out_read_is_treated_as_unreadable(monkeypatch):
    _balance(monkeypatch, error=5.0)
    monkeypatch.setattr(lifecycle, "_BALANCE_READ_TIMEOUT_S", 0.01)
    vs = _session()
    await _refresh(vs)
    assert _talk_limit(vs) == 180


@pytest.mark.asyncio
async def test_a_later_good_read_replaces_the_provisional_cap(monkeypatch):
    _balance(monkeypatch, error=RuntimeError("down"))
    vs = _session(used_s=10)
    await _refresh(vs)
    assert _talk_limit(vs) == 180
    _balance(monkeypatch, 600)
    await _refresh(vs)
    assert vs._balance_deadline_seconds == 10 + (590 - TICK)
    assert _deadline(vs) == SOFT_S  # the 300 s soft cap is below the balance limit


@pytest.mark.asyncio
async def test_one_warning_per_tenant_per_outage(monkeypatch, caplog):
    _balance(monkeypatch, error=RuntimeError("down"))
    vs = _session()
    with caplog.at_level(logging.WARNING, logger=lifecycle.logger.name):
        await _refresh(vs)
        await _refresh(vs)
        await _refresh(vs)
    unreadable = [r for r in caplog.records if "outbound_balance_unreadable" in r.getMessage()]
    assert len(unreadable) == 1
    # Recovery then a second outage warns again.
    _balance(monkeypatch, 500)
    await _refresh(vs)
    _balance(monkeypatch, error=RuntimeError("down again"))
    with caplog.at_level(logging.WARNING, logger=lifecycle.logger.name):
        await _refresh(vs)
    unreadable = [r for r in caplog.records if "outbound_balance_unreadable" in r.getMessage()]
    assert len(unreadable) == 2


# --------------------------------------------------------------------------- #
# Interaction with the other limits
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_the_accounts_own_maximum_wins_when_it_is_smaller(monkeypatch):
    _balance(monkeypatch, 240)  # balance deadline 210 s
    vs = _session()
    vs._max_call_duration_seconds = 90
    await _refresh(vs)
    assert _deadline(vs) == 90
    assert vs._max_call_duration_seconds == 90  # the balance never overwrites it


@pytest.mark.asyncio
async def test_the_balance_wins_when_it_is_smaller_than_the_accounts_maximum(monkeypatch):
    _balance(monkeypatch, 60)
    vs = _session()
    vs._max_call_duration_seconds = 90
    await _refresh(vs)
    assert _deadline(vs) == 60 - TICK
    assert vs._max_call_duration_seconds == 90


@pytest.mark.asyncio
async def test_ringing_time_is_not_charged_against_the_balance(monkeypatch):
    _balance(monkeypatch, 120)
    vs = _session(ring_s=50, used_s=0)
    await _refresh(vs)
    # The watchdog measures from session creation, so 50 s of ringing moves the
    # deadline out by 50 s: the callee still gets all 120 s of talk time, less
    # the one-tick margin.
    assert _deadline(vs) == 50 + 120 - TICK
    vs.call_session.duration = 139
    _, overlong = lifecycle._collect_expired_sessions(
        _items(vs), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
    )
    assert overlong == []
    vs.call_session.duration = 141
    _, overlong = lifecycle._collect_expired_sessions(
        _items(vs), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
    )
    assert overlong == ["call-0"]


@pytest.mark.asyncio
async def test_the_balance_limit_binds_even_while_the_agent_is_closing(monkeypatch):
    """The soft cap is waived for a deal being closed; the balance is not."""
    _balance(monkeypatch, 60)
    vs = _session()
    vs._deal_closing = True
    await _refresh(vs)
    vs.call_session.duration = 61
    _, overlong = lifecycle._collect_expired_sessions(
        _items(vs), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
    )
    assert overlong == ["call-0"]


# --------------------------------------------------------------------------- #
# Recorded usage
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_recorded_length_is_the_actual_confirmed_length_even_past_the_share(monkeypatch):
    _balance(monkeypatch, 0)  # zero share: the call runs until the watchdog ends it
    vs = _session()
    await _refresh(vs)
    answered = vs._answered_at_monotonic
    assert lifecycle._confirmed_outbound_duration_seconds(
        vs, answered + 80, answered_at_monotonic=answered
    ) == 80
    # Only the call's own cap still limits it.
    vs._max_call_duration_seconds = 60
    assert lifecycle._confirmed_outbound_duration_seconds(
        vs, answered + 80, answered_at_monotonic=answered
    ) == 60


def test_recorded_length_is_unchanged_without_a_balance_limit():
    vs = SimpleNamespace(_answered_at_monotonic=100.0)
    assert lifecycle._confirmed_outbound_duration_seconds(vs, 280.0) == 180


# --------------------------------------------------------------------------- #
# Inbound is unchanged
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_inbound_calls_are_never_read_or_capped(monkeypatch):
    reads = _balance(monkeypatch, 10)
    vs = _session(inbound=True, used_s=20)
    vs._max_call_duration_seconds = 120
    vs._soft_call_cap_seconds = 0
    await _refresh(vs)
    assert reads == []
    assert _deadline(vs) == 120
    assert not hasattr(vs, "_balance_deadline_seconds")
    vs.call_session.duration = 119
    _, overlong = lifecycle._collect_expired_sessions(
        _items(vs), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
    )
    assert overlong == []
    vs.call_session.duration = 121
    _, overlong = lifecycle._collect_expired_sessions(
        _items(vs), inactivity_timeout_s=300, max_duration_s=MAX_S, soft_cap_s=SOFT_S
    )
    assert overlong == ["call-0"]


@pytest.mark.asyncio
async def test_an_inbound_call_does_not_take_a_share_of_the_outbound_balance(monkeypatch):
    _balance(monkeypatch, 120)
    out, inbound = _session(), _session(inbound=True)
    await _refresh(out, inbound)
    assert _talk_limit(out) == 120 - TICK


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_answer_time_task_refreshes_the_whole_tenant(monkeypatch):
    _balance(monkeypatch, 120)
    first, second = _session(used_s=0), _session(used_s=0)
    LIVE["items"] = _items(first, second)
    await lifecycle._apply_outbound_balance_cap(second)
    assert _talk_limit(first) == 60 - TICK and _talk_limit(second) == 60 - TICK


@pytest.mark.asyncio
async def test_a_failing_refresh_never_raises_into_the_watchdog(monkeypatch):
    _balance(monkeypatch, 120)
    bad = SimpleNamespace(call_session=None)
    bad.__dict__["_answered_at_monotonic"] = NOW
    await lifecycle._refresh_outbound_balance_caps([("x", bad), ("y", object())])


# --------------------------------------------------------------------------- #
# Overlapping refreshes, tenants, lock bookkeeping
# --------------------------------------------------------------------------- #

def _gated_balance(monkeypatch, remaining, gated_tenants):
    """Balance reads for ``gated_tenants`` wait on a gate the test opens."""
    gates = {t: asyncio.Event() for t in gated_tenants}
    started = {t: asyncio.Event() for t in gated_tenants}

    async def fake_remaining(conn, tenant_id):
        if tenant_id in gates:
            started[tenant_id].set()
            await gates[tenant_id].wait()
        return remaining[tenant_id] if isinstance(remaining, dict) else remaining

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kwargs):
        yield object()

    monkeypatch.setattr(minutes_quota, "outbound_remaining_seconds", fake_remaining)
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", scoped)
    monkeypatch.setattr(
        "app.core.container.get_container", lambda: SimpleNamespace(db_pool=object())
    )
    return gates, started


@pytest.mark.asyncio
async def test_overlapping_refreshes_share_the_balance_over_both_calls(monkeypatch):
    gates, started = _gated_balance(monkeypatch, 120, [TENANT])
    first, second = _session(), _session()
    LIVE["items"] = _items(first)
    # The watchdog tick starts a refresh over the one call it can see ...
    tick = asyncio.create_task(lifecycle._refresh_outbound_balance_caps(list(LIVE["items"])))
    await asyncio.wait_for(started[TENANT].wait(), 1)
    # ... a second call is answered while that read waits ...
    LIVE["items"] = _items(first, second)
    answer = asyncio.create_task(lifecycle._apply_outbound_balance_cap(second))
    await asyncio.sleep(0)
    gates[TENANT].set()
    await asyncio.wait_for(asyncio.gather(tick, answer), 2)
    # ... and whichever finished last, the balance is split over two calls.
    assert _talk_limit(first) == 60 - TICK and _talk_limit(second) == 60 - TICK
    assert _talk_limit(first) + _talk_limit(second) <= 120


@pytest.mark.asyncio
async def test_a_slow_read_for_one_tenant_does_not_delay_another(monkeypatch):
    gates, started = _gated_balance(monkeypatch, {TENANT: 120, OTHER_TENANT: 90}, [TENANT])
    a, b = _session(), _session(tenant=OTHER_TENANT)
    LIVE["items"] = _items(a, b)
    task = asyncio.create_task(lifecycle._refresh_outbound_balance_caps(list(LIVE["items"])))
    await asyncio.wait_for(started[TENANT].wait(), 1)
    for _ in range(20):
        await asyncio.sleep(0)
    assert _talk_limit(b) == 90 - TICK  # tenant B is already capped
    assert not hasattr(a, "_balance_deadline_seconds")  # tenant A still waits
    gates[TENANT].set()
    await asyncio.wait_for(task, 2)
    assert _talk_limit(a) == 120 - TICK


@pytest.mark.asyncio
async def test_a_session_that_ended_during_the_read_is_skipped(monkeypatch):
    gates, started = _gated_balance(monkeypatch, 120, [TENANT])
    stays, ends = _session(), _session()
    LIVE["items"] = _items(stays, ends)
    task = asyncio.create_task(lifecycle._refresh_outbound_balance_caps(list(LIVE["items"])))
    await asyncio.wait_for(started[TENANT].wait(), 1)
    LIVE["items"] = _items(stays)  # the second call hung up while the read waited
    gates[TENANT].set()
    await asyncio.wait_for(task, 2)
    assert not hasattr(ends, "_balance_deadline_seconds")
    assert _talk_limit(stays) == 120 - TICK  # the whole balance, not half


@pytest.mark.asyncio
async def test_no_lock_is_kept_for_a_tenant_without_live_outbound_calls(monkeypatch):
    _balance(monkeypatch, 120)
    vs = _session()
    await _refresh(vs)
    assert lifecycle._balance_tenant_locks.keys() <= {TENANT}
    await _refresh()  # the call ended
    assert lifecycle._balance_tenant_locks == {}
    # And a call that vanished during its own refresh leaves nothing behind.
    gates, started = _gated_balance(monkeypatch, 120, [TENANT])
    gone = _session()
    LIVE["items"] = _items(gone)
    task = asyncio.create_task(lifecycle._refresh_outbound_balance_caps(list(LIVE["items"])))
    await asyncio.wait_for(started[TENANT].wait(), 1)
    LIVE["items"] = []
    gates[TENANT].set()
    await asyncio.wait_for(task, 2)
    assert lifecycle._balance_tenant_locks == {}

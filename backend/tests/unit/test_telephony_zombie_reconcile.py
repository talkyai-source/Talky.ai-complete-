"""Unit tests for the watchdog's zombie-session reconcile logic.

Guards the fix for the 10/10 concurrency-leak incident (2026-06-09): a call
whose ChannelDestroyed event was missed left a voice session in the local
dict, the watchdog kept refreshing its global concurrency lease, and ~10 such
leaks filled the cap and blocked ALL outbound calls. `_detect_zombie_sessions`
reconciles local sessions against Asterisk's live channel list so a session
with no real channel is torn down (releasing its slot) within ~60s.
"""
from __future__ import annotations

from app.domain.services.telephony.lifecycle import (
    _detect_zombie_sessions,
    _zombie_channel_ticks,
    _ZOMBIE_TICK_THRESHOLD,
)


def _reset():
    _zombie_channel_ticks.clear()


def test_threshold_is_two_ticks():
    # ~60s at the 30s watchdog cadence — fast enough to never hit the cap,
    # slow enough to debounce a transient ARI hiccup.
    assert _ZOMBIE_TICK_THRESHOLD == 2


def test_none_channel_list_is_a_noop():
    """ARI unreachable → return nothing AND advance no counter, so a flaky
    read can never tear down a live call."""
    _reset()
    assert _detect_zombie_sessions(["a", "b"], None) == []
    assert _zombie_channel_ticks == {}


def test_present_session_is_never_a_zombie():
    _reset()
    for _ in range(5):
        assert _detect_zombie_sessions(["a"], {"a"}) == []
    assert "a" not in _zombie_channel_ticks


def test_missing_session_trips_only_after_threshold():
    _reset()
    # Tick 1: absent but below threshold — not yet a zombie.
    assert _detect_zombie_sessions(["a"], set()) == []
    assert _zombie_channel_ticks["a"] == 1
    # Tick 2: absent again → zombie.
    assert _detect_zombie_sessions(["a"], set()) == ["a"]


def test_reappearance_resets_the_counter():
    """A call that blips out of the list for one tick then returns must NOT
    be killed — the counter resets on presence."""
    _reset()
    assert _detect_zombie_sessions(["a"], set()) == []      # miss 1
    assert _detect_zombie_sessions(["a"], {"a"}) == []       # present → reset
    assert "a" not in _zombie_channel_ticks
    assert _detect_zombie_sessions(["a"], set()) == []       # miss 1 again, not 2


def test_counters_pruned_for_sessions_no_longer_local():
    """A session that ended normally (gone from the local dict) must not leave
    a dangling counter."""
    _reset()
    _detect_zombie_sessions(["a"], set())                    # a → counter 1
    assert "a" in _zombie_channel_ticks
    _detect_zombie_sessions(["b"], {"b"})                    # a no longer local
    assert "a" not in _zombie_channel_ticks


def test_only_the_missing_session_among_many_is_flagged():
    _reset()
    live = {"live1", "live2"}
    # Two live + one zombie; only the zombie trips after 2 ticks.
    assert _detect_zombie_sessions(["live1", "live2", "dead"], live) == []
    assert _detect_zombie_sessions(["live1", "live2", "dead"], live) == ["dead"]

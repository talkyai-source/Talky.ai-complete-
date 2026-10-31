"""W2 item 5: ``AsteriskAdapter.get_hangup_cause`` must be a non-destructive read.

The lifecycle asks for the captured Q.850 cause once at teardown. Settlement
can be retried (durable terminal outbox / orphan recovery), and each retry
re-resolves the outcome. ``get_hangup_cause`` used to ``pop`` the entry, so the
second resolution saw no cause and a busy / rejected call became NO_ANSWER
(+24 h redial). The read now leaves the entry in place and expires it by age so
memory stays bounded.
"""
from __future__ import annotations

import pytest

from app.infrastructure.telephony import asterisk_adapter as adapter_module
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


@pytest.fixture
def adapter() -> AsteriskAdapter:
    return AsteriskAdapter()


def test_hangup_cause_survives_repeated_reads(adapter):
    adapter._hangup_causes["chan-1"] = "User busy"

    assert adapter.get_hangup_cause("chan-1") == "User busy"
    # A settlement retry asks again and must get the same answer.
    assert adapter.get_hangup_cause("chan-1") == "User busy"
    assert adapter.get_hangup_cause("chan-1") == "User busy"


def test_unknown_channel_still_returns_none(adapter):
    assert adapter.get_hangup_cause("never-seen") is None


def test_hangup_cause_expires_after_the_retention_window(adapter, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(adapter_module.time, "monotonic", lambda: clock["now"])
    adapter._hangup_causes["chan-1"] = "Call Rejected"

    assert adapter.get_hangup_cause("chan-1") == "Call Rejected"

    # Still there just inside the window ...
    clock["now"] += 3599.0
    assert adapter.get_hangup_cause("chan-1") == "Call Rejected"

    # ... gone once it has aged out, and the map itself is cleaned (bounded).
    clock["now"] += 3700.0
    assert adapter.get_hangup_cause("chan-1") is None
    assert "chan-1" not in adapter._hangup_causes


def test_expired_entries_are_swept_by_any_later_read(adapter, monkeypatch):
    clock = {"now": 50.0}
    monkeypatch.setattr(adapter_module.time, "monotonic", lambda: clock["now"])
    for i in range(10):
        adapter._hangup_causes[f"chan-{i}"] = "User busy"
        assert adapter.get_hangup_cause(f"chan-{i}") == "User busy"

    clock["now"] += 7200.0
    adapter._hangup_causes["fresh"] = "No user responding"
    assert adapter.get_hangup_cause("fresh") == "No user responding"

    assert set(adapter._hangup_causes) == {"fresh"}


def test_read_does_not_resurrect_an_entry_evicted_by_the_writer(adapter):
    # The ChannelDestroyed writer evicts the oldest half at 2000 entries; a
    # read bookkeeping record for an evicted id must not leak or mis-answer.
    adapter._hangup_causes["chan-1"] = "User busy"
    assert adapter.get_hangup_cause("chan-1") == "User busy"
    adapter._hangup_causes.pop("chan-1")

    assert adapter.get_hangup_cause("chan-1") is None

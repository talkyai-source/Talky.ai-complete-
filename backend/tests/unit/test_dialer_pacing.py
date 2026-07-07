"""Pacing controls — concurrency (batch size) + inter-call gap.

The critical guarantee: there is NO inter-call gap by default. A wait is only
ever applied when a positive value is explicitly configured on the campaign.
"""
import pytest

from app.workers.dialer_worker import DialerWorker


def _worker() -> DialerWorker:
    # __init__ only constructs the queue/rules helpers (no DB/Redis connection),
    # so a bare instance is enough to exercise the pure resolver methods.
    return DialerWorker()


# ── inter-call gap: OFF by default ──────────────────────────────────────
def test_gap_zero_when_no_config(monkeypatch):
    monkeypatch.delenv("DIALER_CALL_GAP_S", raising=False)
    w = _worker()
    assert w._resolve_call_gap(None) == 0
    assert w._resolve_call_gap({}) == 0
    assert w._resolve_call_gap({"batch_size": 10}) == 0  # unrelated keys don't add a gap


def test_gap_zero_when_explicitly_zero(monkeypatch):
    monkeypatch.delenv("DIALER_CALL_GAP_S", raising=False)
    w = _worker()
    assert w._resolve_call_gap({"call_gap_seconds": 0}) == 0


def test_gap_respected_only_when_selected(monkeypatch):
    monkeypatch.delenv("DIALER_CALL_GAP_S", raising=False)
    w = _worker()
    assert w._resolve_call_gap({"call_gap_seconds": 30}) == 30
    assert w._resolve_call_gap({"call_gap_seconds": 150}) == 150  # 2m30s


def test_gap_bad_or_negative_value_is_no_gap(monkeypatch):
    monkeypatch.delenv("DIALER_CALL_GAP_S", raising=False)
    w = _worker()
    assert w._resolve_call_gap({"call_gap_seconds": "abc"}) == 0
    assert w._resolve_call_gap({"call_gap_seconds": -5}) == 0  # clamped to 0


# ── batch size (concurrency) still has its sensible default ─────────────
def test_batch_size_default_and_selection(monkeypatch):
    monkeypatch.delenv("DIALER_BATCH_SIZE", raising=False)
    w = _worker()
    assert w._resolve_batch_size(None) == 10       # default concurrency
    assert w._resolve_batch_size({"batch_size": 20}) == 20
    assert w._resolve_batch_size({"batch_size": 0}) == 0  # explicitly unbounded

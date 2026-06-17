"""Unit tests for the rolling-P95 latency alerter."""
import app.domain.services.voice_pipeline.latency_alerter as la
from app.domain.services.voice_pipeline.latency_alerter import LatencyAlerter


def _tune(monkeypatch, *, min_samples=3, alert=1000, clear=800, cooldown=0, window=10, window_s=1000):
    monkeypatch.setattr(la, "_MIN_SAMPLES", min_samples)
    monkeypatch.setattr(la, "_ALERT_MS", float(alert))
    monkeypatch.setattr(la, "_CLEAR_MS", float(clear))
    monkeypatch.setattr(la, "_COOLDOWN_S", float(cooldown))
    monkeypatch.setattr(la, "_WINDOW", window)
    monkeypatch.setattr(la, "_WINDOW_S", float(window_s))
    # silence the prometheus side effects
    monkeypatch.setattr(LatencyAlerter, "_publish_gauge", staticmethod(lambda p95: None))
    monkeypatch.setattr(LatencyAlerter, "_record_transition", staticmethod(lambda state: None))


def test_no_alert_below_min_samples(monkeypatch):
    _tune(monkeypatch, min_samples=5)
    a = LatencyAlerter()
    for i in range(4):
        assert a.record(5000, now=float(i)) is None   # huge, but too few samples
    assert a._firing is False


def test_fires_when_p95_crosses_threshold(monkeypatch):
    _tune(monkeypatch, min_samples=3, alert=1000)
    a = LatencyAlerter()
    p95 = None
    for i in range(5):
        p95 = a.record(2000, now=float(i))
    assert a._firing is True
    assert p95 is not None and p95 >= 1000


def test_does_not_fire_when_fast(monkeypatch):
    _tune(monkeypatch, min_samples=3, alert=1000)
    a = LatencyAlerter()
    for i in range(6):
        a.record(300, now=float(i))
    assert a._firing is False


def test_clears_after_recovery_with_hysteresis(monkeypatch):
    _tune(monkeypatch, min_samples=3, alert=1000, clear=800, window=10)
    a = LatencyAlerter()
    t = 0.0
    for _ in range(5):                # drive it into firing
        a.record(2000, now=t); t += 1
    assert a._firing is True
    for _ in range(10):              # fill the window with fast turns
        a.record(300, now=t); t += 1
    assert a._firing is False         # recovered below clear threshold


def test_cooldown_blocks_immediate_refire(monkeypatch):
    _tune(monkeypatch, min_samples=3, alert=1000, clear=800, cooldown=100, window=20)
    a = LatencyAlerter()
    t = 0.0
    for _ in range(4):
        a.record(2000, now=t); t += 1
    assert a._firing is True
    # recover
    for _ in range(20):
        a.record(300, now=t); t += 1
    assert a._firing is False
    # spike again immediately — within cooldown, must NOT refire
    for _ in range(4):
        a.record(2000, now=t); t += 0.1
    assert a._firing is False
    # after cooldown elapses, it fires again
    t += 200
    for _ in range(4):
        a.record(2000, now=t); t += 0.1
    assert a._firing is True


def test_negative_and_none_ignored(monkeypatch):
    _tune(monkeypatch, min_samples=1)
    a = LatencyAlerter()
    assert a.record(None, now=1.0) is None
    assert a.record(-5, now=2.0) is None

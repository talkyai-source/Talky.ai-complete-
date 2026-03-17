from __future__ import annotations

import math

import pytest

from app.core import telephony_observability as obs


def _metric_value(payload: str, name: str) -> float:
    for line in payload.splitlines():
        if line.startswith(f"{name} "):
            return float(line.split(" ", 1)[1].strip())
    raise AssertionError(f"Metric not found: {name}")


def test_metrics_token_auth_behavior(monkeypatch):
    monkeypatch.delenv("TELEPHONY_METRICS_TOKEN", raising=False)
    assert obs.is_metrics_request_authorized(None)
    assert obs.is_metrics_request_authorized("anything")

    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "secret-token")
    assert not obs.is_metrics_request_authorized(None)
    assert not obs.is_metrics_request_authorized("wrong-token")
    assert obs.is_metrics_request_authorized("secret-token")


def test_metrics_window_minutes_is_clamped(monkeypatch):
    monkeypatch.setenv("TELEPHONY_METRICS_WINDOW_MINUTES", "2")
    assert obs.get_metrics_window_minutes() == 5

    monkeypatch.setenv("TELEPHONY_METRICS_WINDOW_MINUTES", "600")
    assert obs.get_metrics_window_minutes() == 600

    monkeypatch.setenv("TELEPHONY_METRICS_WINDOW_MINUTES", "999999")
    assert obs.get_metrics_window_minutes() == 7 * 24 * 60


@pytest.mark.asyncio
async def test_refresh_telephony_slo_metrics_updates_gauges(monkeypatch):
    async def _runtime(*_, **__):
        return obs.RuntimeMetrics(
            activation_attempts=20,
            activation_successes=19,
            rollback_attempts=4,
            rollback_successes=3,
            rollback_p50_seconds=1.2,
            rollback_p95_seconds=2.5,
            rollback_max_seconds=3.7,
        )

    async def _calls(*_, **__):
        return obs.CallMetrics(
            setup_attempts=100,
            setup_successes=98,
            answer_latency_p50_seconds=0.4,
            answer_latency_p95_seconds=1.3,
            answer_latency_max_seconds=2.1,
        )

    monkeypatch.setattr(obs, "_fetch_runtime_metrics", _runtime)
    monkeypatch.setattr(obs, "_fetch_call_metrics", _calls)
    monkeypatch.setattr(
        obs,
        "_read_transfer_metrics",
        lambda: obs.TransferMetrics(attempts=25, successes=24, inflight=2),
    )
    monkeypatch.setattr(
        obs,
        "_read_canary_metrics",
        lambda: obs.CanaryMetrics(enabled=True, percent=25.0, frozen=False),
    )

    await obs.refresh_telephony_slo_metrics(db_pool=object(), window_minutes=30)

    payload = obs.render_prometheus_metrics().decode("utf-8")

    assert _metric_value(payload, "talky_telephony_metrics_scrape_success") == 1.0
    assert _metric_value(payload, "talky_telephony_metrics_window_minutes") == 30.0

    assert _metric_value(payload, "talky_telephony_calls_setup_attempts") == 100.0
    assert _metric_value(payload, "talky_telephony_calls_setup_successes") == 98.0
    assert math.isclose(
        _metric_value(payload, "talky_telephony_calls_setup_success_ratio"),
        0.98,
        rel_tol=1e-9,
    )

    assert _metric_value(payload, "talky_telephony_transfers_attempts") == 25.0
    assert _metric_value(payload, "talky_telephony_transfers_successes") == 24.0
    assert math.isclose(
        _metric_value(payload, "talky_telephony_transfers_success_ratio"),
        24.0 / 25.0,
        rel_tol=1e-9,
    )
    assert _metric_value(payload, "talky_telephony_transfers_inflight") == 2.0

    assert _metric_value(payload, "talky_telephony_runtime_activation_attempts") == 20.0
    assert _metric_value(payload, "talky_telephony_runtime_activation_successes") == 19.0
    assert math.isclose(
        _metric_value(payload, "talky_telephony_runtime_activation_success_ratio"),
        19.0 / 20.0,
        rel_tol=1e-9,
    )
    assert _metric_value(payload, "talky_telephony_runtime_rollback_attempts") == 4.0
    assert _metric_value(payload, "talky_telephony_runtime_rollback_successes") == 3.0

    assert _metric_value(payload, "talky_telephony_canary_enabled") == 1.0
    assert _metric_value(payload, "talky_telephony_canary_percent") == 25.0
    assert _metric_value(payload, "talky_telephony_canary_frozen") == 0.0

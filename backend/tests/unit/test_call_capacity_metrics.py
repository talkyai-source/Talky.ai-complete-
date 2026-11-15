"""The scrape exposes live calls, the call limit and per-provider concurrency.

infra/alertmanager/talky-alerts.yaml and the capacity dashboard read
talky_active_calls, talky_pod_capacity, talky_provider_inflight and
talky_provider_max_concurrent; the app used to produce none of them.
"""
from __future__ import annotations

import importlib
import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import telephony_observability as obs


def _value(text: str, name: str, labels: str = "") -> float | None:
    m = re.search(rf"^{re.escape(name)}{re.escape(labels)} ([0-9.e+-]+)$", text, re.M)
    return float(m.group(1)) if m else None


def _client() -> TestClient:
    operational = importlib.import_module("app.api.operational")
    app = FastAPI()
    operational.register_operational_routes(app)
    return TestClient(app)


@pytest.fixture()
def authed(monkeypatch):
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "s3cr3t")
    monkeypatch.setenv("TELEPHONY_ENABLED", "true")
    return {"X-Metrics-Token": "s3cr3t"}


def _patch_sources(monkeypatch, *, active=7, limit=50, providers=None):
    import app.domain.services.telephony.config as cfg
    import app.domain.services.telephony.state_backend as sb
    import app.infrastructure.providers.provider_concurrency as pc

    snaps = providers if providers is not None else [
        {"provider": "deepgram", "max": 10, "in_flight": 4}
    ]
    monkeypatch.setattr(
        sb, "get_state_backend", lambda: SimpleNamespace(voice_session_count=lambda: active)
    )
    monkeypatch.setattr(cfg, "_MAX_TELEPHONY_SESSIONS", limit)
    monkeypatch.setattr(
        pc, "all_guards", lambda: [SimpleNamespace(snapshot=lambda s=s: s) for s in snaps]
    )


def test_set_call_capacity_metrics_renders_values():
    obs.set_call_capacity_metrics(
        active_calls=2,
        pod_capacity=50,
        providers=[{"provider": "groq", "max": 8, "in_flight": 3}],
    )
    text = obs.render_prometheus_metrics().decode()
    assert _value(text, "talky_active_calls") == 2.0
    assert _value(text, "talky_pod_capacity") == 50.0
    assert _value(text, "talky_provider_inflight", '{provider="groq"}') == 3.0
    assert _value(text, "talky_provider_max_concurrent", '{provider="groq"}') == 8.0


def test_endpoint_reports_live_calls_and_limit(monkeypatch, authed):
    _patch_sources(monkeypatch, active=7, limit=50)
    resp = _client().get("/metrics", headers=authed)
    assert resp.status_code == 200
    assert _value(resp.text, "talky_active_calls") == 7.0
    assert _value(resp.text, "talky_pod_capacity") == 50.0
    assert _value(resp.text, "talky_provider_inflight", '{provider="deepgram"}') == 4.0
    assert _value(resp.text, "talky_provider_max_concurrent", '{provider="deepgram"}') == 10.0


def test_process_without_telephony_reports_zero(monkeypatch, authed):
    monkeypatch.setenv("TELEPHONY_ENABLED", "false")
    _patch_sources(monkeypatch, active=7, limit=50)
    resp = _client().get("/metrics", headers=authed)
    assert resp.status_code == 200
    assert _value(resp.text, "talky_active_calls") == 0.0
    assert _value(resp.text, "talky_pod_capacity") == 0.0


def test_gathering_failure_does_not_break_scrape(monkeypatch, authed):
    import app.domain.services.telephony.state_backend as sb

    def boom():
        raise RuntimeError("state backend down")

    monkeypatch.setattr(sb, "get_state_backend", boom)
    resp = _client().get("/metrics", headers=authed)
    assert resp.status_code == 200
    assert "# HELP talky_telephony_transfers_inflight" in resp.text


def test_token_rules_unchanged(monkeypatch):
    monkeypatch.delenv("TELEPHONY_METRICS_TOKEN", raising=False)
    assert _client().get("/metrics").status_code == 503
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "s3cr3t")
    assert _client().get("/metrics", headers={"X-Metrics-Token": "wrong"}).status_code == 401

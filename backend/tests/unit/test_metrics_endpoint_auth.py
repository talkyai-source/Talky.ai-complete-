"""
Regression test for P1-12: /metrics must fail CLOSED.

Previously, when TELEPHONY_METRICS_TOKEN was unset, the endpoint served
internal Prometheus metrics with no authentication at all. It must now
return 503 (metrics disabled) when unset, and require a correct token
(constant-time compared) otherwise.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_client() -> TestClient:
    # Re-import so the module picks up the current os.environ at call time
    # (the handler reads os.getenv on every request, not at import time).
    operational = importlib.import_module("app.api.operational")
    app = FastAPI()
    operational.register_operational_routes(app)
    return TestClient(app)


@pytest.fixture()
def client(monkeypatch):
    return _build_client()


def test_metrics_disabled_when_token_unset(client, monkeypatch):
    monkeypatch.delenv("TELEPHONY_METRICS_TOKEN", raising=False)
    resp = client.get("/metrics")
    assert resp.status_code == 503


def test_metrics_disabled_when_token_blank(client, monkeypatch):
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "   ")
    resp = client.get("/metrics")
    assert resp.status_code == 503


def test_metrics_rejects_missing_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "s3cr3t")
    resp = client.get("/metrics")
    assert resp.status_code == 401


def test_metrics_rejects_wrong_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "s3cr3t")
    resp = client.get("/metrics", headers={"X-Metrics-Token": "wrong"})
    assert resp.status_code == 401


def test_metrics_allows_correct_token(client, monkeypatch):
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "s3cr3t")
    resp = client.get("/metrics", headers={"X-Metrics-Token": "s3cr3t"})
    assert resp.status_code == 200
    assert resp.content is not None

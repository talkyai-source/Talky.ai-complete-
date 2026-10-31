from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.core import telephony_observability as obs

_TENANT_ID = "11111111-1111-4111-8111-111111111111"
_CONFIG_ID = "22222222-2222-4222-8222-222222222222"
_CANARY_DID = "15551234567"
_CANDIDATE_DIGEST = "sha256:" + ("a" * 64)
_RUN_ID = "33333333-3333-4333-8333-333333333333"
_GATE_STARTED_AT = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)


def _scope() -> obs.CanaryEvidenceScope:
    return obs.CanaryEvidenceScope(
        tenant_id=_TENANT_ID,
        config_id=_CONFIG_ID,
        did=_CANARY_DID,
        candidate_digest=_CANDIDATE_DIGEST,
        run_id=_RUN_ID,
        gate_started_at=_GATE_STARTED_AT,
    )


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
            latest_call_timestamp_seconds=_GATE_STARTED_AT.timestamp() + 60,
        )

    monkeypatch.setattr(obs, "_fetch_runtime_metrics", _runtime)
    monkeypatch.setattr(obs, "_fetch_call_metrics", _calls)

    async def _transfers(*_, **__):
        return obs.TransferMetrics(attempts=25, successes=24, inflight=2)

    monkeypatch.setattr(obs, "_fetch_transfer_metrics", _transfers)
    monkeypatch.setattr(obs, "_read_canary_evidence_scope", _scope)
    monkeypatch.setattr(
        obs,
        "_read_canary_metrics",
        lambda: obs.CanaryMetrics(enabled=True, percent=25.0, frozen=False),
    )

    await obs.refresh_telephony_slo_metrics(db_pool=object(), window_minutes=30)

    payload = obs.render_prometheus_metrics().decode("utf-8")

    assert _metric_value(payload, "talky_telephony_metrics_scrape_success") == 1.0
    assert _metric_value(payload, "talky_telephony_metrics_window_minutes") == 30.0
    assert _metric_value(payload, "talky_telephony_canary_scope_valid") == 1.0
    assert (
        _metric_value(payload, "talky_telephony_canary_evidence_baseline_timestamp_seconds")
        == _GATE_STARTED_AT.timestamp()
    )
    assert f'talky_telephony_canary_scope_info{{scope_hash="{_scope().scope_hash}"}} 1.0' in payload

    assert _metric_value(payload, "talky_telephony_calls_setup_attempts") == 100.0
    assert _metric_value(payload, "talky_telephony_canary_unique_call_ids") == 100.0
    assert (
        _metric_value(payload, "talky_telephony_canary_latest_call_timestamp_seconds")
        == _GATE_STARTED_AT.timestamp() + 60
    )
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


def test_zero_attempts_are_not_reported_as_perfect_evidence():
    assert obs._safe_ratio(0, 0) == 0.0
    assert obs._safe_ratio(1, 0) == 0.0


def test_canary_evidence_scope_is_exact_and_stably_hashed(monkeypatch):
    monkeypatch.setenv("TELEPHONY_CANARY_TENANT_ID", _TENANT_ID.upper())
    monkeypatch.setenv("TELEPHONY_CANARY_CONFIG_ID", _CONFIG_ID.upper())
    monkeypatch.setenv("TELEPHONY_CANARY_DID", _CANARY_DID)
    monkeypatch.setenv("TELEPHONY_CANARY_CANDIDATE_DIGEST", _CANDIDATE_DIGEST)
    monkeypatch.setenv("TELEPHONY_CANARY_RUN_ID", _RUN_ID.upper())
    monkeypatch.setenv(
        "TELEPHONY_CANARY_GATE_STARTED_AT", _GATE_STARTED_AT.strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    scope = obs._read_canary_evidence_scope()

    assert scope == _scope()
    assert len(scope.scope_hash) == 64
    assert scope.scope_hash == _scope().scope_hash

    assert (
        obs.CanaryEvidenceScope(
            **{
                **scope.__dict__,
                "candidate_digest": "sha256:" + ("b" * 64),
            }
        ).scope_hash
        != scope.scope_hash
    )
    assert (
        obs.CanaryEvidenceScope(
            **{
                **scope.__dict__,
                "run_id": "44444444-4444-4444-8444-444444444444",
            }
        ).scope_hash
        != scope.scope_hash
    )
    assert (
        obs.CanaryEvidenceScope(
            **{
                **scope.__dict__,
                "gate_started_at": scope.gate_started_at + timedelta(seconds=1),
            }
        ).scope_hash
        != scope.scope_hash
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TELEPHONY_CANARY_TENANT_ID", ""),
        ("TELEPHONY_CANARY_CONFIG_ID", "not-a-uuid"),
        ("TELEPHONY_CANARY_DID", "+15551234567"),
        ("TELEPHONY_CANARY_DID", "1555*1234567"),
        ("TELEPHONY_CANARY_CANDIDATE_DIGEST", "a" * 64),
        ("TELEPHONY_CANARY_RUN_ID", "33333333-3333-3333-8333-333333333333"),
        ("TELEPHONY_CANARY_GATE_STARTED_AT", "2026-08-29T00:00:00+00:00"),
    ],
)
def test_canary_evidence_scope_rejects_missing_or_noncanonical_values(monkeypatch, name, value):
    monkeypatch.setenv("TELEPHONY_CANARY_TENANT_ID", _TENANT_ID)
    monkeypatch.setenv("TELEPHONY_CANARY_CONFIG_ID", _CONFIG_ID)
    monkeypatch.setenv("TELEPHONY_CANARY_DID", _CANARY_DID)
    monkeypatch.setenv("TELEPHONY_CANARY_CANDIDATE_DIGEST", _CANDIDATE_DIGEST)
    monkeypatch.setenv("TELEPHONY_CANARY_RUN_ID", _RUN_ID)
    monkeypatch.setenv(
        "TELEPHONY_CANARY_GATE_STARTED_AT", _GATE_STARTED_AT.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        obs._read_canary_evidence_scope()


@pytest.mark.parametrize(
    "offset",
    [
        timedelta(hours=-7),
        timedelta(minutes=2),
    ],
    ids=("stale", "future"),
)
def test_canary_evidence_scope_rejects_stale_or_future_run(monkeypatch, offset):
    # Derive the boundary case when the test executes. Constructing it during
    # collection makes the future case turn into a past timestamp in the full
    # multi-minute suite, creating an order/runtime-dependent false failure.
    started_at = datetime.now(UTC) + offset
    monkeypatch.setenv("TELEPHONY_CANARY_TENANT_ID", _TENANT_ID)
    monkeypatch.setenv("TELEPHONY_CANARY_CONFIG_ID", _CONFIG_ID)
    monkeypatch.setenv("TELEPHONY_CANARY_DID", _CANARY_DID)
    monkeypatch.setenv("TELEPHONY_CANARY_CANDIDATE_DIGEST", _CANDIDATE_DIGEST)
    monkeypatch.setenv("TELEPHONY_CANARY_RUN_ID", _RUN_ID)
    monkeypatch.setenv(
        "TELEPHONY_CANARY_GATE_STARTED_AT",
        started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    with pytest.raises(ValueError):
        obs._read_canary_evidence_scope()


@pytest.mark.asyncio
async def test_refresh_fails_closed_without_exact_scope(monkeypatch):
    monkeypatch.delenv("TELEPHONY_CANARY_TENANT_ID", raising=False)
    monkeypatch.delenv("TELEPHONY_CANARY_CONFIG_ID", raising=False)
    monkeypatch.delenv("TELEPHONY_CANARY_DID", raising=False)
    monkeypatch.delenv("TELEPHONY_CANARY_CANDIDATE_DIGEST", raising=False)
    monkeypatch.delenv("TELEPHONY_CANARY_RUN_ID", raising=False)
    monkeypatch.delenv("TELEPHONY_CANARY_GATE_STARTED_AT", raising=False)

    async def _must_not_fetch(*_, **__):
        raise AssertionError("database evidence must not be queried without exact scope")

    monkeypatch.setattr(obs, "_fetch_runtime_metrics", _must_not_fetch)
    monkeypatch.setattr(obs, "_fetch_call_metrics", _must_not_fetch)
    monkeypatch.setattr(obs, "_fetch_transfer_metrics", _must_not_fetch)

    await obs.refresh_telephony_slo_metrics(db_pool=object(), window_minutes=30)
    payload = obs.render_prometheus_metrics().decode("utf-8")

    assert _metric_value(payload, "talky_telephony_canary_scope_valid") == 0.0
    assert _metric_value(payload, "talky_telephony_metrics_scrape_success") == 0.0
    assert _metric_value(payload, "talky_telephony_calls_setup_attempts") == 0.0
    assert (
        _metric_value(payload, "talky_telephony_canary_evidence_baseline_timestamp_seconds") == 0.0
    )
    assert "talky_telephony_canary_scope_info{" not in payload


@pytest.mark.asyncio
async def test_refresh_zeros_all_evidence_when_any_scoped_query_fails(monkeypatch):
    async def _runtime(*_, **__):
        return obs.RuntimeMetrics(4, 4, 2, 2, 1.0, 1.0, 1.0)

    async def _calls(*_, **__):
        raise RuntimeError("database timeout")

    async def _transfers(*_, **__):
        return obs.TransferMetrics(10, 10, 0)

    monkeypatch.setattr(obs, "_read_canary_evidence_scope", _scope)
    monkeypatch.setattr(obs, "_fetch_runtime_metrics", _runtime)
    monkeypatch.setattr(obs, "_fetch_call_metrics", _calls)
    monkeypatch.setattr(obs, "_fetch_transfer_metrics", _transfers)

    await obs.refresh_telephony_slo_metrics(db_pool=object(), window_minutes=30)
    payload = obs.render_prometheus_metrics().decode("utf-8")

    assert _metric_value(payload, "talky_telephony_metrics_scrape_success") == 0.0
    assert _metric_value(payload, "talky_telephony_runtime_activation_attempts") == 0.0
    assert _metric_value(payload, "talky_telephony_runtime_rollback_attempts") == 0.0
    assert _metric_value(payload, "talky_telephony_calls_setup_attempts") == 0.0
    assert _metric_value(payload, "talky_telephony_canary_unique_call_ids") == 0.0
    assert _metric_value(payload, "talky_telephony_transfers_attempts") == 0.0


class _CaptureConnection:
    def __init__(self, row):
        self.row = row
        self.query = ""
        self.args = ()
        self.commands = []

    def transaction(self):
        return _AcquireContext(self)

    async def execute(self, query):
        self.commands.append(query)

    async def fetchrow(self, query, *args):
        self.query = query
        self.args = args
        return self.row


class _AcquireContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _CapturePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AcquireContext(self.connection)


@pytest.mark.asyncio
async def test_call_and_transfer_queries_use_exact_inbound_scope():
    call_conn = _CaptureConnection(
        {
            "setup_attempts": 0,
            "setup_successes": 0,
            "p50_seconds": 0,
            "p95_seconds": 0,
            "max_seconds": 0,
            "latest_call_timestamp_seconds": 0,
        }
    )
    await obs._fetch_call_metrics(_CapturePool(call_conn), _scope())
    assert call_conn.commands == ["SET LOCAL app.bypass_rls = 'on'"]
    assert "direction = 'inbound'" in call_conn.query
    assert "AND NOT is_test" in call_conn.query
    assert "tenant_id = $2::uuid" in call_conn.query
    assert "called_did = ('+' || $3)" in call_conn.query
    assert "route_snapshot #>> '{inbound_config,id}'" in call_conn.query
    assert "route_snapshot #>> '{route,config_id}'" in call_conn.query
    assert "created_at >= $1::timestamptz" in call_conn.query
    assert "COUNT(DISTINCT id)" in call_conn.query
    assert "INTERVAL '1 minute'" not in call_conn.query
    assert call_conn.args == (
        _GATE_STARTED_AT,
        _TENANT_ID,
        _CANARY_DID,
        _CONFIG_ID,
    )

    transfer_conn = _CaptureConnection({"attempts": 0, "successes": 0, "inflight": 0})
    await obs._fetch_transfer_metrics(_CapturePool(transfer_conn), _scope())
    assert "parent.direction = 'inbound'" in transfer_conn.query
    assert "AND NOT parent.is_test" in transfer_conn.query
    assert "handoff_persisted" in transfer_conn.query
    assert "parent.tenant_id = $2::uuid" in transfer_conn.query
    assert "parent.called_did = ('+' || $3)" in transfer_conn.query
    assert "parent.route_snapshot #>> '{inbound_config,id}'" in transfer_conn.query
    assert "parent.route_snapshot #>> '{route,config_id}'" in transfer_conn.query
    assert "parent.created_at >= $1::timestamptz" in transfer_conn.query
    assert "COUNT(DISTINCT parent_call_id)" in transfer_conn.query
    assert transfer_conn.args == (
        _GATE_STARTED_AT,
        _TENANT_ID,
        _CANARY_DID,
        _CONFIG_ID,
    )


@pytest.mark.asyncio
async def test_runtime_query_requires_exact_tenant_config_and_matching_did_route():
    runtime_conn = _CaptureConnection(
        {
            "activation_attempts": 0,
            "activation_successes": 0,
            "rollback_attempts": 0,
            "rollback_successes": 0,
            "p50_seconds": 0,
            "p95_seconds": 0,
            "max_seconds": 0,
        }
    )
    await obs._fetch_runtime_metrics(_CapturePool(runtime_conn), _scope())
    assert "tenant_runtime_policy_versions" in runtime_conn.query
    assert "event.tenant_id = $2::uuid" in runtime_conn.query
    assert "route->>'route_type' = 'inbound'" in runtime_conn.query
    assert "->>'inbound_config_id' = $3" in runtime_conn.query
    assert "->>'did' = $4" in runtime_conn.query
    assert "->>'candidate_digest' = $5" in runtime_conn.query
    assert "->>'run_id' = $6" in runtime_conn.query
    assert "$4 ~ (route->>'match_pattern')" in runtime_conn.query
    assert "event.created_at >= $1::timestamptz" in runtime_conn.query
    assert "event.request_id IS NOT NULL" in runtime_conn.query
    assert "started.stage = 'precheck'" in runtime_conn.query
    assert "started.status = 'started'" in runtime_conn.query
    assert "started.created_at <= terminal.created_at" in runtime_conn.query
    assert "INTERVAL '1 minute'" not in runtime_conn.query
    assert runtime_conn.args == (
        _GATE_STARTED_AT,
        _TENANT_ID,
        _CONFIG_ID,
        _CANARY_DID,
        _CANDIDATE_DIGEST,
        _RUN_ID,
    )

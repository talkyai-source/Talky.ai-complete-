"""Fail-closed contracts for inbound Prometheus and paging artifacts."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from prometheus_client import generate_latest

import app.core.telephony_observability  # noqa: F401 - registers backend gauges
from app.infrastructure.metrics import inbound_metrics

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
RULES_PATH = (
    ROOT / "telephony" / "observability" / "prometheus" / "rules" / "telephony_ws_k_rules.yml"
)
PROMETHEUS_PATH = ROOT / "telephony" / "observability" / "prometheus" / "prometheus.yml"
ALERTMANAGER_PATH = ROOT / "telephony" / "observability" / "alertmanager" / "alertmanager.yml"
LEGACY_ALERTMANAGER_PATH = ROOT / "alertmanager.yml"
OBSERVABILITY_README = ROOT / "telephony" / "observability" / "README.md"
RELEASE_GATE = ROOT / "docs" / "INBOUND_CALLING_RELEASE_GATE.md"


class _LabeledCounter:
    def __init__(self) -> None:
        self.seen: list[dict[str, str]] = []

    def labels(self, **labels: str) -> _LabeledCounter:
        self.seen.append(labels)
        return self

    def inc(self) -> None:
        return None


def _alert_rules() -> dict[str, dict[str, object]]:
    document = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    alerts: dict[str, dict[str, object]] = {}
    for group in document["groups"]:
        for rule in group["rules"]:
            if "alert" in rule:
                alerts[rule["alert"]] = rule
    return alerts


def test_usage_metrics_preserve_only_real_bounded_alert_dimensions(monkeypatch) -> None:
    counter = _LabeledCounter()
    monkeypatch.setattr(inbound_metrics, "_usage_events", counter)

    inbound_metrics.record_usage_event("finalize", "provider_answer_ambiguous")
    inbound_metrics.record_usage_event("finalize", "settlement_switch_disabled")
    inbound_metrics.record_usage_event("finalize", "usage_exceeded_reservation")
    inbound_metrics.record_usage_event("recovery", "queued")
    inbound_metrics.record_usage_event("tenant-shaped-event", "call-shaped-result")

    assert counter.seen == [
        {"event": "finalize", "result": "provider_answer_ambiguous"},
        {"event": "finalize", "result": "settlement_switch_disabled"},
        {"event": "finalize", "result": "usage_exceeded_reservation"},
        {"event": "recovery", "result": "queued"},
        {"event": "other", "result": "other"},
    ]


def test_inbound_alerts_reference_exported_application_metrics() -> None:
    alerts = _alert_rules()
    required = {
        "TalkyTelephonyMetricsRefreshStale",
        "TalkyInboundRoutingOrAdmissionDependencyFailure",
        "TalkyInboundBillingHoldCreated",
        "TalkyInboundStaleReservationRecoveryQueued",
        "TalkyAsteriskInboundTransferCleanupUnconfirmed",
        "TalkyAsteriskInboundTransferInflightStuck",
    }
    assert required <= alerts.keys()

    expressions = "\n".join(str(alerts[name]["expr"]) for name in required)
    referenced = set(re.findall(r"\b(?:inbound|asterisk)_inbound_[a-z0-9_]+", expressions))
    # inbound_decisions_total and inbound_usage_events_total start with
    # ``inbound_`` rather than ``inbound_inbound_``.
    referenced.update(re.findall(r"\binbound_(?:decisions|usage_events)_total\b", expressions))

    source = (BACKEND / "app" / "infrastructure" / "metrics" / "inbound_metrics.py").read_text(
        encoding="utf-8"
    )
    assert referenced
    assert {name for name in referenced if f'"{name}"' not in source} == set()
    exposition = generate_latest().decode("utf-8")
    assert {name for name in referenced if f"# HELP {name} " not in exposition} == set()

    billing_expr = str(alerts["TalkyInboundBillingHoldCreated"]["expr"])
    for result in (
        "provider_answer_ambiguous",
        "settlement_switch_disabled",
        "usage_exceeded_reservation",
    ):
        assert result in billing_expr


def test_missing_exporter_signals_remain_documented_release_blockers() -> None:
    rules = RULES_PATH.read_text(encoding="utf-8")
    prometheus = yaml.safe_load(PROMETHEUS_PATH.read_text(encoding="utf-8"))
    readme = OBSERVABILITY_README.read_text(encoding="utf-8")
    normalized_readme = re.sub(r"\s+", " ", readme)
    release = RELEASE_GATE.read_text(encoding="utf-8")
    normalized_release = re.sub(r"\s+", " ", release)

    jobs = {item["job_name"] for item in prometheus["scrape_configs"]}
    assert jobs == {"talky_backend_telephony"}
    assert "redis_" not in rules
    assert "pg_" not in rules
    assert "queue_depth" not in rules

    for marker in (
        "oldest unresolved billing-hold age",
        "general ARI orphan backlog",
        "PostgreSQL/Redis target or saturation metrics",
        "dialer queue depth",
    ):
        assert marker in normalized_readme
    assert (
        "observability and paging remain explicit external release blockers" in normalized_release
    )
    assert "Do not add rules for imagined metric names" in normalized_release


def test_checked_in_alertmanager_has_no_fake_or_inline_destination() -> None:
    for path in (ALERTMANAGER_PATH, LEGACY_ALERTMANAGER_PATH):
        raw = path.read_text(encoding="utf-8")
        config = yaml.safe_load(raw)

        receivers = {receiver["name"]: receiver for receiver in config["receivers"]}
        assert {
            "telephony-default",
            "telephony-critical",
            "telephony-warning",
        } <= receivers.keys()
        assert all(set(receiver) == {"name"} for receiver in receivers.values())
        assert config["route"]["receiver"] in receivers
        assert all(route["receiver"] in receivers for route in config["route"]["routes"])

        for forbidden in (
            "${",
            "oncall@your-domain.com",
            "slack_configs",
            "email_configs",
            "webhook_configs",
            "pagerduty_configs",
            "opsgenie_configs",
        ):
            assert forbidden not in raw

    readme = OBSERVABILITY_README.read_text(encoding="utf-8")
    normalized_readme = re.sub(r"\s+", " ", readme)
    assert "receivers deliberately have no delivery integrations" in normalized_readme
    assert "source credentials from mounted secret files" in normalized_readme
    assert "cannot notify a person" in normalized_readme

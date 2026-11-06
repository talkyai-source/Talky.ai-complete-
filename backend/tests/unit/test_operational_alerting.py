"""Item 16: destination-aware delivery and real unit wiring; no real sends."""
from __future__ import annotations

import asyncio
import configparser
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import Request, Response
from pydantic import SecretStr


BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
WEBHOOK = "https://alerts.example.invalid/synthetic-secret-token"
ADDRESS = "oncall@example.invalid"


def settings(*, webhook=None, email=None):
    return NS(talky_alert_webhook_url=SecretStr(webhook) if webhook else None,
              talky_alert_email_to=email)


@pytest.fixture
def alerts(monkeypatch):
    path = BACKEND / "app/core/operational_alerts.py"
    assert path.is_file(), "F9-01 has no operational notification delivery contract"
    module = importlib.import_module("app.core.operational_alerts")
    monkeypatch.setattr(module, "get_settings", lambda: settings())
    return module


class Client:
    def __init__(self, calls, *, status=200, failure=None):
        self.calls, self.status, self.failure = calls, status, failure

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, json):
        self.calls.append((url, json))
        if self.failure:
            raise self.failure
        return NS(status_code=self.status)


def ports(monkeypatch, alerts, *, status=200, failure=None, email_result=True):
    calls, options = [], []

    def client(**kwargs):
        options.append(kwargs)
        return Client(calls, status=status, failure=failure)

    sender = NS(send_email=AsyncMock(return_value=email_result))
    monkeypatch.setattr(alerts.httpx, "AsyncClient", client)
    monkeypatch.setattr(alerts, "get_email_service", lambda: sender)
    return calls, options, sender


@pytest.mark.parametrize("webhook,email,expected", [(None, None, "unconfigured"),
                                                   (WEBHOOK, None, "configured"),
                                                   (None, ADDRESS, "configured"),
                                                   (WEBHOOK, ADDRESS, "configured")])
def test_destination_status_does_not_claim_delivery(alerts, webhook, email, expected):
    assert alerts.alerting_status(settings(webhook=webhook, email=email)) == expected


async def test_unconfigured_logs_exact_message_and_sends_nothing(alerts, monkeypatch, caplog):
    calls, _, sender = ports(monkeypatch, alerts)
    result = await alerts.send_alert("talky-healthwatch.service", settings=settings())
    assert result["delivered"] is False and result["channels"] == {}
    assert "alert not delivered: no destination" in caplog.text
    assert calls == []
    sender.send_email.assert_not_awaited()


@pytest.mark.parametrize("webhook,email", [(WEBHOOK, None), (None, ADDRESS), (WEBHOOK, ADDRESS)])
async def test_configured_destinations_receive_fixed_synthetic_alert(alerts, monkeypatch, webhook, email):
    calls, options, sender = ports(monkeypatch, alerts)
    result = await alerts.send_alert("talky-dialer-worker.service",
                                    settings=settings(webhook=webhook, email=email), synthetic=True)
    assert result["delivered"] is True
    assert result["channels"] == {name: "delivered" for name, value in
                                  (("webhook", webhook), ("email", email)) if value}
    if webhook:
        assert calls[0][0] == WEBHOOK
        assert "talky-dialer-worker.service" in calls[0][1]["text"]
        assert "test" in calls[0][1]["text"].lower()
        assert WEBHOOK not in json.dumps(calls[0][1])
        assert options[0]["follow_redirects"] is False
        assert 0 < options[0]["timeout"] <= 10
    else:
        assert calls == []
    if email:
        sender.send_email.assert_awaited_once()
        args = sender.send_email.call_args
        assert args.kwargs["recipient_email"] == ADDRESS
        assert "test" in args.kwargs["subject"].lower()
    else:
        sender.send_email.assert_not_awaited()


@pytest.mark.parametrize("status", [301, 400, 429, 500])
async def test_non_success_webhook_is_not_reported_delivered(alerts, monkeypatch, status):
    ports(monkeypatch, alerts, status=status)
    result = await alerts.send_alert("talky-api.service", settings=settings(webhook=WEBHOOK))
    assert result["delivered"] is False and result["channels"] == {"webhook": "failed"}


async def test_failed_webhook_still_attempts_configured_email_without_logging_credentials(alerts, monkeypatch, caplog):
    ports(monkeypatch, alerts, failure=RuntimeError(WEBHOOK + " synthetic-password"))
    result = await alerts.send_alert("talky-api.service", settings=settings(webhook=WEBHOOK, email=ADDRESS))
    assert result["channels"] == {"webhook": "failed", "email": "delivered"}
    assert result["delivered"] is True
    assert WEBHOOK not in caplog.text and "synthetic-password" not in caplog.text


async def test_existing_sender_false_is_an_observable_delivery_failure(alerts, monkeypatch):
    ports(monkeypatch, alerts, email_result=False)
    result = await alerts.send_alert("talky-api.service", settings=settings(email=ADDRESS))
    assert result["delivered"] is False and result["channels"] == {"email": "failed"}


async def test_email_hang_is_bounded_and_cancellation_is_not_swallowed(alerts, monkeypatch):
    _, _, sender = ports(monkeypatch, alerts)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def hang(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    sender.send_email.side_effect = hang
    monkeypatch.setattr(alerts, "DELIVERY_TIMEOUT_SECONDS", 0.01)
    result = await asyncio.wait_for(alerts.send_alert("talky-api.service", settings=settings(email=ADDRESS)), 0.5)
    assert result["delivered"] is False and cancelled.is_set()
    entered.clear()
    monkeypatch.setattr(alerts, "DELIVERY_TIMEOUT_SECONDS", 10)
    task = asyncio.create_task(alerts.send_alert("talky-api.service", settings=settings(email=ADDRESS)))
    await asyncio.wait_for(entered.wait(), 0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("unit", ["talky-api.service\nsecret", "../../etc/passwd", "x" * 300])
async def test_untrusted_unit_text_is_rejected_before_delivery(alerts, monkeypatch, unit):
    calls, _, sender = ports(monkeypatch, alerts)
    with pytest.raises(ValueError):
        await alerts.send_alert(unit, settings=settings(webhook=WEBHOOK, email=ADDRESS))
    assert calls == []
    sender.send_email.assert_not_awaited()


@pytest.mark.parametrize("configured,initialized", [(False, True), (True, True), (False, False)])
async def test_real_deep_health_exposes_destination_state_without_echoing_destination(monkeypatch, configured, initialized):
    from app.api.v1.endpoints import health
    read_settings = lambda: settings(webhook=WEBHOOK if configured else None)
    monkeypatch.setattr("app.core.config.get_settings", read_settings)
    # Earlier delivery cases may have imported the helper's ordinary binding.
    if "app.core.operational_alerts" in sys.modules:
        monkeypatch.setattr(sys.modules["app.core.operational_alerts"], "get_settings", read_settings)
    monkeypatch.setattr("app.core.container.get_container", lambda: NS(
        is_initialized=initialized, db_pool=NS(fetchval=AsyncMock(return_value=1)),
        redis=NS(ping=AsyncMock(return_value=True))))
    response = Response()
    result = await health.deep_readiness_probe(response, Request({"type": "http", "headers": []}))
    assert result["alerting"] == ("configured" if configured else "unconfigured")
    assert result["ready"] is initialized
    assert WEBHOOK not in json.dumps(result)


def unit_config(path):
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.read(path, encoding="utf-8")
    return config


def test_every_talky_unit_routes_failure_except_notifier_itself():
    units = [path for path in (BACKEND / "systemd").glob("talky.*")]
    units += [path for path in (BACKEND / "systemd").glob("talky-*")
              if path.suffix in {".service", ".timer", ".target"}]
    units += list((ROOT / "scripts/database/systemd").glob("talklee-*.service"))
    units += list((ROOT / "scripts/database/systemd").glob("talklee-*.timer"))
    assert len(units) >= 25
    for path in units:
        config = unit_config(path)
        route = config.get("Unit", "OnFailure", fallback="")
        if path.name == "talky-notify@.service":
            assert route == "", "Notifier must not recursively notify its own failure"
        else:
            assert route == "talky-notify@%n.service", path.name
            assert path.read_text().count("OnFailure=") == 1, path.name
            assert not config.has_option("Service", "OnFailure"), path.name


def test_notifier_is_bounded_nonprivileged_and_not_enabled_as_a_daemon():
    path = BACKEND / "systemd/talky-notify@.service"
    assert path.is_file(), "F9-01 has no failure notifier unit"
    config = unit_config(path)
    assert config["Service"]["Type"] == "oneshot"
    assert config["Service"]["User"] == "talky"
    assert config["Service"]["Group"] == "talky"
    assert int(config["Service"]["TimeoutStartSec"]) <= 30
    assert "--unit %i" in config["Service"]["ExecStart"]
    assert "--send" in config["Service"]["ExecStart"]
    assert config.get("Service", "Restart", fallback="no") == "no"
    assert not config.has_section("Install")


@pytest.fixture
def notify_cli(alerts):
    path = BACKEND / "scripts/notify_failure.py"
    assert path.is_file(), "F9-01 lacks a synthetic alert-test entrypoint"
    spec = importlib.util.spec_from_file_location("qa_notify_failure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_alert_test_defaults_to_dry_run(notify_cli, monkeypatch, capsys):
    send = AsyncMock(return_value={"delivered": True, "channels": {"webhook": "delivered"}})
    monkeypatch.setattr(notify_cli, "send_alert", send)
    assert notify_cli.main(["--test"]) == 0
    send.assert_not_awaited()
    assert "dry-run" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("delivered,expected", [(True, 0), (False, 1)])
def test_explicit_synthetic_send_reports_actual_delivery_result(notify_cli, monkeypatch, delivered, expected):
    send = AsyncMock(return_value={"delivered": delivered, "channels": {}})
    monkeypatch.setattr(notify_cli, "send_alert", send)
    assert notify_cli.main(["--test", "--send"]) == expected
    send.assert_awaited_once()
    assert send.call_args.kwargs["synthetic"] is True


def test_installer_exposes_synthetic_test_command_and_keeps_existing_timers(tmp_path):
    from tests.unit.test_bh_ops_scripts_deploy import _launch, _real_bash, _write
    if _real_bash() is None:
        pytest.skip("no POSIX bash available")
    shim = tmp_path / "shim"
    shim.mkdir()
    operations = tmp_path / "operations.log"
    for command in ("ln", "install", "systemctl"):
        _write(shim / command, f'#!/usr/bin/env bash\nprintf "%s\\n" "{command} $*" >> "$OPERATIONS"\n')
    result = _launch("exec bash systemd/install-services.sh",
                     dict(os.environ, OPERATIONS=str(operations), TALKY_PROD_HOST="nobody@example.invalid",
                          TALKY_PROD_KEY="fake-key"), BACKEND, shim)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = operations.read_text().splitlines()
    assert any("deploy/talky-alert-test /usr/local/bin/talky-alert-test" in line for line in lines)
    assert any("systemctl enable talky-model-catalog.timer --now" in line for line in lines)
    assert any("systemctl enable talky-recording-discrepancy.timer --now" in line for line in lines)
    assert not any("systemctl enable talky-notify@" in line for line in lines)

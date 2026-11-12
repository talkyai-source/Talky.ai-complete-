"""QA F9-01 follow-up: a unit that keeps failing alerts once an hour, not every run.

talky-notify@ sent one alert per failure. The extended health watch runs every
minute, so one stuck check (the refused carrier line, an unset certificate
path) meant an alert a minute. Now a failing unit alerts once per hour; for the
health watch a different set of failing checks alerts at once, and the alert
names those checks. Synthetic tests are never limited, an undelivered alert
frees its slot, and Redis trouble sends anyway.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import fakeredis
import fakeredis.aioredis
import pytest
from pydantic import SecretStr

BACKEND = Path(__file__).resolve().parents[2]
WEBHOOK = "https://alerts.example.invalid/synthetic-token"


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, BACKEND / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def alerts(monkeypatch, server):
    module = importlib.import_module("app.core.operational_alerts")

    async def client():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr(module, "_alert_state_client", client)
    return module


@pytest.fixture
def notify(alerts):
    return _load("qa_notify_failure_repeat", "scripts/notify_failure.py")


async def _set(server, key, value):
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    await client.set(key, value)
    await client.aclose()


def test_a_unit_that_keeps_failing_alerts_once_an_hour(notify, monkeypatch, capsys):
    send = AsyncMock(return_value={"delivered": True, "channels": {"email": "delivered"}})
    monkeypatch.setattr(notify, "send_alert", send)

    assert notify.main(["--unit", "talky-db-backup.service", "--send"]) == 0
    assert notify.main(["--unit", "talky-db-backup.service", "--send"]) == 0

    send.assert_awaited_once()
    assert "alert suppressed" in capsys.readouterr().out
    # Another unit is not held back by the first one.
    assert notify.main(["--unit", "talky-dialer-worker.service", "--send"]) == 0
    assert send.await_count == 2


async def test_a_new_set_of_failing_health_checks_alerts_at_once(alerts, server):
    unit = "talky-healthwatch.service"
    await _set(server, alerts.HEALTHWATCH_FAILING_KEY, "trunks")

    assert await alerts.claim_alert_slot(unit) == (True, "trunks")
    assert await alerts.claim_alert_slot(unit) == (False, "trunks")

    await _set(server, alerts.HEALTHWATCH_FAILING_KEY, "disk,trunks")
    assert await alerts.claim_alert_slot(unit) == (True, "disk,trunks")


async def test_a_malformed_failing_set_is_ignored(alerts, server):
    await _set(server, alerts.HEALTHWATCH_FAILING_KEY, "disk\nBcc: someone")

    assert await alerts.claim_alert_slot("talky-healthwatch.service") == (True, "")


def test_synthetic_tests_never_touch_the_repeat_limit(notify, alerts, monkeypatch):
    async def no_redis():
        raise AssertionError("a synthetic test must not consult the repeat limit")

    monkeypatch.setattr(alerts, "_alert_state_client", no_redis)
    send = AsyncMock(return_value={"delivered": True, "channels": {}})
    monkeypatch.setattr(notify, "send_alert", send)

    assert notify.main(["--test", "--send"]) == 0
    assert notify.main(["--test", "--send"]) == 0
    assert send.await_count == 2


async def test_redis_trouble_sends_anyway(alerts, monkeypatch):
    async def down():
        raise ConnectionError("redis down")

    monkeypatch.setattr(alerts, "_alert_state_client", down)

    assert await alerts.claim_alert_slot("talky-api.service") == (True, "")


def test_an_undelivered_alert_frees_its_slot(notify, monkeypatch):
    send = AsyncMock(side_effect=[
        {"delivered": False, "channels": {"email": "failed"}},
        {"delivered": True, "channels": {"email": "delivered"}},
    ])
    monkeypatch.setattr(notify, "send_alert", send)

    assert notify.main(["--unit", "talky-api.service", "--send"]) == 1
    assert notify.main(["--unit", "talky-api.service", "--send"]) == 0
    assert send.await_count == 2


class _Client:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, json):
        self.calls.append(json)
        return NS(status_code=200)


@pytest.mark.parametrize("detail,expected", [
    ("disk,trunks", "(failing checks: disk, trunks)"),
    ("trunks\nBcc: x", None),
    ("", None),
])
async def test_the_alert_names_the_failing_checks(alerts, monkeypatch, detail, expected):
    calls = []
    monkeypatch.setattr(alerts.httpx, "AsyncClient", lambda **_kw: _Client(calls))
    settings = NS(talky_alert_webhook_url=SecretStr(WEBHOOK), talky_alert_email_to=None)

    result = await alerts.send_alert("talky-healthwatch.service", settings=settings, detail=detail)

    assert result["delivered"] is True
    text = calls[0]["text"]
    if expected:
        assert expected in text
    else:
        assert "failing checks" not in text and "Bcc" not in text


async def test_the_health_watch_records_only_failing_check_names(server):
    watch = _load("qa_healthwatch_repeat", "scripts/healthwatch.py")
    redis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    result = {
        "disk": {"status": "ok", "used_percent": 40.0},
        "trunks": {"status": "alert", "alert_count": 1},
        "certificates": {"status": "unconfigured", "reason": "certificate_paths_not_configured"},
        "provider_error_rate": {"status": "unavailable"},
    }

    await watch._record_failing_checks(redis, result)
    assert await redis.get(watch.FAILING_KEY) == "certificates,trunks"
    assert 0 < await redis.ttl(watch.FAILING_KEY) <= 600

    await watch._record_failing_checks(redis, {"disk": {"status": "ok"}})
    assert await redis.get(watch.FAILING_KEY) is None
    await redis.aclose()


def test_the_watch_and_the_notifier_agree_on_the_key(alerts):
    watch = _load("qa_healthwatch_key", "scripts/healthwatch.py")
    assert watch.FAILING_KEY == alerts.HEALTHWATCH_FAILING_KEY

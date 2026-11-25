"""A check that is already red must not hide a new failure of the same kind.

With payments, trunks and units permanently red, a 17th failing phone line, a
7th payment to review, or the status updater dying produced no alert because
only the check NAMES were recorded. Now a fingerprint of the evidence is
recorded; a changed fingerprint alerts at once, an unchanged one repeats once
a day. The mailbox check also sees a mailbox that is silent or dying, and the
alert text says in plain words what is wrong.
"""
from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import fakeredis
import fakeredis.aioredis
import pytest
from pydantic import SecretStr

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def watch():
    return _load("hw_new_failures", BACKEND / "scripts/healthwatch.py")


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
async def redis(server):
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    yield client
    await client.aclose()


# ---- item 1: fingerprints ---------------------------------------------------
async def test_trunks_16_then_17_is_recorded_differently_and_alerts_again(watch, alerts, redis):
    unit = "talky-healthwatch.service"
    await watch._record_failing_checks(redis, {"trunks": {"status": "alert", "alert_count": 16}})
    first = await redis.get(watch.FAILING_KEY)
    assert first == "trunks:alert:16"
    assert await alerts.claim_alert_slot(unit) == (True, first)
    assert await alerts.claim_alert_slot(unit) == (False, first)  # same value twice: one send

    await watch._record_failing_checks(redis, {"trunks": {"status": "alert", "alert_count": 17}})
    second = await redis.get(watch.FAILING_KEY)
    assert second == "trunks:alert:17"
    assert await alerts.claim_alert_slot(unit) == (True, second)


async def test_trunks_alert_to_unavailable_is_a_new_failure(watch, alerts, redis):
    unit = "talky-healthwatch.service"
    await watch._record_failing_checks(redis, {"trunks": {"status": "alert", "alert_count": 16}})
    assert (await alerts.claim_alert_slot(unit))[0] is True
    await watch._record_failing_checks(redis, {"trunks": {"status": "unavailable", "reason": "x",
                                                          "alert_count": 16}})
    assert await redis.get(watch.FAILING_KEY) == "trunks:unavailable"
    assert (await alerts.claim_alert_slot(unit))[0] is True


async def test_every_check_has_its_fingerprint(watch, redis):
    result = {
        "trunks": {"status": "alert", "alert_count": 16},
        "payments": {"status": "alert", "needs_review": 6, "stuck": 0},
        "units": {"status": "alert", "failed": ["talky-inbound-synthetic.service", "talky-db-backup.service"]},
        "recordings": {"status": "alert", "missing": 2, "pending_stuck": None, "transfer_missing": 1,
                       "unconfirmed": 0},
        "email": {"status": "stale"},
        "disk": {"status": "alert", "used_percent": 90.0},
        "memory": {"status": "unavailable", "reason": "observation_unavailable"},
        "certificates": {"status": "unconfigured"},
        "ok_one": {"status": "ok"},
        "provider_error_rate": {"status": "unavailable"},
    }
    await watch._record_failing_checks(redis, result)
    got = (await redis.get(watch.FAILING_KEY)).split(",")
    assert got == sorted([
        "certificates:unconfigured", "disk:alert", "email:stale", "memory:unavailable",
        "payments:alert:6+0", "recordings:alert:2+0+1+0", "trunks:alert:16",
        "units:alert:talky-db-backup.service+talky-inbound-synthetic.service",
    ])


async def test_a_seventh_payment_and_a_third_unit_are_new_failures(watch, alerts, redis):
    unit = "talky-healthwatch.service"
    for payments, units, expected in [
        (6, ["a.service", "b.service"], True), (6, ["a.service", "b.service"], False),
        (7, ["a.service", "b.service"], True), (7, ["a.service", "b.service", "c.service"], True),
    ]:
        await watch._record_failing_checks(redis, {
            "payments": {"status": "alert", "needs_review": payments, "stuck": 0},
            "units": {"status": "alert", "failed": units}})
        assert (await alerts.claim_alert_slot(unit))[0] is expected


async def test_free_text_still_never_reaches_the_detail(alerts, server):
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    await client.set(alerts.HEALTHWATCH_FAILING_KEY, "trunks:alert:16\nBcc: x")
    await client.aclose()
    assert await alerts.claim_alert_slot("talky-healthwatch.service") == (True, "")


def _uptime():
    return _load("uptime_new_failures", ROOT / "scripts/uptime_watch.py")


def test_uptime_watch_sees_a_changed_failing_count():
    uw = _uptime()
    url = "https://api.talkleeai.com/api/v1/healthz/watch"

    def result(count):
        body = (b'{"clear": false, "failing": %d, "ran_recently": true}' % count) if count else \
            b'{"clear": true, "failing": 0, "ran_recently": true}'
        return uw.check_http("server-health-watch", url, 10, "clear", lambda u, t: (200, body))

    def issue(count, minutes=5):
        return {"number": 7, "failing": {"server-health-watch"}, "watch_failing": count,
                "last_alert_at": NOW - timedelta(minutes=minutes)}

    assert uw.decide([result(4)], issue(3), NOW) == "comment"
    assert uw.decide([result(3)], issue(3), NOW) == "none"
    assert uw.decide([result(0)], issue(3), NOW) == "close"


def test_the_uptime_issue_body_carries_the_count_and_it_is_read_back():
    uw = _uptime()
    results = [{"name": "server-health-watch", "ok": False, "detail": "x", "watch_failing": 3}]
    body = uw.body_text(results, NOW)

    def gh(args):
        if args[:2] == ["issue", "list"]:
            import json
            return 0, json.dumps([{"number": 1, "title": uw.ISSUE_TITLE, "createdAt": "2026-11-20T11:00:00Z",
                                   "body": body}])
        return 0, ""

    assert uw.find_open_issue(gh)["watch_failing"] == 3


# ---- item 2: the mailbox -----------------------------------------------------
async def test_alert_mail_failure_is_recorded_without_an_initialised_container(monkeypatch, redis):
    from app.domain.services import email_service as mail
    from app.infrastructure.metrics import email_delivery

    monkeypatch.setattr(email_delivery, "_redis_client", lambda: None)

    async def fallback():
        return fakeredis.aioredis.FakeRedis(server=redis.connection_pool.connection_kwargs["server"],
                                            decode_responses=True)

    monkeypatch.setattr(email_delivery, "_fallback_client", fallback, raising=False)
    values = NS(email_user="login@example.invalid", email_pass="fake-only", from_email="a@example.invalid",
                brand_name="Talk-Lee", frontend_url="https://app.example.invalid",
                email_smtp_host="smtp.gmail.com")
    monkeypatch.setattr(mail, "get_settings", lambda: values)

    class Connection:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            raise OSError("smtp down")

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(mail.aiosmtplib, "SMTP", Connection)
    ops = importlib.import_module("app.core.operational_alerts")
    monkeypatch.setattr(ops, "get_email_service", lambda: mail.EmailService())
    settings = NS(talky_alert_webhook_url=None, talky_alert_email_to="owner@example.invalid")

    result = await ops.send_alert("talky-db-backup.service", settings=settings)

    assert result["channels"]["email"] == "failed"
    assert (await redis.hgetall(email_delivery.KEY))["outcome"] == "failed"


async def test_an_old_ok_record_is_stale_and_failing_a_fresh_one_is_ok(watch, redis):
    from app.infrastructure.metrics import email_delivery

    await email_delivery.record_email_outcome("ok", client=redis, now=(NOW - timedelta(hours=80)).timestamp())
    stale = await watch.check_email(redis, now=NOW)
    assert stale["status"] == "stale"
    await watch._record_failing_checks(redis, {"email": stale})
    assert await redis.get(watch.FAILING_KEY) == "email:stale"

    await email_delivery.record_email_outcome("ok", client=redis, now=(NOW - timedelta(hours=2)).timestamp())
    fresh = await watch.check_email(redis, now=NOW)
    assert fresh["status"] == "ok"
    await watch._record_failing_checks(redis, {"email": fresh})
    assert await redis.get(watch.FAILING_KEY) is None


async def test_no_record_stays_non_failing(watch, redis):
    assert await watch.check_email(redis, now=NOW) == {"status": "no_recent_evidence"}


# ---- item 3: plain words, once a day -----------------------------------------
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


async def _text(alerts, monkeypatch, detail):
    calls = []
    monkeypatch.setattr(alerts.httpx, "AsyncClient", lambda **_kw: _Client(calls))
    settings = NS(talky_alert_webhook_url=SecretStr("https://alerts.example.invalid/t"), talky_alert_email_to=None)
    await alerts.send_alert("talky-healthwatch.service", settings=settings, detail=detail)
    return calls[0]["text"]


async def test_the_alert_says_what_is_wrong_in_plain_words(alerts, monkeypatch):
    text = await _text(alerts, monkeypatch,
                       "payments:alert:6+0,trunks:alert:16,units:alert:talky-db-backup.service+talky-inbound-synthetic.service")
    assert text.startswith("Talky service failure: talky-healthwatch.service (failing checks: payments, trunks, units).")
    assert "6 payments need a review. 0 payments have been stuck for more than 6 hours." in text
    assert "16 phone lines are not connected." in text
    assert "These scheduled jobs failed: talky-db-backup.service, talky-inbound-synthetic.service." in text
    assert text.endswith("Details are in the server's log for this job.")
    print(text)


async def test_other_sentences_and_the_unknown_fallback(alerts, monkeypatch):
    text = await _text(alerts, monkeypatch, "trunks:unavailable,recordings:alert:2+0+1+0,email:stale,mystery:alert")
    assert "The phone line status could not be read." in text
    assert "2 answered calls have no recording." in text
    assert "No email has been confirmed sent for more than 3 days." in text
    assert "mystery is failing." in text


async def test_an_unchanged_set_repeats_daily_and_a_changed_set_at_once(alerts, server):
    unit = "talky-healthwatch.service"
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    await client.set(alerts.HEALTHWATCH_FAILING_KEY, "trunks:alert:16")
    assert (await alerts.claim_alert_slot(unit))[0] is True
    ttl = await client.ttl(alerts._slot_key(unit, "trunks:alert:16"))
    assert 3600 < ttl <= 86400
    assert (await alerts.claim_alert_slot(unit))[0] is False
    await client.set(alerts.HEALTHWATCH_FAILING_KEY, "trunks:alert:17")
    assert (await alerts.claim_alert_slot(unit))[0] is True
    await client.aclose()


async def test_an_incoming_only_recordings_alert_names_the_incoming_calls(alerts, monkeypatch):
    text = await _text(alerts, monkeypatch, "recordings:alert:0+0+0+0+in2")
    assert "0 answered calls have no recording. 2 answered incoming calls have no recording. " in text


async def test_one_incoming_recording_is_singular(alerts, monkeypatch):
    text = await _text(alerts, monkeypatch, "recordings:alert:2+0+1+0+in1")
    assert "2 answered calls have no recording. 1 answered incoming call has no recording. " in text


async def test_recordings_evidence_without_an_incoming_part_is_unchanged(alerts, monkeypatch):
    text = await _text(alerts, monkeypatch, "recordings:alert:2+0+1+0")
    assert "2 answered calls have no recording. 0 recordings are waiting too long to be saved. " in text
    assert "incoming" not in text


@pytest.mark.parametrize("evidence", ["2+0+1+0+inx", "2+0+1+0+in", "2+0+1+0+x3", "2+0+1+0+in0", "2+0+1+0+in-1"])
async def test_a_malformed_or_zero_incoming_part_is_ignored(alerts, monkeypatch, evidence):
    text = await _text(alerts, monkeypatch, f"recordings:alert:{evidence}")
    assert "2 answered calls have no recording. 0 recordings are waiting" in text
    assert "incoming" not in text

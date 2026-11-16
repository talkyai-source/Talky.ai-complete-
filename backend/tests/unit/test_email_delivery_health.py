"""A failing platform mailbox becomes a failing health check.

Sign-up codes, password resets and invites failed from 3 Nov 2026 and nobody
was told for 11 days: the alerts were sent through the same mailbox, and no
health check looked at email. Each real send now leaves its outcome in Redis,
and the health watch reads it, so the outside watch (GET /healthz/watch, which
opens a GitHub issue) sees the failure without needing email. Mail is captured
here, never sent; Redis is fakeredis.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import smtplib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosmtplib
import fakeredis.aioredis
import pytest

from app.domain.services import email_service as mail
from app.domain.services import notification_service as notifications
from app.infrastructure.metrics import email_delivery

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
RECIPIENT = "person@example.invalid"


@pytest.fixture
def watch():
    spec = importlib.util.spec_from_file_location("email_healthwatch", BACKEND / "scripts/healthwatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(email_delivery, "_redis_client", lambda: client)
    yield client
    await client.aclose()


@pytest.fixture
def settings(monkeypatch):
    from app.core import config

    values = SimpleNamespace(
        email_user="login@example.invalid", email_pass="fake-only",
        from_email="accounts@talkleeai.com", brand_name="Talk-Lee",
        frontend_url="https://app.example.invalid", email_smtp_host="smtp.gmail.com",
    )
    monkeypatch.setattr(mail, "get_settings", lambda: values)
    monkeypatch.setattr(config, "get_settings", lambda: values)
    return values


@pytest.fixture
def smtp(monkeypatch):
    """The account mail connection; set ``login``/``send_message`` side effects per test."""
    connection = SimpleNamespace(login=AsyncMock(), send_message=AsyncMock())

    class Connection:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return connection

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(mail.aiosmtplib, "SMTP", Connection)
    return connection


def refused_login():
    return aiosmtplib.SMTPAuthenticationError(535, "5.7.8 Username and Password not accepted")


async def send(text="<p>code</p>"):
    return await mail.EmailService().send_email(RECIPIENT, "Your sign-up code", text)


async def test_a_refused_mailbox_login_fails_the_email_check_at_once(watch, redis, settings, smtp):
    smtp.login.side_effect = refused_login()
    assert await send() is False

    result = await watch.check_email(redis, now=NOW)
    assert result["status"] == "alert"
    assert result["error"] == "authentication"
    assert result["consecutive_failures"] == 1

    await watch._record_failing_checks(redis, {"email": result})
    assert await redis.get(watch.FAILING_KEY) == "email"


async def test_one_successful_send_clears_the_failure(watch, redis, settings, smtp):
    smtp.login.side_effect = refused_login()
    await send()
    await send()
    smtp.login.side_effect = None
    assert await send() is True

    result = await watch.check_email(redis, now=NOW)
    assert result["status"] == "ok"
    assert await redis.hgetall(email_delivery.KEY) == {"outcome": "ok", "at": await redis.hget(email_delivery.KEY, "at")}


async def test_a_single_time_out_waits_for_a_second_one_in_a_row(watch, redis, settings, smtp):
    smtp.send_message.side_effect = aiosmtplib.SMTPTimeoutError("timed out")
    await send()
    first = await watch.check_email(redis, now=NOW)
    assert first["status"] == "pending"
    assert first["error"] == "timeout"
    await watch._record_failing_checks(redis, {"email": first})
    assert await redis.get(watch.FAILING_KEY) is None

    await send()
    second = await watch.check_email(redis, now=NOW)
    assert second["status"] == "alert"
    assert second["consecutive_failures"] == 2


async def test_the_failure_run_keeps_the_time_it_started(watch, redis):
    start = NOW - timedelta(hours=26)
    await email_delivery.record_email_outcome("failed", "transport", now=start.timestamp())
    await email_delivery.record_email_outcome("failed", "transport", now=(NOW - timedelta(hours=1)).timestamp())
    result = await watch.check_email(redis, now=NOW)
    assert result["status"] == "alert"
    assert result["failing_for_minutes"] == 26 * 60


async def test_missing_mailbox_settings_fail_the_check(watch, redis, settings, smtp):
    settings.email_pass = ""
    assert await send() is False
    result = await watch.check_email(redis, now=NOW)
    assert (result["status"], result["error"]) == ("alert", "unconfigured")
    smtp.login.assert_not_awaited()


async def test_a_refused_recipient_says_nothing_about_the_mailbox(watch, redis, settings, smtp):
    smtp.send_message.side_effect = aiosmtplib.SMTPRecipientsRefused([])
    assert await send() is False
    assert await watch.check_email(redis, now=NOW) == {"status": "no_recent_evidence"}


async def test_notification_mail_through_the_same_mailbox_is_recorded_too(watch, redis, settings, monkeypatch):
    class Refusing:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self):
            pass

        def login(self, *_args):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    monkeypatch.setattr(smtplib, "SMTP", Refusing)
    outcome = await notifications.NotificationService()._send_via_smtp(RECIPIENT, "Alert", "<p>alert</p>")
    assert outcome["status"] == "error"
    result = await watch.check_email(redis, now=NOW)
    assert (result["status"], result["error"]) == ("alert", "authentication")


async def test_only_the_outcome_is_kept_never_the_message(redis, settings, smtp):
    smtp.login.side_effect = refused_login()
    await send("<p>secret code 482913</p>")
    stored = await redis.hgetall(email_delivery.KEY)
    assert set(stored) == {"outcome", "at", "error", "consecutive_failures", "failing_since"}
    joined = " ".join(stored.values())
    for private in (RECIPIENT, "482913", "sign-up", settings.email_user, settings.email_pass, "535", "accepted"):
        assert private not in joined
    assert 0 < await redis.ttl(email_delivery.KEY) <= email_delivery.TTL_SECONDS


async def test_a_broken_store_never_changes_the_send_result(settings, smtp, monkeypatch):
    class Broken:
        async def eval(self, *_args):
            raise ConnectionError("redis down")

    monkeypatch.setattr(email_delivery, "_redis_client", lambda: Broken())
    assert await send() is True
    smtp.login.side_effect = refused_login()
    assert await send() is False


async def test_a_hanging_store_cannot_hold_a_send_for_long(settings, smtp, monkeypatch):
    class Hanging:
        async def eval(self, *_args):
            await asyncio.sleep(30)

    monkeypatch.setattr(email_delivery, "_redis_client", lambda: Hanging())
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await send() is True
    assert loop.time() - started < email_delivery.IO_TIMEOUT_SECONDS + 1


async def test_an_unreadable_or_damaged_record_is_never_reported_as_healthy(watch, redis):
    assert (await watch.check_email(None, now=NOW))["status"] == "unavailable"
    await redis.hset(email_delivery.KEY, mapping={"outcome": "failed", "at": "nan", "error": "authentication",
                                                  "consecutive_failures": "1", "failing_since": "1"})
    assert (await watch.check_email(redis, now=NOW))["status"] == "unavailable"
    await redis.hset(email_delivery.KEY, mapping={"at": "1", "error": "made-up"})
    assert (await watch.check_email(redis, now=NOW))["status"] == "unavailable"


@pytest.mark.parametrize("error,expected", [
    (aiosmtplib.SMTPAuthenticationError(535, "no"), "authentication"),
    (smtplib.SMTPAuthenticationError(535, b"no"), "authentication"),
    (aiosmtplib.SMTPTimeoutError("slow"), "timeout"),
    (TimeoutError(), "timeout"),
    (aiosmtplib.SMTPConnectError("refused"), "transport"),
    (aiosmtplib.SMTPServerDisconnected("closed"), "transport"),
    (smtplib.SMTPServerDisconnected("closed"), "transport"),
    (OSError("name or service not known"), "transport"),
    (smtplib.SMTPDataError(554, b"rejected"), "provider"),
    (aiosmtplib.SMTPDataError(554, "rejected"), "provider"),
    (ValueError("bad header"), "provider"),
    (aiosmtplib.SMTPRecipientsRefused([]), None),
    (smtplib.SMTPRecipientsRefused({}), None),
])
def test_each_failure_is_put_in_one_category(error, expected):
    assert email_delivery.error_category(error) == expected

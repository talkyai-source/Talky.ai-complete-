"""Sign-up, password-reset and invite emails all failed from 3 Nov 2026.

The sending mailbox had moved from Microsoft 365 to Google Workspace, and the
mail server name was fixed in code to Microsoft 365, so every login was offered
to a server the mailbox no longer lived on ("535 5.7.3 Authentication
unsuccessful"). The server name is now a setting, EMAIL_SMTP_HOST. Mail is
captured here, never sent.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services import email_service as mail
from app.domain.services import notification_service as notifications

MICROSOFT_365 = "smtp.office365.com"
GOOGLE = "smtp.gmail.com"


@pytest.fixture
def use_host(monkeypatch):
    from app.core import config

    def apply(host):
        settings = SimpleNamespace(
            email_user="login@example.invalid", email_pass="fake-only",
            from_email="accounts@talkleeai.com", brand_name="Talk-Lee",
            frontend_url="https://app.example.invalid", email_smtp_host=host,
        )
        monkeypatch.setattr(mail, "get_settings", lambda: settings)
        monkeypatch.setattr(config, "get_settings", lambda: settings)
        return settings

    return apply


@pytest.fixture
def captured(monkeypatch):
    """Stands in for the SMTP connection and records how it was opened."""
    seen = {}
    smtp = SimpleNamespace(login=AsyncMock(), send_message=AsyncMock())

    class Connection:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return smtp

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(mail.aiosmtplib, "SMTP", Connection)
    seen["smtp"] = smtp
    return seen


@pytest.mark.asyncio
async def test_account_email_is_sent_through_the_configured_mail_server(use_host, captured):
    settings = use_host(GOOGLE)
    assert await mail.EmailService().send_signup_code_email("person@example.invalid", "Person", "123456")
    assert captured["hostname"] == GOOGLE
    assert captured["port"] == 587
    assert captured["start_tls"] is True
    captured["smtp"].login.assert_awaited_once_with(settings.email_user, settings.email_pass)


@pytest.mark.asyncio
async def test_without_the_setting_the_microsoft_365_default_is_unchanged(use_host, captured):
    use_host(None)
    assert await mail.EmailService().send_email("person@example.invalid", "Synthetic", "<p>safe</p>")
    assert captured["hostname"] == MICROSOFT_365
    assert mail.EmailService.SMTP_HOST == MICROSOFT_365


def test_the_setting_is_trimmed_and_case_does_not_matter(use_host):
    use_host("  SMTP.Gmail.com ")
    assert mail.account_smtp_host() == GOOGLE
    use_host("")
    assert mail.account_smtp_host() == MICROSOFT_365


@pytest.mark.parametrize("bad", [
    "smtp.gmail.com:465", "https://smtp.gmail.com", "smtp gmail com", "localhost", "-smtp.gmail.com",
    "smtp.gmail.com/inbox", "smtp.gmail.com\r\nX-Injected: 1", "user@smtp.gmail.com", "smtp..gmail.com",
])
def test_a_value_that_is_not_a_host_name_falls_back_to_the_default(use_host, bad):
    use_host(bad)
    assert mail.account_smtp_host() == MICROSOFT_365


def test_alert_and_billing_notifications_use_the_same_mail_server(use_host):
    use_host(GOOGLE)
    assert notifications.NotificationService().smtp_host == GOOGLE
    use_host(None)
    assert notifications.NotificationService().smtp_host == MICROSOFT_365


def test_the_setting_is_read_from_the_environment(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("EMAIL_SMTP_HOST", raising=False)
    assert Settings(_env_file=None).email_smtp_host is None
    monkeypatch.setenv("EMAIL_SMTP_HOST", GOOGLE)
    assert Settings(_env_file=None).email_smtp_host == GOOGLE

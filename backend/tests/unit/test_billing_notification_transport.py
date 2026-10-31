"""Bound billing mail sockets without changing unrelated adapter policies."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
import smtplib

from app.domain.services.notification_service import NotificationService


def service(billing):
    result = NotificationService.__new__(NotificationService)
    result.from_email, result.from_name = "synthetic@example.invalid", "Synthetic"
    result.smtp_host, result.smtp_port = "synthetic.invalid", 587
    result.smtp_user = result.smtp_password = "synthetic"
    result.aws_region = "us-east-1"
    result.aws_access_key = result.aws_secret_key = "synthetic"
    if billing:
        result.delivery_timeout_seconds = 8
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("billing", [True, False])
async def test_smtp_billing_is_bounded_and_other_callers_keep_their_existing_policy(monkeypatch, billing):
    smtp = MagicMock()
    monkeypatch.setattr(smtplib, "SMTP", smtp)
    outcome = await service(billing)._send_via_smtp("synthetic@example.invalid", "Synthetic", "Synthetic")
    assert outcome["status"] == "success"
    smtp.assert_called_once_with("synthetic.invalid", 587, **({"timeout": 8} if billing else {}))
    smtp.return_value.__enter__.return_value.send_message.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("billing", [True, False])
async def test_ses_billing_disables_ambiguous_sdk_retry_and_keeps_acceptance_identity(monkeypatch, billing):
    sdk = MagicMock(return_value=SimpleNamespace(send_email=MagicMock(return_value={"MessageId": "synthetic-accepted-id"})))
    monkeypatch.setattr(boto3, "client", sdk)
    outcome = await service(billing)._send_via_ses("synthetic@example.invalid", "Synthetic", "Synthetic")
    assert outcome == {"status": "success", "message_id": "synthetic-accepted-id"}
    options = sdk.call_args.kwargs
    if billing:
        assert options["config"].connect_timeout == options["config"].read_timeout == 8
        assert options["config"].retries == {"max_attempts": 0}
    else:
        assert "config" not in options
    sdk.return_value.send_email.assert_called_once()

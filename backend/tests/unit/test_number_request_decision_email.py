"""The decision email for a number request: plain words, no document details, never fatal."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from app.domain.services.number_requests import notify

SUBMITTED = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)


class Mailer:
    brand_name = "Talk-Lee"

    def __init__(self, result=True, raises=None):
        self.result, self.raises, self.sent = result, raises, []

    async def send_email(self, **kw):
        if self.raises:
            raise self.raises
        self.sent.append(kw)
        return self.result


@pytest.fixture
def mailer(monkeypatch):
    m = Mailer()
    monkeypatch.setattr(notify, "get_email_service", lambda: m)
    return m


async def test_approved_email_text_is_exact_and_has_no_document_details(mailer):
    out = await notify.send_decision_email(recipient="owner@example.com", decision="approved", note="ignored",
                                           submitted_at=SUBMITTED)
    assert out == "sent"
    (mail,) = mailer.sent
    assert mail["recipient_email"] == "owner@example.com"
    assert mail["subject"] == "Your phone number request was approved"
    assert mail["text_body"] == (
        "Hi,\n\nWe have checked the documents you sent on 3 November 2026 to buy a phone number.\n\n"
        "Your request was approved. Our team will contact you about the next steps.\n\n— Talk-Lee")
    for word in ("registration", "photo", "utility", "passport", ".pdf", "http", "ignored"):
        assert word not in mail["text_body"].lower() and word not in mail["html_body"].lower()


async def test_rejected_email_carries_the_cleaned_reason(mailer):
    note = "Bill is  too old\n\n\tsend <b>a new</b> one\x00\x1b[31m​"
    out = await notify.send_decision_email(recipient="owner@example.com", decision="rejected", note=note,
                                           submitted_at=SUBMITTED)
    assert out == "sent"
    (mail,) = mailer.sent
    assert mail["subject"] == "Your phone number request was not approved"
    assert mail["text_body"] == (
        "Hi,\n\nWe have checked the documents you sent on 3 November 2026 to buy a phone number.\n\n"
        "Your request was not approved.\n\nReason: Bill is too old send <b>a new</b> one[31m\n\n"
        "You can send new documents in Settings → Telephony → Buy Now.\n\n— Talk-Lee")
    assert "<b>" not in mail["html_body"] and "&lt;b&gt;a new&lt;/b&gt;" in mail["html_body"]
    assert not {c for c in mail["text_body"].replace("\n", "") if ord(c) < 32}


async def test_a_reject_with_a_blank_cleaned_note_omits_the_reason_line(mailer):
    await notify.send_decision_email(recipient="o@example.com", decision="rejected", note="\x00 \n ",
                                     submitted_at=SUBMITTED)
    assert "Reason" not in mailer.sent[0]["text_body"]


async def test_no_recipient_is_skipped_and_sends_nothing(mailer):
    for recipient in (None, ""):
        assert await notify.send_decision_email(recipient=recipient, decision="approved", note=None,
                                                submitted_at=SUBMITTED) == "skipped"
    assert mailer.sent == []


async def test_a_send_failure_is_failed_and_logs_no_address_or_text(monkeypatch, caplog):
    monkeypatch.setattr(notify, "get_email_service",
                        lambda: Mailer(raises=RuntimeError("smtp said owner@example.com refused")))
    with caplog.at_level(logging.DEBUG):
        out = await notify.send_decision_email(recipient="owner@example.com", decision="rejected",
                                               note="zq9reason", submitted_at=SUBMITTED)
    assert out == "failed"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "owner@example.com" not in text and "zq9reason" not in text and "RuntimeError" in text


async def test_a_false_from_the_mailer_is_failed(monkeypatch):
    monkeypatch.setattr(notify, "get_email_service", lambda: Mailer(result=False))
    assert await notify.send_decision_email(recipient="o@example.com", decision="approved", note=None,
                                            submitted_at=SUBMITTED) == "failed"


async def test_the_reject_note_can_be_left_out_by_the_decision_constant(monkeypatch, mailer):
    monkeypatch.setattr(notify.config, "EMAIL_INCLUDES_REJECT_NOTE", False)
    await notify.send_decision_email(recipient="o@example.com", decision="rejected", note="secret reason",
                                     submitted_at=SUBMITTED)
    assert "secret reason" not in mailer.sent[0]["text_body"]

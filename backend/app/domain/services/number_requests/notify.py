"""The decision email for a number request.

Plain text plus the same paragraphs escaped as HTML. It never names a document
kind, a file name or a link, and its failure never undoes the decision.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from html import escape
from typing import Literal

from app.domain.services.email_service import get_email_service
from app.domain.services.number_requests import config

logger = logging.getLogger(__name__)

Outcome = Literal["sent", "failed", "skipped"]

_SPACE = re.compile(r"\s+")


def clean_note(note: str | None) -> str:
    """Whitespace collapsed, control characters removed."""
    if not note:
        return ""
    spaced = "".join(" " if ch.isspace() else ch for ch in note)
    kept = "".join(ch for ch in spaced if unicodedata.category(ch) not in ("Cc", "Cf"))
    return _SPACE.sub(" ", kept).strip()


def _date(value: datetime | None) -> str:
    if value is None:
        return "recently"
    return f"{value.day} {value:%B} {value.year}"


def build_message(*, decision: str, note: str | None, submitted_at: datetime | None,
                  brand: str) -> tuple[str, list[str]]:
    """Return (subject, paragraphs). The last paragraph is the signature."""
    opening = ["Hi,",
               f"We have checked the documents you sent on {_date(submitted_at)} to buy a phone number."]
    if decision == "approved":
        subject = "Your phone number request was approved"
        body = ["Your request was approved. Our team will contact you about the next steps."]
    else:
        subject = "Your phone number request was not approved"
        body = ["Your request was not approved."]
        reason = clean_note(note) if config.EMAIL_INCLUDES_REJECT_NOTE else ""
        if reason:
            body.append(f"Reason: {reason}")
        body.append("You can send new documents in Settings → Telephony → Buy Now.")
    return subject, opening + body + [f"— {brand}"]


async def send_decision_email(*, recipient: str | None, decision: str, note: str | None,
                              submitted_at: datetime | None) -> Outcome:
    if not recipient:
        return "skipped"
    try:
        service = get_email_service()
        subject, paragraphs = build_message(
            decision=decision, note=note, submitted_at=submitted_at, brand=service.brand_name)
        text = "\n\n".join(paragraphs)
        html = "".join(f"<p>{escape(p)}</p>" for p in paragraphs)
        sent = await service.send_email(
            recipient_email=recipient, subject=subject, html_body=html, text_body=text)
    except Exception as exc:  # the decision stands; never log the address or the text
        logger.error("number_request_decision_email failed type=%s", type(exc).__name__)
        return "failed"
    if not sent:
        logger.error("number_request_decision_email failed type=not_sent")
        return "failed"
    return "sent"

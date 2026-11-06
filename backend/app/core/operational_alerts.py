"""Bounded operational notifications; destination presence is not delivery proof."""
from __future__ import annotations

import asyncio
import html
import logging
import re

import httpx

from app.core.config import get_settings
from app.domain.services.email_service import get_email_service


logger = logging.getLogger(__name__)
DELIVERY_TIMEOUT_SECONDS = 10
_UNIT = re.compile(r"[A-Za-z0-9_.@:-]{1,200}\Z")


def _destinations(settings):
    webhook = getattr(settings, "talky_alert_webhook_url", None)
    if hasattr(webhook, "get_secret_value"):
        webhook = webhook.get_secret_value()
    email = getattr(settings, "talky_alert_email_to", None)
    return (webhook or "").strip(), (email or "").strip()


def alerting_status(settings=None):
    return "configured" if any(_destinations(settings or get_settings())) else "unconfigured"


async def send_alert(unit, *, settings=None, synthetic=False):
    if not isinstance(unit, str) or not _UNIT.fullmatch(unit):
        raise ValueError("Invalid systemd unit name")
    webhook, email = _destinations(settings or get_settings())
    result = {"delivered": False, "channels": {}}
    if not webhook and not email:
        logger.error("alert not delivered: no destination")
        return result
    subject = "Talky synthetic alert test" if synthetic else "Talky service failure"
    message = f"{subject}: {unit}. Inspect the service journal on the host."
    # Never attach journal output, customer data, credentials or destination URLs.
    if webhook:
        try:
            async with asyncio.timeout(DELIVERY_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                    response = await client.post(webhook, json={"text": message})
            result["channels"]["webhook"] = "delivered" if 200 <= response.status_code < 300 else "failed"
        except Exception as exc:
            logger.error("alert webhook failed: %s", type(exc).__name__)
            result["channels"]["webhook"] = "failed"
    if email:
        try:
            async with asyncio.timeout(DELIVERY_TIMEOUT_SECONDS):
                sent = await get_email_service().send_email(
                    recipient_email=email, subject=subject,
                    html_body=f"<p>{html.escape(message)}</p>", text_body=message,
                )
            result["channels"]["email"] = "delivered" if sent is True else "failed"
        except Exception as exc:
            logger.error("alert email failed: %s", type(exc).__name__)
            result["channels"]["email"] = "failed"
    result["delivered"] = any(value == "delivered" for value in result["channels"].values())
    for channel, outcome in result["channels"].items():
        logger.log(logging.INFO if outcome == "delivered" else logging.ERROR,
                   "alert channel=%s result=%s unit=%s", channel, outcome, unit)
    return result

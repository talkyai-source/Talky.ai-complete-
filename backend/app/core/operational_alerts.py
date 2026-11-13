"""Bounded operational notifications; destination presence is not delivery proof."""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re

import httpx

from app.core.config import get_settings
from app.domain.services.email_service import get_email_service


logger = logging.getLogger(__name__)
DELIVERY_TIMEOUT_SECONDS = 10
_UNIT = re.compile(r"[A-Za-z0-9_.@:-]{1,200}\Z")
# A unit that keeps failing alerts once per hour, not on every failure: the
# health watch runs every minute, so one stuck check used to mean an alert a
# minute. A different set of failing health checks alerts at once.
REPEAT_LIMIT_SECONDS = 3600
HEALTHWATCH_FAILING_KEY = "talky:healthwatch:failing"
_DETAIL = re.compile(r"[a-z_]{1,40}(?:,[a-z_]{1,40}){0,15}\Z")


def _destinations(settings):
    webhook = getattr(settings, "talky_alert_webhook_url", None)
    if hasattr(webhook, "get_secret_value"):
        webhook = webhook.get_secret_value()
    email = getattr(settings, "talky_alert_email_to", None)
    return (webhook or "").strip(), (email or "").strip()


def alerting_status(settings=None):
    return "configured" if any(_destinations(settings or get_settings())) else "unconfigured"


async def _alert_state_client():
    from app.core.container import _build_cluster_client, _build_redis_url, _build_sentinel_client
    import redis.asyncio as redis

    mode = (os.getenv("REDIS_MODE") or "single").strip().lower()
    if mode == "sentinel":
        return await _build_sentinel_client()
    if mode == "cluster":
        return await _build_cluster_client()
    return redis.from_url(_build_redis_url(), decode_responses=True,
                          socket_timeout=1, socket_connect_timeout=1, max_connections=1)


def _slot_key(unit, detail):
    return f"talky:alert:sent:{unit}:{detail}"


async def _close(client):
    if client is not None:
        try:
            await asyncio.wait_for(client.aclose(), 1)
        except Exception:
            pass


async def claim_alert_slot(unit, *, client_factory=None):
    """``(send, detail)`` for a failing unit. ``send`` is False when the same
    unit (and, for the health watch, the same failing checks) already alerted
    within REPEAT_LIMIT_SECONDS. Any Redis trouble sends anyway: a missed
    alert is worse than a repeated one."""
    if not isinstance(unit, str) or not _UNIT.fullmatch(unit):
        raise ValueError("Invalid systemd unit name")
    client = None
    try:
        async with asyncio.timeout(3):
            client = await (client_factory or _alert_state_client)()
            detail = ""
            if unit.startswith("talky-healthwatch"):
                detail = await client.get(HEALTHWATCH_FAILING_KEY) or ""
                detail = detail if _DETAIL.fullmatch(detail) else ""
            claimed = await client.set(_slot_key(unit, detail), "1", nx=True, ex=REPEAT_LIMIT_SECONDS)
            return bool(claimed), detail
    except Exception as exc:
        logger.warning("alert repeat limit unavailable (%s); sending anyway", type(exc).__name__)
        return True, ""
    finally:
        await _close(client)


async def release_alert_slot(unit, detail="", *, client_factory=None):
    """Undo a claim whose alert was not delivered, so the next failure retries."""
    client = None
    try:
        async with asyncio.timeout(3):
            client = await (client_factory or _alert_state_client)()
            await client.delete(_slot_key(unit, detail))
    except Exception as exc:
        logger.warning("alert repeat slot not released (%s)", type(exc).__name__)
    finally:
        await _close(client)


async def send_alert(unit, *, settings=None, synthetic=False, detail=""):
    if not isinstance(unit, str) or not _UNIT.fullmatch(unit):
        raise ValueError("Invalid systemd unit name")
    webhook, email = _destinations(settings or get_settings())
    result = {"delivered": False, "channels": {}}
    if not webhook and not email:
        logger.error("alert not delivered: no destination")
        return result
    subject = "Talky synthetic alert test" if synthetic else "Talky service failure"
    checks = f" (failing checks: {detail.replace(',', ', ')})" if _DETAIL.fullmatch(detail or "") else ""
    message = f"{subject}: {unit}{checks}. Inspect the service journal on the host."
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

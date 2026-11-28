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
# The health watch's failing set, when unchanged, repeats once a day; a changed
# fingerprint has another slot key and alerts at once.
HEALTHWATCH_REPEAT_LIMIT_SECONDS = 24 * 3600
HEALTHWATCH_FAILING_KEY = "talky:healthwatch:failing"
HEALTHWATCH_LAST_RUN_KEY = "talky:healthwatch:last_run"
# One entry per failing check: name, or name:status, or name:status:evidence
# (digits, unit names, "+"). No free text.
_ITEM = r"[a-z_]{1,40}(?::[a-z_]{1,40}(?::[A-Za-z0-9_.@+-]{1,600})?)?"
_DETAIL = re.compile(rf"{_ITEM}(?:,{_ITEM}){{0,15}}\Z")


def _n(evidence, index):
    parts = (evidence or "").split("+")
    return parts[index] if index < len(parts) and parts[index].isdigit() else "0"


def _plural(count, one, many):
    return one if count == "1" else many


def _incoming_missing(evidence):
    """The N of a fifth evidence part "in<N>" (incoming recordings missing), else "0"."""
    parts = (evidence or "").split("+")
    last = parts[4] if len(parts) > 4 else ""
    return last[2:] if last.startswith("in") and last[2:].isdecimal() else "0"


def _has_word(evidence, word):
    """True when the evidence carries `word` among the parts after the four counts: the
    health watch adds "all" when four in five answered calls have no confirmed recording
    notice, and "now" when none of the answered calls of the last 4 hours has one."""
    return word in (evidence or "").split("+")[4:]


# Fixed sentences per check, filled only from the fingerprint's numbers.
_SENTENCES = {
    "trunks": lambda status, ev: (
        f"{_n(ev, 0)} {_plural(_n(ev, 0), 'phone line is', 'phone lines are')} not connected."
        if status == "alert" else "The phone line status could not be read."),
    "payments": lambda status, ev: (
        f"{_n(ev, 0)} {_plural(_n(ev, 0), 'payment needs', 'payments need')} a review. "
        f"{_n(ev, 1)} {_plural(_n(ev, 1), 'payment has', 'payments have')} been stuck for more than 6 hours."
        if status == "alert" else "The payment records could not be checked."),
    "units": lambda status, ev: (
        f"These scheduled jobs failed: {', '.join(ev.split('+'))}."
        if status == "alert" and ev else "Some scheduled jobs have failed."),
    "recordings": lambda status, ev: (
        f"{_n(ev, 0)} answered {_plural(_n(ev, 0), 'call has', 'calls have')} no recording. "
        + (f"{_incoming_missing(ev)} answered incoming "
           f"{_plural(_incoming_missing(ev), 'call has', 'calls have')} no recording. "
           if int(_incoming_missing(ev)) > 0 else "")
        + f"{_n(ev, 1)} {_plural(_n(ev, 1), 'recording is', 'recordings are')} waiting too long to be saved. "
        f"{_n(ev, 2)} {_plural(_n(ev, 2), 'transferred call is', 'transferred calls are')} missing "
        f"the part after the transfer. "
        f"{_n(ev, 3)} {_plural(_n(ev, 3), 'recording of a call of 30 seconds or more was', 'recordings of calls of 30 seconds or more were')} saved without a confirmed notice."
        + (" Four in five answered calls have no confirmed recording notice." if _has_word(ev, "all") else "")
        + (" None of the answered calls of the last 4 hours got a confirmed recording notice."
           if _has_word(ev, "now") else "")
        if status == "alert" and ev else "The call recordings could not be checked."),
    "email": lambda status, ev: (
        "No email has been confirmed sent for more than 3 days." if status == "stale"
        else "The mailbox is failing to send email." if status == "alert"
        else "The mailbox could not be checked."),
    "disk": lambda status, ev: "The server disk is nearly full." if status == "alert"
    else "The server disk could not be checked.",
    "memory": lambda status, ev: "The server memory is nearly full." if status == "alert"
    else "The server memory could not be checked.",
    "certificates": lambda status, ev: "A security certificate expires in less than 14 days." if status == "alert"
    else "The security certificates could not be checked.",
    "redis": lambda status, ev: "The fast data store is nearly full." if status == "alert"
    else "The fast data store could not be checked.",
    "workers": lambda status, ev: "A background worker is not running." if status == "alert"
    else "The background workers could not be checked.",
    "ntp": lambda status, ev: "The server clock is not in sync." if status == "alert"
    else "The server clock could not be checked.",
}


def _sentences(detail):
    """One plain sentence per failing check; an unknown check is only named."""
    out = []
    for item in detail.split(","):
        name, _, rest = item.partition(":")
        status, _, evidence = rest.partition(":")
        builder = _SENTENCES.get(name)
        out.append(builder(status or "alert", evidence) if builder else f"{name} is failing.")
    return out


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


async def healthwatch_public_state(client):
    """What anyone may know about the server's own health watch: whether it
    completed a run in the last ten minutes, and how many checks it holds as
    failing. The names and values stay in the journal. Anything unreadable is
    reported as not clear, never as healthy."""
    state = {"clear": False, "failing": None, "ran_recently": False}
    if client is None:
        return state
    try:
        async with asyncio.timeout(1):
            last_run = await client.get(HEALTHWATCH_LAST_RUN_KEY)
            failing = await client.get(HEALTHWATCH_FAILING_KEY)
    except Exception:
        return state
    if isinstance(failing, (bytes, bytearray)):
        failing = failing.decode("utf-8", "replace")
    names = [name for name in (failing or "").split(",") if name]
    ran_recently = last_run is not None
    return {"clear": ran_recently and not names, "failing": len(names), "ran_recently": ran_recently}


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
            limit = HEALTHWATCH_REPEAT_LIMIT_SECONDS if detail else REPEAT_LIMIT_SECONDS
            claimed = await client.set(_slot_key(unit, detail), "1", nx=True, ex=limit)
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
    valid = bool(_DETAIL.fullmatch(detail or ""))
    names = ", ".join(dict.fromkeys(item.split(":")[0] for item in detail.split(","))) if valid else ""
    checks = f" (failing checks: {names})" if valid else ""
    said = (" " + " ".join(_sentences(detail))) if valid else ""
    message = f"{subject}: {unit}{checks}.{said} Details are in the server's log for this job."
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

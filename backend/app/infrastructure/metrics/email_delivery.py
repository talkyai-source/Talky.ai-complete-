"""Outcome of the platform mailbox's own sends: sign-up codes, password resets,
invites and notifications.

The mailbox stopped working on 3 Nov 2026 and nobody was told for 11 days: the
alerts went out through the same mailbox, and no health check looked at email.
Every send through the platform mailbox now leaves its outcome here, and the
health watch (scripts/healthwatch.py) turns a failing mailbox into a failing
check, which the outside watch reads through GET /healthz/watch without email.

Only an outcome, an error category, a failure count and times are kept: never
an address, subject, body, login or server reply. Writes are best effort and
never raise into a send.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import time
from typing import Any

import aiosmtplib

logger = logging.getLogger(__name__)

KEY = "talky:email:delivery:v1"
# Long enough to hold the last answer across a quiet weekend; a mailbox that
# sends nothing for two weeks has no evidence either way.
TTL_SECONDS = 14 * 24 * 3600
IO_TIMEOUT_SECONDS = 0.5
ERRORS = frozenset({"unconfigured", "authentication", "timeout", "transport", "provider"})

# One key, one atomic step: a success clears the failure run; a failure counts
# the run and keeps the time it started.
_RECORD = """
if ARGV[1] == 'ok' then
  redis.call('DEL', KEYS[1])
  redis.call('HSET', KEYS[1], 'outcome', 'ok', 'at', ARGV[2])
else
  local failing = redis.call('HGET', KEYS[1], 'outcome') == 'failed'
  local count = 1
  local since = ARGV[2]
  if failing then
    count = (tonumber(redis.call('HGET', KEYS[1], 'consecutive_failures')) or 0) + 1
    since = redis.call('HGET', KEYS[1], 'failing_since') or ARGV[2]
  end
  redis.call('HSET', KEYS[1], 'outcome', 'failed', 'at', ARGV[2], 'error', ARGV[3],
             'consecutive_failures', count, 'failing_since', since)
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
return 1
"""

_RECIPIENT_REFUSALS = (
    aiosmtplib.SMTPRecipientsRefused, aiosmtplib.SMTPRecipientRefused,
    smtplib.SMTPRecipientsRefused,
)
_AUTHENTICATION = (aiosmtplib.SMTPAuthenticationError, smtplib.SMTPAuthenticationError)


def error_category(exc: BaseException) -> str | None:
    """What a failed send says about the mailbox. None for a refused recipient:
    the mail service took the login, so the mailbox itself is working."""
    if isinstance(exc, _RECIPIENT_REFUSALS):
        return None
    if isinstance(exc, _AUTHENTICATION):
        return "authentication"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (ConnectionError, smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)):
        return "transport"
    # smtplib's errors are OSErrors too: a refusal by the mail service is
    # checked before a network error (DNS, TLS) is.
    if isinstance(exc, (smtplib.SMTPException, aiosmtplib.SMTPException)):
        return "provider"
    if isinstance(exc, OSError):
        return "transport"
    return "provider"


def _redis_client() -> Any:
    try:
        from app.core.container import get_container
        container = get_container()
        return getattr(container, "redis", None) if container.is_initialized else None
    except Exception:
        return None


async def record_email_outcome(outcome: str, error: str | None = None, *,
                               client: Any = None, now: float | None = None) -> None:
    """Keep the outcome of one send. ``outcome`` is "ok" or "failed"; a failure
    names one of ERRORS. Never raises."""
    if outcome not in {"ok", "failed"} or (outcome == "failed" and error not in ERRORS):
        return
    client = client if client is not None else _redis_client()
    if client is None:
        return
    stamp = repr(float(now if now is not None else time.time()))
    try:
        await asyncio.wait_for(
            client.eval(_RECORD, 1, KEY, outcome, stamp, error or "", TTL_SECONDS),
            IO_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("email delivery outcome not recorded (%s)", type(exc).__name__)


async def record_send_failure(exc: BaseException, **kwargs: Any) -> None:
    category = error_category(exc)
    if category is not None:
        await record_email_outcome("failed", category, **kwargs)


async def read_email_outcome(client: Any) -> dict[str, str]:
    """The last recorded outcome, {} when there is none. Raises when Redis
    cannot be read, so an unreadable store is never taken for a healthy one."""
    raw = await asyncio.wait_for(client.hgetall(KEY), IO_TIMEOUT_SECONDS)
    return {
        (key.decode() if isinstance(key, bytes) else key): (value.decode() if isinstance(value, bytes) else value)
        for key, value in (raw or {}).items()
    }

"""Shared, fail-closed Redis budgets for unauthenticated account flows."""
import hashlib
import logging
import os

import httpx

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_DISPOSABLE_DOMAINS = frozenset({
    "10minutemail.com", "dispostable.com", "getnada.com", "guerrillamail.com",
    "guerrillamail.net", "maildrop.cc", "mailinator.com", "sharklasers.com",
    "temp-mail.org", "tempmail.com", "trashmail.com", "yopmail.com",
})

_CLAIM_WINDOW = """
local n = redis.call('INCR', KEYS[1])
if n == 1 or redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return n
"""

_RESET_FAILURE_WINDOW = """
local counts = {}
for i = 1, 2 do
  local raw = redis.call('GET', KEYS[i])
  local n = tonumber(raw or '0')
  if not n or n < 0 or n ~= math.floor(n) then
    return redis.error_reply('Invalid reset failure budget')
  end
  counts[i] = n
  if raw and redis.call('TTL', KEYS[i]) < 0 then
    redis.call('EXPIRE', KEYS[i], tonumber(ARGV[1]))
  end
end
if counts[1] >= tonumber(ARGV[2]) or counts[2] >= tonumber(ARGV[3]) then
  return 0
end
if ARGV[4] == '1' then
  for i = 1, 2 do
    redis.call('INCR', KEYS[i])
    if redis.call('TTL', KEYS[i]) < 0 then
      redis.call('EXPIRE', KEYS[i], tonumber(ARGV[1]))
    end
  end
end
return 1
"""


def budget_key(scope: str, identifier: str) -> str:
    digest = hashlib.sha256(identifier.strip().lower().encode()).hexdigest()
    return f"auth:budget:{scope}:{digest}"


async def claim_budget(redis, key: str, *, limit: int, seconds: int) -> bool:
    """Count and expire in one operation, shared across workers and codes."""
    try:
        count = int(await redis.eval(_CLAIM_WINDOW, 1, key, seconds))
    except Exception as exc:
        logger.warning("auth_abuse_budget_unavailable error=%s", type(exc).__name__)
        raise HTTPException(503, "Account service temporarily unavailable.") from exc
    if count == limit + 1:
        # One event per bucket crossing; never log the email, IP, code or token.
        logger.warning("auth_abuse_budget_exceeded limit=%d window_seconds=%d", limit, seconds)
    return count <= limit


async def enforce_budget(redis, scope: str, identifier: str, *, limit: int, seconds: int) -> None:
    if not await claim_budget(redis, budget_key(scope, identifier), limit=limit, seconds=seconds):
        raise HTTPException(429, "Too many attempts. Please try again later.",
                            headers={"Retry-After": str(seconds)})


async def enforce_reset_failure_budget(redis, email: str, address: str, *, failed: bool) -> None:
    """Atomically admit a reset outcome, counting failures only across workers.

    Call with failed=True before returning an invalid-code result, and with
    failed=False before accepting a matching code. A separate precheck alone
    cannot enforce the global cap against concurrent verification outcomes.
    """
    email = email.strip().lower()
    keys = (
        budget_key("reset-failed-email-ip-day", f"{email}\0{address.strip()}"),
        budget_key("reset-failed-email-day", email),
    )
    try:
        admitted = int(await redis.eval(_RESET_FAILURE_WINDOW, 2, *keys, 86400, 20, 100, int(failed)))
        if admitted not in (0, 1):
            raise ValueError("Invalid reset budget result")
    except Exception as exc:
        logger.warning("auth_reset_failure_budget_unavailable error=%s", type(exc).__name__)
        raise HTTPException(503, "Account service temporarily unavailable.") from exc
    if not admitted:
        raise HTTPException(429, "Too many attempts. Please try again later.",
                            headers={"Retry-After": "86400"})


def reject_disposable_signup_email(email: str) -> None:
    domain = email.rsplit("@", 1)[-1].casefold().rstrip(".")
    if any(domain == blocked or domain.endswith("." + blocked) for blocked in _DISPOSABLE_DOMAINS):
        raise HTTPException(400, "Please use a permanent email address.")


async def verify_turnstile(token: str | None, ip: str, action: str) -> None:
    """Enforce the coordinated browser rollout when explicitly configured.

    Cloudflare validates expiry and single use. Bind its success response to
    the intended form and an owner-configured hostname, never request headers.
    https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
    """
    required = os.getenv("AUTH_TURNSTILE_REQUIRED", "false").strip().lower()
    if required in {"false", "0", "off", "no", ""}:
        return
    if required not in {"true", "1", "on", "yes"}:
        raise HTTPException(503, "Bot verification is not configured.")
    secret = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
    hostnames = {host.strip().lower() for host in
                 os.getenv("TURNSTILE_ALLOWED_HOSTNAMES", "").split(",") if host.strip()}
    if not secret or not hostnames:
        raise HTTPException(503, "Bot verification is not configured.")
    if not token or len(token) > 2048:
        raise HTTPException(403, "Please complete the bot verification challenge.")
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            response = await client.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                                         data={"secret": secret, "response": token, "remoteip": ip})
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Do not log the exception: HTTP clients can embed credential material.
        logger.warning("auth_bot_verification_unavailable error=%s", type(exc).__name__)
        raise HTTPException(503, "Bot verification is temporarily unavailable.") from exc
    if (not isinstance(result, dict) or result.get("success") is not True
            or not isinstance(result.get("hostname"), str)
            or result["hostname"] not in hostnames or result.get("action") != action):
        raise HTTPException(403, "Bot verification failed. Please try again.")

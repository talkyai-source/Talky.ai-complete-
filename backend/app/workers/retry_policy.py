"""Retry classification for dialer job failures.

Before this module, every dialer failure routed through the same code
path: log "No call_id returned from telephony provider", schedule a
retry in 7200 seconds (the tenant's `retry_delay_seconds`), repeat
until `max_retry_attempts` was reached. That treated every category of
failure identically — a transient CSRF rejection waited 2 hours, an
invalid phone number burned three attempts, a real carrier reject also
waited 2 hours. Triage from logs was impossible because the error
string was the same string.

This module classifies a failure into one of five categories and maps
each to a sensible retry strategy:

* ``TRANSIENT_NETWORK`` — bridge returned 5xx, connection refused,
  timeout, DNS issue. Fast geometric backoff: 30s → 2m → 10m.
* ``AUTH_GATE`` — bridge returned 401/403 from CSRF / Origin /
  caller-id verification. Should not happen in steady state; a single
  short retry buys time for an in-flight config fix, then give up.
* ``CARRIER_REJECT`` — SIP 4xx/6xx from upstream carrier, no-answer,
  busy, voicemail. Real carrier signal — back off generously so the
  number gets a fair second chance: 5m → 30m → 2h.
* ``INVALID_INPUT`` — phone number malformed, blocked region,
  caller_id invalid for tenant. Don't retry — re-trying the same bad
  input wastes capacity. Mark lead terminal.
* ``INTERNAL`` — uncaught exception, 500 from bridge, schema mismatch.
  Single retry after a minute, then give up so a crashing code path
  doesn't poison every job.

The classifier reads the canonical error envelope from Track 1
(``{"error":{"code":..., "message":..., "details":...}}``). Falls back
to HTTP status code + free-text scanning for legacy callers.

This module is **pure logic** — no DB writes, no Redis, no logging.
It returns ``RetryDecision`` and the caller (``dialer_worker``)
chooses what to do with it. Easy to unit-test, easy to reason about.

Feature flag: ``RETRY_POLICY`` env var.
  * ``smart``  (default) — use this classifier.
  * ``legacy`` — call ``decide_legacy()`` to mirror the old behaviour
    (`should_retry()` + tenant `retry_delay_seconds`). Used for
    rollback without redeploy.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────
# Categories
# ─────────────────────────────────────────────────────────────────────


class FailureCategory(str, Enum):
    """Top-level reason a dial attempt failed.

    Values are stable strings — they end up in `dialer_jobs.failure_category`,
    in log lines, and in any future error-rate dashboard. Renaming is a
    breaking change.
    """
    TRANSIENT_NETWORK = "transient_network"
    AUTH_GATE = "auth_gate"
    CARRIER_REJECT = "carrier_reject"
    INVALID_INPUT = "invalid_input"
    INTERNAL = "internal"


@dataclass(frozen=True)
class RetryDecision:
    """What the worker should do with a failed job.

    ``should_retry`` — true if the worker should call
    ``queue_service.schedule_retry(job, delay_seconds=delay_seconds)``.

    ``delay_seconds`` — when to retry. Ignored when ``should_retry`` is
    false. Already accounts for attempt number.

    ``category`` / ``reason`` — written to ``dialer_jobs.failure_category``
    and ``dialer_jobs.failure_reason``. Reason is a fine-grained
    snake_case identifier; expect dashboards to facet on category and
    drill into reason.
    """
    should_retry: bool
    delay_seconds: int
    category: FailureCategory
    reason: str
    # Brief human-readable string for the log line. The structured fields
    # above are what dashboards consume.
    log_message: str


# ─────────────────────────────────────────────────────────────────────
# Per-category retry schedules
# ─────────────────────────────────────────────────────────────────────
#
# Each entry is a list of delays in seconds, indexed by attempt number
# (1 = first retry, 2 = second retry, etc.). When the worker has
# exhausted the list, no further retry is scheduled.

_RETRY_SCHEDULES: dict[FailureCategory, list[int]] = {
    # Network glitches usually clear in seconds. Three quick attempts
    # then give up — sustained network failure indicates a bigger problem
    # that the dialer can't fix.
    FailureCategory.TRANSIENT_NETWORK: [30, 120, 600],
    # Auth gates should never fire in steady state. One retry gives a
    # human operator a 30-second window to fix the config (env var,
    # caller_id record, etc.) before the job gets parked.
    FailureCategory.AUTH_GATE: [30],
    # Carrier-side: voicemail / busy / no-answer / SIP 4xx — back off
    # generously so the prospect isn't hammered, then give up.
    FailureCategory.CARRIER_REJECT: [300, 1800, 7200],
    # Bad input doesn't retry. The number stays broken until someone
    # corrects the lead row.
    FailureCategory.INVALID_INPUT: [],
    # An internal exception got through. One retry handles a transient
    # crash in worker code; sustained failure means we have a bug.
    FailureCategory.INTERNAL: [60],
}


# ─────────────────────────────────────────────────────────────────────
# Bridge-response codes → category mapping
# ─────────────────────────────────────────────────────────────────────
#
# These are the `error.code` values the telephony bridge returns. New
# codes added on the backend side should be added here too — otherwise
# they fall through to INTERNAL and trigger a single 60s retry.

_CODE_TO_CATEGORY: dict[str, FailureCategory] = {
    # Auth / config
    "caller_id_not_verified": FailureCategory.AUTH_GATE,
    "forbidden": FailureCategory.AUTH_GATE,
    "unauthorized": FailureCategory.AUTH_GATE,
    "csrf_failed": FailureCategory.AUTH_GATE,
    # Input
    "invalid_phone_number": FailureCategory.INVALID_INPUT,
    "blocked_region": FailureCategory.INVALID_INPUT,
    "dnc_match": FailureCategory.INVALID_INPUT,
    # Carrier
    "carrier_rejected": FailureCategory.CARRIER_REJECT,
    "no_answer": FailureCategory.CARRIER_REJECT,
    "busy": FailureCategory.CARRIER_REJECT,
    "voicemail": FailureCategory.CARRIER_REJECT,
    # Infra
    "pipeline_unavailable": FailureCategory.TRANSIENT_NETWORK,
    "pod_at_capacity": FailureCategory.TRANSIENT_NETWORK,
    "pod_draining": FailureCategory.TRANSIENT_NETWORK,
    "bad_gateway": FailureCategory.TRANSIENT_NETWORK,
    "service_unavailable": FailureCategory.TRANSIENT_NETWORK,
    "gateway_timeout": FailureCategory.TRANSIENT_NETWORK,
}


def classify_telephony_response(
    *,
    http_status: Optional[int],
    error_code: Optional[str],
    message: Optional[str] = None,
) -> tuple[FailureCategory, str]:
    """Map a bridge response to (category, reason).

    Lookup precedence:
      1. explicit ``error_code`` (canonical envelope) → table.
      2. HTTP status family fallback (5xx → transient network,
         4xx auth → auth_gate, 4xx other → invalid_input).
      3. None of the above → INTERNAL.

    Returns a tuple ``(category, reason)``. ``reason`` is always a
    snake_case identifier suitable for storage; it equals ``error_code``
    when one was supplied, otherwise it falls back to a class derived
    from the HTTP status.
    """
    if error_code:
        category = _CODE_TO_CATEGORY.get(error_code)
        if category is not None:
            return category, error_code
        # Unknown code — treat as internal so we get observability but
        # don't retry forever.
        return FailureCategory.INTERNAL, f"unknown_code_{error_code}"

    if http_status is None:
        return FailureCategory.INTERNAL, "no_response"

    if http_status >= 500:
        return FailureCategory.TRANSIENT_NETWORK, f"http_{http_status}"
    if http_status in (401, 403):
        return FailureCategory.AUTH_GATE, f"http_{http_status}"
    if http_status == 429:
        return FailureCategory.TRANSIENT_NETWORK, "rate_limited"
    if http_status >= 400:
        # Free-text scan as a last resort for messages from legacy paths.
        msg = (message or "").lower()
        if "invalid" in msg or "malformed" in msg:
            return FailureCategory.INVALID_INPUT, "http_400_invalid"
        return FailureCategory.INVALID_INPUT, f"http_{http_status}"

    # 2xx/3xx shouldn't reach here, but be safe.
    return FailureCategory.INTERNAL, f"unexpected_http_{http_status}"


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def smart_decision(
    *,
    category: FailureCategory,
    reason: str,
    attempt_number: int,
) -> RetryDecision:
    """Build a retry decision from a classified failure.

    ``attempt_number`` is the just-completed attempt (1 for the first
    try, 2 after one retry, etc.). The next-retry delay is read from
    ``_RETRY_SCHEDULES[category]`` at index ``attempt_number - 1``;
    when that index is out of range we stop retrying.
    """
    schedule = _RETRY_SCHEDULES.get(category, [])
    next_idx = max(0, attempt_number - 1)
    if next_idx >= len(schedule):
        return RetryDecision(
            should_retry=False,
            delay_seconds=0,
            category=category,
            reason=reason,
            log_message=(
                f"dial_failed_terminal category={category.value} reason={reason} "
                f"attempt={attempt_number} (max attempts for category exhausted)"
            ),
        )
    delay = schedule[next_idx]
    return RetryDecision(
        should_retry=True,
        delay_seconds=delay,
        category=category,
        reason=reason,
        log_message=(
            f"dial_failed category={category.value} reason={reason} "
            f"attempt={attempt_number} next_retry_in={delay}s"
        ),
    )


def legacy_decision(
    *,
    attempt_number: int,
    max_attempts: int,
    delay_seconds: int,
    reason: str = "legacy_unknown",
) -> RetryDecision:
    """Reproduce the old behaviour for the ``RETRY_POLICY=legacy`` flag.

    No classification — always uses the tenant's flat
    ``retry_delay_seconds`` and stops when ``attempt_number`` reaches
    ``max_attempts``. Records the failure category as ``INTERNAL`` so
    rows are still queryable, but the policy is identical to pre-Track-2.
    """
    should_retry = attempt_number < max_attempts
    return RetryDecision(
        should_retry=should_retry,
        delay_seconds=delay_seconds if should_retry else 0,
        category=FailureCategory.INTERNAL,
        reason=reason,
        log_message=(
            f"dial_failed_legacy attempt={attempt_number}/{max_attempts} "
            f"delay={delay_seconds}s reason={reason}"
        ),
    )


def use_smart_policy() -> bool:
    """Feature flag — read once per call so an env update takes effect
    without a redeploy (only a process restart, which systemd handles
    via ``Environment=`` overrides)."""
    return os.getenv("RETRY_POLICY", "smart").strip().lower() != "legacy"


# ─────────────────────────────────────────────────────────────────────
# Bridge-response parsing
# ─────────────────────────────────────────────────────────────────────


def parse_bridge_error(body_text: str | None) -> tuple[Optional[str], Optional[str]]:
    """Extract ``(code, message)`` from a bridge error body.

    Accepts the canonical envelope as JSON. Returns ``(None, None)``
    when the body isn't parseable JSON or doesn't contain the envelope
    — callers should fall back to HTTP-status classification.
    """
    if not body_text:
        return None, None
    try:
        import json
        parsed: Any = json.loads(body_text)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    err = parsed.get("error")
    if not isinstance(err, dict):
        return None, None
    code = err.get("code") if isinstance(err.get("code"), str) else None
    message = err.get("message") if isinstance(err.get("message"), str) else None
    return code, message

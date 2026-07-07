"""Disposition-based retry policy for *answered* dial outcomes.

Two retry brains used to live in this codebase and they disagreed:

* ``retry_policy.py`` classifies a failure that happened **before the
  call connected** — the telephony bridge rejected the originate (HTTP
  4xx/5xx, bad caller-id, DNS). It maps the bridge response to a
  ``FailureCategory`` and a per-category backoff schedule.

* ``call_service._handle_job_completion`` handled everything that
  happened **after the call connected** — busy, no-answer, voicemail,
  rejected, a real conversation. But it used a single flat
  ``RETRY_DELAY_SECONDS = 7200`` (2h) for *every* retryable outcome and
  a flat cap of 3. Busy waited 2h. No-answer waited 2h. Voicemail
  waited 2h. That is exactly the "treats every failure identically"
  problem ``retry_policy.py`` was written to kill — just on the other
  code path.

This module is the second brain done right. It takes a post-answer
``CallOutcome`` (the enum on ``app.domain.models.dialer_job``) and the
just-completed attempt number, and returns a ``DispositionDecision``:
whether to retry, after how long, and why. Pure logic — no DB, no
Redis, no logging — so it is trivially unit-testable and the caller
(``call_service``) owns the side effects.

Cadence (confirmed product policy, 2026-06-12):

    DISPOSITION   RETRY SCHEDULE              CAP
    ───────────   ──────────────────────────  ───
    Busy          5m → 15m → 45m              4
    No-answer     2h → next-day (~20h)        3
    Voicemail     4h (once)                   2
    Rejected      no retry — stop             1
    Failed        30s → 2m                    3
    Timeout       30s → 2m                    3
    Answered      done — never redial         —
    Goal achieved done — never redial         —

CAP is the maximum *total* dial attempts for the lead (initial + all
retries). The schedule for a retryable outcome therefore has
``CAP - 1`` entries; when the just-completed ``attempt_number`` reaches
``CAP`` (or the schedule is exhausted) we stop.

"Next-day" is expressed as a concrete ~20h delay rather than a sentinel:
combined with the dialer's calling-window gate (``RulesEngine.can_make_
call`` re-checks the tenant's allowed hours before every originate), a
20h offset reliably lands the retry on the following day at a different
time of day, and the window gate finalises legality. Keeping the
schedule as plain integers keeps this module pure and easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.models.dialer_job import CallOutcome


# ─────────────────────────────────────────────────────────────────────
# Cadence tables
# ─────────────────────────────────────────────────────────────────────
#
# Each schedule is a list of delays in seconds indexed by the
# just-completed attempt number (attempt 1 → index 0 picks the delay
# before attempt 2). An outcome absent from this table is terminal:
# either a success we never redial, or a hard reject.

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR

_RETRY_SCHEDULES: dict[CallOutcome, list[int]] = {
    # Busy: the line is alive, the person is simply on another call.
    # Worth a few quick-ish attempts spaced out over the next hour.
    CallOutcome.BUSY: [5 * _MINUTE, 15 * _MINUTE, 45 * _MINUTE],
    # No-answer: rang out, nobody picked up. Product rule (2026-07-07):
    # NEVER redial the same day — always wait a full 24h so the next
    # attempt lands on a following day. Two next-day attempts, then stop
    # (they're screening). The calling-window gate still finalises legality.
    CallOutcome.NO_ANSWER: [_DAY, _DAY],
    # Voicemail: we reached an answering machine (we hang up immediately,
    # never leave a message). Retry a full day later, not the same day.
    CallOutcome.VOICEMAIL: [_DAY],
    # Unavailable / phone switched off: not reachable right now but may be
    # on later. Treat like no-answer — retry a full day later, not same day.
    CallOutcome.UNAVAILABLE: [_DAY, _DAY],
    # Failed / timeout: transient pipeline or network trouble, not a
    # signal about the prospect. Fast geometric backoff. Cap is 3 total
    # attempts (2 retries) — the originate-side retry_policy keeps its
    # own deeper [30, 2m, 10m] schedule; this is the post-answer path.
    CallOutcome.FAILED: [30, 2 * _MINUTE],
    CallOutcome.TIMEOUT: [30, 2 * _MINUTE],
}

# Maximum *total* attempts per lead for each retryable disposition. This
# is a defensive cap enforced independently of the schedule length so a
# future schedule edit can't accidentally exceed the agreed ceiling.
_ATTEMPT_CAPS: dict[CallOutcome, int] = {
    CallOutcome.BUSY: 4,
    CallOutcome.NO_ANSWER: 3,
    CallOutcome.VOICEMAIL: 2,
    CallOutcome.UNAVAILABLE: 3,
    CallOutcome.FAILED: 3,
    CallOutcome.TIMEOUT: 3,
}

# Outcomes that mean the lead is done and must never be redialled.
_SUCCESS_OUTCOMES: frozenset[CallOutcome] = frozenset(
    {CallOutcome.GOAL_ACHIEVED, CallOutcome.ANSWERED}
)

# Outcomes that are terminal failures — record the result, never retry.
# REJECTED: they actively declined. GOAL_NOT_ACHIEVED: a real
# conversation that didn't convert — redialing is a human decision, not
# an automatic one. SPAM/INVALID/DISCONNECTED: dead input — the number is
# not active, so redialing it is pointless. (UNAVAILABLE / phone-off is
# NOT here: it may be reachable later, so it retries +24h — see schedules.)
_TERMINAL_NO_RETRY: frozenset[CallOutcome] = frozenset(
    {
        CallOutcome.REJECTED,
        CallOutcome.GOAL_NOT_ACHIEVED,
        CallOutcome.SPAM,
        CallOutcome.INVALID,
        CallOutcome.DISCONNECTED,
    }
)


@dataclass(frozen=True)
class DispositionDecision:
    """What the caller should do with a just-finished, answered call.

    ``should_retry`` — true → reschedule the dialer job after
    ``delay_seconds``. false → finalise the job (terminal).

    ``delay_seconds`` — seconds to wait before the next attempt. 0 when
    ``should_retry`` is false.

    ``is_success`` — the call reached a positive terminal state
    (goal achieved, or a real conversation). The caller uses this to
    pick the terminal job status (``goal_achieved`` vs ``completed``)
    and to avoid counting it as a failure.

    ``reason`` — stable snake_case identifier (the outcome value, plus
    ``_max_attempts`` / ``_terminal`` suffixes) for storage + dashboards.

    ``log_message`` — human-readable one-liner for the log.
    """

    should_retry: bool
    delay_seconds: int
    is_success: bool
    reason: str
    log_message: str


def is_success(outcome: CallOutcome) -> bool:
    """True for outcomes that should never be redialled because the
    call succeeded (goal achieved or a genuine conversation)."""
    return outcome in _SUCCESS_OUTCOMES


def decide(outcome: CallOutcome, attempt_number: int) -> DispositionDecision:
    """Decide retry vs terminal for a post-answer call outcome.

    ``attempt_number`` is the attempt that just completed (1 = the very
    first dial). The next-retry delay is read from the schedule at index
    ``attempt_number - 1``; when that index is out of range, or the
    per-disposition attempt cap is reached, we stop.
    """
    # Positive terminal — done, never redial.
    if outcome in _SUCCESS_OUTCOMES:
        return DispositionDecision(
            should_retry=False,
            delay_seconds=0,
            is_success=True,
            reason=outcome.value,
            log_message=f"disposition={outcome.value} success — no retry",
        )

    # Negative terminal — record, never redial.
    if outcome in _TERMINAL_NO_RETRY:
        return DispositionDecision(
            should_retry=False,
            delay_seconds=0,
            is_success=False,
            reason=outcome.value,
            log_message=f"disposition={outcome.value} terminal — no retry",
        )

    schedule = _RETRY_SCHEDULES.get(outcome)
    cap = _ATTEMPT_CAPS.get(outcome)
    if not schedule or cap is None:
        # Unknown / unmapped outcome — be conservative: finalise rather
        # than retry forever. Surfaces as `<outcome>_unmapped` so it's
        # visible if a new outcome value slips through.
        return DispositionDecision(
            should_retry=False,
            delay_seconds=0,
            is_success=False,
            reason=f"{outcome.value}_unmapped",
            log_message=f"disposition={outcome.value} unmapped — no retry",
        )

    next_idx = attempt_number - 1
    cap_reached = attempt_number >= cap
    if cap_reached or next_idx < 0 or next_idx >= len(schedule):
        return DispositionDecision(
            should_retry=False,
            delay_seconds=0,
            is_success=False,
            reason=f"{outcome.value}_max_attempts",
            log_message=(
                f"disposition={outcome.value} attempt={attempt_number}/"
                f"{cap} — cap reached, no retry"
            ),
        )

    delay = schedule[next_idx]
    return DispositionDecision(
        should_retry=True,
        delay_seconds=delay,
        is_success=False,
        reason=outcome.value,
        log_message=(
            f"disposition={outcome.value} attempt={attempt_number}/{cap} "
            f"next_retry_in={delay}s"
        ),
    )

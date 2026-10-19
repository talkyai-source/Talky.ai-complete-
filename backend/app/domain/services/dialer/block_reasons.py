"""Structured, user-facing reasons a dial did NOT happen.

Why this exists
---------------
Every pre-dial gate in ``dialer_worker.process_job`` produces a *raw* reason
string — ``calling_not_allowed_on_Tue``, ``outside_time_window_14:00_17:00``,
``max_concurrent_calls_reached_10/10`` — and those strings only ever reached a
worker log. A real incident (campaign 35c79aeb, 2026-07-28) ran for 8 minutes,
placed zero calls, and the user's only signal was "I can't call the number".
The dialer knew the answer the whole time: it was Tuesday, and the campaign
allowed Mon & Fri 14:00-17:00 Europe/London.

This module turns each raw reason into a :class:`BlockReason`:

  * a STABLE machine ``code`` the UI can switch on (never a log string),
  * a human ``message`` that says what to DO,
  * ``next_eligible_at`` wherever it is computable (schedule windows,
    cooldowns, per-day caps, pacing gaps),
  * ``severity`` / ``stage`` matching the vocabulary already used by
    ``app.domain.services.call_issue_advice`` so both surfaces agree.

The raw strings are still emitted verbatim into ``dialer_jobs.failure_reason``
(``details["raw"]`` keeps them too) — nothing downstream that matches on them
breaks. This is an ADDITIVE layer on top.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from app.domain.models.calling_rules import CallingRules


class BlockCode(str, Enum):
    """Stable, machine-readable reasons a call was not placed.

    These values are a UI contract — rename one and the frontend stops
    recognising it. Add new members; do not repurpose existing ones.
    """

    # Campaign / account level
    CAMPAIGN_NOT_RUNNING = "campaign_not_running"
    OUT_OF_MINUTES = "out_of_minutes"
    METERING_UNAVAILABLE = "metering_unavailable"

    # Schedule (compliance-relevant — see the COMPLIANCE note in
    # dialer/testing_override.py before changing anything here)
    SCHEDULE_DAY_NOT_ALLOWED = "schedule_day_not_allowed"
    SCHEDULE_OUTSIDE_WINDOW = "schedule_outside_window"
    # The window itself is unreadable or ends before it starts: it never opens,
    # so it is NOT in SCHEDULE_BLOCK_CODES (no testing bypass, no "wait for the
    # window"). A person has to fix the calling hours.
    SCHEDULE_INVALID = "schedule_invalid"

    # Per-lead
    LEAD_COOLDOWN = "lead_cooldown"
    DAILY_LEAD_CAP = "daily_lead_cap"

    # Capacity / pacing (self-clearing, benign)
    MAX_CONCURRENT_CALLS = "max_concurrent_calls"
    BATCH_CAPACITY = "batch_capacity"
    CALL_GAP = "call_gap"
    TENANT_GAP = "tenant_gap"

    # Safety
    CALL_GUARD_BLOCKED = "call_guard_blocked"
    CALL_GUARD_THROTTLED = "call_guard_throttled"
    CALL_GUARD_QUEUED = "call_guard_queued"
    DNC = "dnc"

    # Infrastructure / provider
    VOICE_PIPELINE_UNAVAILABLE = "voice_pipeline_unavailable"
    CALLER_ID_NOT_VERIFIED = "caller_id_not_verified"
    ORIGINATE_FAILED = "originate_failed"
    # The bridge refused the tenant's phone line (422 tenant_pbx_required): no
    # ready line, the line is not registered, or its caller ID is unverified.
    LINE_NOT_READY = "line_not_ready"
    # The calling service itself is not connected to the phone system (adapter
    # down, pod draining/at capacity, telephony off on this node). Not a
    # voice-provider (TTS/STT) problem.
    TELEPHONY_UNAVAILABLE = "telephony_unavailable"

    UNKNOWN = "unknown"

    # NOT a block — the schedule gate was DELIBERATELY bypassed by the
    # opt-in testing override. Surfaced through the same channel so the UI
    # can show a loud "TESTING MODE" banner instead of silently dialling
    # outside the configured hours.
    TESTING_MODE_SCHEDULE_BYPASSED = "testing_mode_schedule_bypassed"


#: The codes that mean "this campaign is WAITING for its calling window", not
#: "this campaign has finished its work". Anything that reports campaign
#: progress must treat these as *deferred*, never as completed.
SCHEDULE_BLOCK_CODES: frozenset[BlockCode] = frozenset({
    BlockCode.SCHEDULE_DAY_NOT_ALLOWED,
    BlockCode.SCHEDULE_OUTSIDE_WINDOW,
})

#: Pacing/capacity codes that clear on their own within seconds or minutes and
#: never need operator action. The Call Issues panel already filters these out
#: at the SQL level; listed here so any other surface can too.
SELF_CLEARING_CODES: frozenset[BlockCode] = frozenset({
    BlockCode.BATCH_CAPACITY,
    BlockCode.CALL_GAP,
    BlockCode.TENANT_GAP,
    BlockCode.CALL_GUARD_THROTTLED,
    BlockCode.CALL_GUARD_QUEUED,
})


@dataclass(frozen=True)
class BlockReason:
    """One structured, user-facing explanation for a call that did not go out."""

    code: BlockCode
    message: str
    severity: str = "warning"          # error | warning | info  (matches call_issue_advice)
    stage: str = "other"               # quota | schedule | campaign | caller_id | voice |
                                       # safety | concurrency | pacing | carrier | other
    next_eligible_at: Optional[datetime] = None
    retry_after_seconds: Optional[int] = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_schedule_block(self) -> bool:
        return self.code in SCHEDULE_BLOCK_CODES

    @property
    def is_self_clearing(self) -> bool:
        return self.code in SELF_CLEARING_CODES

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe shape — this is what the UI receives."""
        return {
            "code": self.code.value,
            "message": self.message,
            "severity": self.severity,
            "stage": self.stage,
            "next_eligible_at": (
                self.next_eligible_at.isoformat() if self.next_eligible_at else None
            ),
            "retry_after_seconds": self.retry_after_seconds,
            "details": dict(self.details or {}),
        }


# ---------------------------------------------------------------------------
# Schedule formatting helpers
# ---------------------------------------------------------------------------

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAY_LONG = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def day_name(weekday: int, *, long: bool = False) -> str:
    """``0`` → ``Mon``/``Monday``. Out-of-range indexes degrade gracefully."""
    names = _DAY_LONG if long else _DAY_NAMES
    try:
        return names[int(weekday) % 7]
    except (TypeError, ValueError):
        return "?"


def describe_days(allowed_days) -> str:
    """``[0, 4]`` → ``"Mon & Fri"``; ``[0,1,2,3,4]`` → ``"Mon-Fri"``."""
    try:
        days = sorted({int(d) % 7 for d in (allowed_days or [])})
    except (TypeError, ValueError):
        days = []
    if not days:
        return "no days"
    if len(days) == 7:
        return "every day"
    # Collapse a single contiguous run (the common Mon-Fri case).
    if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
        return f"{day_name(days[0])}-{day_name(days[-1])}"
    names = [day_name(d) for d in days]
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} & {names[-1]}"


def describe_schedule(rules: CallingRules) -> str:
    """``"Mon & Fri, 14:00-17:00 Europe/London"`` — the sentence fragment the
    user needs in order to recognise (and fix) their own configuration."""
    return (
        f"{describe_days(getattr(rules, 'allowed_days', None))}, "
        f"{getattr(rules, 'time_window_start', '?')}-"
        f"{getattr(rules, 'time_window_end', '?')} "
        f"{getattr(rules, 'timezone', 'UTC')}"
    )


def _humanize_delta(seconds: Optional[float]) -> str:
    """``9000`` → ``"in about 2 hours"``. Empty string when unknown/past."""
    if seconds is None or seconds <= 0:
        return ""
    mins = int(seconds // 60)
    if mins < 1:
        return "in under a minute"
    if mins < 60:
        return f"in about {mins} minute{'s' if mins != 1 else ''}"
    hours = mins / 60
    if hours < 24:
        h = int(round(hours))
        return f"in about {h} hour{'s' if h != 1 else ''}"
    days = int(round(hours / 24))
    return f"in about {days} day{'s' if days != 1 else ''}"


def format_when(when: Optional[datetime], rules: Optional[CallingRules] = None) -> str:
    """``"Fri 01 Aug at 14:00 Europe/London (in about 2 days)"``.

    Rendered in the schedule's OWN timezone — a user who configured
    Europe/London must not be shown a UTC timestamp and have to convert.
    """
    if when is None:
        return ""
    tzname = getattr(rules, "timezone", None) or "UTC"
    local = when
    try:
        import pytz

        local = when.astimezone(pytz.timezone(tzname))
    except Exception:  # noqa: BLE001 — bad tz must never break the message
        tzname = "UTC"
        local = when.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    delta = _humanize_delta((when - now).total_seconds())
    stamp = f"{day_name(local.weekday())} {local:%d %b} at {local:%H:%M} {tzname}"
    return f"{stamp} ({delta})" if delta else stamp


def next_window_start(
    rules: Optional[CallingRules], *, now: Optional[datetime] = None
) -> Optional[datetime]:
    """UTC datetime of the next moment the calling window opens, or None.

    Thin wrapper over ``CallingRules.get_next_window_start`` that never
    raises — a malformed schedule must degrade to "we don't know when",
    not break the reason pipeline.
    """
    if rules is None:
        return None
    try:
        nxt = rules.get_next_window_start(from_time=now)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        return nxt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Raw reason  →  BlockReason
# ---------------------------------------------------------------------------

_COOLDOWN_RE = re.compile(r"lead_cooldown_(?P<done>[\d.]+)h_of_(?P<need>[\d.]+)h")
_CONCURRENCY_RE = re.compile(r"max_concurrent_calls_reached_(?P<cur>\d+)/(?P<cap>\d+)")
_DAILY_CAP_RE = re.compile(r"daily_lead_cap_reached_(?P<cur>\d+)/(?P<cap>\d+)")


def _schedule_reason(
    code: BlockCode,
    raw: str,
    rules: Optional[CallingRules],
    now: Optional[datetime],
) -> BlockReason:
    schedule = describe_schedule(rules) if rules is not None else "its configured hours"
    nxt = next_window_start(rules, now=now)
    when = format_when(nxt, rules)

    if code is BlockCode.SCHEDULE_DAY_NOT_ALLOWED:
        today = (now or datetime.now(timezone.utc))
        # Report the day in the SCHEDULE's timezone — "it's Tuesday" has to
        # mean Tuesday where the campaign dials, not on the server.
        try:
            import pytz

            today = today.astimezone(pytz.timezone(getattr(rules, "timezone", "UTC") or "UTC"))
        except Exception:  # noqa: BLE001
            pass
        lead = (
            f"{day_name(today.weekday(), long=True)} is not one of this campaign's "
            f"calling days."
        )
    else:
        lead = "The current time is outside this campaign's calling window."

    msg = f"{lead} Calling is scheduled for {schedule}."
    if when:
        msg += f" Next window: {when}."
    msg += (
        " It will dial automatically then — or change the calling days/hours "
        "in the campaign's calling rules to start sooner."
    )

    retry = None
    if nxt is not None:
        # Derive from the SAME clock `nxt` was computed against. This used
        # datetime.now(timezone.utc) unconditionally while `nxt` came from the
        # injected `now`, so the two fields could describe different instants —
        # a reason card could say "Next window: Friday 14:00" and simultaneously
        # report a retry delay measured from a different moment. In production
        # `now` is normally None and they agreed by luck; anywhere it is passed
        # (tests, replay, backfill) they silently diverged.
        _ref = now or datetime.now(timezone.utc)
        retry = max(0, int((nxt - _ref).total_seconds()))

    return BlockReason(
        code=code,
        message=msg,
        severity="warning",
        stage="schedule",
        next_eligible_at=nxt,
        retry_after_seconds=retry,
        details={
            "raw": raw,
            "schedule": schedule,
            "timezone": getattr(rules, "timezone", None),
            "allowed_days": list(getattr(rules, "allowed_days", []) or []),
            "time_window_start": getattr(rules, "time_window_start", None),
            "time_window_end": getattr(rules, "time_window_end", None),
        },
    )


_LINE_NOT_READY_TAIL = " Calls resume automatically once it is fixed."

# trunk_resolver refusal reasons and the bridge's 422 code for them.
_RAW_LINE_REASON_TOKENS = (
    "tenant_pbx_required",
    "no_ready_trunk",
    "no_own_trunk",
    "trunk_not_ready",
    "trunk_caller_id_not_verified",
    "trunk_invalid",
    "trunk_lookup_failed",
    "trunk_not_authorized",
)

# Bridge 503 codes that mean the phone system (not the voice provider) is down.
_TELEPHONY_DOWN_TOKENS = (
    "telephony_unavailable",
    "telephony_adapter_unavailable",
    "telephony_not_active_on_node",
    "telephony_disabled_on_node",
    "pod_draining",
    "pod_at_capacity",
)


def _line_not_ready_reason(
    line_reason: str,
    extra: dict[str, Any],
    next_eligible_at: Optional[datetime],
    retry_after_seconds: Optional[int],
) -> BlockReason:
    """Name WHY the bridge refused the tenant's line.

    ``line_reason`` is the ``reason`` token of ``trunk_resolver`` (carried in
    the 422 body); matching is by substring so every ``campaign_/pool_assigned_*``
    variant is covered.
    """
    if "caller_id" in line_reason:
        cause = (
            "The caller ID for this phone line is not verified (or not set), so "
            "the carrier would refuse the call. Ask support to verify the "
            "number."
        )
    elif "not_ready" in line_reason:
        cause = (
            "The phone line chosen for this campaign is not registered with the "
            "carrier right now, so no calls can go out on it."
        )
    elif line_reason in ("no_ready_trunk", "no_own_trunk"):
        cause = (
            "There is no ready phone line for this account: none is set up, "
            "active and registered with the carrier."
        )
    elif "invalid" in line_reason or "lookup_failed" in line_reason:
        cause = (
            "The phone line assigned to this campaign is missing or could not "
            "be checked."
        )
    else:
        cause = (
            "The phone line for this campaign is not ready: it is not "
            "registered, or its caller ID is not verified."
        )
    return BlockReason(
        BlockCode.LINE_NOT_READY,
        cause + _LINE_NOT_READY_TAIL,
        severity="error", stage="carrier",
        next_eligible_at=next_eligible_at,
        retry_after_seconds=retry_after_seconds,
        details={**extra, "line_reason": line_reason or None},
    )


def classify(
    raw_reason: Optional[str],
    *,
    rules: Optional[CallingRules] = None,
    retry_after_seconds: Optional[int] = None,
    now: Optional[datetime] = None,
    details: Optional[dict[str, Any]] = None,
) -> BlockReason:
    """Map a raw dialer gate reason to a structured, user-facing reason.

    ``rules`` are the EFFECTIVE calling rules (tenant defaults overlaid with
    the campaign's schedule — see ``dialer.campaign_schedule.effective_rules``)
    so the message quotes what the user actually configured.

    ``retry_after_seconds`` is the delay the worker already decided on; it is
    used to derive ``next_eligible_at`` for gates whose next-eligible moment
    isn't otherwise computable (pacing gaps, guard throttles).
    """
    raw = (raw_reason or "").strip()
    low = raw.lower()
    now = now or datetime.now(timezone.utc)
    extra: dict[str, Any] = {"raw": raw, **(details or {})}

    def _fallback_next() -> Optional[datetime]:
        if retry_after_seconds is None:
            return None
        return now + timedelta(seconds=max(0, int(retry_after_seconds)))

    # ---- refused phone line ---------------------------------------------
    # First: ``line_not_ready:<bridge reason>`` embeds the bridge's own reason
    # token (e.g. ``trunk_caller_id_not_verified``), which the older substring
    # branches below would otherwise claim.
    if low.startswith("line_not_ready"):
        return _line_not_ready_reason(
            low.partition(":")[2].strip(), extra, _fallback_next(), retry_after_seconds,
        )
    # Raw route-refusal tokens (stored on older rows, or a bare 422 code)
    # describe the same thing: a phone-line problem, not a generic failure.
    if any(token in low for token in _RAW_LINE_REASON_TOKENS):
        return _line_not_ready_reason(low, extra, _fallback_next(), retry_after_seconds)

    # ---- schedule -------------------------------------------------------
    # ``calling_not_allowed_on_Tue`` (day gate) and
    # ``outside_time_window_14:00_17:00`` (hour gate) both come from
    # CallingRules.is_within_time_window.
    if "invalid_time_window" in low or "schedule_invalid" in low:
        return BlockReason(
            BlockCode.SCHEDULE_INVALID,
            "No calls are going out because this campaign's calling hours are "
            "invalid: the start time must be before the end time on the same "
            "day (24-hour HH:MM; calling hours can't run past midnight). Fix "
            "the calling hours and calls resume within the hour.",
            severity="error", stage="schedule", details=extra,
        )
    if "calling_not_allowed_on" in low:
        return _schedule_reason(BlockCode.SCHEDULE_DAY_NOT_ALLOWED, raw, rules, now)
    if "outside_time_window" in low or "outside_calling_window" in low:
        return _schedule_reason(BlockCode.SCHEDULE_OUTSIDE_WINDOW, raw, rules, now)

    # ---- account / campaign --------------------------------------------
    if "metering_unavailable" in low:
        return BlockReason(
            BlockCode.METERING_UNAVAILABLE,
            "Minute allowance could not be verified. This queued call is deferred; "
            "the same request will be checked again when metering recovers.",
            severity="warning", stage="quota", details=extra,
        )
    if "out_of_minutes" in low:
        return BlockReason(
            BlockCode.OUT_OF_MINUTES,
            "This account has used all of this month's plan minutes, so calls are "
            "paused. Upgrade the plan or wait for the next billing cycle, then "
            "start the campaign again.",
            severity="error", stage="quota", details=extra,
        )
    if "campaign_paused" in low:
        # Paused is temporary and loses nothing: the queued calls are HELD.
        return BlockReason(
            BlockCode.CAMPAIGN_NOT_RUNNING,
            "The campaign is paused, so its queued calls are on hold. Resume "
            "the campaign to continue dialling.",
            severity="warning", stage="campaign", details=extra,
        )
    if "campaign_stopped" in low or "campaign_not_runnable" in low:
        return BlockReason(
            BlockCode.CAMPAIGN_NOT_RUNNING,
            "The campaign isn't running, so queued calls are skipped. Press Start "
            "to begin dialling.",
            severity="warning", stage="campaign", details=extra,
        )

    # ---- per-lead -------------------------------------------------------
    m = _COOLDOWN_RE.search(low)
    if m or "lead_cooldown" in low or "min_hours_between_calls" in low:
        nxt = None
        if m:
            try:
                remaining_h = float(m.group("need")) - float(m.group("done"))
                if remaining_h > 0:
                    nxt = now + timedelta(hours=remaining_h)
                extra.update(
                    hours_since_last_call=float(m.group("done")),
                    min_hours_between_calls=float(m.group("need")),
                )
            except (TypeError, ValueError):
                nxt = None
        return BlockReason(
            BlockCode.LEAD_COOLDOWN,
            "This contact was called recently and is inside the minimum gap "
            "between calls. It retries automatically once the cooldown passes — "
            "or lower 'minimum hours between calls' in the calling rules."
            + (f" Next attempt: {format_when(nxt, rules)}." if nxt else ""),
            severity="warning", stage="schedule",
            next_eligible_at=nxt,
            retry_after_seconds=(
                int((nxt - now).total_seconds()) if nxt else retry_after_seconds
            ),
            details=extra,
        )

    m = _DAILY_CAP_RE.search(low)
    if m or "daily_lead_cap" in low:
        if m:
            extra.update(attempts_today=int(m.group("cur")), daily_cap=int(m.group("cap")))
        nxt = _fallback_next()
        return BlockReason(
            BlockCode.DAILY_LEAD_CAP,
            "This contact has already been dialled the maximum number of times "
            "allowed in one day. It resumes after midnight UTC — or raise "
            "'max calls per lead per day' in the calling rules."
            + (f" Next attempt: {format_when(nxt, rules)}." if nxt else ""),
            severity="warning", stage="schedule",
            next_eligible_at=nxt, retry_after_seconds=retry_after_seconds,
            details=extra,
        )

    # ---- capacity / pacing ---------------------------------------------
    m = _CONCURRENCY_RE.search(low)
    if m or "max_concurrent_calls_reached" in low:
        if m:
            extra.update(active_calls=int(m.group("cur")), max_concurrent=int(m.group("cap")))
        cur = extra.get("active_calls")
        cap = extra.get("max_concurrent")
        detail = f" ({cur} of {cap} in use)" if cur is not None and cap is not None else ""
        return BlockReason(
            BlockCode.MAX_CONCURRENT_CALLS,
            f"This account is already running the maximum number of simultaneous "
            f"calls{detail}. Calls resume as slots free up — or raise 'max "
            f"concurrent calls' in the calling rules.",
            severity="warning", stage="concurrency",
            next_eligible_at=_fallback_next(),
            retry_after_seconds=retry_after_seconds, details=extra,
        )
    if "batch_capacity" in low:
        return BlockReason(
            BlockCode.BATCH_CAPACITY,
            "The campaign dials in batches and every slot in the current batch is "
            "busy. This number starts as soon as an in-flight call finishes — no "
            "action needed.",
            severity="info", stage="pacing",
            next_eligible_at=_fallback_next(),
            retry_after_seconds=retry_after_seconds, details=extra,
        )
    if "tenant_gap" in low:
        return BlockReason(
            BlockCode.TENANT_GAP,
            "Another campaign on this account just placed a call; campaigns take "
            "turns so only one call originates at a time — no action needed.",
            severity="info", stage="pacing",
            next_eligible_at=_fallback_next(),
            retry_after_seconds=retry_after_seconds, details=extra,
        )
    if "call_gap" in low:
        return BlockReason(
            BlockCode.CALL_GAP,
            "The campaign spaces its calls (inter-call gap). This number dials "
            "automatically when its turn comes — no action needed.",
            severity="info", stage="pacing",
            next_eligible_at=_fallback_next(),
            retry_after_seconds=retry_after_seconds, details=extra,
        )

    # ---- safety ---------------------------------------------------------
    if "call_guard_blocked" in low:
        return BlockReason(
            BlockCode.CALL_GUARD_BLOCKED,
            "A safety check blocked this call — the number may be on a "
            "Do-Not-Call list, or blocked by geographic/abuse rules. Remove it "
            "from the campaign or review the account's safety settings.",
            severity="error", stage="safety", details=extra,
        )
    if "call_guard_throttled" in low:
        return BlockReason(
            BlockCode.CALL_GUARD_THROTTLED,
            "Calls are going out faster than this account's rate limit allows. "
            "The dialer retries shortly — no action needed.",
            severity="warning", stage="safety",
            next_eligible_at=_fallback_next(),
            retry_after_seconds=retry_after_seconds, details=extra,
        )
    if "call_guard_queued" in low:
        return BlockReason(
            BlockCode.CALL_GUARD_QUEUED,
            "The call is queued behind the account's rate limiter and dials "
            "automatically when a slot frees up.",
            severity="info", stage="safety",
            next_eligible_at=_fallback_next(),
            retry_after_seconds=retry_after_seconds, details=extra,
        )
    if "dnc" in low or "do_not_call" in low:
        return BlockReason(
            BlockCode.DNC,
            "This number is on a Do-Not-Call list and will not be dialled. "
            "Remove it from the campaign.",
            severity="error", stage="safety", details=extra,
        )

    # ---- infrastructure -------------------------------------------------
    if "caller_id_not_verified" in low:
        return BlockReason(
            BlockCode.CALLER_ID_NOT_VERIFIED,
            "The caller ID this call would use is not verified for this "
            "account, so the call was not placed. Ask support to verify the "
            "number (only a platform administrator can verify numbers).",
            severity="error", stage="caller_id", details=extra,
        )
    if any(token in low for token in _TELEPHONY_DOWN_TOKENS):
        return BlockReason(
            BlockCode.TELEPHONY_UNAVAILABLE,
            "The calling service is not connected to the phone system. Calls "
            "resume automatically when it reconnects — no action needed.",
            severity="warning", stage="carrier",
            next_eligible_at=_fallback_next(),
            retry_after_seconds=retry_after_seconds, details=extra,
        )
    if "voice_pipeline_unavailable" in low or "pre_originate_warmup" in low:
        return BlockReason(
            BlockCode.VOICE_PIPELINE_UNAVAILABLE,
            "The voice pipeline (text-to-speech / speech-to-text) wasn't ready, "
            "so the call was held back to avoid dead air on pickup. Check the "
            "campaign's voice-provider key/health, or switch provider. It keeps "
            "retrying automatically.",
            severity="error", stage="voice",
            next_eligible_at=_fallback_next(),
            retry_after_seconds=retry_after_seconds, details=extra,
        )

    if not raw:
        return BlockReason(
            BlockCode.UNKNOWN,
            "The call was held back and the dialer did not report a reason. If it "
            "doesn't clear on its own, check the campaign's settings or contact "
            "support.",
            severity="warning", stage="other", details=extra,
        )

    return BlockReason(
        BlockCode.ORIGINATE_FAILED,
        "The call could not be placed. If it doesn't clear on its own, check the "
        "campaign's settings (caller ID, voice provider, calling hours) or "
        "contact support.",
        severity="warning", stage="other",
        next_eligible_at=_fallback_next(),
        retry_after_seconds=retry_after_seconds, details=extra,
    )


def testing_override_notice(
    rules: Optional[CallingRules], *, source: str, raw_reason: Optional[str] = None,
) -> BlockReason:
    """The reason surfaced when a call is placed DESPITE the schedule gate,
    because the operator turned on the explicit testing override.

    Not a block — it is deliberately routed through the same channel so the
    override cannot be invisible: the UI shows "TESTING MODE: schedule
    bypassed" wherever it shows blocking reasons.
    """
    schedule = describe_schedule(rules) if rules is not None else "its configured hours"
    return BlockReason(
        BlockCode.TESTING_MODE_SCHEDULE_BYPASSED,
        "TESTING MODE: schedule bypassed. Calls are going out even though the "
        f"current time is outside the configured calling hours ({schedule}). "
        "This override is for testing only — turn it off before real calling, "
        "because calling-hour rules exist for legal/compliance reasons.",
        severity="warning", stage="schedule",
        details={"raw": raw_reason or "", "override_source": source, "schedule": schedule},
    )

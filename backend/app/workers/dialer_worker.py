"""
Dialer Worker
Background worker for processing outbound call jobs

Run as separate process:
    python -m app.workers.dialer_worker
"""

import asyncio
import logging
import os
import signal
import sys
import time
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from contextlib import asynccontextmanager

from app.core.dotenv_compat import load_dotenv

# Load environment variables
load_dotenv()

try:
    import redis.asyncio as redis
    import asyncpg
except ImportError as e:
    raise ImportError(f"Required dependency not installed: {e}")

from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.models.calling_rules import CallingRules
from app.domain.models.voice_contract import generate_talklee_call_id
from app.domain.services.queue_service import DialerQueueService
from app.domain.services.scheduling_rules import SchedulingRuleEngine
from app.core.db import init_db_pool, close_db_pool, Database
from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DialerCallIntent:
    """One durable provider-attempt identity.

    ``dialer_job_id`` alone is not an idempotency key because a job can make a
    legitimate later retry.  The database uniqueness boundary is the job plus
    its one-based attempt number; every replay of that pair receives these same
    call identities.
    """

    call_id: str
    talklee_call_id: str
    leg_id: str
    status: str
    provider_call_id: Optional[str]
    created: bool


class CampaignStatusUnavailable(RuntimeError):
    """Campaign existence/runnability could not be proven due to DB failure."""


class ComplianceLookupUnavailable(RuntimeError):
    """A pre-dial compliance input (tenant rules, campaign schedule, lead
    cooldown / daily-cap / timezone) could not be read because the QUERY failed.

    A failed read is not an absent row: absent means "use the defaults",
    failed means the gate cannot be evaluated, so the job is re-deferred
    instead of dialled.
    """


class AttemptSettledFailed(RuntimeError):
    """A replay found its attempt already settled FAILED without an answer by
    the bridge (provider absence proven, no call-end finalizer involved), so no
    retry exists for it. Routed through the generic failure boundary, which
    classifies it and schedules attempt N+1 instead of dropping the lead."""

    def __init__(self, call_id: str):
        # str(exc) lands in dialer_jobs.last_error (the Call Issues panel):
        # a bare call UUID there explains nothing.
        super().__init__(
            f"attempt settled failed by the bridge without an answer (call {call_id})"
        )


class HeldIntentExpired(RuntimeError):
    """A same-attempt hold can no longer be kept on its committed, never-claimed
    intent: either it is about to outlive the stuck-call reaper (which writes a
    provider-less pre-answer row off as ``termination_pending`` after 10 min,
    a state nothing settles), or the calling window closed while it waited.

    Routed through the generic failure boundary: the intent is terminalised
    (provider absence is provable) and attempt N+1 is staged, whose fresh pass
    re-runs every new-call gate. ``retry_reason`` set means "not a failure of
    the lead": retried whatever the attempt number."""

    def __init__(self, message: str, *, retry_reason: Optional[str] = None):
        super().__init__(message)
        self.retry_reason = retry_reason


# CallGuard denials that clear by themselves (an operator un-pauses outbound
# calls, business hours open, the abuse window slides, a payment lands, a
# concurrency slot frees). They are DEFERRED, not terminally blocked.
_GUARD_TRANSIENT_CHECKS = frozenset(
    {
        "platform_calls_enabled",
        "business_hours",
        "velocity_check",
        "concurrency_limit",
        "rate_limit",
    }
)
# Account-state checks: transient unless the tenant is gone or its
# subscription is cancelled for good (reason is `tenant_<status>` /
# `subscription_<status>`).
_GUARD_ACCOUNT_CHECKS = frozenset({"tenant_active", "subscription_valid", "partner_active"})
_GUARD_PERMANENT_ACCOUNT_REASONS = frozenset(
    {
        "tenant_not_found",
        # The tenant is leaving (tenants.status); unlike a suspension it does
        # not come back, so its leads stop instead of re-deferring forever.
        "tenant_pending_deletion",
        "partner_not_found",
        "tenant_cancelled",
        "tenant_canceled",
        "subscription_cancelled",
        "subscription_canceled",
        "subscription_incomplete_expired",
        "partner_cancelled",
        "partner_canceled",
    }
)
# An infrastructure error inside a fail-closed check (CallGuard blocks rather
# than allow). The query failed; nothing is known about the number itself.
_GUARD_INFRA_REASON_PREFIXES = (
    "check_error_blocked", "dnc_check_unavailable", "configuration_load_error",
)
# Seconds to hold a deferred job. Every guard evaluation INCRs the rate-limit
# counter and writes a call_guard_decisions row, so a platform pause must never
# be polled on a short loop.
_GUARD_DEFER_SECONDS = 600
_GUARD_INFRA_DEFER_SECONDS = 60


def _classify_guard_denial(guard_result) -> Optional[tuple[str, int]]:
    """Return ``(reason, delay_seconds)`` when EVERY failed check of a guard
    denial is transient, else ``None`` (the denial is permanent / unknown)."""
    failed = [
        c for c in (getattr(guard_result, "check_results", None) or [])
        if not getattr(c, "passed", True)
    ]
    if not failed:
        return None
    first_reason = None
    delay = _GUARD_DEFER_SECONDS
    for check in failed:
        name = getattr(getattr(check, "check", None), "value", getattr(check, "check", None))
        reason = str(getattr(check, "reason", None) or name or "")
        low = reason.lower()
        if low.startswith(_GUARD_INFRA_REASON_PREFIXES):
            delay = min(delay, _GUARD_INFRA_DEFER_SECONDS)
            # Neutral token: the raw reason may contain "dnc", which the
            # block-reason vocabulary would present as a DNC match.
            reason = "guard_check_unavailable"
        elif name in _GUARD_ACCOUNT_CHECKS:
            if low in _GUARD_PERMANENT_ACCOUNT_REASONS:
                return None
        elif name in _GUARD_TRANSIENT_CHECKS:
            if name in ("concurrency_limit", "rate_limit"):
                delay = min(delay, _GUARD_INFRA_DEFER_SECONDS)
        else:
            return None
        first_reason = first_reason or reason
    return (first_reason or "call_guard_deferred"), delay

# Configure logging for worker
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


class DialerWorker:
    """
    Background worker for processing dialer jobs.

    Responsibilities:
    - Dequeue jobs from Redis
    - Check scheduling rules (time window, concurrent limits)
    - Initiate outbound calls via telephony provider
    - Handle call results and schedule retries

    Architecture:
    - Runs as separate process from FastAPI
    - Connects to same Redis and PostgreSQL instances
    - Publishes call events for Voice Worker to handle
    """

    # Worker configuration
    POLL_INTERVAL = 1.0  # Seconds between queue checks when empty
    # Re-defer delay when a pre-dial compliance lookup fails (never a tight loop).
    COMPLIANCE_LOOKUP_REDEFER_SECONDS = 30
    SCHEDULED_CHECK_INTERVAL = 60  # Seconds between scheduled job checks
    MAX_CONSECUTIVE_ERRORS = 10

    # API base URL for webhooks
    API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

    def __init__(self):
        self.queue_service = DialerQueueService()
        self.rules_engine = SchedulingRuleEngine()

        self.running = False
        self._db_pool: Optional[asyncpg.Pool] = None
        self._redis: Optional[redis.Redis] = None

        # Stats
        self._jobs_processed = 0
        self._jobs_failed = 0
        # Set to epoch so the very first loop iteration runs the scheduled check
        self._last_scheduled_check = datetime(2000, 1, 1, tzinfo=timezone.utc)
        # Stuck-job reaper cadence (epoch → run on first iteration).
        self._last_reap_check = datetime(2000, 1, 1, tzinfo=timezone.utc)

    async def initialize(self) -> None:
        """Initialize connections to Redis and PostgreSQL."""
        logger.info("Initializing Dialer Worker...")

        # Initialize queue service (Redis)
        await self.queue_service.initialize()

        # Initialize PostgreSQL pool — reuse the container's pool when running
        # inside FastAPI to avoid creating a second connection pool.
        try:
            from app.core.container import get_container

            container = get_container()
            if container.is_initialized and container.db_pool:
                self._db_pool = container.db_pool
                logger.info("Dialer Worker reusing container DB pool")
            else:
                self._db_pool = await init_db_pool()
                logger.info("Dialer Worker created standalone DB pool")
        except Exception:
            self._db_pool = await init_db_pool()
            logger.info("Dialer Worker created standalone DB pool (fallback)")

        # Initialize separate Redis connection for pub/sub
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self._redis = await redis.from_url(redis_url, decode_responses=True)

        logger.info("Dialer Worker initialized successfully")

    async def run(self) -> None:
        """
        Main worker loop.

        Continuously:
        1. Process any due scheduled retries
        2. Dequeue and process jobs
        3. Handle errors gracefully
        """
        await self.initialize()

        self.running = True
        consecutive_errors = 0

        logger.info("Dialer Worker started - listening for jobs")

        # Liveness layer: a concurrent heartbeat task that (a) writes a Redis
        # heartbeat timestamp the health API can watch, (b) sends the systemd
        # READY=1 handshake once and pets the WATCHDOG=1 timer every tick. It
        # runs on THIS event loop, so a wedged loop stops petting the watchdog
        # and systemd restarts the process. Started before the main loop so
        # READY=1 is reachable on the normal startup path (initialize() already
        # succeeded above).
        heartbeat_task = asyncio.create_task(self._heartbeat())

        try:
            await self._run_loop(consecutive_errors)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass

        await self.shutdown()

    async def _run_loop(self, consecutive_errors: int) -> None:
        """The main dequeue/process loop, extracted so ``run`` can own the
        heartbeat task lifecycle in a try/finally."""
        while self.running:
            try:
                # 1. Check for due scheduled jobs (every 10s)
                now_utc = datetime.now(timezone.utc)
                if self._last_scheduled_check.tzinfo is None:
                    self._last_scheduled_check = self._last_scheduled_check.replace(
                        tzinfo=timezone.utc
                    )
                if (now_utc - self._last_scheduled_check).total_seconds() > 10:
                    moved = await self.queue_service.process_scheduled_jobs()
                    if moved > 0:
                        logger.info(f"Moved {moved} scheduled jobs to queue")
                    self._last_scheduled_check = now_utc

                # 1b. Reap stuck in-flight jobs (zombies) every 30s so they
                # don't linger as "dialing" forever and free the lead.
                if self._last_reap_check.tzinfo is None:
                    self._last_reap_check = self._last_reap_check.replace(tzinfo=timezone.utc)
                if (now_utc - self._last_reap_check).total_seconds() > 30:
                    await self._reap_stuck_jobs_tick()
                    self._last_reap_check = now_utc

                # 2. Get active tenants
                tenant_ids = await self._get_active_tenant_ids()

                # 3. Dequeue next job
                job = await self.queue_service.dequeue_job(tenant_ids=tenant_ids, timeout=5)

                if job:
                    await self.process_job(job)
                    consecutive_errors = 0
                else:
                    # No jobs available, wait before checking again
                    await asyncio.sleep(self.POLL_INTERVAL)

            except asyncio.CancelledError:
                logger.info("Worker received cancellation signal")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Worker error ({consecutive_errors}): {e}", exc_info=True)

                if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    # Fatal: exit with a non-zero code so systemd (Restart=always)
                    # sees a failure and restarts the process cleanly, instead of
                    # silently break-ing out of the loop and lingering as a live
                    # but idle unit that no monitor can distinguish from healthy.
                    logger.critical(
                        "Too many consecutive errors (%d) — exiting for systemd restart",
                        consecutive_errors,
                    )
                    sys.exit(1)

                await asyncio.sleep(min(5 * consecutive_errors, 60))

    async def process_job(self, job: DialerJob) -> None:
        """
        Process a single dialer job.

        Steps:
        1. Get tenant calling rules
        2. Check if we can make call now
        3. Initiate the call
        4. Create call record in database
        """
        logger.info(
            f"Processing job {job.job_id} for lead {job.lead_id} (attempt {job.attempt_number})"
        )

        # Reset bridge-response state captured by `_make_call` — must be
        # cleared per-job so a previous failure doesn't classify the
        # next one.
        self._last_bridge_http_status = None
        self._last_bridge_body = None
        call_intent: Optional[DialerCallIntent] = None
        tenant_pacing_claimed = False
        # Set only when the opt-in TESTING schedule override let this job past
        # the calling-window gate; read by `_create_call_record` so the call
        # is stamped as placed-under-override and stays auditable afterwards.
        self._schedule_override = None

        try:
            # Resolve a previously committed attempt before applying campaign
            # state. A stopped campaign may still own a live/ambiguous PSTN leg;
            # terminally skipping its job first would release the per-lead slot
            # and allow a duplicate call on resume.
            try:
                call_intent = await self._load_existing_call_intent(job)
            except Exception as exc:
                logger.error(
                    "dialer_intent_reconciliation_unavailable job=%s attempt=%s err=%s",
                    job.job_id,
                    job.attempt_number,
                    exc,
                )
                await self._redefer_before_intent_resolution(
                    job,
                    reason="intent_reconciliation_unavailable",
                )
                return

            if call_intent is not None and self._call_status_is_terminal(
                call_intent.status
            ):
                await self._resume_existing_call_intent(job, call_intent)
                return

            try:
                campaign_status = await self._get_campaign_status(
                    job.campaign_id,
                    job.tenant_id,
                )
            except CampaignStatusUnavailable:
                if call_intent is not None:
                    await self._park_uncertain_origination(job, call_intent)
                else:
                    await self._redefer_before_intent_resolution(
                        job,
                        reason="campaign_status_unavailable",
                    )
                return
            if campaign_status not in {"running", "active"}:
                reason = f"campaign_not_runnable:{campaign_status or 'missing'}"
                logger.info(
                    "Skipping job %s because campaign %s is %s",
                    job.job_id,
                    job.campaign_id,
                    campaign_status or "missing",
                )
                if call_intent is not None:
                    # A provider-bound or non-initial intent stays active until
                    # the bridge/reaper proves every leg absent. A provider-null
                    # initial row can be terminalized with a guarded CAS because
                    # the bridge stamps the planned provider ID before ARI.
                    provider_absent = (
                        call_intent.provider_call_id is None
                        and call_intent.status in {"queued", "initiated"}
                        and await self._mark_call_intent_not_originated(
                            job,
                            call_intent,
                            reason=reason,
                        )
                    )
                    if not provider_absent:
                        await self._park_uncertain_origination(job, call_intent)
                        return
                await self._end_or_hold_non_runnable_campaign_job(
                    job, campaign_status, block_reason=reason, error=reason,
                    # A paused hold keeps the SAME attempt; an attempt whose
                    # intent row was just terminalised would replay into the
                    # settled-failed path and burn the lead while paused.
                    allow_hold=call_intent is None,
                )
                return

            # A transport-ambiguous attempt is re-deferred with the SAME
            # attempt number. Reconcile its existing intent before any
            # new-call gate: the intent itself would consume batch_size=1,
            # and pacing/CallGuard have already been charged for this attempt.
            # Re-running those gates can wedge the replay forever or double
            # count rate-limit side effects.
            if call_intent is not None:
                await self._resume_existing_call_intent(job, call_intent)
                return

            # 0.4 Stale-payload gate. A Redis payload is only a delivery of work
            # that Postgres still wants done: a lead removed from the campaign,
            # a job the stop/remove paths cancelled, or a contact flagged
            # do-not-call after it was queued all leave their payload behind.
            # Checked BEFORE every deferring gate below so a removed contact is
            # never re-deferred (a redefer writes the lead back to 'pending').
            # An unreadable state raises ComplianceLookupUnavailable (-> defer,
            # never dial).
            stale_reason = await self._stale_payload_reason(job)
            if stale_reason is not None:
                await self._drop_stale_payload(job, stale_reason)
                return

            # 0.5 Minutes quota gate. Stop originating once the tenant has burned
            # its plan minutes for the month. Minute tracking was previously
            # display-only, so tenants could overrun the plan with no cap.
            from app.domain.services.minutes_quota import MeteringUnavailable
            try:
                minutes_exhausted = await self._tenant_minutes_exhausted(job.tenant_id)
            except MeteringUnavailable:
                await self._publish_block(job, "metering_unavailable")
                await self._redefer_before_intent_resolution(job, reason="metering_unavailable")
                return
            if minutes_exhausted:
                logger.info(
                    "Skipping job %s — tenant %s is out of plan minutes",
                    job.job_id,
                    job.tenant_id,
                )
                await self._publish_block(job, "out_of_minutes")
                await self._emit_out_of_minutes_event(job)
                # Keep the DB job ACTIVE (retry_scheduled) while the payload
                # waits: a SKIPPED write here made the stale-payload gate drop
                # the lead for good, so a top-up never resumed it.
                await self._redefer_before_intent_resolution(job, reason="out_of_minutes")
                return

            # 1. Calling rules: tenant defaults overlaid with the campaign's
            # per-campaign schedule (timezone/window/days). The window is
            # evaluated in the CAMPAIGN's timezone (Phase 3c-v2). If the
            # client enabled the "call anytime" override we skip the window
            # gate entirely — the UI still warns, but we never block.
            tenant_rules = await self._get_tenant_rules(job.tenant_id)
            campaign_cfg = await self._get_campaign_calling_config(job.campaign_id)
            from app.domain.services.dialer.campaign_schedule import (
                effective_rules,
                schedule_ignored,
            )

            rules = effective_rules(tenant_rules, campaign_cfg)
            ignore_schedule = schedule_ignored(campaign_cfg)

            # 2. Get lead info for cooldown check
            lead_last_called = await self._get_lead_last_called(job.lead_id)

            # 2b. COMPLIANCE: the calling window is evaluated in the LEAD's
            # timezone, not the account owner's. `can_make_call` has always
            # taken a `lead_timezone`, but nothing passed it, so a London
            # campaign's 09:00-19:00 window authorised dialling a California
            # lead at 01:00 their time. Precedence (see lead_timezone.py):
            #   leads.timezone (customer told us)
            #     > phone-derived zone (a guess, behind DIALER_PER_LEAD_TIMEZONE)
            #     > None -> CallingRules._resolve_tz falls back to the
            #       campaign/tenant tz, i.e. exactly today's behaviour.
            # Every failure path resolves to None; none of them raises.
            from app.domain.services.dialer.lead_timezone import (
                resolve_effective_lead_timezone,
            )

            lead_tz = resolve_effective_lead_timezone(
                explicit_timezone=await self._get_lead_timezone(job.lead_id),
                phone_number=job.phone_number,
            )

            # 3. Check scheduling rules. Gate concurrency on THIS TENANT's live
            # calls read from Postgres, NOT the dialer's in-memory counter (it
            # had no decrement signal, so it leaked monotonically to the cap and
            # wedged every outbound call: the 10/10 outage) and NOT the bridge's
            # platform-wide global_concurrency set (one SCARD over every
            # tenant's calls: ten live calls on one tenant read as "10/10" for
            # all of them). A None count (DB unavailable) falls through to the
            # in-memory fallback.
            active_override = await self._tenant_live_call_count(job.tenant_id)

            # Daily per-lead cap: only pay the COUNT query when the tenant
            # actually enabled the ceiling (default off → zero overhead).
            lead_attempts_today = None
            if getattr(rules, "max_calls_per_lead_per_day", 0):
                lead_attempts_today = await self._get_lead_attempts_today(job.lead_id)

            can_call, reason = await self.rules_engine.can_make_call(
                tenant_id=job.tenant_id,
                campaign_id=job.campaign_id,
                rules=rules,
                lead_last_called=lead_last_called,
                active_calls_override=active_override,
                lead_attempts_today=lead_attempts_today,
                lead_timezone=lead_tz,
                enforce_window=not ignore_schedule,
            )

            # 3b. TESTING OVERRIDE — explicit, opt-in, OFF by default.
            #
            # ⚠️ COMPLIANCE: calling-hour/day rules exist for legal reasons
            # (UK Ofcom permitted hours, TCPA-style local-time windows). The
            # gate above still RUNS and still produced its normal structured
            # reason; nothing here widens the default schedule for anyone.
            # This only lets an operator who deliberately switched the
            # override on dial anyway — loudly (WARNING per call), visibly
            # (surfaced on the campaign's reason channel as "TESTING MODE:
            # schedule bypassed"), and auditably (stamped on the call record
            # below). Reversible by unsetting the switch. See
            # app/domain/services/dialer/testing_override.py.
            self._schedule_override = None
            if not can_call:
                from app.domain.services.dialer.block_reasons import (
                    SCHEDULE_BLOCK_CODES,
                    classify,
                    describe_schedule,
                    testing_override_notice,
                )
                from app.domain.services.dialer.testing_override import (
                    log_override_used,
                    schedule_override_source,
                )

                blocked = classify(reason, rules=rules)
                override_source = (
                    schedule_override_source(campaign_cfg)
                    if blocked.code in SCHEDULE_BLOCK_CODES
                    else None
                )
                if override_source:
                    log_override_used(
                        source=override_source,
                        campaign_id=job.campaign_id,
                        tenant_id=job.tenant_id,
                        phone_number=job.phone_number,
                        blocked_reason=reason,
                        schedule=describe_schedule(rules),
                    )
                    self._schedule_override = {
                        "source": override_source,
                        "blocked_reason": reason,
                        "schedule": describe_schedule(rules),
                    }
                    # Surface the override through the SAME channel that shows
                    # blocking reasons so it can never be invisible.
                    await self._publish_reason(
                        job,
                        testing_override_notice(
                            rules,
                            source=override_source,
                            raw_reason=reason,
                        ),
                    )
                    can_call = True

            if not can_call:
                logger.info(f"Cannot call now: {reason}")

                # Calculate delay until next window or retry.
                # Matched on the STRUCTURED code, not substrings: the raw
                # day-gate reason is "calling_not_allowed_on_Tue", which
                # contains neither "time_window" nor "day", so the old
                # substring test missed it and fell through to the generic
                # 300s retry — a campaign blocked on a non-calling day
                # re-woke every 5 minutes for days instead of sleeping until
                # the window actually opened.
                if blocked.code in SCHEDULE_BLOCK_CODES:
                    # Same timezone the block was decided in — otherwise the
                    # job is held against the lead's window but re-woken on
                    # the campaign's, and lands outside the window again.
                    delay = self.rules_engine.get_delay_until_next_window(
                        rules,
                        lead_timezone=lead_tz,
                    )
                    logger.info(
                        f"Outside calling window (tz={lead_tz or rules.timezone}"
                        f"{' [lead]' if lead_tz else ''}, "
                        f"window={rules.time_window_start}-{rules.time_window_end}, "
                        f"days={rules.allowed_days}). "
                        f"Retrying in {delay}s (~{delay/3600:.1f}h)"
                    )
                elif "lead_cooldown" in reason:
                    # The cooldown timestamp was set at call *origination* (not at answer)
                    # due to a now-fixed bug, which can leave a stale last_called_at with
                    # no real call behind it. That workaround was UNCONDITIONAL, so it
                    # also fired on a genuine cooldown: 2026-09-23, +16478471491 was
                    # answered at 18:05:45 (job 4cc194a0/call 8b3176ca) and this branch
                    # cleared the cooldown and redialled it again 3.6 minutes later
                    # (job 620d9ac5/call 6e0e221b), well inside the 2h window even
                    # though the first call had already ended (8b3176ca ended 18:06:00).
                    # attempt_number was also bumped only in memory, never persisted to
                    # dialer_jobs, which desynced the column
                    # `_record_ambiguous_attempt_state` guards its UPDATE on
                    # (dialer_jobs.attempt_number = job.attempt_number) — leaving
                    # dialer_jobs.call_id NULL for an answered call. Only treat this as
                    # the stale-timestamp case when no call for this lead in the
                    # cooldown window was ever answered or is still in flight;
                    # otherwise respect the cooldown like any other blocked reason.
                    cooldown_hours = getattr(rules, "min_hours_between_calls", 0) or 0
                    if not await self._lead_has_live_or_answered_call(
                        job, cooldown_hours
                    ):
                        logger.info(
                            f"Clearing stale last_called_at for lead {job.lead_id} "
                            f"(was set at origination, not at answer)"
                        )
                        await self._clear_lead_last_called(job)
                        # Cleanup did not attempt a call. Re-evaluate promptly
                        # with the original payload and active job ownership.
                        await self._publish_reason(job, blocked)
                        await self._redefer_before_intent_resolution(
                            job, reason=reason, delay_seconds=1,
                        )
                        return
                    # Genuine cooldown: a real call for this lead was answered, or is
                    # still live, inside the window. Respect it like any other
                    # blocked reason instead of clearing it out from under a live call.
                    #
                    # Review non-blocking finding on this same 2026-09-23 case: a
                    # fixed delay=300 here re-woke this branch every 5 minutes for
                    # the whole cooldown (~24 cycles on a 2h window), and each
                    # cycle bumped job.attempt_number via queue_service.
                    # schedule_retry -- Redis only, never persisted -- so the lead
                    # was finally redialled at attempt_number ~25.
                    # call_service._effective_attempt_number takes max(db, live),
                    # so decide_disposition then treated any no-answer right after
                    # the cooldown as terminal. Schedule ONE retry for when the
                    # cooldown actually expires instead of polling every 5 minutes.
                    remaining = await self._lead_cooldown_remaining_seconds(
                        job, cooldown_hours
                    )
                    fallback_delay = cooldown_hours * 3600.0
                    delay = int(
                        max(300.0, remaining if remaining is not None else fallback_delay)
                    )
                elif "daily_lead_cap" in reason:
                    # The per-day ceiling resets at UTC midnight. Reschedule
                    # the lead for just after the day rolls over; the
                    # calling-window gate then holds it until the tenant's
                    # allowed hours. Avoids burning retries hammering the cap.
                    now = datetime.now(timezone.utc)
                    next_midnight = (now + timedelta(days=1)).replace(
                        hour=0,
                        minute=5,
                        second=0,
                        microsecond=0,
                    )
                    delay = max(300, int((next_midnight - now).total_seconds()))
                    logger.info(
                        "Daily per-lead cap hit for lead %s (%s) — retrying after "
                        "midnight in %ds (~%.1fh)",
                        job.lead_id,
                        reason,
                        delay,
                        delay / 3600,
                    )
                else:
                    delay = 300  # 5 minutes for other reasons (concurrent limit, etc.)

                # Re-classify with the delay we actually chose so the reason
                # carries a truthful next_eligible_at for the gates whose next
                # eligible moment is only known here.
                blocked = classify(reason, rules=rules, retry_after_seconds=delay)
                await self._publish_reason(job, blocked)
                # No attempt occurred. Keep the original crash-safe payload
                # and active per-lead ownership through the selected window.
                await self._redefer_before_intent_resolution(
                    job, reason=reason, delay_seconds=delay,
                )
                return

            # 4. Concurrency: active_calls_override (read above) is THIS
            # tenant's own answered/in_call count from _tenant_live_call_count.
            # (The bridge's global_concurrency ledger is platform-wide and is
            # enforced as the global cap elsewhere.) The dialer no longer feeds its own
            # in-memory counter: it had no decrement signal, so it leaked to
            # the cap and wedged all calls. (register_call_start/end remain on
            # the rules engine for unit tests.)

            # Re-check campaign status immediately before originating. The
            # validation above (rules / scheduling / guard) can take 100-200ms,
            # and the user may hit Stop in that window. Without this, a job that
            # passed the top-of-function check still originates into a stopped
            # campaign — a call stuck "dialing" that the stop-sweep (already run)
            # never sees.
            try:
                campaign_status = await self._get_campaign_status(
                    job.campaign_id,
                    job.tenant_id,
                )
            except CampaignStatusUnavailable:
                await self._redefer_before_intent_resolution(
                    job,
                    reason="campaign_status_unavailable",
                )
                return
            if campaign_status not in {"running", "active"}:
                logger.info(
                    "Campaign %s went %s during job %s validation — skipping originate",
                    job.campaign_id,
                    campaign_status or "missing",
                    job.job_id,
                )
                await self._end_or_hold_non_runnable_campaign_job(
                    job,
                    campaign_status,
                    block_reason="campaign_stopped_before_originate",
                    error="campaign_stopped_before_originate",
                )
                return

            # Batch-dispatch gate. Unlike the concurrency guard — which counts
            # only ANSWERED calls and so let hundreds of calls ring at once —
            # this caps the number of calls a campaign has IN FLIGHT (dialing /
            # ringing / answered / in-call) at its configured batch size. The
            # campaign dials in controlled batches of N; a new call is only
            # originated once an earlier one reaches a terminal outcome
            # (answered-&-ended / no-answer / voicemail / invalid / off), which
            # is exactly the "batch of 10, then the next batch" behaviour. Batch
            # size is per-campaign and client-selectable (calling_config.
            # batch_size); 0 disables the gate (unbounded, legacy behaviour).
            batch_size = self._resolve_batch_size(campaign_cfg)
            if batch_size > 0:
                inflight = await self._campaign_inflight_calls(job.campaign_id)
                if inflight >= batch_size:
                    logger.debug(
                        "batch_gate: campaign %s at capacity (%d/%d in flight) — "
                        "deferring job %s",
                        job.campaign_id,
                        inflight,
                        batch_size,
                        job.job_id,
                    )
                    await self._publish_block(
                        job,
                        "batch_capacity",
                        rules=rules,
                        retry_after_seconds=5,
                    )
                    await self._redefer_before_intent_resolution(
                        job, reason="batch_capacity", delay_seconds=5,
                    )
                    return

            # Inter-call gap. Enforce a minimum wait between consecutive
            # originations for this campaign so calls are PACED (a gentle,
            # human-like cadence) rather than fired back-to-back the instant a
            # batch slot frees. Works alongside the batch gate: a new call goes
            # out only when BOTH there's a free slot AND at least `call_gap`
            # seconds have passed since the campaign's last dial. Per-campaign,
            # client-selectable (calling_config.call_gap_seconds); 0 = no gap.
            call_gap = self._resolve_call_gap(campaign_cfg)
            if call_gap > 0:
                since_last = await self._campaign_seconds_since_last_dial(job.campaign_id)
                if since_last is not None and since_last < call_gap:
                    wait = max(1, call_gap - since_last)
                    logger.debug(
                        "call_gap: campaign %s dialed %ds ago (<%ds) — deferring " "job %s by %ds",
                        job.campaign_id,
                        since_last,
                        call_gap,
                        job.job_id,
                        wait,
                    )
                    await self._publish_block(
                        job,
                        "call_gap",
                        rules=rules,
                        retry_after_seconds=wait,
                    )
                    await self._redefer_before_intent_resolution(
                        job, reason="call_gap", delay_seconds=wait,
                    )
                    return

            # 4.9. TENANT-wide pacing: one origination at a time across ALL
            # campaigns. The per-campaign gap above works, but two running
            # campaigns phase-lock and fire near-simultaneously every cycle,
            # doubling load on the single voice pipeline at the same instant
            # (measured: 12-23s replies, audio gaps). Atomic Redis claim —
            # whoever wins dials; everyone else waits out the window.
            from app.domain.services.dialer.global_pacing import (
                claim_tenant_dial_slot,
                release_tenant_dial_slot,
            )

            _tenant_wait = await claim_tenant_dial_slot(self._redis, job.tenant_id)
            if _tenant_wait > 0:
                logger.debug(
                    "tenant_gap: tenant %s dialed recently — deferring job %s by %ds",
                    job.tenant_id,
                    job.job_id,
                    _tenant_wait,
                )
                await self._publish_block(
                    job,
                    "tenant_gap",
                    rules=rules,
                    retry_after_seconds=_tenant_wait,
                )
                await self._redefer_before_intent_resolution(
                    job, reason="tenant_gap", delay_seconds=_tenant_wait,
                )
                return
            tenant_pacing_claimed = True

            # 4.95. Run Call Guard validation. Moved here (2026-07-13) from
            # BEFORE the batch/call_gap/tenant_gap gates above:
            # CallGuard.evaluate() INCRs the fixed-window rate-limit counter
            # in telephony_rate_limiter as a side effect, plus writes a
            # call_guard_decisions row (~15 DB round-trips). Evaluating it
            # before those gates meant every job any later gate went on to
            # DEFER got counted anyway, with no decrement — the counter
            # tracked churn (re-evaluations) instead of actual dials, so a
            # big campaign could climb to throttle on volume it never
            # originated. Running the guard here — after EVERY deferral
            # gate, including the tenant pacing claim, right before the
            # originate call — means only a job that is actually about to
            # dial gets counted.
            #
            # Slot-leak note: the tenant pacing slot above is already
            # CLAIMED at this point (SET NX EX — see claim_tenant_dial_
            # slot), so every early-return below (block/throttle/queue)
            # explicitly releases it first. Without that release the slot
            # would sit claimed for the full tenant-gap window even though
            # nothing was dialed, needlessly pacing out the next legitimate
            # job from ANY campaign on this tenant.
            guard_decision = await self._evaluate_call_guard(job, rules)
            if guard_decision != "allow":
                logger.warning(f"Call guard decision for job {job.job_id}: {guard_decision}")
                await release_tenant_dial_slot(self._redis, job.tenant_id)

                if guard_decision == "metering_unavailable":
                    await self._publish_block(job, "metering_unavailable", rules=rules)
                    await self._redefer_before_intent_resolution(job, reason="metering_unavailable")
                    return
                if guard_decision == "defer":
                    # Transient denial (platform pause, business hours, abuse
                    # window, subscription state): nothing was attempted, so
                    # keep the original attempt and active job ownership and
                    # hold the job for minutes, never a tight loop.
                    await self._publish_block(
                        job,
                        self._guard_defer_reason,
                        rules=rules,
                        retry_after_seconds=self._guard_defer_delay,
                    )
                    await self._redefer_before_intent_resolution(
                        job,
                        reason=self._guard_defer_reason,
                        delay_seconds=self._guard_defer_delay,
                    )
                    return
                if guard_decision == "block":
                    # Block the call - mark job as blocked, don't retry
                    await self._publish_block(job, "call_guard_blocked", rules=rules)
                    await self._update_job_status(
                        job, JobStatus.BLOCKED, reason="call_guard_blocked"
                    )
                    return
                elif guard_decision == "throttle":
                    # Throttle - reschedule with delay
                    await self._publish_block(
                        job,
                        "call_guard_throttled",
                        rules=rules,
                        retry_after_seconds=60,
                    )
                    # Admission has not attempted a call. Keep the original
                    # attempt and durable active owner while waiting.
                    await self._redefer_before_intent_resolution(
                        job, reason="call_guard_throttled", delay_seconds=60,
                    )
                    return
                elif guard_decision == "queue":
                    # Queue - reschedule to retry later
                    await self._publish_block(
                        job,
                        "call_guard_queued",
                        rules=rules,
                        retry_after_seconds=30,
                    )
                    await self._redefer_before_intent_resolution(
                        job, reason="call_guard_queued", delay_seconds=30,
                    )
                    return
                else:
                    # An unrecognised decision must never fall through to the
                    # originate path below: fail closed.
                    await self._publish_block(job, "call_guard_blocked", rules=rules)
                    await self._update_job_status(
                        job, JobStatus.BLOCKED, reason="call_guard_blocked"
                    )
                    return

            try:
                # 5. Commit the attempt BEFORE any provider/PBX action.  The
                # (dialer_job_id, attempt_number) uniqueness boundary makes a
                # crash replay reuse this row while still allowing a genuine
                # later retry (attempt N+1) to own a new row.
                call_intent = await self._create_call_intent(job)

                if self._call_status_is_terminal(call_intent.status):
                    # A stale Redis delivery replayed an attempt whose call has
                    # already settled. Never re-originate it. The durable call
                    # finalizer owns the authoritative DB job/lead disposition;
                    # this only drops the duplicate queue delivery -- unless the
                    # BRIDGE settled it failed-without-answer and no finalizer
                    # ever ran, in which case nothing else will retry it.
                    if await self._attempt_settled_failed_unanswered(job):
                        raise AttemptSettledFailed(call_intent.call_id)
                    job.call_id = call_intent.call_id
                    await self.queue_service.mark_completed(
                        job.job_id,
                        outcome="duplicate_terminal_attempt",
                    )
                    logger.warning(
                        "dialer_attempt_terminal_replay job=%s attempt=%s call=%s status=%s",
                        job.job_id,
                        job.attempt_number,
                        call_intent.call_id,
                        call_intent.status,
                    )
                    return

                # 6. Initiate through the bridge using the already-committed
                # identity. The bridge treats that identity as an idempotency
                # key and stamps it onto the voice session before ARI runs.
                provider_call_id = await self._make_call(
                    job,
                    rules,
                    call_intent=call_intent,
                )

                if provider_call_id in (self._METERING_UNAVAILABLE, self._TELEPHONY_SELECTION_UNAVAILABLE):
                    # The bridge explicitly rejected before provider admission.
                    # Preserve the committed intent and the same queue attempt.
                    reason = ("metering_unavailable" if provider_call_id == self._METERING_UNAVAILABLE
                              else "telephony_selection_unavailable")
                    await release_tenant_dial_slot(self._redis, job.tenant_id)
                    await self._publish_block(job, reason, rules=rules)
                    await self._redefer_before_intent_resolution(job, reason=reason)
                    return

                if provider_call_id == self._BRIDGE_TRANSIENT_REJECT:
                    defer_reason, defer_delay = self._last_bridge_defer
                    if not await self._held_intent_has_headroom(
                        job, call_intent, defer_delay
                    ):
                        if defer_reason == self._LINE_REFUSAL_CODE:
                            # A refused line is not a failure of the lead: do
                            # not classify it (that burns attempts and finally
                            # fails the lead). Release the never-sent intent and
                            # wait for the line on the SAME attempt.
                            await release_tenant_dial_slot(self._redis, job.tenant_id)
                            await self._defer_refused_line(job, call_intent, rules)
                            return
                        # Too old to keep holding: the classifier (which saw the
                        # stashed bridge status/code) decides, capped.
                        raise HeldIntentExpired(
                            f"{defer_reason}: held attempt reached the stuck-call horizon"
                        )
                    logger.warning(
                        "dialer_bridge_rejected_transient job=%s attempt=%s code=%s "
                        "hold=%ss (same attempt re-deferred)",
                        job.job_id, job.attempt_number, defer_reason, defer_delay,
                    )
                    await release_tenant_dial_slot(self._redis, job.tenant_id)
                    block_reason, job_reason = self._bridge_defer_labels(defer_reason)
                    await self._publish_block(
                        job, block_reason, rules=rules, retry_after_seconds=defer_delay,
                    )
                    await self._redefer_before_intent_resolution(
                        job, reason=job_reason, delay_seconds=defer_delay,
                    )
                    return

                if provider_call_id == self._ORIGINATION_UNCERTAIN:
                    await self._park_uncertain_origination(job, call_intent)
                    return

                if provider_call_id == self._ATTEMPT_ALREADY_TERMINAL:
                    if await self._attempt_settled_failed_unanswered(job):
                        raise AttemptSettledFailed(call_intent.call_id)
                    job.call_id = call_intent.call_id
                    await self.queue_service.mark_completed(
                        job.job_id,
                        outcome="duplicate_terminal_attempt",
                    )
                    return

                # Voice pipeline temporarily unavailable (pod draining / at
                # capacity, TTS/STT warmup failed). An infra condition, not a
                # bad lead: hold the SAME attempt. The committed intent stays
                # 'initiated' (nothing reached the provider) and is reused on
                # the next dequeue, so no attempt is consumed and no extra
                # failed calls row is written. The hold is bounded; past the
                # bound the failure goes through the classifier (which does
                # consume attempts, capped) instead of looping forever.
                if provider_call_id == self._PIPELINE_UNAVAILABLE:
                    await self._defer_pipeline_unavailable(job, rules, call_intent)
                    # Nothing dialed — give the tenant slot back so the other
                    # campaign isn't paced against a failed attempt.
                    await release_tenant_dial_slot(self._redis, job.tenant_id)
                    return

                if provider_call_id:
                    # The bridge already bound the provider to this durable row.
                    # Reconcile the worker-owned leg metadata idempotently; do
                    # not create another calls row after the provider response.
                    try:
                        await self._bind_call_intent(
                            job,
                            call_intent,
                            provider_call_id,
                        )
                    except Exception as bind_exc:
                        logger.error(
                            "dialer_call_intent_bind_uncertain job=%s call=%s err=%s",
                            job.job_id,
                            call_intent.call_id,
                            bind_exc,
                        )
                        await self._park_uncertain_origination(job, call_intent)
                        return

                    internal_call_id = call_intent.call_id
                    talklee_call_id = call_intent.talklee_call_id
                    leg_id = call_intent.leg_id

                    # B1: transition the call into the public state machine
                    # (Track B). The dialer worker drove the call to "dialing"
                    # the moment the bridge accepted the originate request;
                    # subsequent transitions (ringing → answered → ended) are
                    # written by the asterisk_adapter ARI callbacks.
                    try:
                        from app.domain.services.call_status import (
                            CallState,
                            record_call_state,
                        )

                        await record_call_state(
                            self._db_pool,
                            call_id=internal_call_id,
                            tenant_id=job.tenant_id,
                            campaign_id=job.campaign_id,
                            new_state=CallState.DIALING,
                            metadata={
                                "phone_number": str(job.phone_number),
                                "agent_name": getattr(job, "agent_name", None),
                                "provider_call_id": provider_call_id,
                                "description": f"Dialing {job.phone_number}",
                            },
                        )
                    except Exception as state_exc:
                        # B1 must never block a successful originate.
                        logger.warning(
                            "call_status.dialing_emit_failed call=%s err=%s",
                            internal_call_id,
                            state_exc,
                        )

                    job.call_id = internal_call_id
                    if await self._attempt_call_settled(job, call_intent):
                        # A pre-answer failure settled this attempt between the
                        # bridge's reply and these writes. Its finalizer owns
                        # the job/lead disposition; writing 'calling' /
                        # PROCESSING now would strand both on a dead call.
                        logger.warning(
                            "dialer_attempt_settled_before_processing job=%s "
                            "attempt=%s call=%s — skipping calling/processing writes",
                            job.job_id, job.attempt_number, internal_call_id,
                        )
                    else:
                        # 7. Update lead status to 'calling'
                        await self._update_lead_status(job, "calling")

                        # 8. Update job with the internal DB call UUID
                        job.status = JobStatus.PROCESSING
                        job.processed_at = datetime.now(timezone.utc)
                        await self._update_job_status(
                            job, JobStatus.PROCESSING, call_id=internal_call_id
                        )

                    # 9. Voice worker notification DISABLED — telephony bridge
                    #    handles the full call lifecycle via ARI callbacks
                    #    (_on_ringing → warmup, _on_new_call → pipeline start).
                    #    Publishing here caused voice_worker to create DUPLICATE
                    #    dead pipelines (BrowserMediaGateway, no audio routed)
                    #    that wasted Deepgram WS connections and caused API-key
                    #    contention, adding 1-3s to the bridge's legitimate
                    #    ringing-phase STT/TTS warmup handshake.
                    # await self._publish_call_event(internal_call_id, job, talklee_call_id, provider_call_id)

                    self._jobs_processed += 1
                    # Stamp the campaign's last-dial time for the inter-call gap.
                    # Tracked in Redis (not the calls table) so it only reflects
                    # dials from the CURRENT run — start_campaign clears the key,
                    # so the FIRST call of a run never waits out the gap; the gap
                    # only spaces the calls that follow.
                    await self._mark_campaign_dialed(job.campaign_id)
                    # A call went out ⇒ the campaign is no longer blocked, so
                    # drop the stored reason rather than let the UI keep
                    # showing a stale blocker. EXCEPTION: when the testing
                    # override is what let this call through, the "TESTING
                    # MODE: schedule bypassed" notice must STAY visible for
                    # exactly as long as calls are going out under it.
                    if not self._schedule_override:
                        try:
                            from app.domain.services.dialer.block_state import (
                                clear_block_reason,
                            )

                            await clear_block_reason(self._redis, job.campaign_id)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("block_state clear failed: %s", exc)
                    logger.info(
                        "Call initiated: internal_call_id=%s provider_call_id=%s job=%s",
                        internal_call_id,
                        provider_call_id,
                        job.job_id,
                    )
                    await self._emit_progress_event_throttled(job)
                else:
                    # The generic failure boundary below owns the provider-null
                    # absence proof. Keeping it in one place prevents a second
                    # CAS from mistaking our own terminal row for ambiguity.
                    raise Exception("No call_id returned from telephony provider")

            finally:
                # Unregister call (will be re-registered when answered if needed)
                # For now, we track at initiation level
                pass

        except asyncio.CancelledError:
            # A shutdown/client cancellation after the intent commit is just as
            # ambiguous as a lost HTTP response. Re-stage the exact Redis
            # payload (same job + attempt) before propagating cancellation; a
            # later worker replay is fenced by the durable call row and bridge
            # CAS, so it can reconcile but cannot place a second call.
            recovery = (
                self._park_uncertain_origination(job, call_intent)
                if call_intent is not None
                else self._redefer_cancelled_before_origination(job)
            )
            from app.core.cancellation import finish_critical_handoff

            await finish_critical_handoff(recovery)
            raise
        except ComplianceLookupUnavailable as exc:
            # Fail CLOSED: a compliance gate (calling window, campaign
            # schedule, lead cooldown, daily cap) could not be evaluated, so
            # nothing is dialled and no attempt is consumed. Same family as
            # CampaignStatusUnavailable / MeteringUnavailable.
            logger.error(
                "dialer_compliance_lookup_unavailable job=%s attempt=%s err=%s",
                job.job_id,
                job.attempt_number,
                exc,
            )
            if call_intent is not None:
                await self._park_uncertain_origination(job, call_intent)
            else:
                await self._publish_block(job, "rules_unavailable")
                await self._redefer_before_intent_resolution(
                    job,
                    reason="rules_unavailable",
                    delay_seconds=self.COMPLIANCE_LOOKUP_REDEFER_SECONDS,
                )
            return
        except Exception as e:
            if call_intent is not None:
                # Once the durable row exists, an unexpected downstream error
                # must not manufacture attempt N+1 unless Postgres still proves
                # that no provider identity was ever claimed. This covers
                # failures in worker-owned metadata after a successful bridge
                # response (lead/job/progress writes): the PSTN leg may be live
                # even though those bookkeeping writes failed.
                absence_proved = await self._mark_call_intent_not_originated(
                    job,
                    call_intent,
                    reason="worker_exception_before_origination_confirmation",
                )
                if not absence_proved:
                    # The bridge may already have PROVED absence and settled
                    # call=failed / job=failed / lead=pending itself (ARI or
                    # originate error after the provider id was stamped). No
                    # call-end event ever fires for a channel that never
                    # existed, so parking would strand the lead: classify and
                    # retry as attempt N+1 like any other failed origination.
                    if not (
                        isinstance(e, AttemptSettledFailed)
                        or await self._attempt_settled_failed_unanswered(job)
                    ):
                        await self._park_uncertain_origination(job, call_intent)
                        return
                    logger.warning(
                        "dialer_attempt_settled_failed_by_bridge job=%s attempt=%s "
                        "call=%s — retrying via classifier",
                        job.job_id,
                        job.attempt_number,
                        call_intent.call_id,
                    )
            # Nothing dialed — release the tenant pacing slot (best-effort)
            # so a failed originate doesn't burn the whole gap window.
            try:
                from app.domain.services.dialer.global_pacing import (
                    release_tenant_dial_slot,
                )

                if tenant_pacing_claimed:
                    await release_tenant_dial_slot(self._redis, job.tenant_id)
            except Exception:
                pass
            self._jobs_failed += 1
            job.last_error = str(e)
            job.last_outcome = CallOutcome.FAILED

            # Track 2: classify the failure (bridge response → category +
            # reason) and ask the policy module how to retry. Feature flag
            # `RETRY_POLICY=legacy` reverts to the old flat-delay path so
            # operators can roll back without redeploying.
            from app.workers.retry_policy import (
                FailureCategory,
                RetryDecision,
                classify_telephony_response,
                legacy_decision,
                parse_bridge_error,
                smart_decision,
                use_smart_policy,
            )

            code, msg = parse_bridge_error(self._last_bridge_body)
            held_reason = getattr(e, "retry_reason", None)
            if held_reason:
                # A same-attempt hold we could no longer keep (the calling
                # window closed under it) is not a failure of the lead, so it
                # is retried whatever the attempt number; the next pass runs
                # every new-call gate again.
                decision = RetryDecision(
                    should_retry=True,
                    delay_seconds=30,
                    category=FailureCategory.TRANSIENT_NETWORK,
                    reason=held_reason,
                    log_message=(
                        f"dial_deferred category=transient_network reason={held_reason} "
                        f"attempt={job.attempt_number} next_retry_in=30s"
                    ),
                )
            # This explicit configuration rejection cannot recover by dialing
            # again. Keep its non-retryable meaning even in legacy retry mode;
            # the absence-proof CAS above remains required before settlement.
            elif use_smart_policy() or code == "cloud_telephony_unavailable":
                category, reason = classify_telephony_response(
                    http_status=self._last_bridge_http_status,
                    error_code=code,
                    message=msg or str(e),
                )
                decision = smart_decision(
                    category=category,
                    reason=reason,
                    attempt_number=job.attempt_number,
                )
            else:
                # Faithful legacy behaviour: tenant's flat retry delay,
                # capped at job.MAX_ATTEMPTS, no classification.
                _should, _ = job.should_retry(goal_achieved=False)
                decision = legacy_decision(
                    attempt_number=job.attempt_number,
                    max_attempts=getattr(job, "MAX_ATTEMPTS", 3),
                    delay_seconds=getattr(job, "RETRY_DELAY_SECONDS", 7200),
                    reason="legacy_no_classification",
                )

            logger.error(
                "%s job=%s lead=%s dest=%s err=%s",
                decision.log_message,
                job.job_id,
                job.lead_id,
                job.phone_number,
                str(e)[:200],
            )
            if decision.reason.startswith("unknown_code_"):
                # A bridge code the classifier does not know must be seen by a
                # human, not silently fail leads.
                await self._emit_unknown_bridge_code_alert(
                    job, decision.reason[len("unknown_code_"):]
                )

            # Persist category/reason on the job + lead. Best-effort —
            # never let a logging/DB hiccup mask the original failure.
            try:
                await self._record_job_failure_classification(
                    job=job,
                    category=decision.category.value,
                    reason=decision.reason,
                )
            except Exception as record_exc:
                logger.warning(
                    "failed to persist failure classification for job=%s: %s",
                    job.job_id,
                    record_exc,
                )

            if decision.should_retry:
                await self._update_lead_status(job, "pending")
                if not await self._schedule_retry_durably(
                    job, delay_seconds=decision.delay_seconds
                ):
                    # Attempt N+1 could not be made durable in BOTH stores.
                    # Never leave a Redis payload whose Postgres row lags:
                    # keep the SAME attempt staged; its replay finds the
                    # settled-failed attempt and retries here again.
                    try:
                        await self.queue_service._redefer_inflight(
                            str(job.job_id),
                            "retry_schedule_failed",
                            delay_seconds=max(30, int(decision.delay_seconds)),
                        )
                    except Exception as redefer_exc:
                        logger.error(
                            "dialer_retry_stage_failed job=%s attempt=%s err=%s",
                            job.job_id, job.attempt_number, redefer_exc,
                        )
                    return
                await self._update_job_status(
                    job,
                    JobStatus.RETRY_SCHEDULED,
                    error=str(e),
                )
            else:
                # Either the category disallows retries (INVALID_INPUT)
                # or the per-category attempt budget is exhausted.
                await self._update_lead_status(job, "failed")
                await self.queue_service.mark_failed(job.job_id, str(e))
                await self._update_job_status(
                    job,
                    JobStatus.FAILED,
                    error=str(e),
                )

    # Sentinel returned by _make_call when the bridge says the voice
    # pipeline is not ready (HTTP 503). Distinct from None ("real failure")
    # so process_job can apply infrastructure-aware backoff without
    # consuming the job's retry budget.
    _PIPELINE_UNAVAILABLE = "__pipeline_unavailable__"
    _METERING_UNAVAILABLE = "__metering_unavailable__"
    _TELEPHONY_SELECTION_UNAVAILABLE = "__telephony_selection_unavailable__"
    # The bridge rejected BEFORE provider admission with a tenant-fixable,
    # self-clearing code (see _BRIDGE_DEFER_CODES). The same attempt is
    # re-deferred; `_last_bridge_defer` carries (reason, delay_seconds).
    _BRIDGE_TRANSIENT_REJECT = "__bridge_transient_reject__"
    # bridge error code -> minimum same-attempt hold in seconds. call_blocked is
    # deliberately absent: it covers DNC / compliance blocks and is never retried.
    _BRIDGE_DEFER_CODES = {"call_throttled": 60, "tenant_pbx_required": 120}
    _BRIDGE_DEFER_MAX_SECONDS = 900
    # The bridge refused the tenant's phone line (422: no ready line, line not
    # registered, caller ID unverified). Held like the other tenant-fixable
    # codes, but an EXPIRED hold never goes through the attempt-consuming
    # classifier -- that burned every lead of the campaign in ~30 minutes. The
    # intent is released and the SAME attempt re-deferred this long, for as long
    # as the line stays refused (only campaign status ends it).
    _LINE_REFUSAL_CODE = "tenant_pbx_required"
    _LINE_REFUSED_REDEFER_SECONDS = 300
    # 503 codes that mean the calling service is not connected to the phone
    # system (adapter down, pod draining / full, telephony off on this node).
    # They are held exactly like a voice-pipeline 503 but are NOT a TTS/STT
    # problem, so they must not be labelled "voice provider not ready".
    _TELEPHONY_UNAVAILABLE_CODES = frozenset(
        {
            "telephony_not_active_on_node",
            "telephony_disabled_on_node",
            "pod_draining",
            "pod_at_capacity",
            "telephony_adapter_unavailable",
        }
    )

    # The HTTP result is unknown after a transport timeout/disconnect, or the
    # bridge explicitly reports proof-aware cleanup in progress. Retrying as a
    # new attempt could call the same person twice, so process_job parks the
    # existing durable attempt for bridge/reaper reconciliation instead.
    _ORIGINATION_UNCERTAIN = "__origination_uncertain__"

    # A race can settle an attempt after the worker loads its intent but before
    # the bridge handles the replay. This explicit bridge response drops only
    # that stale queue delivery and never manufactures a provider identity.
    _ATTEMPT_ALREADY_TERMINAL = "__attempt_already_terminal__"

    # Set by `_make_call` whenever a non-success response from the
    # telephony bridge would otherwise return None. The except-clause
    # in `process_job` reads these so the retry classifier (Track 2)
    # can map them to a FailureCategory and choose a sensible delay.
    # Reset on every job to avoid leaking state across attempts.
    _last_bridge_http_status: Optional[int] = None
    _last_bridge_body: Optional[str] = None
    _last_bridge_defer: tuple = ("bridge_rejected", 120)

    # Set by `_evaluate_call_guard` just before it returns "defer".
    _guard_defer_reason: str = "call_guard_deferred"
    _guard_defer_delay: int = _GUARD_DEFER_SECONDS

    # Populated per-job when the explicit TESTING schedule override permitted a
    # dial the calling-window gate would otherwise have blocked. None on every
    # normal call — the override is OFF by default (see
    # app/domain/services/dialer/testing_override.py).
    _schedule_override: Optional[Dict[str, Any]] = None

    # ---------------------------------------------------------------- reasons
    async def _publish_reason(self, job: DialerJob, reason) -> None:
        """Publish an already-built :class:`BlockReason` on the campaign's live
        reason channel (Redis current-state + one ``stream_events`` row on
        CHANGE — never one per poll). See dialer/block_state.py.

        Strictly fire-and-forget: this is observability, and a Redis or DB
        hiccup must never alter whether a call is placed.
        """
        try:
            from app.domain.services.dialer.block_state import publish_block_reason

            await publish_block_reason(
                self._redis,
                self._db_pool,
                tenant_id=job.tenant_id,
                campaign_id=job.campaign_id,
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "block_reason publish failed job=%s err=%s",
                job.job_id,
                exc,
            )

    async def _publish_block(
        self,
        job: DialerJob,
        raw_reason: str,
        *,
        rules: Optional[CallingRules] = None,
        retry_after_seconds: Optional[int] = None,
    ) -> None:
        """Classify a raw pre-dial gate reason and publish it. Convenience
        wrapper around ``classify`` + ``_publish_reason`` for the gates that
        don't already hold a structured reason."""
        try:
            from app.domain.services.dialer.block_reasons import classify

            reason = classify(
                raw_reason,
                rules=rules,
                retry_after_seconds=retry_after_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("block_reason classify failed raw=%s err=%s", raw_reason, exc)
            return
        await self._publish_reason(job, reason)

    def _bridge_defer_labels(self, defer_reason: str) -> tuple[str, str]:
        """``(block reason to publish, dialer_jobs reason)`` for a same-attempt
        bridge hold. A refused line publishes the dedicated ``line_not_ready``
        reason carrying the bridge's own cause (read from the stashed 422 body);
        the job row keeps the bare token so the Call Issues panel's substring
        advice cannot mistake the cause for a caller-ID page that does not
        exist."""
        if defer_reason != self._LINE_REFUSAL_CODE:
            return defer_reason, defer_reason
        from app.workers.retry_policy import parse_bridge_reason

        cause = parse_bridge_reason(self._last_bridge_body)
        return (f"line_not_ready:{cause}" if cause else "line_not_ready"), "line_not_ready"

    def _pipeline_hold_reason(self) -> str:
        """Why a 503 hold is happening, from the bridge code stashed by
        ``_make_call``: the phone system is unreachable, or (default) the voice
        pipeline was not ready."""
        from app.workers.retry_policy import parse_bridge_error

        code, _ = parse_bridge_error(self._last_bridge_body)
        if code in self._TELEPHONY_UNAVAILABLE_CODES:
            return "telephony_unavailable"
        return "voice_pipeline_unavailable"

    async def _make_call(
        self,
        job: DialerJob,
        rules: CallingRules,
        *,
        call_intent: Optional[DialerCallIntent] = None,
    ) -> Optional[str]:
        """
        Initiate an outbound call through the telephony bridge HTTP endpoint.

        Delegates to POST /api/v1/sip/telephony/call so the bridge's persistent
        ARI/ESL adapter owns the channel for its entire lifetime.  Creating a
        separate adapter here and disconnecting it after origination caused
        Asterisk to immediately hang up the channel (ARI drops all channels
        belonging to a disconnected app).

        Returns:
            provider call_id (Asterisk channel ID) if successful, None otherwise.
        """
        import aiohttp

        api_base = os.getenv("API_BASE_URL", "http://localhost:8000")
        caller_id = getattr(rules, "caller_id", None) or os.getenv("DEFAULT_CALLER_ID", "1001")
        url = f"{api_base}/api/v1/sip/telephony/call"

        # JSON body (not a query string) so E.164 numbers with a leading
        # "+" can't be mangled by URL form-encoding (the old query-string
        # path decoded "+" as a space → caller_id mismatch → 403).
        payload: dict = {
            "destination": str(job.phone_number),
            "caller_id": str(caller_id),
            "tenant_id": str(job.tenant_id) if job.tenant_id else None,
            "campaign_id": str(job.campaign_id) if job.campaign_id else None,
            "first_speaker": job.first_speaker,
        }
        if job.agent_name:
            payload["agent_name"] = job.agent_name
        # Thread the lead's identity so the agent can greet the callee by name
        # and confirm it reached the right person. All optional — a nameless
        # lead simply omits these and the call dials blind (unchanged behaviour).
        if getattr(job, "lead_first_name", None):
            payload["lead_first_name"] = job.lead_first_name
        if getattr(job, "lead_last_name", None):
            payload["lead_last_name"] = job.lead_last_name
        if getattr(job, "lead_company", None):
            payload["lead_company"] = job.lead_company
        # The lead id, so the bridge can load the richer contact context
        # (job title, best time to call, calling notes) itself. Sending the id
        # rather than the values keeps this worker from having to know which
        # fields the agent is allowed to see — that rule lives in one place,
        # contact_fields.agent_usable, and cannot drift out of step here.
        if getattr(job, "lead_id", None):
            payload["lead_id"] = str(job.lead_id)
        if call_intent is not None:
            # These fields are not caller-selected identities. They name the
            # row this worker already committed for this exact attempt; the
            # internal-only bridge path verifies every field against Postgres.
            payload.update(
                {
                    "durable_call_id": call_intent.call_id,
                    "talklee_call_id": call_intent.talklee_call_id,
                    "dialer_job_id": str(job.job_id),
                    "dialer_attempt_number": int(job.attempt_number),
                }
            )

        # Authenticate as an internal service with the shared-secret
        # X-Internal-Service-Token header (CSRF-exempt + accepted by the
        # telephony origination auth gate — see core/security/csrf and
        # core/security/internal_auth). The legacy Origin:<FRONTEND_URL>
        # spoof fallback was REMOVED (it was the cover for the unauthenticated
        # cross-tenant origination hole): if the token is missing we fail
        # LOUD (the API correctly rejects with 401) rather than sneaking
        # through an insecure path.
        internal_token = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()
        try:
            headers = {"Content-Type": "application/json"}
            if internal_token:
                headers["X-Internal-Service-Token"] = internal_token
            else:
                logger.error(
                    "INTERNAL_SERVICE_TOKEN is not set on the dialer worker — "
                    "outbound origination will be rejected (401) by the API auth "
                    "gate. Provision the token in the worker environment."
                )
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    body = await resp.text()
                    if resp.status == 503:
                        try:
                            parsed = json.loads(body)
                            error = parsed.get("error", parsed.get("detail", {}))
                            if isinstance(error, dict) and error.get("code") == "metering_unavailable":
                                return self._METERING_UNAVAILABLE
                            if isinstance(error, dict) and error.get("code") == "telephony_selection_unavailable":
                                return self._TELEPHONY_SELECTION_UNAVAILABLE
                        except (ValueError, TypeError, AttributeError):
                            pass
                        # Stash for the classifier: the same-attempt hold is
                        # bounded, and past the bound the failure boundary
                        # classifies this 503 as transient.
                        self._last_bridge_http_status = resp.status
                        self._last_bridge_body = body
                        logger.warning(
                            "Bridge unavailable (503: voice pipeline or phone system) "
                            "— holding the same "
                            "attempt (no attempt budget consumed). dest=%s body=%s",
                            job.phone_number,
                            body[:300],
                        )
                        return self._PIPELINE_UNAVAILABLE
                    if resp.status not in (200, 202):
                        # Stash for the classifier in process_job's except branch.
                        self._last_bridge_http_status = resp.status
                        self._last_bridge_body = body
                        logger.error(
                            "Telephony bridge rejected call: status=%s body=%s dest=%s",
                            resp.status,
                            body[:200],
                            job.phone_number,
                        )
                        from app.workers.retry_policy import (
                            parse_bridge_error,
                            parse_bridge_retry_after,
                        )

                        # One parser for every body shape (flat string,
                        # canonical envelope, FastAPI {"detail": {...}}).
                        bridge_code, _ = parse_bridge_error(body)
                        if resp.status == 409 and bridge_code in {
                            "origination_cleanup_pending",
                            "origination_in_progress",
                        }:
                            return self._ORIGINATION_UNCERTAIN
                        hold = self._BRIDGE_DEFER_CODES.get(bridge_code or "")
                        if hold is not None:
                            # Rejected before provider admission and clears on
                            # its own: keep the same attempt, never burn one.
                            asked = parse_bridge_retry_after(body)
                            if asked:
                                hold = max(hold, min(asked, self._BRIDGE_DEFER_MAX_SECONDS))
                            self._last_bridge_defer = (bridge_code, hold)
                            return self._BRIDGE_TRANSIENT_REJECT
                        return None

                    try:
                        data = json.loads(body)
                    except (TypeError, ValueError):
                        # A success response whose body was lost/corrupt may
                        # still represent a live call. Never turn it into a new
                        # attempt; the durable identity can be reconciled.
                        logger.error(
                            "Bridge success response was not valid JSON; "
                            "origination result is unknown for job=%s",
                            job.job_id,
                        )
                        return self._ORIGINATION_UNCERTAIN
                    if data.get("status") == "terminal":
                        return self._ATTEMPT_ALREADY_TERMINAL
                    call_id: Optional[str] = data.get("call_id")
                    self._last_provider_name = data.get("adapter", "asterisk")

                    if call_id:
                        logger.info(
                            "CALL INITIATED via bridge (%s): %s call_id=%s... "
                            "(campaign=%s, lead=%s)",
                            self._last_provider_name,
                            job.phone_number,
                            call_id[:8],
                            job.campaign_id,
                            job.lead_id,
                        )
                    else:
                        logger.warning(
                            "CALL FAILED via bridge: %s (campaign=%s, lead=%s)",
                            job.phone_number,
                            job.campaign_id,
                            job.lead_id,
                        )
                    return call_id

        except asyncio.CancelledError:
            # Worker shutdown must not translate cancellation into a retry.
            # The committed intent remains available to the reaper/next run.
            raise
        except Exception as e:
            logger.error("Originate error for %s: %s", job.phone_number, e)
            return self._ORIGINATION_UNCERTAIN

    async def _evaluate_call_guard(self, job: DialerJob, rules: CallingRules) -> str:
        """
        Evaluate call through CallGuard security checks.

        Returns:
            "allow" | "block" | "throttle" | "queue" | "defer"
            ("defer" = a transient denial; ``_guard_defer_reason`` /
            ``_guard_defer_delay`` carry the reason and hold time)
        """
        try:
            from app.domain.services.call_guard import CallGuard, GuardDecision

            guard = CallGuard(
                db_pool=self._db_pool,
                redis_client=self._redis,
            )

            guard_result = await guard.evaluate(
                tenant_id=str(job.tenant_id),
                phone_number=job.phone_number,
                campaign_id=str(job.campaign_id) if job.campaign_id else None,
                # Lets the guard's DNC check also honour the per-lead
                # leads.do_not_call flag. Campaign selection already excludes
                # flagged leads, so this only ever catches a job that was
                # queued BEFORE the contact was flagged (scheduled-set
                # promotion / crash-orphan reclaim re-enqueue existing jobs).
                lead_id=str(job.lead_id) if job.lead_id else None,
                call_type="outbound",
            )

            if any(not check.passed and check.reason == "metering_unavailable"
                   for check in guard_result.check_results):
                return "metering_unavailable"
            decision = guard_result.decision.value
            if decision == "block":
                # A denial that clears on its own must not terminally block the
                # queued backlog; permanent ones (DNC, geography, invalid
                # number, tenant gone) stay "block".
                transient = _classify_guard_denial(guard_result)
                if transient is not None:
                    self._guard_defer_reason, self._guard_defer_delay = transient
                    return "defer"
            return decision

        except Exception as e:
            logger.error(f"CallGuard evaluation failed for job {job.job_id}: {e}", exc_info=True)
            # Fail-closed (never dial on a guard error) but not terminal: a DB
            # blip used to permanently block every lead it touched.
            self._guard_defer_reason = "guard_check_unavailable"
            self._guard_defer_delay = _GUARD_INFRA_DEFER_SECONDS
            return "defer"

    async def _emit_progress_event_throttled(self, job) -> None:
        """Emit a "Campaign progress updated" stream event, throttled.

        Uses Redis SETNX with a 60-second TTL so each campaign emits at
        most one event per minute regardless of call rate. Fire-and-forget
        — emit failures must never fail a successful call origination.
        """
        try:
            if not self._redis or not job.tenant_id or not job.campaign_id:
                return
            key = f"evt:throttle:progress:{job.campaign_id}"
            # NX: only set if absent; EX: 60-second TTL.
            acquired = await self._redis.set(key, "1", nx=True, ex=60)
            if not acquired:
                return  # within the same 60-second window — skip

            from app.domain.services.event_emitter import emit_event_via_pool

            await emit_event_via_pool(
                self._db_pool,
                tenant_id=str(job.tenant_id),
                category="campaign",
                title="Campaign progress updated",
                description="Dialer processed a new batch of calls.",
                related_campaign_id=str(job.campaign_id),
                metadata={"window_seconds": 60},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("emit_progress_event_throttled failed: %s", exc)

    @asynccontextmanager
    async def _acquire_db(self):
        """
        Acquire a connection from the pool with backend-service RLS context.

        The dialer worker is a backend service with no per-request user
        context, but it needs to read campaigns / leads / tenants across
        every tenant to drive jobs. RLS policies on those tables would
        otherwise either filter every row out (returning None / empty
        result, surfacing as 'campaign is missing') or throw an
        invalid-UUID error when the GUC is unset.

        The canonical helper sets both the bypass and nil tenant UUID inside
        one transaction. Neither value can leak to the next pool borrower or
        disappear between statements under transaction pooling.
        """
        pool = self._db_pool
        async with acquire_with_tenant(pool, None) as conn:
            yield conn

    async def _reap_stuck_jobs_tick(self) -> None:
        """Diagnose uncertain jobs and request proof-aware call termination.

        Stale job/retry owners stay active with a reconciliation-required
        diagnostic. Only authoritative finalization/cancellation releases them;
        age cannot prove the original queued payload is unable to resume.
        Existing Redis inflight recovery remains independent and best-effort.
        """
        try:
            from app.domain.services.dialer.stuck_job_reaper import (
                reap_orphaned_scheduled_jobs,
                reap_stuck_jobs,
                reap_stuck_calls,
            )

            async with self._acquire_db() as conn:
                await reap_stuck_jobs(conn)
                await reap_stuck_calls(conn)
                await reap_orphaned_scheduled_jobs(conn)
        except Exception as exc:
            logger.warning("reaper tick failed: %s", exc)
        # Self-heal the Redis in-flight ZSET: age out members whose call ended
        # without a terminal mark (the dialer:processing pile-up). Independent
        # of the DB reapers — its own try/except so a Redis blip can't skip them.
        try:
            await self.queue_service.reap_stale_processing()
        except Exception as exc:
            logger.warning("processing-zset reaper tick failed: %s", exc)

    async def _get_active_tenant_ids(self) -> List[str]:
        """Get list of tenants with active/running campaigns."""
        try:
            async with self._acquire_db() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT tenant_id FROM campaigns "
                    "WHERE direction='outbound' AND status IN ('running', 'active')"
                )
                return [str(r["tenant_id"]) for r in rows] if rows else []

        except Exception as e:
            logger.error(f"Failed to get active tenants: {e}")
            return []

    async def _tenant_minutes_exhausted(self, tenant_id: str) -> bool:
        """Exhaustion is known; unavailable metering defers the same attempt."""
        from app.domain.services.minutes_quota import compute_minutes_status, MeteringUnavailable
        try:
            async with self._acquire_db() as conn:
                result = await compute_minutes_status(conn, tenant_id)
                return result.require_available().exhausted
        except Exception as exc:
            raise MeteringUnavailable("metering_unavailable") from exc

    async def _emit_unknown_bridge_code_alert(self, job, code: str) -> None:
        """Surface an unclassified telephony-bridge error code (throttled 5 min
        per code). Best-effort: it must never alter how the failure is handled."""
        try:
            if self._redis is not None:
                acquired = await self._redis.set(
                    f"evt:unknown_bridge_code:{code}", "1", nx=True, ex=300
                )
                if not acquired:
                    return
            from app.domain.services.event_emitter import emit_event_via_pool

            await emit_event_via_pool(
                self._db_pool,
                tenant_id=str(job.tenant_id),
                category="alert",
                severity="warning",
                title="Unrecognised telephony error",
                description=(
                    f"The telephony bridge rejected a call with an error code the "
                    f"dialer does not recognise ({code}). Leads may be failing; "
                    f"add the code to the retry classifier."
                ),
                related_campaign_id=str(job.campaign_id) if job.campaign_id else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("emit unknown_bridge_code failed: %s", exc)

    async def _emit_out_of_minutes_event(self, job) -> None:
        """Surface an out-of-minutes alert in the UI (throttled 5 min per tenant)."""
        try:
            if self._redis is not None and job.tenant_id:
                key = f"evt:out_of_minutes:{job.tenant_id}"
                acquired = await self._redis.set(key, "1", nx=True, ex=300)
                if not acquired:
                    return  # already alerted within the last 5 minutes
            from app.domain.services.event_emitter import emit_event_via_pool

            await emit_event_via_pool(
                self._db_pool,
                tenant_id=str(job.tenant_id),
                category="alert",
                severity="critical",
                title="Out of plan minutes",
                description=(
                    "Calls are paused — this month's plan minutes are used up. "
                    "Upgrade your plan or wait for the next billing cycle."
                ),
                related_campaign_id=str(job.campaign_id) if job.campaign_id else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("emit out_of_minutes failed: %s", exc)

    async def _get_campaign_status(
        self,
        campaign_id: str,
        tenant_id: str,
    ) -> Optional[str]:
        """Return campaign status so dequeued jobs can be revalidated before originate."""
        try:
            async with self._acquire_db() as conn:
                return await conn.fetchval(
                    """
                    SELECT status
                      FROM campaigns
                     WHERE id = $1::uuid
                       AND tenant_id = $2::uuid
                       AND direction = 'outbound'
                    """,
                    campaign_id,
                    tenant_id,
                )
        except Exception as exc:
            logger.error("Failed to get campaign status for %s: %s", campaign_id, exc)
            raise CampaignStatusUnavailable(
                f"campaign status unavailable for {campaign_id}"
            ) from exc

    #: ``mark_skipped`` reason for a job whose campaign can never run it again.
    #: Deliberately NOT in ``DialerQueueService.NON_TERMINAL_SKIP_REASONS``: those
    #: reasons re-defer the payload, which made a DELETED campaign's job cycle
    #: every 120 s forever.
    _CAMPAIGN_NOT_RUNNABLE_SKIP = "campaign_not_runnable"

    async def _end_or_hold_non_runnable_campaign_job(
        self,
        job: DialerJob,
        campaign_status: Optional[str],
        *,
        block_reason: str,
        error: str,
        allow_hold: bool = True,
    ) -> None:
        """A dequeued job whose campaign is not running.

        ``paused`` is temporary: the payload is re-deferred and the DB job stays
        ACTIVE, so a resume finds the same job instead of minting a second one
        for the lead (the old terminal SKIPPED write freed the lead's slot while
        the payload survived, so both dialled). Every other status (deleted,
        stopped, completed, cancelled, draft, archived, missing) is final: the
        payload leaves Redis and the DB job is SKIPPED, which also releases the
        lead's one-active-job slot so a restart re-queues it (``start_campaign``
        enqueues leads that are still pending/calling).
        """
        if campaign_status == "paused" and allow_hold:
            # A contact removed (or flagged dnc) while the campaign is paused
            # must not be written back to 'pending' by the hold's projection.
            stale_reason = await self._stale_payload_reason(job)
            if stale_reason is not None:
                await self._drop_stale_payload(job, stale_reason)
                return
            await self._publish_block(job, "campaign_paused")
            await self._redefer_before_intent_resolution(
                job, reason="campaign_paused",
            )
            return
        await self._publish_block(job, block_reason)
        await self.queue_service.mark_skipped(
            job.job_id, reason=self._CAMPAIGN_NOT_RUNNABLE_SKIP
        )
        await self._update_job_status(job, JobStatus.SKIPPED, error=error)

    async def _stale_payload_reason(self, job: DialerJob) -> Optional[str]:
        """Why this dequeued payload must NOT be dialled, or ``None``.

        ONE tenant-scoped query reads the DB job and the lead together. The
        payload is only a delivery of work Postgres still wants:

        * ``job_not_active`` -- the DB job is missing or outside
          ``ACTIVE_STATUSES`` (cancelled by a stop / lead removal, already
          settled, skipped ...);
        * ``lead_not_dialable`` -- the lead is gone, soft-deleted / ``dnc``, or
          flagged ``do_not_call`` after it was queued.

        A failed read raises ``ComplianceLookupUnavailable`` (the caller
        defers; an unreadable state is never a licence to dial).
        """
        from app.domain.services.dialer.job_states import ACTIVE_STATUSES

        try:
            async with self._acquire_db() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT j.status       AS job_status,
                           l.id           AS lead_row_id,
                           l.status       AS lead_status,
                           l.do_not_call  AS lead_do_not_call
                      FROM (SELECT 1) AS base
                      LEFT JOIN dialer_jobs AS j
                        ON j.id = $1::uuid AND j.tenant_id = $2::uuid
                      LEFT JOIN leads AS l
                        ON l.id = $3::uuid AND l.tenant_id = $2::uuid
                    """,
                    str(job.job_id),
                    str(job.tenant_id),
                    str(job.lead_id),
                )
        except Exception as exc:
            logger.warning(
                "dialer_payload_state_unavailable job=%s err=%s", job.job_id, exc
            )
            raise ComplianceLookupUnavailable("dialer job/lead state unavailable") from exc
        if row is None:
            # The query always yields one row; none means the read is unusable.
            raise ComplianceLookupUnavailable("dialer job/lead state unavailable")
        if str(row["job_status"] or "") not in ACTIVE_STATUSES:
            return "job_not_active"
        if (
            row["lead_row_id"] is None
            or str(row["lead_status"] or "") in ("deleted", "dnc")
            or bool(row["lead_do_not_call"])
        ):
            return "lead_not_dialable"
        return None

    async def _drop_stale_payload(self, job: DialerJob, reason: str) -> None:
        """Terminal-skip a payload Postgres no longer wants dialled.

        The payload always leaves Redis (``reason`` is not in
        ``NON_TERMINAL_SKIP_REASONS``). ``job_not_active`` leaves the DB alone:
        the job is already ended and its history must not be rewritten.
        ``lead_not_dialable`` ends the still-active job. The lead is never
        touched: a removed contact must not be written back to 'pending', so a
        failed job write is logged, not raised into the failure boundary.
        """
        logger.warning(
            "dialer_stale_payload_dropped job=%s lead=%s campaign=%s reason=%s",
            job.job_id, job.lead_id, job.campaign_id, reason,
        )
        await self.queue_service.mark_skipped(job.job_id, reason=reason)
        if reason == "lead_not_dialable":
            try:
                await self._update_job_status(job, JobStatus.SKIPPED, error=reason)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "dialer_stale_payload_job_write_failed job=%s reason=%s err=%s",
                    job.job_id, reason, exc,
                )

    async def _get_tenant_rules(self, tenant_id: str) -> CallingRules:
        """Get calling rules for a tenant.

        An absent/NULL row means "use the defaults"; a FAILED query raises
        ``ComplianceLookupUnavailable`` (the default window is not a safe
        stand-in for a tenant's configured one).
        """
        try:
            async with self._acquire_db() as conn:
                row = await conn.fetchrow(
                    "SELECT calling_rules FROM tenants WHERE id = $1", tenant_id
                )
        except Exception as e:
            logger.warning(f"Failed to get tenant rules: {e}")
            raise ComplianceLookupUnavailable("tenant calling rules unavailable") from e

        try:
            if row and row["calling_rules"]:
                # asyncpg returns JSON/JSONB as string or dict depending on driver config
                # assuming standard driver config (string/dict)
                rules_data = row["calling_rules"]
                if isinstance(rules_data, str):
                    rules_data = json.loads(rules_data)
                return CallingRules.from_dict(rules_data)
        except Exception as e:
            logger.warning(f"Failed to parse tenant rules, using defaults: {e}")

        return CallingRules.default()

    async def _get_campaign_calling_config(self, campaign_id: str) -> Optional[dict]:
        """Load a campaign's per-campaign calling schedule (timezone, window,
        days, ignore_schedule override). Returns None when unset so the
        worker falls back to tenant defaults; a FAILED query raises
        ``ComplianceLookupUnavailable`` (the schedule overlay is a gate)."""
        try:
            async with self._acquire_db() as conn:
                cfg = await conn.fetchval(
                    "SELECT calling_config FROM campaigns WHERE id = $1",
                    campaign_id,
                )
        except Exception as e:
            logger.warning(f"Failed to load campaign calling_config for {campaign_id}: {e}")
            raise ComplianceLookupUnavailable("campaign calling config unavailable") from e
        try:
            if cfg:
                if isinstance(cfg, str):
                    cfg = json.loads(cfg)
                if isinstance(cfg, dict):
                    return cfg
        except Exception as e:
            logger.warning(f"Failed to parse campaign calling_config for {campaign_id}: {e}")
        return None

    def _resolve_batch_size(self, campaign_cfg: Optional[dict]) -> int:
        """Resolve the per-campaign batch size (max calls in flight at once).

        Client-selectable via ``calling_config.batch_size``; falls back to the
        ``DIALER_BATCH_SIZE`` env default (10). 0 (or negative) disables the
        batch gate — unbounded, legacy behaviour.
        """
        default = int(os.getenv("DIALER_BATCH_SIZE", "10"))
        if isinstance(campaign_cfg, dict):
            raw = campaign_cfg.get("batch_size")
            if raw is not None:
                try:
                    return max(0, int(raw))
                except (TypeError, ValueError):
                    pass
        return max(0, default)

    async def _campaign_inflight_calls(self, campaign_id: str) -> int:
        """Count calls currently IN FLIGHT for a campaign — those still holding
        a batch slot (dialing / ringing / answered / in_call / initiated).
        Terminal states (ended / completed / failed) have freed their slot.

        Anti-wedge safety net applies only before answer: a stale initiated /
        dialing / ringing claim stops consuming a slot after the configured
        pre-answer age. Answered/in-call rows keep consuming a slot until the
        proof-aware stuck-call reaper moves them to termination_pending; their
        supported lifetime can be four hours, so the old global 600s age filter
        silently over-dialled batch_size after ten minutes.

        Fail-open: on a transient DB error return 0 so a hiccup never wedges
        dispatch (the concurrency guard remains as a backstop).
        """
        # Ring/prewarm ceiling plus buffer. Connected calls deliberately have
        # no short age predicate here; the state-aware reaper owns that bound.
        max_age = int(os.getenv("DIALER_INFLIGHT_MAX_AGE_S", "600"))
        try:
            async with self._acquire_db() as conn:
                val = await conn.fetchval(
                    """
                    SELECT count(*) FROM calls
                     WHERE campaign_id = $1
                       AND (
                              status IN ('answered', 'in_call')
                           OR (
                                  status IN ('dialing', 'ringing', 'initiated')
                              AND created_at
                                  > now() - make_interval(secs => $2::int)
                              )
                       )
                       -- A browser test session holds no telephony channel, so
                       -- counting it would throttle real dialling.
                       AND NOT is_test
                    """,
                    campaign_id,
                    max_age,
                )
            return int(val or 0)
        except Exception as exc:
            logger.warning(
                "batch_gate: in-flight count failed campaign=%s err=%s",
                campaign_id,
                exc,
            )
            return 0

    async def _tenant_live_call_count(self, tenant_id: str) -> Optional[int]:
        """THIS tenant's connected calls, for the max-concurrent-calls gate.

        Counts the tenant's ``answered`` / ``in_call`` rows (the same meaning as
        the bridge's answer-to-hangup ledger) with an explicit ``tenant_id``
        predicate (the production role bypasses RLS). The recency bound is the
        stuck-call reaper's own connected-call ceiling: a row older than that is
        already headed for ``termination_pending`` and must not hold a slot
        forever. Browser test sessions hold no channel and are excluded.

        ``None`` (query failed) lets the caller fall back to the in-memory
        counter, exactly as the old Redis-unavailable path did.
        """
        try:
            from app.domain.services.dialer.stuck_job_reaper import (
                connected_call_stuck_timeout_seconds,
            )

            async with self._acquire_db() as conn:
                val = await conn.fetchval(
                    """
                    SELECT count(*) FROM calls
                     WHERE tenant_id = $1::uuid
                       AND status IN ('answered', 'in_call')
                       AND COALESCE(answered_at, created_at)
                           > now() - make_interval(secs => $2::int)
                       AND NOT is_test
                    """,
                    str(tenant_id),
                    int(connected_call_stuck_timeout_seconds()),
                )
            return int(val or 0)
        except Exception as exc:
            logger.warning(
                "dialer_tenant_live_count_failed tenant=%s err=%s", tenant_id, exc
            )
            return None

    def _resolve_call_gap(self, campaign_cfg: Optional[dict]) -> int:
        """Resolve the per-campaign inter-call gap in seconds — the minimum wait
        between consecutive originations. Client-selectable via
        ``calling_config.call_gap_seconds``; falls back to the ``DIALER_CALL_GAP_S``
        env default (0 = no gap). Negative is clamped to 0.
        """
        default = int(os.getenv("DIALER_CALL_GAP_S", "0"))
        if isinstance(campaign_cfg, dict):
            raw = campaign_cfg.get("call_gap_seconds")
            if raw is not None:
                try:
                    return max(0, int(raw))
                except (TypeError, ValueError):
                    pass
        return max(0, default)

    @staticmethod
    def _campaign_last_dial_key(campaign_id: str) -> str:
        return f"dialer:last_dial:{campaign_id}"

    async def _mark_campaign_dialed(self, campaign_id: str) -> None:
        """Record 'a call was just originated for this campaign' in Redis, for
        the inter-call gap. Kept in Redis (not the calls table) so it reflects
        only the CURRENT run: start_campaign clears it, so the first call of a
        run never waits. TTL comfortably exceeds the max gap (60 min)."""
        try:
            if self._redis is None:
                return
            now = datetime.now(timezone.utc).timestamp()
            await self._redis.set(
                self._campaign_last_dial_key(campaign_id),
                str(now),
                ex=3700,
            )
        except Exception as exc:
            logger.debug("call_gap: mark_dialed failed campaign=%s err=%s", campaign_id, exc)

    async def _campaign_seconds_since_last_dial(self, campaign_id: str) -> Optional[int]:
        """Seconds since this campaign's last origination IN THE CURRENT RUN.

        Reads the Redis last-dial key set by ``_mark_campaign_dialed``. Returns
        None when there's no key — i.e. the FIRST call of a run (start_campaign
        clears it) or Redis is unavailable — so the gap is NOT applied and the
        first call dials immediately. The gap only ever spaces subsequent calls.
        """
        try:
            if self._redis is None:
                return None
            val = await self._redis.get(self._campaign_last_dial_key(campaign_id))
            if not val:
                return None
            last = float(val)
            return max(0, int(datetime.now(timezone.utc).timestamp() - last))
        except Exception as exc:
            logger.warning(
                "call_gap: last-dial lookup failed campaign=%s err=%s",
                campaign_id,
                exc,
            )
            return None

    async def _get_lead_last_called(self, lead_id: str) -> Optional[datetime]:
        """Get the last time a lead was called.

        NULL means never called; a FAILED query raises
        ``ComplianceLookupUnavailable`` (None here would skip the cooldown).
        """
        try:
            async with self._acquire_db() as conn:
                val = await conn.fetchval("SELECT last_called_at FROM leads WHERE id = $1", lead_id)
                return val  # asyncpg returns appropriate datetime object

        except Exception as e:
            logger.warning(f"Failed to get lead last_called_at: {e}")
            raise ComplianceLookupUnavailable("lead last_called_at unavailable") from e

    async def _get_lead_timezone(self, lead_id: str) -> Optional[str]:
        """Read the customer-supplied per-lead IANA timezone.

        ``leads.timezone`` (migration 0020, captured by CSV import and the
        manual contact form) is the authoritative statement of where the
        prospect actually is, so the calling-window check prefers it over
        the phone-number-derived guess.

        Best-effort only for the column being ABSENT (a database that has
        not taken 0020): that returns None and the caller falls back to the
        derived zone and then the campaign timezone. A genuine database
        failure (query error, timeout, connection loss) raises
        ``ComplianceLookupUnavailable`` so the calling window is never judged
        on a fallback zone because of a blip. Anything else (not a database
        failure) stays non-fatal: a timezone lookup must not drop a dial by
        an unrelated exception.
        """
        try:
            async with self._acquire_db() as conn:
                return await conn.fetchval("SELECT timezone FROM leads WHERE id = $1", lead_id)
        except asyncpg.UndefinedColumnError as e:
            logger.warning(f"Failed to get lead timezone (column absent): {e}")
        except (
            asyncpg.PostgresError,
            asyncpg.InterfaceError,
            asyncio.TimeoutError,
            OSError,
        ) as e:
            logger.warning(f"Failed to get lead timezone: {e}")
            raise ComplianceLookupUnavailable("lead timezone unavailable") from e
        except Exception as e:
            logger.warning(f"Failed to get lead timezone: {e}")
        return None

    async def _get_lead_attempts_today(self, lead_id: str) -> int:
        """Count dial attempts already made to a lead since UTC midnight.

        Used only when the tenant has a daily per-lead cap configured
        (``max_calls_per_lead_per_day`` > 0); otherwise this is never
        called, so it adds zero overhead to the default path. Counts
        ``calls`` rows rather than the cumulative ``leads.call_attempts``
        because the cap is a *per-day* ceiling.
        """
        try:
            async with self._acquire_db() as conn:
                val = await conn.fetchval(
                    "SELECT COUNT(*) FROM calls "
                    "WHERE lead_id = $1 AND created_at >= date_trunc('day', now()) "
                    # Testing an agent must not burn a lead's daily attempt quota.
                    "AND NOT is_test "
                    # Nor may intents released while the line was refused:
                    # those calls were never handed to a carrier.
                    "AND NOT (provider_call_id IS NULL AND COALESCE(failure_reason, '') "
                    "IN ('line_not_ready', 'telephony_unavailable'))",
                    lead_id,
                )
                return int(val or 0)
        except Exception as e:
            logger.warning(f"Failed to count lead attempts today: {e}")
            # 0 would silently disable the per-lead daily cap.
            raise ComplianceLookupUnavailable("lead attempts today unavailable") from e

    @staticmethod
    def _new_call_identity() -> tuple[str, str, str]:
        return str(uuid.uuid4()), generate_talklee_call_id(), str(uuid.uuid4())

    @staticmethod
    def _call_status_is_terminal(status: str) -> bool:
        return str(status or "").strip().lower() in {
            "ended",
            "completed",
            "failed",
            "cancelled",
            "canceled",
            "rejected",
            "busy",
            "no_answer",
        }

    def _schedule_override_audit(self) -> dict:
        if not self._schedule_override:
            return {}
        try:
            from app.domain.services.dialer.testing_override import (
                override_audit_payload,
            )

            return override_audit_payload(
                source=self._schedule_override.get("source", "unknown"),
                blocked_reason=self._schedule_override.get("blocked_reason"),
                schedule=self._schedule_override.get("schedule"),
            )
        except Exception as exc:  # noqa: BLE001 - audit cannot bypass durability
            logger.warning("schedule_override audit payload failed: %s", exc)
            return {"schedule_override": True}

    @staticmethod
    def _validated_existing_call_intent(job: DialerJob, existing) -> DialerCallIntent:
        """Convert a tenant-scoped row into the exact attempt's replay token."""
        expected = {
            "tenant_id": str(job.tenant_id),
            "campaign_id": str(job.campaign_id),
            "lead_id": str(job.lead_id),
            "phone_number": str(job.phone_number),
            "direction": "outbound",
        }
        for field, value in expected.items():
            if str(existing[field] or "") != value:
                raise RuntimeError(f"replayed call intent has mismatched {field}")
        if not existing["talklee_call_id"] or not existing["leg_id"]:
            raise RuntimeError("replayed call intent is incomplete")
        return DialerCallIntent(
            call_id=str(existing["id"]),
            talklee_call_id=str(existing["talklee_call_id"]),
            leg_id=str(existing["leg_id"]),
            status=str(existing["status"]),
            provider_call_id=(
                str(existing["provider_call_id"])
                if existing["provider_call_id"]
                else None
            ),
            created=False,
        )

    async def _load_existing_call_intent(
        self, job: DialerJob
    ) -> Optional[DialerCallIntent]:
        """Load an already-committed attempt without creating a new one."""
        async with self._acquire_db() as conn:
            existing = await conn.fetchrow(
                """
                SELECT c.id, c.tenant_id, c.campaign_id, c.lead_id,
                       c.phone_number, c.direction, c.talklee_call_id,
                       c.status,
                       COALESCE(c.provider_call_id, c.external_call_uuid)
                           AS provider_call_id,
                       leg.id AS leg_id
                  FROM calls AS c
                  LEFT JOIN LATERAL (
                      SELECT id
                        FROM call_legs
                       WHERE call_id = c.id
                         AND leg_type = 'pstn_outbound'
                       ORDER BY created_at, id
                       LIMIT 1
                  ) AS leg ON TRUE
                 WHERE c.tenant_id = $1::uuid
                   AND c.dialer_job_id = $2::uuid
                   AND c.dialer_attempt_number = $3
                 LIMIT 1
                """,
                str(job.tenant_id),
                str(job.job_id),
                int(job.attempt_number),
            )
        if existing is None:
            return None
        return self._validated_existing_call_intent(job, existing)

    async def _create_call_intent(self, job: DialerJob) -> DialerCallIntent:
        """Commit or load the one durable row for this exact dial attempt.

        Provider identity is deliberately absent at insert time. The internal
        bridge verifies this row, persists its planned provider identity, and
        only then invokes ARI. Any database error raises: no durable row means
        the worker must never ask the provider to dial.
        """
        call_id, talklee_call_id, leg_id = self._new_call_identity()
        override_audit = self._schedule_override_audit()
        try:
            async with self._acquire_db() as conn:
                inserted = await conn.fetchrow(
                    """
                    INSERT INTO calls (
                        id, tenant_id, campaign_id, lead_id, phone_number,
                        status, talklee_call_id, dialer_job_id,
                        dialer_attempt_number, direction, created_at, updated_at
                    ) VALUES (
                        $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5,
                        'initiated', $6, $7::uuid, $8, 'outbound', NOW(), NOW()
                    )
                    ON CONFLICT (dialer_job_id, dialer_attempt_number)
                        WHERE dialer_job_id IS NOT NULL
                          AND dialer_attempt_number IS NOT NULL
                    DO NOTHING
                    RETURNING id, talklee_call_id, status, provider_call_id
                    """,
                    call_id,
                    str(job.tenant_id),
                    str(job.campaign_id),
                    str(job.lead_id),
                    str(job.phone_number),
                    talklee_call_id,
                    str(job.job_id),
                    int(job.attempt_number),
                )
                if inserted is not None:
                    await conn.execute(
                        """
                        INSERT INTO call_legs (
                            id, call_id, talklee_call_id, leg_type, direction,
                            provider, provider_leg_id, to_number, status,
                            metadata, created_at
                        ) VALUES (
                            $1::uuid, $2::uuid, $3, 'pstn_outbound', 'outbound',
                            'pending', NULL, $4, 'initiated', $5::jsonb, NOW()
                        )
                        """,
                        leg_id,
                        call_id,
                        talklee_call_id,
                        str(job.phone_number),
                        json.dumps(
                            {
                                "job_id": str(job.job_id),
                                "campaign_id": str(job.campaign_id),
                                "dialer_attempt_number": int(job.attempt_number),
                                **override_audit,
                            }
                        ),
                    )
                    await conn.execute(
                        """
                        INSERT INTO call_events (
                            call_id, talklee_call_id, leg_id, event_type, source,
                            event_data, new_state, created_at
                        ) VALUES (
                            $1::uuid, $2, $3::uuid, 'origination_intent',
                            'dialer_worker', $4::jsonb, 'initiated', NOW()
                        )
                        """,
                        call_id,
                        talklee_call_id,
                        leg_id,
                        json.dumps(
                            {
                                "job_id": str(job.job_id),
                                "campaign_id": str(job.campaign_id),
                                "dialer_attempt_number": int(job.attempt_number),
                                **override_audit,
                            }
                        ),
                    )
                    if override_audit:
                        await conn.execute(
                            """
                            INSERT INTO call_events (
                                call_id, talklee_call_id, leg_id, event_type,
                                source, event_data, created_at
                            ) VALUES (
                                $1::uuid, $2, $3::uuid, 'schedule_override',
                                'dialer_worker', $4::jsonb, NOW()
                            )
                            """,
                            call_id,
                            talklee_call_id,
                            leg_id,
                            json.dumps(override_audit),
                        )
                    return DialerCallIntent(
                        call_id=str(inserted["id"]),
                        talklee_call_id=str(inserted["talklee_call_id"]),
                        leg_id=leg_id,
                        status=str(inserted["status"]),
                        provider_call_id=(
                            str(inserted["provider_call_id"])
                            if inserted["provider_call_id"]
                            else None
                        ),
                        created=True,
                    )

                # A concurrent worker or crash replay won the unique key. This
                # is a separate statement so READ COMMITTED sees the winner
                # after ON CONFLICT waited for it to commit.
                existing = await conn.fetchrow(
                    """
                    SELECT c.id, c.tenant_id, c.campaign_id, c.lead_id,
                           c.phone_number, c.direction, c.talklee_call_id,
                           c.status,
                           COALESCE(c.provider_call_id, c.external_call_uuid)
                               AS provider_call_id,
                           leg.id AS leg_id
                      FROM calls AS c
                      LEFT JOIN LATERAL (
                          SELECT id
                            FROM call_legs
                           WHERE call_id = c.id
                             AND leg_type = 'pstn_outbound'
                           ORDER BY created_at, id
                           LIMIT 1
                      ) AS leg ON TRUE
                     WHERE c.tenant_id = $1::uuid
                       AND c.dialer_job_id = $2::uuid
                       AND c.dialer_attempt_number = $3
                     LIMIT 1
                    """,
                    str(job.tenant_id),
                    str(job.job_id),
                    int(job.attempt_number),
                )
                if existing is None:
                    raise RuntimeError("idempotency winner is not tenant-visible")
                return self._validated_existing_call_intent(job, existing)
        except Exception as exc:
            logger.error(
                "durable_call_intent_failed job=%s attempt=%s err=%s",
                job.job_id,
                job.attempt_number,
                exc,
            )
            raise RuntimeError("durable call intent could not be committed") from exc

    async def _resume_existing_call_intent(
        self,
        job: DialerJob,
        intent: DialerCallIntent,
    ) -> None:
        """Reconcile one committed attempt without charging new-call gates."""
        if self._call_status_is_terminal(intent.status):
            if await self._attempt_settled_failed_unanswered(job):
                raise AttemptSettledFailed(intent.call_id)
            job.call_id = intent.call_id
            await self.queue_service.mark_completed(
                job.job_id,
                outcome="duplicate_terminal_attempt",
            )
            return

        from app.domain.services.dialer.campaign_schedule import effective_rules

        tenant_rules = await self._get_tenant_rules(job.tenant_id)
        campaign_cfg = await self._get_campaign_calling_config(job.campaign_id)
        rules = effective_rules(tenant_rules, campaign_cfg)
        if (
            intent.provider_call_id is None
            and str(intent.status or "").strip().lower() in {"queued", "initiated"}
        ):
            # The bridge never claimed this attempt, so this replay is about to
            # be its FIRST real origination (a same-attempt hold: pipeline down,
            # throttle, trunk not ready). The gates were applied when the hold
            # began, possibly minutes ago.
            await self._require_open_window_for_unclaimed_intent(
                job, rules, campaign_cfg
            )
        provider_call_id = await self._make_call(
            job,
            rules,
            call_intent=intent,
        )
        if provider_call_id == self._ORIGINATION_UNCERTAIN:
            await self._park_uncertain_origination(job, intent)
            return
        if provider_call_id == self._ATTEMPT_ALREADY_TERMINAL:
            if await self._attempt_settled_failed_unanswered(job):
                raise AttemptSettledFailed(intent.call_id)
            job.call_id = intent.call_id
            await self.queue_service.mark_completed(
                job.job_id,
                outcome="duplicate_terminal_attempt",
            )
            return
        if provider_call_id in (self._METERING_UNAVAILABLE, self._TELEPHONY_SELECTION_UNAVAILABLE):
            reason = ("metering_unavailable" if provider_call_id == self._METERING_UNAVAILABLE
                      else "telephony_selection_unavailable")
            await self._publish_block(job, reason, rules=rules)
            await self._redefer_before_intent_resolution(job, reason=reason)
            return
        if provider_call_id == self._BRIDGE_TRANSIENT_REJECT:
            defer_reason, defer_delay = self._last_bridge_defer
            if not await self._held_intent_has_headroom(job, intent, defer_delay):
                if defer_reason == self._LINE_REFUSAL_CODE:
                    # See process_job: a refused line never burns the attempt.
                    await self._defer_refused_line(job, intent, rules)
                    return
                raise HeldIntentExpired(
                    f"{defer_reason}: held attempt reached the stuck-call horizon"
                )
            logger.warning(
                "dialer_bridge_rejected_transient job=%s attempt=%s code=%s "
                "hold=%ss (same attempt re-deferred)",
                job.job_id, job.attempt_number, defer_reason, defer_delay,
            )
            block_reason, job_reason = self._bridge_defer_labels(defer_reason)
            await self._publish_block(
                job, block_reason, rules=rules, retry_after_seconds=defer_delay,
            )
            await self._redefer_before_intent_resolution(
                job, reason=job_reason, delay_seconds=defer_delay,
            )
            return
        if provider_call_id == self._PIPELINE_UNAVAILABLE:
            # Same-attempt hold on the committed intent (see process_job).
            await self._defer_pipeline_unavailable(job, rules, intent)
            return
        if not provider_call_id:
            # `process_job` performs the single absence-proof CAS before its
            # retry policy is allowed to manufacture attempt N+1.
            raise RuntimeError("No call_id returned from telephony provider")

        try:
            await self._bind_call_intent(job, intent, provider_call_id)
        except Exception as exc:
            logger.error(
                "dialer_call_intent_replay_bind_uncertain job=%s call=%s err=%s",
                job.job_id,
                intent.call_id,
                exc,
            )
            await self._park_uncertain_origination(job, intent)
            return

        try:
            from app.domain.services.call_status import CallState, record_call_state

            await record_call_state(
                self._db_pool,
                call_id=intent.call_id,
                tenant_id=job.tenant_id,
                campaign_id=job.campaign_id,
                new_state=CallState.DIALING,
                metadata={
                    "phone_number": str(job.phone_number),
                    "agent_name": getattr(job, "agent_name", None),
                    "provider_call_id": provider_call_id,
                    "description": f"Dialing {job.phone_number}",
                },
            )
        except Exception as exc:
            logger.warning(
                "call_status.dialing_replay_emit_failed call=%s err=%s",
                intent.call_id,
                exc,
            )

        job.call_id = intent.call_id
        if await self._attempt_call_settled(job, intent):
            logger.warning(
                "dialer_attempt_settled_before_processing job=%s attempt=%s "
                "call=%s — skipping calling/processing writes",
                job.job_id, job.attempt_number, intent.call_id,
            )
        else:
            await self._update_lead_status(job, "calling")
            job.status = JobStatus.PROCESSING
            job.processed_at = datetime.now(timezone.utc)
            await self._update_job_status(
                job,
                JobStatus.PROCESSING,
                call_id=intent.call_id,
            )
        self._jobs_processed += 1
        await self._mark_campaign_dialed(job.campaign_id)
        try:
            from app.domain.services.dialer.block_state import clear_block_reason

            await clear_block_reason(self._redis, job.campaign_id)
        except Exception as exc:
            logger.debug("block_state replay clear failed: %s", exc)
        logger.info(
            "Call replay reconciled: internal_call_id=%s provider_call_id=%s job=%s",
            intent.call_id,
            provider_call_id,
            job.job_id,
        )
        await self._emit_progress_event_throttled(job)

    @staticmethod
    def _pipeline_unavailable_max_defers() -> int:
        try:
            return max(1, int(os.getenv("DIALER_PIPELINE_UNAVAILABLE_MAX_DEFERS", "30")))
        except (TypeError, ValueError):
            return 30

    async def _pipeline_unavailable_defer_count(self, job: DialerJob) -> int:
        """Same-attempt holds already spent on this job's CURRENT attempt.

        Keyed per (job, attempt) so a later attempt gets a fresh budget, with a
        TTL so the key can never outlive the job. Without Redis there is no
        count (0): the hold stays unbounded exactly as the other pre-intent
        defers are.
        """
        if self._redis is None:
            return 0
        key = f"dialer:pipeline_unavailable:{job.job_id}:{int(job.attempt_number)}"
        try:
            count = int(await self._redis.incr(key))
            await self._redis.expire(key, 7200)
            return count
        except Exception as exc:  # noqa: BLE001
            logger.warning("pipeline_unavailable_count_failed job=%s err=%s", job.job_id, exc)
            return 0

    @staticmethod
    def _held_intent_max_age_seconds() -> int:
        """Longest a never-claimed intent may be held on the SAME attempt.

        ``reap_stuck_calls`` writes any pre-answer row older than
        ``CALL_STUCK_TIMEOUT_S`` (600 s) off as ``termination_pending``. A
        provider-less row has no identity for the telephony owner to settle,
        so the job would then park forever on the bridge's 409
        ``origination_cleanup_pending``. Half the horizon leaves room for queue
        latency between the due time and the actual replay.
        """
        try:
            from app.domain.services.dialer.stuck_job_reaper import CALL_STUCK_TIMEOUT_S

            horizon = int(CALL_STUCK_TIMEOUT_S)
        except Exception:  # noqa: BLE001
            horizon = 600
        try:
            return max(60, int(os.getenv("DIALER_HELD_INTENT_MAX_AGE_S", str(horizon // 2))))
        except (TypeError, ValueError):
            return max(60, horizon // 2)

    async def _held_intent_has_headroom(
        self, job: DialerJob, intent: Optional[DialerCallIntent], delay_seconds: float
    ) -> bool:
        """True while holding the intent another ``delay_seconds`` keeps it
        younger than :meth:`_held_intent_max_age_seconds` (age = the reaper's
        own clock, ``calls.created_at``). When the age cannot be read the hold
        is kept: holding never dials, and the hold itself needs the same DB."""
        if intent is None:
            return True
        try:
            async with self._acquire_db() as conn:
                age = await conn.fetchval(
                    """
                    SELECT EXTRACT(EPOCH FROM (NOW() - created_at))
                      FROM calls
                     WHERE id = $1::uuid
                       AND tenant_id = $2::uuid
                       AND dialer_job_id = $3::uuid
                       AND dialer_attempt_number = $4
                       AND direction = 'outbound'
                    """,
                    intent.call_id,
                    str(job.tenant_id),
                    str(job.job_id),
                    int(job.attempt_number),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "held_intent_age_unavailable job=%s attempt=%s err=%s",
                job.job_id, job.attempt_number, exc,
            )
            return True
        if age is None:
            return True
        return float(age) + float(delay_seconds) <= self._held_intent_max_age_seconds()

    async def _require_open_window_for_unclaimed_intent(
        self,
        job: DialerJob,
        rules: CallingRules,
        campaign_cfg: Optional[dict],
    ) -> None:
        """Re-apply the calling-window gate to a replayed, never-claimed attempt.

        ``process_job`` resumes a committed intent WITHOUT the new-call gates:
        they are stateful (the batch gate counts the intent itself, pacing and
        CallGuard were already charged), which is right for an attempt that may
        already be live. The window is pure time arithmetic, so it is safe to
        evaluate again, and a hold that outlives the end of the window must not
        become a dial outside it. Raises :class:`HeldIntentExpired`.
        """
        from app.domain.services.dialer.campaign_schedule import schedule_ignored
        from app.domain.services.dialer.lead_timezone import (
            resolve_effective_lead_timezone,
        )

        lead_tz = resolve_effective_lead_timezone(
            explicit_timezone=await self._get_lead_timezone(job.lead_id),
            phone_number=job.phone_number,
        )
        # The same oracle the fresh path uses, but with ONLY the window input:
        # no cooldown / daily-cap data (this attempt's own row would count
        # against them) and no live-call override.
        in_window, window_reason = await self.rules_engine.can_make_call(
            tenant_id=job.tenant_id,
            campaign_id=job.campaign_id,
            rules=rules,
            lead_last_called=None,
            active_calls_override=None,
            lead_attempts_today=None,
            lead_timezone=lead_tz,
            enforce_window=not schedule_ignored(campaign_cfg),
        )
        if in_window:
            return
        from app.domain.services.dialer.testing_override import (
            log_override_used,
            schedule_override_source,
        )

        override_source = schedule_override_source(campaign_cfg)
        if override_source:
            from app.domain.services.dialer.block_reasons import describe_schedule

            log_override_used(
                source=override_source,
                campaign_id=job.campaign_id,
                tenant_id=job.tenant_id,
                phone_number=job.phone_number,
                blocked_reason=window_reason,
                schedule=describe_schedule(rules),
            )
            return
        raise HeldIntentExpired(
            f"calling window closed while the attempt was held ({window_reason})",
            retry_reason="outside_calling_window",
        )

    async def _defer_pipeline_unavailable(
        self,
        job: DialerJob,
        rules: Optional[CallingRules],
        intent: Optional[DialerCallIntent] = None,
    ) -> None:
        """Hold the SAME attempt for 60 s after a 503 from the bridge.

        Raises once the bound is exceeded (or the held intent is about to
        outlive the stuck-call reaper) so the generic failure boundary
        classifies the outage (consuming capped attempts) instead of looping.
        """
        hold_reason = self._pipeline_hold_reason()
        spent = await self._pipeline_unavailable_defer_count(job)
        over_budget = spent > self._pipeline_unavailable_max_defers()
        if over_budget or not await self._held_intent_has_headroom(job, intent, 60):
            if hold_reason == "telephony_unavailable" and intent is not None:
                # The PHONE SYSTEM is down (adapter disconnected, telephony
                # owner fenced, pod draining). Like a refused line this is not
                # the lead's fault: never burn its attempts on an outage.
                await self._defer_telephony_outage(job, intent, rules)
                return
            if over_budget:
                raise RuntimeError(
                    f"voice pipeline unavailable after {spent - 1} same-attempt holds"
                )
            raise HeldIntentExpired(
                "voice pipeline unavailable: held attempt reached the stuck-call horizon"
            )
        await self._publish_block(
            job,
            hold_reason,
            rules=rules,
            retry_after_seconds=60,
        )
        await self._redefer_before_intent_resolution(
            job, reason=hold_reason, delay_seconds=60,
        )

    async def _defer_telephony_outage(
        self,
        job: DialerJob,
        intent: DialerCallIntent,
        rules: Optional[CallingRules],
    ) -> None:
        """A held telephony-outage 503 ran past its bound.

        Same treatment as :meth:`_defer_refused_line`: release the never-sent
        intent (``failure_reason='telephony_unavailable'``) and re-defer the
        SAME attempt, so a platform outage longer than the hold window does not
        fail every queued lead after four attempts.
        """
        if not await self._release_intent_line_not_ready(
            job, intent, reason="telephony_unavailable",
        ):
            await self._park_uncertain_origination(job, intent)
            return
        logger.warning(
            "dialer_telephony_outage_deferred job=%s attempt=%s redefer=%ss "
            "(same attempt, nothing consumed)",
            job.job_id, job.attempt_number, self._LINE_REFUSED_REDEFER_SECONDS,
        )
        await self._publish_block(
            job, "telephony_unavailable", rules=rules,
            retry_after_seconds=self._LINE_REFUSED_REDEFER_SECONDS,
        )
        await self._redefer_before_intent_resolution(
            job, reason="telephony_unavailable",
            delay_seconds=self._LINE_REFUSED_REDEFER_SECONDS,
        )

    async def _defer_refused_line(
        self,
        job: DialerJob,
        intent: DialerCallIntent,
        rules: Optional[CallingRules],
    ) -> None:
        """A held line refusal (422 ``tenant_pbx_required``) ran out of time.

        The lead is not at fault and no call was sent, so nothing is classified
        and no attempt is consumed (the old ``HeldIntentExpired`` route made
        every lead of the campaign ``failed`` after four ~4-minute holds). The
        never-sent intent is released as ``line_not_ready`` and the SAME attempt
        is re-deferred ``_LINE_REFUSED_REDEFER_SECONDS`` out, so dialling
        resumes by itself once the line is ready. That wait is the loop's only
        pace; it ends when the campaign is stopped, paused or deleted.
        """
        if not await self._release_intent_line_not_ready(job, intent):
            # Absence is no longer provable (a provider id appeared, or the row
            # settled): never guess -- keep the attempt owned.
            await self._park_uncertain_origination(job, intent)
            return
        block_reason, job_reason = self._bridge_defer_labels(self._LINE_REFUSAL_CODE)
        logger.warning(
            "dialer_line_not_ready_deferred job=%s attempt=%s redefer=%ss "
            "(same attempt, nothing consumed)",
            job.job_id, job.attempt_number, self._LINE_REFUSED_REDEFER_SECONDS,
        )
        await self._publish_block(
            job,
            block_reason,
            rules=rules,
            retry_after_seconds=self._LINE_REFUSED_REDEFER_SECONDS,
        )
        await self._redefer_before_intent_resolution(
            job,
            reason=job_reason,
            delay_seconds=self._LINE_REFUSED_REDEFER_SECONDS,
        )

    async def _attempt_settled_failed_unanswered(self, job: DialerJob) -> bool:
        """True when THIS attempt was settled failed-without-answer by the
        bridge (or by this worker's own absence CAS) and nothing will retry it.

        Conservative on purpose -- every condition must hold, and any error
        answers False (the caller then keeps its previous park/drop behaviour):

        * the attempt's call is ``failed`` and was never answered;
        * ``dialer_jobs.last_outcome IS NULL``: the call-end finalizer writes
          it on every branch it takes, so NULL proves no finalizer ran and
          therefore none scheduled a retry (no double-dial);
        * the job row still names THIS attempt (a later ``attempt_number``
          means a retry already exists) and is not in a finalizer/operator
          terminal state (completed, goal_achieved, non_retryable, cancelled,
          skipped, blocked);
        * no OTHER active job owns the lead: a campaign restart may already
          have re-queued it, and reviving this (terminal) job would dial the
          lead twice and trip ``uq_dialer_jobs_one_active_per_lead``.
        """
        try:
            from app.domain.services.dialer.job_states import ACTIVE_STATUSES

            async with self._acquire_db() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT c.status AS call_status,
                           c.answered_at AS answered_at,
                           j.status AS job_status,
                           j.last_outcome AS last_outcome,
                           COALESCE(j.attempt_number, 1) AS job_attempt
                      FROM calls AS c
                      JOIN dialer_jobs AS j
                        ON j.id = c.dialer_job_id
                       AND j.tenant_id = c.tenant_id
                       AND j.lead_id = c.lead_id
                     WHERE c.tenant_id = $1::uuid
                       AND c.dialer_job_id = $2::uuid
                       AND c.dialer_attempt_number = $3
                       AND c.direction = 'outbound'
                       AND NOT EXISTS (
                           SELECT 1
                             FROM dialer_jobs AS other_job
                            WHERE other_job.tenant_id = c.tenant_id
                              AND other_job.lead_id = c.lead_id
                              AND other_job.id <> j.id
                              AND other_job.status = ANY($4::text[])
                       )
                     LIMIT 1
                    """,
                    str(job.tenant_id),
                    str(job.job_id),
                    int(job.attempt_number),
                    list(ACTIVE_STATUSES),
                )
            if row is None:
                return False
            return (
                str(row["call_status"] or "").strip().lower() == "failed"
                and row["answered_at"] is None
                and row["last_outcome"] is None
                and int(row["job_attempt"]) == int(job.attempt_number)
                and str(row["job_status"] or "") in (*ACTIVE_STATUSES, "failed")
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dialer_settled_attempt_check_failed job=%s attempt=%s err=%s",
                job.job_id, job.attempt_number, exc,
            )
            return False

    async def _schedule_retry_durably(self, job: DialerJob, *, delay_seconds: int) -> bool:
        """Schedule attempt N+1 so Redis and Postgres agree BEFORE it dequeues.

        ``queue_service.schedule_retry`` bumps ``attempt_number`` on the Redis
        payload only. Left like that, ``dialer_jobs.attempt_number`` lags and
        every attempt-guarded settlement (``_record_ambiguous_attempt_state``,
        the bridge's ``_mark_durable_outbound_call_failed``) misses on attempt
        2 and later. So the bumped value is persisted FIRST (ownership-guarded,
        tenant/campaign/lead scoped); only then is the Redis payload scheduled.
        Returns False -- with ``job.attempt_number`` and Postgres restored --
        when either step failed.
        """
        prior = int(job.attempt_number)
        job.attempt_number = prior + 1
        try:
            await self._persist_job_attempt_number(job)
        except Exception as exc:  # noqa: BLE001
            job.attempt_number = prior
            logger.error(
                "dialer_attempt_persist_failed job=%s attempt=%s err=%s",
                job.job_id, prior + 1, exc,
            )
            return False
        job.attempt_number = prior  # schedule_retry owns the Redis increment
        scheduled = await self.queue_service.schedule_retry(
            job, delay_seconds=delay_seconds
        )
        if scheduled:
            return True
        job.attempt_number = prior
        try:
            async with self._acquire_db() as conn:
                await conn.execute(
                    """
                    UPDATE dialer_jobs
                       SET attempt_number = $1, updated_at = NOW()
                     WHERE id = $2::uuid
                       AND tenant_id = $3::uuid
                       AND campaign_id = $4::uuid
                       AND lead_id = $5::uuid
                       AND attempt_number = $6
                    """,
                    prior,
                    str(job.job_id),
                    str(job.tenant_id),
                    str(job.campaign_id),
                    str(job.lead_id),
                    prior + 1,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "dialer_attempt_persist_revert_failed job=%s attempt=%s err=%s",
                job.job_id, prior + 1, exc,
            )
        return False

    async def _bind_call_intent(
        self,
        job: DialerJob,
        intent: DialerCallIntent,
        provider_call_id: str,
    ) -> None:
        """Idempotently attach the bridge-confirmed provider to intent metadata."""
        provider = str(getattr(self, "_last_provider_name", "sip") or "sip")
        async with self._acquire_db() as conn:
            updated = await conn.execute(
                """
                UPDATE calls
                   SET external_call_uuid = COALESCE(external_call_uuid, $5),
                       provider_call_id = CASE
                           WHEN provider_call_id IS NULL
                             OR provider_call_id = external_call_uuid
                           THEN $5
                           ELSE provider_call_id
                       END,
                       provider = $6,
                       status = CASE
                           WHEN status IN ('queued', 'initiated') THEN 'dialing'
                           ELSE status
                       END,
                       updated_at = NOW()
                 WHERE id = $1::uuid
                   AND tenant_id = $2::uuid
                   AND dialer_job_id = $3::uuid
                   AND dialer_attempt_number = $4
                   AND direction = 'outbound'
                """,
                intent.call_id,
                str(job.tenant_id),
                str(job.job_id),
                int(job.attempt_number),
                str(provider_call_id),
                provider,
            )
            if updated != "UPDATE 1":
                raise RuntimeError(f"durable call bind affected {updated}")
            leg_updated = await conn.execute(
                """
                UPDATE call_legs
                   SET provider = $3,
                       provider_leg_id = COALESCE(provider_leg_id, $4),
                       status = CASE
                           WHEN status IN ('queued', 'initiated') THEN 'dialing'
                           ELSE status
                       END
                 WHERE id = $1::uuid
                   AND call_id = $2::uuid
                   AND leg_type = 'pstn_outbound'
                """,
                intent.leg_id,
                intent.call_id,
                provider,
                str(provider_call_id),
            )
            if leg_updated != "UPDATE 1":
                raise RuntimeError(f"durable call leg bind affected {leg_updated}")

    async def _mark_call_intent_not_originated(
        self,
        job: DialerJob,
        intent: DialerCallIntent,
        *,
        reason: str,
    ) -> bool:
        """Terminalize only when the row still proves no provider was bound."""
        try:
            async with self._acquire_db() as conn:
                updated = await conn.execute(
                    """
                    UPDATE calls
                       SET status = 'failed', outcome = 'failed',
                           failure_reason = COALESCE(failure_reason, $5),
                           ended_at = COALESCE(ended_at, NOW()), updated_at = NOW()
                     WHERE id = $1::uuid
                       AND tenant_id = $2::uuid
                       AND dialer_job_id = $3::uuid
                       AND dialer_attempt_number = $4
                       AND direction = 'outbound'
                       AND provider_call_id IS NULL
                       AND external_call_uuid IS NULL
                       AND status IN ('queued', 'initiated')
                    """,
                    intent.call_id,
                    str(job.tenant_id),
                    str(job.job_id),
                    int(job.attempt_number),
                    reason[:500],
                )
                return updated == "UPDATE 1"
        except Exception as exc:
            logger.error(
                "dialer_call_intent_terminalize_failed call=%s err=%s",
                intent.call_id,
                exc,
            )
            return False

    async def _release_intent_line_not_ready(
        self,
        job: DialerJob,
        intent: DialerCallIntent,
        *,
        reason: str = "line_not_ready",
    ) -> bool:
        """Release a never-sent intent the bridge refused for a not-ready line.

        Same absence proof as :meth:`_mark_call_intent_not_originated` (no
        provider identity, still ``queued``/``initiated``, tenant-scoped), but
        the row is written ``cancelled`` -- it never was a call, so it must not
        count as a failed one -- with ``failure_reason='line_not_ready'``, and:

        * ``dialer_attempt_number = NULL`` frees the ``(job, attempt)`` unique
          key, so the SAME attempt number gets a fresh intent when the
          re-deferred payload is dequeued (otherwise the replay finds this
          terminal row and the attempt is either dropped or burned);
        * ``terminal_settled_at`` marks it settled, so the telephony
          termination sweep (``terminal_settled_at IS NULL``) never runs the
          job finalizer on a call that was never placed.
        """
        # Whitelisted literal (never caller text): which outage released it.
        reason_literal = (
            "telephony_unavailable" if reason == "telephony_unavailable" else "line_not_ready"
        )
        try:
            async with self._acquire_db() as conn:
                updated = await conn.execute(
                    f"""
                    UPDATE calls
                       SET status = 'cancelled',
                           failure_reason = COALESCE(failure_reason, '{reason_literal}'),
                           ended_at = COALESCE(ended_at, NOW()),
                           terminal_settled_at = COALESCE(terminal_settled_at, NOW()),
                           dialer_attempt_number = NULL,
                           updated_at = NOW()
                     WHERE id = $1::uuid
                       AND tenant_id = $2::uuid
                       AND dialer_job_id = $3::uuid
                       AND dialer_attempt_number = $4
                       AND direction = 'outbound'
                       AND provider_call_id IS NULL
                       AND external_call_uuid IS NULL
                       AND status IN ('queued', 'initiated')
                    """,
                    intent.call_id,
                    str(job.tenant_id),
                    str(job.job_id),
                    int(job.attempt_number),
                )
                return updated == "UPDATE 1"
        except Exception as exc:
            logger.error(
                "dialer_line_not_ready_release_failed call=%s err=%s",
                intent.call_id,
                exc,
            )
            return False

    async def _attempt_call_settled(
        self,
        job: DialerJob,
        intent: DialerCallIntent,
    ) -> bool:
        """True when THIS attempt's call is already terminal / settled.

        A pre-answer failure can settle the call between the bridge's reply and
        the worker's own 'calling' / PROCESSING writes; those writes must then
        be skipped (the finalizer owns the disposition). Fail-open: when the
        row cannot be read the previous behaviour (write) is kept, because the
        writes need the same database.
        """
        try:
            from app.domain.services.call_status import TERMINAL_CALL_STATUSES

            async with self._acquire_db() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT status, terminal_settled_at
                      FROM calls
                     WHERE id = $1::uuid
                       AND tenant_id = $2::uuid
                       AND dialer_job_id = $3::uuid
                       AND dialer_attempt_number = $4
                       AND direction = 'outbound'
                    """,
                    intent.call_id,
                    str(job.tenant_id),
                    str(job.job_id),
                    int(job.attempt_number),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dialer_attempt_settled_check_failed job=%s attempt=%s err=%s",
                job.job_id, job.attempt_number, exc,
            )
            return False
        if row is None:
            return False
        return (
            str(row["status"] or "").strip().lower() in TERMINAL_CALL_STATUSES
            or row["terminal_settled_at"] is not None
        )

    async def _park_uncertain_origination(
        self,
        job: DialerJob,
        intent: DialerCallIntent,
    ) -> None:
        """Keep one ambiguous attempt owned; never turn it into attempt N+1."""
        job.call_id = intent.call_id
        same_attempt_redeferred = False
        try:
            # `_redefer_inflight` reads the crash-safe original payload and
            # stages it without incrementing attempt_number. This is a replay,
            # not a new call attempt: the bridge will return the existing
            # provider identity or atomically claim the still-unclaimed row.
            same_attempt_redeferred = bool(
                await self.queue_service._redefer_inflight(
                    str(job.job_id),
                    "origination_result_unknown",
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Keeping PROCESSING is safer than scheduling attempt N+1. The DB
            # and call reapers remain the last-resort proof-aware recovery.
            logger.error(
                "dialer_same_attempt_redefer_failed job=%s call=%s err=%s",
                job.job_id,
                intent.call_id,
                exc,
            )
        parked_status = (
            JobStatus.RETRY_SCHEDULED
            if same_attempt_redeferred
            else JobStatus.PROCESSING
        )
        job.status = parked_status
        job.processed_at = datetime.now(timezone.utc)
        state_recorded = await self._record_ambiguous_attempt_state(
            job,
            intent,
            parked_status=parked_status,
        )
        logger.error(
            "dialer_origination_result_unknown job=%s attempt=%s call=%s; "
            "same_attempt_redeferred=%s state_recorded=%s",
            job.job_id,
            job.attempt_number,
            intent.call_id,
            same_attempt_redeferred,
            state_recorded,
        )

    async def _record_ambiguous_attempt_state(
        self,
        job: DialerJob,
        intent: DialerCallIntent,
        *,
        parked_status: JobStatus,
    ) -> bool:
        """Keep an ambiguous attempt active only while its call is nonterminal.

        The bridge may have proved the provider leg absent and atomically
        failed call/job/lead while this worker was losing its HTTP response or
        being cancelled. A blind status write here would resurrect that job as
        ``retry_scheduled`` and put the lead back into ``calling``. The guarded
        transaction makes the durable call row the authority.
        """
        try:
            from app.domain.services.call_status import TERMINAL_CALL_STATUSES
            from app.domain.services.dialer.job_states import ACTIVE_STATUSES

            status_value = (
                parked_status.value
                if hasattr(parked_status, "value")
                else str(parked_status)
            )
            async with self._acquire_db() as conn:
                updated = await conn.execute(
                    """
                    UPDATE dialer_jobs AS job
                       SET status = $5,
                           call_id = $4::uuid,
                           processed_at = COALESCE(job.processed_at, NOW()),
                           last_error = 'origination_result_unknown',
                           updated_at = NOW()
                     WHERE job.id = $1::uuid
                       AND job.tenant_id = $2::uuid
                       AND job.lead_id = $3::uuid
                       AND job.attempt_number = $7
                       AND job.status = ANY($6::text[])
                       AND EXISTS (
                           SELECT 1
                             FROM calls AS call_row
                            WHERE call_row.id = $4::uuid
                              AND call_row.tenant_id = $2::uuid
                              AND call_row.dialer_job_id = $1::uuid
                              AND call_row.dialer_attempt_number = $7
                              AND call_row.lead_id = $3::uuid
                              AND call_row.direction = 'outbound'
                              AND call_row.status <> ALL($8::text[])
                       )
                    """,
                    str(job.job_id),
                    str(job.tenant_id),
                    str(job.lead_id),
                    intent.call_id,
                    status_value,
                    list(ACTIVE_STATUSES),
                    int(job.attempt_number),
                    list(TERMINAL_CALL_STATUSES),
                )
                if updated != "UPDATE 1":
                    return False
                lead_updated = await conn.execute(
                    """
                    UPDATE leads
                       SET status = 'calling', updated_at = NOW()
                     WHERE id = $1::uuid
                       AND tenant_id = $2::uuid
                       AND status IN ('pending', 'queued', 'calling')
                    """,
                    str(job.lead_id),
                    str(job.tenant_id),
                )
                if lead_updated not in {"UPDATE 0", "UPDATE 1"}:
                    raise RuntimeError(
                        f"ambiguous attempt lead update affected {lead_updated}"
                    )
            return True
        except Exception as exc:
            logger.error(
                "dialer_ambiguous_attempt_state_failed job=%s call=%s err=%s",
                job.job_id,
                intent.call_id,
                exc,
            )
            return False

    async def _redefer_cancelled_before_origination(self, job: DialerJob) -> None:
        """Return a cancelled pre-provider payload without minting an attempt."""
        await self._redefer_before_intent_resolution(
            job,
            reason="worker_cancelled_before_origination",
        )

    async def _redefer_before_intent_resolution(
        self,
        job: DialerJob,
        *,
        reason: str,
        delay_seconds: int | None = None,
    ) -> None:
        """Re-stage the same payload before a new attempt can be admitted."""
        delay = (DialerQueueService._pause_redefer_seconds() if delay_seconds is None
                 else max(1, int(delay_seconds)))
        due = datetime.now(timezone.utc) + timedelta(seconds=delay)
        try:
            # Commit the selected due time before publishing future work. This
            # guarded UPDATE cannot revive a terminal/newer-attempt owner.
            await self._update_job_status(
                job, JobStatus.RETRY_SCHEDULED, error=reason, reason=reason,
                scheduled_at=due,
            )
        except Exception as exc:
            logger.warning(
                "dialer_redefer_persist_failed job=%s attempt=%s error_type=%s",
                job.job_id, job.attempt_number, type(exc).__name__,
            )
            return  # The original inflight payload remains recoverable.
        job.status = JobStatus.RETRY_SCHEDULED
        try:
            moved = await self.queue_service._redefer_inflight(
                str(job.job_id), reason, delay_seconds=delay, scheduled_at=due,
            )
        except Exception as exc:
            logger.warning(
                "dialer_pre_intent_redefer_failed job=%s error_type=%s",
                job.job_id, type(exc).__name__,
            )
            return
        if not moved:
            return
        try:
            await self._update_lead_status(job, "pending")
        except Exception as exc:
            # Future work already has its durable owner. An ancillary lead
            # projection failure must not manufacture attempted-call retry N+1.
            logger.warning(
                "dialer_redefer_projection_failed job=%s attempt=%s error_type=%s",
                job.job_id, job.attempt_number, type(exc).__name__,
            )
        logger.info(
            "dialer_pre_intent_redeferred job=%s attempt=%s reason=%s",
            job.job_id,
            job.attempt_number,
            reason,
        )

    async def _update_lead_status(self, job: DialerJob, status: str) -> None:
        """Update exactly the lead owned by this tenant/campaign payload."""
        async with self._acquire_db() as conn:
            timestamp = (
                ""
                if status in ("pending", "calling")
                else ", last_called_at = NOW()"
            )
            updated = await conn.execute(
                f"""
                UPDATE leads
                   SET status = $1{timestamp}
                 WHERE id = $2::uuid
                   AND tenant_id = $3::uuid
                   AND campaign_id = $4::uuid
                """,
                status,
                str(job.lead_id),
                str(job.tenant_id),
                str(job.campaign_id),
            )
            if updated != "UPDATE 1":
                raise RuntimeError(
                    f"lead ownership update affected {updated} for job {job.job_id}"
                )

    async def _clear_lead_last_called(self, job: DialerJob) -> None:
        """Clear cooldown only on the fully owned lead row."""
        async with self._acquire_db() as conn:
            updated = await conn.execute(
                """
                UPDATE leads
                   SET last_called_at = NULL
                 WHERE id = $1::uuid
                   AND tenant_id = $2::uuid
                   AND campaign_id = $3::uuid
                """,
                str(job.lead_id),
                str(job.tenant_id),
                str(job.campaign_id),
            )
            if updated != "UPDATE 1":
                raise RuntimeError(
                    f"lead cooldown ownership update affected {updated} "
                    f"for job {job.job_id}"
                )

    async def _lead_has_live_or_answered_call(
        self, job: DialerJob, window_hours: float
    ) -> bool:
        """Tenant-scoped: was a call for this lead, inside the cooldown
        window, ever answered or is it still in flight (non-terminal)?

        Distinguishes a genuinely stale `last_called_at` (set at
        origination by a call that never actually connected — the case
        the caller's clear+redial workaround is for) from an active
        cooldown that must be respected. See the 2026-09-23 orphaning
        this backs: +16478471491 was answered 3.6 minutes earlier and got
        cleared and redialled anyway (job 620d9ac5/call 6e0e221b).
        window_hours<=0 means no cooldown is configured — nothing to check.
        """
        if window_hours <= 0:
            return False
        from app.domain.services.call_status import TERMINAL_CALL_STATUSES

        async with self._acquire_db() as conn:
            row = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM calls
                     WHERE tenant_id = $1::uuid
                       AND lead_id = $2::uuid
                       -- make_interval()'s `hours` param is int; float8->int
                       -- is only an assignment cast, so `make_interval(hours
                       -- => $3::float8)` throws UndefinedFunctionError on
                       -- every lead_cooldown hit (review of e8e93870,
                       -- verified against PG 16.1). Multiply an interval
                       -- literal instead, which accepts a float8 factor.
                       AND created_at > now() - ($3::float8 * interval '1 hour')
                       AND (
                             answered_at IS NOT NULL
                          OR NOT (status = ANY($4::text[]))
                           )
                )
                """,
                str(job.tenant_id),
                str(job.lead_id),
                float(window_hours),
                list(TERMINAL_CALL_STATUSES),
            )
        return bool(row)

    async def _lead_cooldown_remaining_seconds(
        self, job: DialerJob, window_hours: float
    ) -> Optional[float]:
        """Seconds left before the lead's cooldown actually clears.

        Anchored on the newest call matching the SAME predicate
        `_lead_has_live_or_answered_call` uses (answered, or still live,
        inside the window) rather than a fixed guess. Review non-blocking
        finding on e8e93870/620d9ac5, 2026-09-23: the genuine-cooldown
        branch retried every 300s via `queue_service.schedule_retry`, which
        bumps `job.attempt_number` on every cycle (Redis only, never
        persisted here) -- a 2h cooldown meant ~24 cycles, so the lead was
        finally redialled at attempt_number ~25 and
        `call_service._effective_attempt_number`'s max(db, live) made the
        first post-cooldown no-answer look terminal. Returns None (caller
        falls back to the full window) when no such call is found, e.g. the
        qualifying call aged out of the window between the two DB reads.
        """
        from app.domain.services.call_status import TERMINAL_CALL_STATUSES

        async with self._acquire_db() as conn:
            anchor = await conn.fetchval(
                """
                SELECT MAX(created_at) FROM calls
                 WHERE tenant_id = $1::uuid
                   AND lead_id = $2::uuid
                   AND created_at > now() - ($3::float8 * interval '1 hour')
                   AND (
                         answered_at IS NOT NULL
                      OR NOT (status = ANY($4::text[]))
                       )
                """,
                str(job.tenant_id),
                str(job.lead_id),
                float(window_hours),
                list(TERMINAL_CALL_STATUSES),
            )
        if anchor is None:
            return None
        elapsed = (datetime.now(timezone.utc) - anchor).total_seconds()
        return (window_hours * 3600.0) - elapsed

    async def _persist_job_attempt_number(self, job: DialerJob) -> None:
        """Persist an in-memory attempt_number bump to dialer_jobs.

        Without this, `_record_ambiguous_attempt_state`'s guarded UPDATE
        (which matches on `dialer_jobs.attempt_number = job.attempt_number`)
        can never match once a cooldown-triggered redial bumps the
        in-memory copy but leaves the DB row behind — the exact desync
        that left dialer_jobs.call_id NULL for jobs 620d9ac5/60ecd24e on
        2026-09-23 (state_recorded=False).
        """
        async with self._acquire_db() as conn:
            updated = await conn.execute(
                """
                UPDATE dialer_jobs
                   SET attempt_number = $1, updated_at = NOW()
                 WHERE id = $2::uuid
                   AND tenant_id = $3::uuid
                   AND campaign_id = $4::uuid
                   AND lead_id = $5::uuid
                   -- Never move the counter backwards: a stale payload
                   -- replaying an older attempt must not undo a newer one.
                   AND COALESCE(attempt_number, 0) <= $1
                """,
                int(job.attempt_number),
                str(job.job_id),
                str(job.tenant_id),
                str(job.campaign_id),
                str(job.lead_id),
            )
            if updated != "UPDATE 1":
                raise RuntimeError(
                    f"dialer job attempt_number persist affected {updated} "
                    f"for job {job.job_id}"
                )

    async def _update_job_status(
        self,
        job: DialerJob,
        status: JobStatus,
        call_id: Optional[str] = None,
        error: Optional[str] = None,
        reason: Optional[str] = None,
        scheduled_at: Optional[datetime] = None,
    ) -> None:
        """Update job status; scheduled deferrals require a current active owner."""
        try:
            # Build update query dynamically or use simple execution
            status_val = status.value if hasattr(status, "value") else status

            async with self._acquire_db() as conn:
                db = Database(conn)
                data = {"status": status_val, "updated_at": datetime.now(timezone.utc)}
                if scheduled_at is not None:
                    data["scheduled_at"] = scheduled_at
                if call_id:
                    data["call_id"] = call_id
                    data["processed_at"] = datetime.now(timezone.utc)
                    # A successful originate supersedes any earlier failure on
                    # this job — clear the stale reason so the Call Issues
                    # panel doesn't show a phantom problem for a now-live call.
                    data["failure_reason"] = None
                    data["last_error"] = None
                if error:
                    data["last_error"] = error
                # Persist the skip/block reason too (was previously dropped),
                # so the Call Issues panel can explain WHY a job didn't dial
                # — campaign_stopped, call_guard_blocked/throttled/queued,
                # max_concurrent_calls_reached, outside_time_window, etc.
                if reason:
                    data["failure_reason"] = reason
                    data["last_error"] = data.get("last_error") or reason

                if status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.GOAL_ACHIEVED]:
                    data["completed_at"] = datetime.now(timezone.utc)

                rows = await db.update(
                    "dialer_jobs",
                    data,
                    (
                        "id = $1::uuid AND tenant_id = $2::uuid "
                        "AND campaign_id = $3::uuid AND lead_id = $4::uuid"
                        + (" AND status IN ('pending','queued','retry_scheduled','processing','calling')"
                           " AND attempt_number <= $5" if scheduled_at is not None else "")
                    ),
                    [
                        str(job.job_id),
                        str(job.tenant_id),
                        str(job.campaign_id),
                        str(job.lead_id),
                        *([int(job.attempt_number)] if scheduled_at is not None else []),
                    ],
                )
                if len(rows) != 1:
                    raise RuntimeError(
                        f"dialer job ownership update affected {len(rows)} "
                        f"rows for {job.job_id}"
                    )
        except Exception as exc:
            logger.error("Failed to update job status: %s", exc)
            raise

    async def _record_job_failure_classification(
        self,
        *,
        job: DialerJob,
        category: str,
        reason: str,
    ) -> None:
        """Persist the Track 2 failure classification on the job row.

        The columns are added by the alembic migration that ships with
        Track 2. The UPDATE is wrapped in a try/except so a missing
        column (mid-deploy, schema drift) doesn't make the failure path
        itself fail — it just logs and moves on.
        """
        try:
            async with self._acquire_db() as conn:
                updated = await conn.execute(
                    """
                    UPDATE dialer_jobs
                    SET failure_category = $2,
                        failure_reason = $3,
                        updated_at = NOW()
                    WHERE id = $1::uuid
                      AND tenant_id = $4::uuid
                      AND campaign_id = $5::uuid
                      AND lead_id = $6::uuid
                    """,
                    str(job.job_id),
                    category,
                    reason,
                    str(job.tenant_id),
                    str(job.campaign_id),
                    str(job.lead_id),
                )
                if updated != "UPDATE 1":
                    raise RuntimeError(
                        f"job classification ownership update affected {updated}"
                    )
        except Exception as exc:
            logger.warning(
                "could not write failure_category/reason for job=%s "
                "(missing columns? not yet migrated?): %s",
                job.job_id,
                exc,
            )

    async def _publish_call_event(
        self,
        call_id: str,
        job: DialerJob,
        talklee_call_id: str,
        provider_call_id: str,
    ) -> None:
        """Publish call event for voice worker to pick up."""
        try:
            event = {
                "event": "call_initiated",
                "call_id": call_id,
                "talklee_call_id": talklee_call_id,
                "provider_call_id": provider_call_id,
                "job_id": job.job_id,
                "campaign_id": job.campaign_id,
                "lead_id": job.lead_id,
                "tenant_id": job.tenant_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            await self._redis.publish("voice:calls:active", json.dumps(event))
            logger.debug(
                "Published call event internal=%s provider=%s talklee=%s",
                call_id,
                provider_call_id,
                talklee_call_id,
            )

        except Exception as e:
            logger.error(f"Failed to publish call event: {e}")

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down Dialer Worker...")
        self.running = False

        # Close connections
        await self.queue_service.close()
        if self._redis:
            await self._redis.aclose()

        if self._db_pool:
            await close_db_pool()

        # Log final stats
        logger.info(
            f"Dialer Worker shutdown complete. "
            f"Processed: {self._jobs_processed}, Failed: {self._jobs_failed}"
        )

    def get_stats(self) -> dict:
        """Get worker statistics."""
        return {
            "running": self.running,
            "jobs_processed": self._jobs_processed,
            "jobs_failed": self._jobs_failed,
            "active_calls": {
                tenant_id: count for tenant_id, count in self.rules_engine._active_calls.items()
            },
        }

    # Redis key the health API (/api/v1/healthz/workers) watches to tell a
    # live worker from a dead/hung one. TTL is set > 2x the heartbeat interval
    # so a single slow tick never makes a healthy worker look stale, but a
    # dead/hung worker's key expires and the probe flips to 503.
    HEARTBEAT_INTERVAL = 60
    HEARTBEAT_TTL = 180
    HEARTBEAT_REDIS_KEY = "dialer:heartbeat_ts"

    async def _heartbeat(self) -> None:
        """Liveness heartbeat loop — the single place this worker proves it is
        alive to both Redis (for the health API) and systemd (watchdog).

        Layers:
          1. Redis: ``SETEX dialer:heartbeat_ts <ttl> <epoch>`` each tick so
             ``/api/v1/healthz/workers`` can report age/health. Wrapped in
             try/except — Redis being down must never kill the heartbeat loop
             (and thus stop petting the systemd watchdog and trigger a restart
             for a Redis outage that the worker itself survives).
          2. systemd: ``READY=1`` once at entry (satisfies ``Type=notify`` so
             startup completes) then ``WATCHDOG=1`` every tick. Because this
             coroutine shares the worker's event loop, a wedged loop stops
             petting the watchdog and systemd (``WatchdogSec=``) restarts it.
             No-op when ``$NOTIFY_SOCKET`` is unset (dev/local).
        """
        from app.core.sd_notify import SystemdNotifier

        notifier = SystemdNotifier()
        # READY=1 once, at loop entry, on the normal startup path.
        notifier.notify_ready()

        while self.running:
            logger.info(
                f"heartbeat: jobs_processed={self._jobs_processed}, "
                f"jobs_failed={self._jobs_failed}"
            )

            # Layer 1 — Redis heartbeat for the health API. Best-effort.
            try:
                if self._redis is not None:
                    await self._redis.setex(
                        self.HEARTBEAT_REDIS_KEY,
                        self.HEARTBEAT_TTL,
                        str(time.time()),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("heartbeat: redis write failed: %s", exc)

            # Layer 2 — pet the systemd watchdog from the live loop.
            notifier.notify_watchdog()

            await asyncio.sleep(self.HEARTBEAT_INTERVAL)


async def main():
    """Entry point for running dialer worker as separate process."""
    # Setup simple logging first
    logging.basicConfig(level=logging.INFO)

    worker = DialerWorker()

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        worker.running = False

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    finally:
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

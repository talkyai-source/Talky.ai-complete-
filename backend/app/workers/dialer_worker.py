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
        # Set only when the opt-in TESTING schedule override let this job past
        # the calling-window gate; read by `_create_call_record` so the call
        # is stamped as placed-under-override and stays auditable afterwards.
        self._schedule_override = None

        try:
            campaign_status = await self._get_campaign_status(job.campaign_id)
            if campaign_status not in {"running", "active"}:
                reason = f"campaign_not_runnable:{campaign_status or 'missing'}"
                logger.info(
                    "Skipping job %s because campaign %s is %s",
                    job.job_id,
                    job.campaign_id,
                    campaign_status or "missing",
                )
                await self._publish_block(job, reason)
                await self.queue_service.mark_skipped(job.job_id, reason="campaign_stopped")
                await self._update_job_status(job.job_id, JobStatus.SKIPPED, error=reason)
                return

            # 0.5 Minutes quota gate. Stop originating once the tenant has burned
            # its plan minutes for the month. Minute tracking was previously
            # display-only, so tenants could overrun the plan with no cap.
            if await self._tenant_minutes_exhausted(job.tenant_id):
                logger.info(
                    "Skipping job %s — tenant %s is out of plan minutes",
                    job.job_id,
                    job.tenant_id,
                )
                await self._publish_block(job, "out_of_minutes")
                await self._emit_out_of_minutes_event(job)
                await self.queue_service.mark_skipped(job.job_id, reason="out_of_minutes")
                await self._update_job_status(job.job_id, JobStatus.SKIPPED, error="out_of_minutes")
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

            # 3. Check scheduling rules. Gate concurrency on the telephony
            # bridge's authoritative live-call count (global_concurrency Redis
            # ledger), NOT the dialer's in-memory counter — the latter had no
            # decrement signal, so it leaked monotonically to the cap and
            # wedged every outbound call (the 10/10 outage). A None count
            # (Redis unavailable) falls through to the in-memory fallback.
            active_override = None
            try:
                from app.domain.services.global_concurrency import current_count

                if self._redis is not None:
                    active_override = await current_count(self._redis)
            except Exception as exc:
                logger.debug("dialer_active_count_failed err=%s", exc)
                active_override = None

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
                    # due to a now-fixed bug.  Clear it and re-enqueue immediately (bypassing
                    # the scheduled-set → 60-second wait round-trip).
                    logger.info(
                        f"Clearing stale last_called_at for lead {job.lead_id} "
                        f"(was set at origination, not at answer)"
                    )
                    await self._clear_lead_last_called(job.lead_id)
                    # Re-enqueue directly into the tenant queue for immediate pickup
                    job.attempt_number += 1
                    await self.queue_service.enqueue_job(job)
                    await self._publish_reason(job, blocked)
                    await self._update_job_status(job.job_id, JobStatus.SKIPPED, reason=reason)
                    return
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
                await self.queue_service.schedule_retry(job, delay_seconds=delay)
                await self._update_job_status(job.job_id, JobStatus.SKIPPED, reason=reason)
                return

            # 4. Concurrency is now tracked authoritatively by the telephony
            # bridge's global_concurrency ledger (acquired on answer, released
            # on hangup, self-healed by the watchdog reconcile) and read above
            # via active_calls_override. The dialer no longer feeds its own
            # in-memory counter: it had no decrement signal, so it leaked to
            # the cap and wedged all calls. (register_call_start/end remain on
            # the rules engine for unit tests.)

            # Re-check campaign status immediately before originating. The
            # validation above (rules / scheduling / guard) can take 100-200ms,
            # and the user may hit Stop in that window. Without this, a job that
            # passed the top-of-function check still originates into a stopped
            # campaign — a call stuck "dialing" that the stop-sweep (already run)
            # never sees.
            campaign_status = await self._get_campaign_status(job.campaign_id)
            if campaign_status not in {"running", "active"}:
                logger.info(
                    "Campaign %s went %s during job %s validation — skipping originate",
                    job.campaign_id,
                    campaign_status or "missing",
                    job.job_id,
                )
                await self._publish_block(job, "campaign_stopped_before_originate")
                await self.queue_service.mark_skipped(job.job_id, reason="campaign_stopped")
                await self._update_job_status(
                    job.job_id,
                    JobStatus.SKIPPED,
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
                    await self.queue_service.schedule_retry(job, delay_seconds=5)
                    await self._update_job_status(
                        job.job_id,
                        JobStatus.RETRY_SCHEDULED,
                        reason="batch_capacity",
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
                    await self.queue_service.schedule_retry(job, delay_seconds=wait)
                    await self._update_job_status(
                        job.job_id,
                        JobStatus.RETRY_SCHEDULED,
                        reason="call_gap",
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
                await self.queue_service.schedule_retry(job, delay_seconds=_tenant_wait)
                await self._update_job_status(
                    job.job_id,
                    JobStatus.RETRY_SCHEDULED,
                    reason="tenant_gap",
                )
                return

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

                if guard_decision == "block":
                    # Block the call - mark job as blocked, don't retry
                    await self._publish_block(job, "call_guard_blocked", rules=rules)
                    await self._update_job_status(
                        job.job_id, JobStatus.BLOCKED, reason="call_guard_blocked"
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
                    await self.queue_service.schedule_retry(job, delay_seconds=60)
                    # RETRY_SCHEDULED, not SKIPPED. `schedule_retry` just put a
                    # LIVE copy of this job in Redis's scheduled set — it is
                    # coming back in 60s. SKIPPED is a TERMINAL status and is
                    # therefore outside the partial unique index
                    # uq_dialer_jobs_one_active_per_lead, so for that whole
                    # window the lead looks like it has no active job: a
                    # campaign restart or a "call this list" re-entry creates a
                    # SECOND job, and both eventually dial the same person
                    # minutes apart. Every sibling gate here (batch capacity,
                    # call gap, tenant gap, voice pipeline) already writes
                    # RETRY_SCHEDULED; these two call-guard branches were the
                    # odd ones out.
                    await self._update_job_status(
                        job.job_id,
                        JobStatus.RETRY_SCHEDULED,
                        reason="call_guard_throttled",
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
                    await self.queue_service.schedule_retry(job, delay_seconds=30)
                    # See the throttle branch above — SKIPPED here would drop
                    # the lead out of the active-job dedup index while a live
                    # retry is still pending in Redis, allowing a duplicate
                    # job (and so a duplicate call) for the same person.
                    await self._update_job_status(
                        job.job_id,
                        JobStatus.RETRY_SCHEDULED,
                        reason="call_guard_queued",
                    )
                    return

            try:
                # 5. Initiate the call via the provider/PBX.
                provider_call_id = await self._make_call(job, rules)

                # Voice pipeline temporarily unavailable (TTS/STT warmup
                # failed). Reschedule with a short delay and DON'T consume
                # the job's retry budget — this is an infra issue, not a
                # bad lead. Without this guard, a 30-second outage burns
                # every job's max_retries and marks them all FAILED.
                if provider_call_id == self._PIPELINE_UNAVAILABLE:
                    # Nothing dialed — give the tenant slot back so the other
                    # campaign isn't paced against a failed attempt.
                    await release_tenant_dial_slot(self._redis, job.tenant_id)
                    await self._publish_block(
                        job,
                        "voice_pipeline_unavailable",
                        rules=rules,
                        retry_after_seconds=60,
                    )
                    await self._update_lead_status(job.lead_id, "pending")
                    await self.queue_service.schedule_retry(job, delay_seconds=60)
                    await self._update_job_status(
                        job.job_id,
                        JobStatus.RETRY_SCHEDULED,
                        reason="voice_pipeline_unavailable",
                    )
                    return

                if provider_call_id:
                    # 6. Create tracked DB records using an internal UUID plus provider call ID.
                    internal_call_id, talklee_call_id, leg_id = await self._create_call_record(
                        job, provider_call_id
                    )

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

                    # 7. Update lead status to 'calling'
                    await self._update_lead_status(job.lead_id, "calling")

                    # 8. Update job with the internal DB call UUID
                    job.call_id = internal_call_id
                    job.status = JobStatus.PROCESSING
                    job.processed_at = datetime.now(timezone.utc)
                    await self._update_job_status(
                        job.job_id, JobStatus.PROCESSING, call_id=internal_call_id
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
                    raise Exception("No call_id returned from telephony provider")

            finally:
                # Unregister call (will be re-registered when answered if needed)
                # For now, we track at initiation level
                pass

        except Exception as e:
            # Nothing dialed — release the tenant pacing slot (best-effort)
            # so a failed originate doesn't burn the whole gap window.
            try:
                from app.domain.services.dialer.global_pacing import (
                    release_tenant_dial_slot,
                )

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
                classify_telephony_response,
                legacy_decision,
                parse_bridge_error,
                smart_decision,
                use_smart_policy,
            )

            if use_smart_policy():
                code, msg = parse_bridge_error(self._last_bridge_body)
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

            # Persist category/reason on the job + lead. Best-effort —
            # never let a logging/DB hiccup mask the original failure.
            try:
                await self._record_job_failure_classification(
                    job_id=job.job_id,
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
                await self._update_lead_status(job.lead_id, "pending")
                await self.queue_service.schedule_retry(
                    job,
                    delay_seconds=decision.delay_seconds,
                )
                await self._update_job_status(
                    job.job_id,
                    JobStatus.RETRY_SCHEDULED,
                    error=str(e),
                )
            else:
                # Either the category disallows retries (INVALID_INPUT)
                # or the per-category attempt budget is exhausted.
                await self._update_lead_status(job.lead_id, "failed")
                await self.queue_service.mark_failed(job.job_id, str(e))
                await self._update_job_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error=str(e),
                )

    # Sentinel returned by _make_call when the bridge says the voice
    # pipeline is not ready (HTTP 503). Distinct from None ("real failure")
    # so process_job can apply infrastructure-aware backoff without
    # consuming the job's retry budget.
    _PIPELINE_UNAVAILABLE = "__pipeline_unavailable__"

    # Set by `_make_call` whenever a non-success response from the
    # telephony bridge would otherwise return None. The except-clause
    # in `process_job` reads these so the retry classifier (Track 2)
    # can map them to a FailureCategory and choose a sensible delay.
    # Reset on every job to avoid leaking state across attempts.
    _last_bridge_http_status: Optional[int] = None
    _last_bridge_body: Optional[str] = None

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

    async def _make_call(self, job: DialerJob, rules: CallingRules) -> Optional[str]:
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
                    if resp.status == 503:
                        body = await resp.text()
                        logger.warning(
                            "Voice pipeline unavailable (503) — will retry "
                            "without consuming attempt budget. dest=%s body=%s",
                            job.phone_number,
                            body[:300],
                        )
                        return self._PIPELINE_UNAVAILABLE
                    if resp.status not in (200, 202):
                        body = await resp.text()
                        # Stash for the classifier in process_job's except branch.
                        self._last_bridge_http_status = resp.status
                        self._last_bridge_body = body
                        logger.error(
                            "Telephony bridge rejected call: status=%s body=%s dest=%s",
                            resp.status,
                            body[:200],
                            job.phone_number,
                        )
                        return None

                    data = await resp.json()
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

        except Exception as e:
            logger.error("Originate error for %s: %s", job.phone_number, e)
            return None

    async def _evaluate_call_guard(self, job: DialerJob, rules: CallingRules) -> str:
        """
        Evaluate call through CallGuard security checks.

        Returns:
            "allow" | "block" | "throttle" | "queue"
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

            return guard_result.decision.value

        except Exception as e:
            logger.error(f"CallGuard evaluation failed for job {job.job_id}: {e}", exc_info=True)
            # Fail-closed: errors in guard = block call
            return "block"

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
        """Reap zombies each tick, best-effort:
        * stuck dialer JOBS (hung originate) → marked failed, lead freed;
        * stuck CALL rows (non-terminal past the max call lifetime) → closed
          as ended, so they leave the live-calls panel AND free their
          batch-dispatch slot (a stale 'dialing' row must never wedge the
          batch gate);
        * ORPHANED retry_scheduled jobs (past any legitimate retry delay) →
          marked failed, freeing the lead's active-job slot. Without this a
          job whose Redis schedule entry was lost holds that slot forever and
          the lead can never be dialled again — found in production wedged
          for 21 days. Logic lives in dialer.stuck_job_reaper."""
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
        """True when the tenant has used >= its plan's monthly minute allocation.

        Delegates to the shared ``minutes_quota`` helper — the single source
        of truth also used by the start-campaign endpoint and the frontend
        quota display — so the per-job skip and the start-block can never
        disagree. Returns False (do NOT block) on any error: a quota lookup
        failure must never wedge the dialer.
        """
        try:
            from app.domain.services.minutes_quota import compute_minutes_status

            async with self._acquire_db() as conn:
                status = await compute_minutes_status(conn, tenant_id)
                return status.exhausted
        except Exception as e:  # noqa: BLE001
            logger.warning("minutes quota check failed for tenant %s: %s", tenant_id, e)
            return False

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

    async def _get_campaign_status(self, campaign_id: str) -> Optional[str]:
        """Return campaign status so dequeued jobs can be revalidated before originate."""
        try:
            async with self._acquire_db() as conn:
                return await conn.fetchval(
                    "SELECT status FROM campaigns WHERE id = $1 AND direction='outbound'",
                    campaign_id,
                )
        except Exception as e:
            logger.error(f"Failed to get campaign status for {campaign_id}: {e}")
            return None

    async def _get_tenant_rules(self, tenant_id: str) -> CallingRules:
        """Get calling rules for a tenant."""
        try:
            async with self._acquire_db() as conn:
                row = await conn.fetchrow(
                    "SELECT calling_rules FROM tenants WHERE id = $1", tenant_id
                )
                if row and row["calling_rules"]:
                    # asyncpg returns JSON/JSONB as string or dict depending on driver config
                    # assuming standard driver config (string/dict)
                    rules_data = row["calling_rules"]
                    if isinstance(rules_data, str):
                        rules_data = json.loads(rules_data)
                    return CallingRules.from_dict(rules_data)

        except Exception as e:
            logger.warning(f"Failed to get tenant rules, using defaults: {e}")

        return CallingRules.default()

    async def _get_campaign_calling_config(self, campaign_id: str) -> Optional[dict]:
        """Load a campaign's per-campaign calling schedule (timezone, window,
        days, ignore_schedule override). Returns None when unset so the
        worker falls back to tenant defaults."""
        try:
            async with self._acquire_db() as conn:
                cfg = await conn.fetchval(
                    "SELECT calling_config FROM campaigns WHERE id = $1",
                    campaign_id,
                )
            if cfg:
                if isinstance(cfg, str):
                    cfg = json.loads(cfg)
                if isinstance(cfg, dict):
                    return cfg
        except Exception as e:
            logger.warning(f"Failed to load campaign calling_config for {campaign_id}: {e}")
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

        Anti-wedge safety net: a call whose status is non-terminal but that is
        OLDER than the max plausible call lifetime is a zombie (originate hung,
        an ARI hangup event was lost, a worker died mid-call) — it is NOT really
        in flight. We stop counting it after DIALER_INFLIGHT_MAX_AGE_S so a
        handful of stuck 'dialing' rows can never permanently wedge the batch
        gate. The stuck-call reaper (run each tick) then closes them for real.

        Fail-open: on a transient DB error return 0 so a hiccup never wedges
        dispatch (the concurrency guard remains as a backstop).
        """
        # Ring window (30s) + hard call ceiling (8m) + buffer. A non-terminal
        # call older than this cannot still be a live conversation.
        max_age = int(os.getenv("DIALER_INFLIGHT_MAX_AGE_S", "600"))
        try:
            async with self._acquire_db() as conn:
                val = await conn.fetchval(
                    """
                    SELECT count(*) FROM calls
                     WHERE campaign_id = $1
                       AND status IN (
                           'dialing', 'ringing', 'answered', 'in_call', 'initiated'
                       )
                       AND created_at > now() - make_interval(secs => $2::int)
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
        """Get the last time a lead was called."""
        try:
            async with self._acquire_db() as conn:
                val = await conn.fetchval("SELECT last_called_at FROM leads WHERE id = $1", lead_id)
                return val  # asyncpg returns appropriate datetime object

        except Exception as e:
            logger.warning(f"Failed to get lead last_called_at: {e}")

        return None

    async def _get_lead_timezone(self, lead_id: str) -> Optional[str]:
        """Read the customer-supplied per-lead IANA timezone.

        ``leads.timezone`` (migration 0020, captured by CSV import and the
        manual contact form) is the authoritative statement of where the
        prospect actually is, so the calling-window check prefers it over
        the phone-number-derived guess.

        Best-effort by design: any failure — pool down, or the column
        absent on a database that hasn't taken 0020 — returns None, and the
        caller falls back to the derived zone and then the campaign
        timezone. A timezone lookup must never drop a dial by exception.
        """
        try:
            async with self._acquire_db() as conn:
                return await conn.fetchval("SELECT timezone FROM leads WHERE id = $1", lead_id)
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
                    "AND NOT is_test",
                    lead_id,
                )
                return int(val or 0)
        except Exception as e:
            logger.warning(f"Failed to count lead attempts today: {e}")
        return 0

    async def _create_call_record(
        self, job: DialerJob, provider_call_id: str
    ) -> tuple[str, str, str]:
        """
        Create a call record in the database with separate internal and provider IDs.

        Returns:
            tuple: (internal_call_id, talklee_call_id, leg_id)
        """
        talklee_call_id = generate_talklee_call_id()
        internal_call_id = str(uuid.uuid4())

        # AUDIT: when the explicit TESTING override let this call past the
        # calling-window gate, stamp that fact on the call itself so it is
        # provable afterwards which calls went out outside the configured
        # hours, under which switch, and what would otherwise have blocked
        # them. Uses existing JSONB columns (call_legs.metadata,
        # call_events.event_data) — no schema change.
        override_audit: dict = {}
        if self._schedule_override:
            try:
                from app.domain.services.dialer.testing_override import (
                    override_audit_payload,
                )

                override_audit = override_audit_payload(
                    source=self._schedule_override.get("source", "unknown"),
                    blocked_reason=self._schedule_override.get("blocked_reason"),
                    schedule=self._schedule_override.get("schedule"),
                )
            except Exception as exc:  # noqa: BLE001 — never fail a live call
                logger.warning("schedule_override audit payload failed: %s", exc)
                override_audit = {"schedule_override": True}

        try:
            async with self._acquire_db() as conn:
                await conn.execute(
                    """
                    INSERT INTO calls (
                        id, tenant_id, campaign_id, lead_id, phone_number,
                        external_call_uuid, status, talklee_call_id,
                        dialer_job_id, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                    """,
                    internal_call_id,
                    job.tenant_id,
                    job.campaign_id,
                    job.lead_id,
                    job.phone_number,
                    provider_call_id,
                    "initiated",
                    talklee_call_id,
                    # 2026-07-13 fix: this was never written, so the pooled
                    # teardown path (call_service._handle_call_status_pooled)
                    # could never resolve dialer_job_id from the calls row —
                    # leads/dialer_jobs were never finalized on the
                    # production (pooled) teardown path. See call_service.py.
                    job.job_id,
                )
                logger.debug(
                    "Created call record internal=%s provider=%s talklee=%s",
                    internal_call_id,
                    provider_call_id,
                    talklee_call_id,
                )

                leg_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO call_legs (
                        id, call_id, talklee_call_id, leg_type, direction,
                        provider, provider_leg_id, to_number, status, metadata, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                    """,
                    leg_id,
                    internal_call_id,
                    talklee_call_id,
                    "pstn_outbound",
                    "outbound",
                    getattr(self, "_last_provider_name", "sip"),
                    provider_call_id,
                    job.phone_number,
                    "initiated",
                    json.dumps(
                        {
                            "job_id": job.job_id,
                            "campaign_id": job.campaign_id,
                            "provider_call_id": provider_call_id,
                            **override_audit,
                        }
                    ),
                )

                await conn.execute(
                    """
                    INSERT INTO call_events (
                        call_id, talklee_call_id, leg_id, event_type, source,
                        event_data, new_state, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                    """,
                    internal_call_id,
                    talklee_call_id,
                    leg_id,
                    "leg_started",
                    "dialer_worker",
                    json.dumps(
                        {
                            "leg_type": "pstn_outbound",
                            "provider": getattr(self, "_last_provider_name", "sip"),
                            "provider_call_id": provider_call_id,
                            **override_audit,
                        }
                    ),
                    "initiated",
                )

                if override_audit:
                    # A dedicated, greppable audit event — one row per call
                    # placed under the testing override. `event_type` is
                    # varchar(30); "schedule_override" is 17 chars.
                    await conn.execute(
                        """
                        INSERT INTO call_events (
                            call_id, talklee_call_id, leg_id, event_type, source,
                            event_data, created_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
                        """,
                        internal_call_id,
                        talklee_call_id,
                        leg_id,
                        "schedule_override",
                        "dialer_worker",
                        json.dumps(override_audit),
                    )

                return internal_call_id, talklee_call_id, leg_id

        except Exception as e:
            logger.error(f"Failed to create call record: {e}")
            return internal_call_id, talklee_call_id, ""

    async def _update_lead_status(self, lead_id: str, status: str) -> None:
        """Update lead status in database."""
        try:
            async with self._acquire_db() as conn:
                if status in ("pending", "calling"):
                    # "pending"  — resetting for retry, keep last_called_at unchanged
                    # "calling"  — origination only, call not yet answered; setting
                    #              last_called_at here would poison the per-lead cooldown
                    #              and block all retries for 2 hours even if the call
                    #              never connected.  last_called_at is set on terminal
                    #              states (completed / failed) instead.
                    await conn.execute(
                        "UPDATE leads SET status = $1 WHERE id = $2", status, lead_id
                    )
                else:
                    # Terminal / completion states (failed, completed, etc.) —
                    # record the timestamp so per-lead cooldown is enforced correctly.
                    await conn.execute(
                        """
                        UPDATE leads SET status = $1, last_called_at = NOW()
                        WHERE id = $2
                        """,
                        status,
                        lead_id,
                    )
        except Exception as e:
            logger.error(f"Failed to update lead status: {e}")

    async def _clear_lead_last_called(self, lead_id: str) -> None:
        """Clear last_called_at so a stale origination-time timestamp cannot block retries."""
        try:
            async with self._acquire_db() as conn:
                await conn.execute("UPDATE leads SET last_called_at = NULL WHERE id = $1", lead_id)
        except Exception as e:
            logger.error(f"Failed to clear lead last_called_at: {e}")

    async def _update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        call_id: Optional[str] = None,
        error: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Update job status in database."""
        try:
            # Build update query dynamically or use simple execution
            status_val = status.value if hasattr(status, "value") else status

            async with self._acquire_db() as conn:
                db = Database(conn)
                data = {"status": status_val, "updated_at": datetime.now(timezone.utc)}
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

                await db.update("dialer_jobs", data, "id = $1", [job_id])

        except Exception as e:
            logger.error(f"Failed to update job status: {e}")

    async def _record_job_failure_classification(
        self,
        *,
        job_id: str,
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
                await conn.execute(
                    """
                    UPDATE dialer_jobs
                    SET failure_category = $2,
                        failure_reason = $3,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    job_id,
                    category,
                    reason,
                )
        except Exception as exc:
            logger.warning(
                "could not write failure_category/reason for job=%s "
                "(missing columns? not yet migrated?): %s",
                job_id,
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

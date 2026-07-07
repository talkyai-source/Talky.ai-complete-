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

logger = logging.getLogger(__name__)

# Configure logging for worker
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
        
        while self.running:
            try:
                # 1. Check for due scheduled jobs (every 10s)
                now_utc = datetime.now(timezone.utc)
                if self._last_scheduled_check.tzinfo is None:
                    self._last_scheduled_check = self._last_scheduled_check.replace(tzinfo=timezone.utc)
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
                job = await self.queue_service.dequeue_job(
                    tenant_ids=tenant_ids,
                    timeout=5
                )
                
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
                    logger.critical("Too many consecutive errors, stopping worker")
                    break
                
                await asyncio.sleep(min(5 * consecutive_errors, 60))
        
        await self.shutdown()
    
    async def process_job(self, job: DialerJob) -> None:
        """
        Process a single dialer job.
        
        Steps:
        1. Get tenant calling rules
        2. Check if we can make call now
        3. Initiate the call
        4. Create call record in database
        """
        logger.info(f"Processing job {job.job_id} for lead {job.lead_id} (attempt {job.attempt_number})")

        # Reset bridge-response state captured by `_make_call` — must be
        # cleared per-job so a previous failure doesn't classify the
        # next one.
        self._last_bridge_http_status = None
        self._last_bridge_body = None

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
                await self.queue_service.mark_skipped(job.job_id, reason="campaign_stopped")
                await self._update_job_status(job.job_id, JobStatus.SKIPPED, error=reason)
                return

            # 0.5 Minutes quota gate. Stop originating once the tenant has burned
            # its plan minutes for the month. Minute tracking was previously
            # display-only, so tenants could overrun the plan with no cap.
            if await self._tenant_minutes_exhausted(job.tenant_id):
                logger.info(
                    "Skipping job %s — tenant %s is out of plan minutes",
                    job.job_id, job.tenant_id,
                )
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
                effective_rules, schedule_ignored,
            )
            rules = effective_rules(tenant_rules, campaign_cfg)
            ignore_schedule = schedule_ignored(campaign_cfg)
            
            # 2. Get lead info for cooldown check
            lead_last_called = await self._get_lead_last_called(job.lead_id)
            
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
                enforce_window=not ignore_schedule,
            )
            
            if not can_call:
                logger.info(f"Cannot call now: {reason}")

                # Calculate delay until next window or retry
                if "time_window" in reason or "day" in reason.lower():
                    delay = self.rules_engine.get_delay_until_next_window(rules)
                    logger.info(
                        f"Outside calling window (tz={rules.timezone}, "
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
                    await self._update_job_status(job.job_id, JobStatus.SKIPPED, reason=reason)
                    return
                elif "daily_lead_cap" in reason:
                    # The per-day ceiling resets at UTC midnight. Reschedule
                    # the lead for just after the day rolls over; the
                    # calling-window gate then holds it until the tenant's
                    # allowed hours. Avoids burning retries hammering the cap.
                    now = datetime.now(timezone.utc)
                    next_midnight = (now + timedelta(days=1)).replace(
                        hour=0, minute=5, second=0, microsecond=0,
                    )
                    delay = max(300, int((next_midnight - now).total_seconds()))
                    logger.info(
                        "Daily per-lead cap hit for lead %s (%s) — retrying after "
                        "midnight in %ds (~%.1fh)",
                        job.lead_id, reason, delay, delay / 3600,
                    )
                else:
                    delay = 300  # 5 minutes for other reasons (concurrent limit, etc.)

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

            # 4.5. Run Call Guard validation before initiating call
            guard_decision = await self._evaluate_call_guard(job, rules)
            if guard_decision != "allow":
                logger.warning(f"Call guard decision for job {job.job_id}: {guard_decision}")

                if guard_decision == "block":
                    # Block the call - mark job as blocked, don't retry
                    await self._update_job_status(job.job_id, JobStatus.BLOCKED, reason="call_guard_blocked")
                    return
                elif guard_decision == "throttle":
                    # Throttle - reschedule with delay
                    await self.queue_service.schedule_retry(job, delay_seconds=60)
                    await self._update_job_status(job.job_id, JobStatus.SKIPPED, reason="call_guard_throttled")
                    return
                elif guard_decision == "queue":
                    # Queue - reschedule to retry later
                    await self.queue_service.schedule_retry(job, delay_seconds=30)
                    await self._update_job_status(job.job_id, JobStatus.SKIPPED, reason="call_guard_queued")
                    return

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
                    job.campaign_id, campaign_status or "missing", job.job_id,
                )
                await self.queue_service.mark_skipped(job.job_id, reason="campaign_stopped")
                await self._update_job_status(
                    job.job_id, JobStatus.SKIPPED, error="campaign_stopped_before_originate",
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
                        "deferring job %s", job.campaign_id, inflight, batch_size,
                        job.job_id,
                    )
                    await self.queue_service.schedule_retry(job, delay_seconds=5)
                    await self._update_job_status(
                        job.job_id, JobStatus.RETRY_SCHEDULED, reason="batch_capacity",
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
                    internal_call_id, talklee_call_id, leg_id = await self._create_call_record(job, provider_call_id)

                    # B1: transition the call into the public state machine
                    # (Track B). The dialer worker drove the call to "dialing"
                    # the moment the bridge accepted the originate request;
                    # subsequent transitions (ringing → answered → ended) are
                    # written by the asterisk_adapter ARI callbacks.
                    try:
                        from app.domain.services.call_status import (
                            CallState, record_call_state,
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
                            internal_call_id, state_exc,
                        )

                    # 7. Update lead status to 'calling'
                    await self._update_lead_status(job.lead_id, "calling")

                    # 8. Update job with the internal DB call UUID
                    job.call_id = internal_call_id
                    job.status = JobStatus.PROCESSING
                    job.processed_at = datetime.now(timezone.utc)
                    await self._update_job_status(job.job_id, JobStatus.PROCESSING, call_id=internal_call_id)

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
                    job.job_id, record_exc,
                )

            if decision.should_retry:
                await self._update_lead_status(job.lead_id, "pending")
                await self.queue_service.schedule_retry(
                    job, delay_seconds=decision.delay_seconds,
                )
                await self._update_job_status(
                    job.job_id, JobStatus.RETRY_SCHEDULED, error=str(e),
                )
            else:
                # Either the category disallows retries (INVALID_INPUT)
                # or the per-category attempt budget is exhausted.
                await self._update_lead_status(job.lead_id, "failed")
                await self.queue_service.mark_failed(job.job_id, str(e))
                await self._update_job_status(
                    job.job_id, JobStatus.FAILED, error=str(e),
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
                async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 503:
                        body = await resp.text()
                        logger.warning(
                            "Voice pipeline unavailable (503) — will retry "
                            "without consuming attempt budget. dest=%s body=%s",
                            job.phone_number, body[:300],
                        )
                        return self._PIPELINE_UNAVAILABLE
                    if resp.status not in (200, 202):
                        body = await resp.text()
                        # Stash for the classifier in process_job's except branch.
                        self._last_bridge_http_status = resp.status
                        self._last_bridge_body = body
                        logger.error(
                            "Telephony bridge rejected call: status=%s body=%s dest=%s",
                            resp.status, body[:200], job.phone_number,
                        )
                        return None

                    data = await resp.json()
                    call_id: Optional[str] = data.get("call_id")
                    self._last_provider_name = data.get("adapter", "asterisk")

                    if call_id:
                        logger.info(
                            "CALL INITIATED via bridge (%s): %s call_id=%s... "
                            "(campaign=%s, lead=%s)",
                            self._last_provider_name, job.phone_number,
                            call_id[:8], job.campaign_id, job.lead_id,
                        )
                    else:
                        logger.warning(
                            "CALL FAILED via bridge: %s (campaign=%s, lead=%s)",
                            job.phone_number, job.campaign_id, job.lead_id,
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

        Setting bypass_rls = on (without LOCAL, no transaction needed)
        keeps the value alive for the connection's lifetime, including
        after it's returned to the pool and reused. The nil-UUID sentinel
        on app.current_tenant_id ensures the policy's UUID cast doesn't
        throw even if some path evaluates the left side of the OR.
        """
        pool = self._db_pool
        async with pool.acquire() as conn:
            await conn.execute("SET app.bypass_rls = 'on'")
            await conn.execute(
                "SET app.current_tenant_id = '00000000-0000-0000-0000-000000000000'"
            )
            yield conn

    async def _reap_stuck_jobs_tick(self) -> None:
        """Mark in-flight jobs hung past the timeout as failed, so zombie
        'dialing' rows don't accumulate and the lead is freed for a fresh
        attempt. Logic lives in dialer.stuck_job_reaper; best-effort."""
        try:
            from app.domain.services.dialer.stuck_job_reaper import reap_stuck_jobs
            async with self._acquire_db() as conn:
                await reap_stuck_jobs(conn)
        except Exception as exc:
            logger.warning("reaper tick failed: %s", exc)

    async def _get_active_tenant_ids(self) -> List[str]:
        """Get list of tenants with active/running campaigns."""
        try:
            async with self._acquire_db() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT tenant_id FROM campaigns WHERE status IN ('running', 'active')"
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
                    "SELECT status FROM campaigns WHERE id = $1",
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
                    "SELECT calling_rules FROM tenants WHERE id = $1",
                    tenant_id
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

        Fail-open: on a transient DB error return 0 so a hiccup never wedges
        dispatch (the concurrency guard remains as a backstop).
        """
        try:
            async with self._acquire_db() as conn:
                val = await conn.fetchval(
                    """
                    SELECT count(*) FROM calls
                     WHERE campaign_id = $1
                       AND status IN (
                           'dialing', 'ringing', 'answered', 'in_call', 'initiated'
                       )
                    """,
                    campaign_id,
                )
            return int(val or 0)
        except Exception as exc:
            logger.warning(
                "batch_gate: in-flight count failed campaign=%s err=%s",
                campaign_id, exc,
            )
            return 0

    async def _get_lead_last_called(self, lead_id: str) -> Optional[datetime]:
        """Get the last time a lead was called."""
        try:
            async with self._acquire_db() as conn:
                val = await conn.fetchval(
                    "SELECT last_called_at FROM leads WHERE id = $1",
                    lead_id
                )
                return val  # asyncpg returns appropriate datetime object
            
        except Exception as e:
            logger.warning(f"Failed to get lead last_called_at: {e}")

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
                    "WHERE lead_id = $1 AND created_at >= date_trunc('day', now())",
                    lead_id,
                )
                return int(val or 0)
        except Exception as e:
            logger.warning(f"Failed to count lead attempts today: {e}")
        return 0

    async def _create_call_record(self, job: DialerJob, provider_call_id: str) -> tuple[str, str, str]:
        """
        Create a call record in the database with separate internal and provider IDs.

        Returns:
            tuple: (internal_call_id, talklee_call_id, leg_id)
        """
        talklee_call_id = generate_talklee_call_id()
        internal_call_id = str(uuid.uuid4())

        try:
            async with self._acquire_db() as conn:
                await conn.execute(
                    """
                    INSERT INTO calls (
                        id, tenant_id, campaign_id, lead_id, phone_number,
                        external_call_uuid, status, talklee_call_id, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                    """,
                    internal_call_id,
                    job.tenant_id,
                    job.campaign_id,
                    job.lead_id,
                    job.phone_number,
                    provider_call_id,
                    "initiated",
                    talklee_call_id,
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
                    json.dumps({
                        "job_id": job.job_id,
                        "campaign_id": job.campaign_id,
                        "provider_call_id": provider_call_id,
                    }),
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
                    json.dumps({
                        "leg_type": "pstn_outbound",
                        "provider": getattr(self, "_last_provider_name", "sip"),
                        "provider_call_id": provider_call_id,
                    }),
                    "initiated",
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
                        "UPDATE leads SET status = $1 WHERE id = $2",
                        status, lead_id
                    )
                else:
                    # Terminal / completion states (failed, completed, etc.) —
                    # record the timestamp so per-lead cooldown is enforced correctly.
                    await conn.execute(
                        """
                        UPDATE leads SET status = $1, last_called_at = NOW()
                        WHERE id = $2
                        """,
                        status, lead_id
                    )
        except Exception as e:
            logger.error(f"Failed to update lead status: {e}")

    async def _clear_lead_last_called(self, lead_id: str) -> None:
        """Clear last_called_at so a stale origination-time timestamp cannot block retries."""
        try:
            async with self._acquire_db() as conn:
                await conn.execute(
                    "UPDATE leads SET last_called_at = NULL WHERE id = $1",
                    lead_id
                )
        except Exception as e:
            logger.error(f"Failed to clear lead last_called_at: {e}")

    async def _update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        call_id: Optional[str] = None,
        error: Optional[str] = None,
        reason: Optional[str] = None
    ) -> None:
        """Update job status in database."""
        try:
            # Build update query dynamically or use simple execution
            status_val = status.value if hasattr(status, 'value') else status
            
            async with self._acquire_db() as conn:
                db = Database(conn)
                data = {
                    "status": status_val,
                    "updated_at": datetime.now(timezone.utc)
                }
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
                    job_id, category, reason,
                )
        except Exception as exc:
            logger.warning(
                "could not write failure_category/reason for job=%s "
                "(missing columns? not yet migrated?): %s",
                job_id, exc,
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
                "timestamp": datetime.now(timezone.utc).isoformat()
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
                tenant_id: count 
                for tenant_id, count in self.rules_engine._active_calls.items()
            }
        }

    async def _heartbeat(self) -> None:
        """Log heartbeat periodically for systemd liveness monitoring."""
        # Using simple config access to avoid dependency issues during migration
        # from app.core.voice_config import get_voice_config
        # interval = get_voice_config().worker_heartbeat_interval
        interval = 60
        while self.running:
            logger.info(
                f"heartbeat: jobs_processed={self._jobs_processed}, "
                f"jobs_failed={self._jobs_failed}"
            )
            await asyncio.sleep(interval)


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

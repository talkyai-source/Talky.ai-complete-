"""
Call Service
Domain service for call lifecycle management.

Extracts business logic from webhooks.py endpoints into a testable,
reusable service following the Domain-Driven Design pattern established
by CampaignService.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import asyncpg

from app.core.postgres_adapter import Client
from app.core.db import DatabasePoolTimeoutError, _ACQUIRE_TIMEOUT_S
from app.core.db_utils import acquire_with_tenant
from app.core.security.tenant_isolation import get_bypass_rls, get_current_tenant_id

from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.services.queue_service import DialerQueueService
from app.domain.repositories.call_repository import CallRepository
from app.domain.repositories.lead_repository import LeadRepository

logger = logging.getLogger(__name__)


class WebhookTargetMismatch(Exception):
    """A webhook body referenced a lead that does not belong to the scoped call.

    Raised by ``mark_as_spam`` when a caller — even one holding the correct
    per-tenant webhook secret — supplies a ``lead_id`` that is not the lead
    of the ``call_id`` they named. The route maps this to a 400 (client
    error), distinct from the 404 used for not-found / cross-tenant ids.
    """


# Retry timing + per-disposition caps now live in
# ``app.workers.disposition_policy`` (the single source of truth for
# post-answer retry cadence). The old flat RETRY_DELAY_SECONDS /
# MAX_RETRY_ATTEMPTS / RETRYABLE_OUTCOMES constants were removed when
# that brain took over — keeping them here would invite the same
# "treats every outcome identically" drift they used to cause.

# Outcomes that should NOT retry (still used for lead DNC marking,
# campaign-counter routing, and the terminal job status).
NON_RETRYABLE_OUTCOMES = {
    CallOutcome.SPAM,
    CallOutcome.INVALID,
    CallOutcome.UNAVAILABLE,
    CallOutcome.DISCONNECTED,
    CallOutcome.REJECTED,
    CallOutcome.GOAL_ACHIEVED,
}


class CallService:
    """
    Domain service for call lifecycle management.
    
    Handles:
    - Call status updates (from telephony webhooks)
    - Lead status synchronization
    - Dialer job completion and retry logic
    - Goal achievement and spam marking
    """
    
    def __init__(
        self,
        db_client: Client,
        queue_service: Optional[DialerQueueService] = None,
        call_repo: Optional[CallRepository] = None,
        lead_repo: Optional[LeadRepository] = None,
        db_pool: Optional[asyncpg.Pool] = None,
    ):
        self._db_client = db_client
        self._queue_service = queue_service
        self._call_repo = call_repo or CallRepository(db_client)
        self._lead_repo = lead_repo or LeadRepository(db_client)
        # 2026-07-08: async asyncpg pool for the handle_call_status hot path.
        # `db_client` (postgres_adapter.Client) blocks the event loop on a
        # shared 4-worker thread pool AND opens a brand-new UNPOOLED
        # asyncpg.connect() per query (see postgres_adapter.QueryBuilder /
        # RpcBuilder._run_sync + _execute_async) — every call teardown paid
        # for that on 4-6 sequential round-trips. When `db_pool` is supplied,
        # handle_call_status routes through `_handle_call_status_pooled`
        # instead, which does the same writes as ONE pooled, non-blocking
        # asyncpg transaction. `db_pool=None` keeps the legacy blocking path
        # so callers that don't pass a pool (unit tests, anything not yet
        # wired through container.py) behave exactly as before.
        self._db_pool = db_pool
    
    # =========================================================================
    # Call Status Handling
    # =========================================================================
    
    async def handle_call_status(
        self,
        call_uuid: str,
        outcome: CallOutcome,
        duration: Optional[int] = None
    ) -> None:
        """
        Handle a call status update from the telephony provider.

        Uses the atomic RPC function (update_call_status) when available,
        falling back to sequential writes for backward compatibility.

        Steps performed:
        1. Update call record + lead status (atomic via RPC, or sequential)
        2. Handle dialer job completion and retry logic
        3. Update campaign counters

        2026-07-08: when this service was constructed with a `db_pool`
        (see __init__), all of the above runs through
        `_handle_call_status_pooled` as ONE non-blocking asyncpg
        transaction instead of the sequential blocking calls below. The
        `db_pool is None` branch is kept byte-for-byte as it was so any
        caller not yet passing a pool (unit tests, etc.) is unaffected.

        Args:
            call_uuid: Unique call identifier from telephony provider
            outcome: The call outcome (answered, busy, failed, etc.)
            duration: Call duration in seconds (if available)
        """
        try:
            outcome_value = outcome.value if hasattr(outcome, 'value') else str(outcome)

            if self._db_pool is not None:
                job_id, campaign_id, retry_args = await self._handle_call_status_pooled(
                    call_uuid, outcome, outcome_value, duration,
                )
                if retry_args is not None:
                    # Redis I/O — deliberately done AFTER the DB transaction
                    # above has committed, so we never hold a pooled
                    # connection while talking to Redis.
                    await self._schedule_retry(*retry_args)
            else:
                # Legacy blocking path — unchanged.
                # Try atomic RPC first (steps 1+2: call + lead in one transaction)
                rpc_result = await self._try_atomic_update(call_uuid, outcome_value, duration)

                if rpc_result:
                    # RPC succeeded — extract metadata for job/campaign handling
                    job_id = rpc_result.get("job_id")
                    campaign_id = rpc_result.get("campaign_id")
                else:
                    # Fallback: sequential writes (RPC not deployed yet)
                    job_id, campaign_id = await self._sequential_update(
                        call_uuid, outcome, outcome_value, duration
                    )

                # Handle dialer job completion (always done app-side for retry logic)
                if job_id:
                    await self._handle_job_completion(
                        job_id=job_id,
                        outcome=outcome,
                        campaign_id=campaign_id or "",
                        lead_id=rpc_result.get("lead_id", "") if rpc_result else ""
                    )

                # Update campaign counters
                if campaign_id:
                    self._update_campaign_counters(campaign_id, outcome)

            logger.info(f"Call {call_uuid} status updated: {outcome}")
            
            # --- Day 1: Event logging (additive, non-blocking) ---
            try:
                from app.domain.repositories.call_event_repository import CallEventRepository
                event_repo = CallEventRepository(self._db_client)
                await event_repo.log_event(
                    call_id=call_uuid,
                    event_type="state_change",
                    source="call_service",
                    event_data={
                        "outcome": outcome_value,
                        "duration": duration,
                        "campaign_id": campaign_id,
                    },
                    new_state=outcome_value,
                )
            except Exception as evt_err:
                logger.debug(f"Event logging failed (non-critical): {evt_err}")
            
        except Exception as e:
            logger.error(f"Error handling call status for {call_uuid}: {e}", exc_info=True)
    
    async def _try_atomic_update(
        self, call_uuid: str, outcome_value: str, duration: Optional[int]
    ) -> Optional[dict]:
        """
        Try to use the atomic RPC function for call+lead update.
        Returns the RPC result dict on success, None if RPC unavailable.
        """
        try:
            rpc_params = {
                "p_call_uuid": call_uuid,
                "p_outcome": outcome_value,
            }
            if duration is not None:
                rpc_params["p_duration"] = int(duration)
            
            response = self._db_client.rpc("update_call_status", rpc_params).execute()
            
            if response.data and response.data.get("found"):
                logger.debug(f"Atomic RPC update succeeded for call {call_uuid}")
                return response.data
            elif response.data and not response.data.get("found"):
                logger.warning(f"Call not found via RPC: {call_uuid}")
                return None
            return None
        except Exception as e:
            # RPC not available (migration not applied) — fall back silently
            logger.debug(f"RPC update_call_status not available, using fallback: {e}")
            return None
    
    async def _sequential_update(
        self, call_uuid: str, outcome: CallOutcome, outcome_value: str,
        duration: Optional[int]
    ) -> tuple:
        """
        Fallback: sequential writes for call + lead update.
        Returns (job_id, campaign_id) for downstream processing.
        """
        # Get call record via repository
        call = await self._call_repo.get_by_id(call_uuid)
        
        if not call:
            logger.warning(f"Call not found: {call_uuid}")
            return None, None
        
        job_id = call.get("dialer_job_id")
        campaign_id = call.get("campaign_id")
        lead_id = call.get("lead_id")
        
        # Update call record via repository
        call_update = {
            "status": "completed",
            "outcome": outcome_value,
            "ended_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        # Persist duration whenever it was computed (including 0) so short/failed
        # calls still record a row that reflects reality instead of leaving
        # duration_seconds NULL. None means "not computed" — leave it untouched.
        if duration is not None:
            call_update["duration_seconds"] = int(duration)

        await self._call_repo.update(call_uuid, call_update)
        
        # Update lead status via repository
        if lead_id:
            await self._update_lead_status(lead_id, outcome)
        
        return job_id, campaign_id

    # =========================================================================
    # Pooled (async, non-blocking) teardown path — 2026-07-08
    # =========================================================================
    #
    # Everything below reproduces the exact SQL the legacy path above issues
    # (postgres_adapter's `_rpc_update_call_status` / `_rpc_increment_
    # campaign_counter`, plus `_sequential_update` / `_update_lead_status` /
    # `_handle_job_completion`), just run against the pooled asyncpg
    # connection inside ONE transaction instead of N sequential blocking
    # round-trips through `Client.table()/.rpc()` (each of which blocks the
    # event loop on a shared 4-worker thread pool AND opens a brand-new
    # unpooled `asyncpg.connect()`). This is a transport change only — same
    # rows, same status values, same fail-soft behavior.

    async def _handle_call_status_pooled(
        self,
        call_uuid: str,
        outcome: CallOutcome,
        outcome_value: str,
        duration: Optional[int],
    ) -> tuple:
        """Non-blocking equivalent of the RPC-then-fallback flow above.

        RLS: the teardown caller (lifecycle.py `_on_call_ended`) has no
        request JWT, so it sets `set_bypass_rls(True)` (+ tenant id, when
        known) on the `tenant_isolation` contextvars before calling
        `handle_call_status`. We read those SAME contextvars — exactly what
        `get_db()` reads — and hand them to `acquire_with_tenant`, which
        opens the pooled connection inside an explicit `conn.transaction()`
        and issues the `SET LOCAL app.bypass_rls` / `app.current_tenant_id`
        for the lifetime of that transaction. (A bare `get_db()` call sets
        those as their own single-statement implicit transaction, which
        reverts before our next query runs — not suitable for the
        multi-statement transaction this method needs, so we go straight to
        `acquire_with_tenant`, which already gets this right.)

        A saturated pool degrades gracefully: `acquire_with_tenant`'s
        `timeout` mirrors `get_db()`'s bounded acquire
        (`PG_POOL_ACQUIRE_TIMEOUT`, default 10s) — on expiry we log and
        return a no-op result instead of stalling teardown indefinitely.

        Returns (job_id, campaign_id, retry_args) where retry_args is either
        None or the positional-argument tuple for `_schedule_retry`,
        deferred until AFTER the transaction commits (Redis I/O must never
        happen while holding a pooled DB connection).

        2026-07-13 fix: this used to only resolve `dialer_job_id` (and only
        update the `leads` row) on the "call row not found on the first
        SELECT" fallback branch — which never happens for dialer calls
        (they always pre-create the row via `dialer_worker._create_call_
        record`). That left `leads.status` stuck on "calling" forever and
        `dialer_jobs` stuck PROCESSING forever on the production pooled
        path. There is now exactly one lookup + one write path; job_id is
        resolved from `calls.dialer_job_id` (now populated at INSERT — see
        `dialer_worker._create_call_record`) and the lead update always
        runs when a lead_id is present, matching the legacy
        `_sequential_update` behavior this method otherwise mirrors.
        """
        bypass = get_bypass_rls()
        tenant_id = get_current_tenant_id()

        if not bypass and not tenant_id:
            # Neither bypass nor a tenant context is set. Not expected on
            # the real teardown path (both lifecycle.py call sites set
            # bypass_rls=True before invoking handle_call_status) — fail
            # loud-but-caught (by the outer try/except in
            # handle_call_status) instead of silently guessing an RLS scope.
            raise RuntimeError(
                "handle_call_status: no bypass_rls and no tenant context "
                "set — refusing to guess an RLS scope for call "
                f"{call_uuid}"
            )

        job_id = None
        campaign_id = None
        lead_id = None
        retry_args = None

        try:
            async with acquire_with_tenant(
                self._db_pool,
                None if bypass else tenant_id,
                timeout=_ACQUIRE_TIMEOUT_S,
            ) as conn:
                # ---- Step 1: resolve the call row ---------------------------
                row = await conn.fetchrow(
                    """
                    SELECT id, lead_id, campaign_id, dialer_job_id, status
                    FROM calls WHERE id = $1
                    """,
                    call_uuid,
                )
                if row is None:
                    logger.warning(f"Call not found: {call_uuid}")
                    return None, None, None

                # Idempotency guard: teardown can be driven twice for the
                # same call (the adapter's pre-Stasis terminal arm racing a
                # later StasisEnd/ChannelDestroyed, or an in-process guard
                # — `lifecycle._ended_calls_in_flight` / the adapter's
                # `_end_dispatched` — getting reset by a worker restart).
                # Once this row is already `completed`, a repeat run must
                # NOT re-increment `leads.call_attempts`, re-bump campaign
                # counters, or re-schedule a retry job. First writer wins;
                # only transition out of a non-terminal status once.
                if row["status"] == "completed":
                    logger.info(
                        "handle_call_status: call %s already completed — "
                        "skipping duplicate teardown", call_uuid,
                    )
                    return None, None, None

                await self._update_call_row_pooled(
                    conn, call_uuid, outcome_value, duration,
                )

                lead_id = str(row["lead_id"]) if row["lead_id"] else None
                campaign_id = str(row["campaign_id"]) if row["campaign_id"] else None
                job_id = str(row["dialer_job_id"]) if row["dialer_job_id"] else None

                if lead_id:
                    await self._update_lead_status_pooled(conn, lead_id, outcome)

                # ---- Step 2: campaign counters ------------------------------
                if campaign_id:
                    await self._update_campaign_counters_pooled(conn, campaign_id, outcome)

                # ---- Step 3: dialer job completion + retry decision --------
                if job_id:
                    retry_args = await self._handle_job_completion_pooled(
                        conn,
                        job_id=job_id,
                        outcome=outcome,
                        campaign_id=campaign_id or "",
                        lead_id=lead_id or "",
                    )
        except (asyncio.TimeoutError, DatabasePoolTimeoutError) as exc:
            logger.error(
                "handle_call_status: DB pool acquire timed out for call=%s "
                "— teardown degrading gracefully (no writes landed): %s",
                call_uuid, exc,
            )
            return None, None, None

        return job_id, campaign_id, retry_args

    async def _update_call_row_pooled(
        self,
        conn: asyncpg.Connection,
        call_uuid: str,
        outcome_value: str,
        duration: Optional[int],
    ) -> None:
        """Same UPDATE the legacy RPC shim / `_sequential_update` issue."""
        if duration is None:
            await conn.execute(
                """
                UPDATE calls
                SET status = 'completed', outcome = $2,
                    ended_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                call_uuid, outcome_value,
            )
        else:
            await conn.execute(
                """
                UPDATE calls
                SET status = 'completed', outcome = $2,
                    duration_seconds = $3,
                    ended_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                call_uuid, outcome_value, int(duration),
            )

    async def _update_lead_status_pooled(
        self, conn: asyncpg.Connection, lead_id: str, outcome: CallOutcome,
    ) -> None:
        """Pooled equivalent of `_update_lead_status` — same status rules."""
        lead_status = "called"
        last_call_result = outcome.value if hasattr(outcome, 'value') else str(outcome)

        if outcome == CallOutcome.ANSWERED:
            lead_status = "contacted"
        elif outcome == CallOutcome.GOAL_ACHIEVED:
            lead_status = "completed"
            last_call_result = "goal_achieved"
        elif outcome in NON_RETRYABLE_OUTCOMES:
            lead_status = "dnc"  # Do not call

        current_attempts = await conn.fetchval(
            "SELECT call_attempts FROM leads WHERE id = $1", lead_id,
        )
        current_attempts = current_attempts or 0

        await conn.execute(
            """
            UPDATE leads
            SET status = $2, last_call_result = $3, last_called_at = NOW(),
                call_attempts = $4, updated_at = NOW()
            WHERE id = $1
            """,
            lead_id, lead_status, last_call_result, current_attempts + 1,
        )

    async def _update_campaign_counters_pooled(
        self, conn: asyncpg.Connection, campaign_id: str, outcome: CallOutcome,
    ) -> None:
        """Pooled equivalent of `_update_campaign_counters` — same routing
        rules (see that method's docstring for the counter table)."""
        non_reachable = NON_RETRYABLE_OUTCOMES - {CallOutcome.GOAL_ACHIEVED}
        counter = "calls_failed" if outcome in non_reachable else "calls_completed"
        # `counter` is one of two hard-coded literals above — never
        # interpolated from caller input — so this is not SQL-injectable.
        await conn.execute(
            f"""
            UPDATE campaigns
            SET {counter} = COALESCE({counter}, 0) + 1, updated_at = NOW()
            WHERE id = $1
            """,
            campaign_id,
        )

    async def _handle_job_completion_pooled(
        self,
        conn: asyncpg.Connection,
        job_id: str,
        outcome: CallOutcome,
        campaign_id: str,
        lead_id: str,
    ) -> Optional[tuple]:
        """Pooled equivalent of `_handle_job_completion`.

        Returns the positional-argument tuple for `_schedule_retry` when a
        retry is due, else None. The caller is responsible for invoking
        `_schedule_retry` AFTER the transaction commits — that call talks to
        Redis and must not run while holding a pooled DB connection.
        """
        job_data = await conn.fetchrow(
            "SELECT * FROM dialer_jobs WHERE id = $1", job_id,
        )
        if job_data is None:
            logger.warning(f"Dialer job not found: {job_id}")
            return None

        attempt_number = job_data["attempt_number"] or 1
        tenant_id = job_data["tenant_id"] or "default-tenant"

        from app.workers.disposition_policy import decide as decide_disposition
        decision = decide_disposition(outcome, attempt_number)

        if decision.is_success:
            final_status = (
                JobStatus.GOAL_ACHIEVED
                if outcome == CallOutcome.GOAL_ACHIEVED
                else JobStatus.COMPLETED
            )
        elif decision.should_retry:
            final_status = JobStatus.RETRY_SCHEDULED
        elif outcome in NON_RETRYABLE_OUTCOMES:
            final_status = JobStatus.NON_RETRYABLE
        else:
            final_status = JobStatus.FAILED

        final_status_value = (
            final_status.value if hasattr(final_status, 'value') else str(final_status)
        )
        outcome_value = outcome.value if hasattr(outcome, 'value') else str(outcome)

        # Idempotency guard (defense in depth, alongside the calls.status
        # check in `_handle_call_status_pooled`): only ever transition a job
        # OUT of PROCESSING once. If a duplicate teardown somehow reaches
        # this far, `updated_job_id` comes back None and we must NOT
        # re-schedule a second retry for the same job.
        if decision.should_retry:
            updated_job_id = await conn.fetchval(
                """
                UPDATE dialer_jobs
                SET status = $2, last_outcome = $3, failure_reason = $4,
                    updated_at = NOW()
                WHERE id = $1 AND status = 'processing'
                RETURNING id
                """,
                job_id, final_status_value, outcome_value, decision.reason,
            )
        else:
            updated_job_id = await conn.fetchval(
                """
                UPDATE dialer_jobs
                SET status = $2, last_outcome = $3, failure_reason = $4,
                    updated_at = NOW(), completed_at = NOW()
                WHERE id = $1 AND status = 'processing'
                RETURNING id
                """,
                job_id, final_status_value, outcome_value, decision.reason,
            )

        if updated_job_id is None:
            logger.info(
                "job_completion job=%s already finalized (status was not "
                "'processing') — skipping duplicate finalize/retry-schedule",
                job_id,
            )
            return None

        logger.info(
            "job_completion job=%s final=%s %s",
            job_id, final_status_value, decision.log_message,
        )

        if not decision.should_retry:
            return None

        logger.info(
            f"Scheduling retry for job {job_id} (attempt {attempt_number + 1}) "
            f"in {decision.delay_seconds}s"
        )
        # `_schedule_retry` reads job_data as a dict (job_data.get(...)); the
        # asyncpg Record supports mapping-style access, but pass a plain
        # dict for exact parity with the legacy path's `job_response.data[0]`.
        return (
            job_id, dict(job_data), outcome, campaign_id, lead_id,
            str(tenant_id), attempt_number, decision.delay_seconds,
        )

    async def _update_lead_status(self, lead_id: str, outcome: CallOutcome) -> None:
        """Update lead status and call tracking fields based on call outcome."""
        lead_status = "called"
        last_call_result = outcome.value if hasattr(outcome, 'value') else str(outcome)
        
        if outcome == CallOutcome.ANSWERED:
            lead_status = "contacted"
        elif outcome == CallOutcome.GOAL_ACHIEVED:
            lead_status = "completed"
            last_call_result = "goal_achieved"
        elif outcome in NON_RETRYABLE_OUTCOMES:
            lead_status = "dnc"  # Do not call
        
        try:
            # Get current call_attempts first
            lead_data = self._db_client.table("leads").select("call_attempts").eq("id", lead_id).execute()
            current_attempts = lead_data.data[0].get("call_attempts", 0) if lead_data.data else 0
            
            self._db_client.table("leads").update({
                "status": lead_status,
                "last_call_result": last_call_result,
                "last_called_at": datetime.utcnow().isoformat(),
                "call_attempts": current_attempts + 1,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", lead_id).execute()
        except Exception as e:
            logger.error(f"Failed to update lead {lead_id}: {e}")
    
    def _update_campaign_counters(self, campaign_id: str, outcome: CallOutcome) -> None:
        """Update campaign completion/failure counters via PostgreSQL RPC.

        Counter rules — every terminal outcome bumps exactly one counter so
        the campaign progress bar reflects "we tried this lead":

          * GOAL_ACHIEVED                              -> calls_completed
          * ANSWERED, GOAL_NOT_ACHIEVED, VOICEMAIL,
            BUSY, NO_ANSWER, TIMEOUT, FAILED           -> calls_completed
            (we DID connect / attempt — it counts toward the success-rate
            denominator but not the success-rate numerator unless the
            agent flags GOAL_ACHIEVED)
          * SPAM, INVALID, UNAVAILABLE, DISCONNECTED,
            REJECTED                                    -> calls_failed
            (could not reach the lead at all — distinct from "we tried")

        Previously this method silently dropped retryable outcomes, which
        left calls_completed / calls_failed at 0 forever for ordinary
        traffic and made the dashboard's progress_pct / success_rate_pct
        look like nothing was happening.
        """
        # NON_RETRYABLE_OUTCOMES historically included GOAL_ACHIEVED
        # (because we don't retry a successful call); split that out so
        # we can route GOAL_ACHIEVED to calls_completed without it
        # falling through to the calls_failed branch below.
        non_reachable = NON_RETRYABLE_OUTCOMES - {CallOutcome.GOAL_ACHIEVED}
        try:
            if outcome in non_reachable:
                counter = "calls_failed"
            else:
                # Everything else (GOAL_ACHIEVED, ANSWERED, retryable
                # outcomes, GOAL_NOT_ACHIEVED) counts toward the
                # "calls we executed" bucket.
                counter = "calls_completed"
            self._db_client.rpc("increment_campaign_counter", {
                "p_campaign_id": campaign_id,
                "p_counter": counter,
            }).execute()
        except Exception as e:
            logger.error(f"Failed to update campaign counters for {campaign_id}: {e}")
    
    # =========================================================================
    # Job Completion & Retry Logic
    # =========================================================================
    
    async def _handle_job_completion(
        self,
        job_id: str,
        outcome: CallOutcome,
        campaign_id: str,
        lead_id: str
    ) -> None:
        """
        Handle dialer job completion — decide retry or finalize.

        Retry policy is owned by ``disposition_policy.decide`` (the single
        source of truth for post-answer outcomes). It replaced the old
        flat ``RETRY_DELAY_SECONDS`` (2h for everything) + ``MAX_RETRY_
        ATTEMPTS`` (3 for everything) logic, which treated busy,
        no-answer and voicemail identically. Each disposition now has its
        own cadence and attempt cap:

            Busy      5m → 15m → 45m   (cap 4)
            No-answer 2h → next-day    (cap 3)
            Voicemail 4h once          (cap 2)
            Rejected  no retry — stop
            Failed    30s → 2m → 10m   (cap 3)
        """
        try:
            # Get job details
            job_response = self._db_client.table("dialer_jobs").select("*").eq("id", job_id).execute()

            if not job_response.data:
                logger.warning(f"Dialer job not found: {job_id}")
                return

            job_data = job_response.data[0]
            attempt_number = job_data.get("attempt_number", 1)
            tenant_id = job_data.get("tenant_id", "default-tenant")

            # Disposition-based decision — see module docstring for the
            # cadence table. Pure logic, no side effects; we own the
            # writes below.
            from app.workers.disposition_policy import decide as decide_disposition
            decision = decide_disposition(outcome, attempt_number)

            if decision.is_success:
                final_status = (
                    JobStatus.GOAL_ACHIEVED
                    if outcome == CallOutcome.GOAL_ACHIEVED
                    else JobStatus.COMPLETED
                )
            elif decision.should_retry:
                final_status = JobStatus.RETRY_SCHEDULED
            elif outcome in NON_RETRYABLE_OUTCOMES:
                final_status = JobStatus.NON_RETRYABLE
            else:
                final_status = JobStatus.FAILED

            # Update job in database
            job_update = {
                "status": final_status.value if hasattr(final_status, 'value') else str(final_status),
                "last_outcome": outcome.value if hasattr(outcome, 'value') else str(outcome),
                "failure_reason": decision.reason,
                "updated_at": datetime.utcnow().isoformat()
            }

            if not decision.should_retry:
                job_update["completed_at"] = datetime.utcnow().isoformat()

            self._db_client.table("dialer_jobs").update(job_update).eq("id", job_id).execute()

            # Schedule retry if needed
            if decision.should_retry:
                await self._schedule_retry(
                    job_id, job_data, outcome, campaign_id, lead_id,
                    tenant_id, attempt_number, decision.delay_seconds,
                )

            logger.info(
                "job_completion job=%s final=%s %s",
                job_id, final_status.value if hasattr(final_status, 'value') else final_status,
                decision.log_message,
            )

        except Exception as e:
            logger.error(f"Error handling job completion for {job_id}: {e}", exc_info=True)
    
    async def _schedule_retry(
        self,
        job_id: str,
        job_data: dict,
        outcome: CallOutcome,
        campaign_id: str,
        lead_id: str,
        tenant_id: str,
        attempt_number: int,
        delay_seconds: int,
    ) -> None:
        """Schedule a retry for a dialer job after ``delay_seconds``.

        Fresh-first sequencing: a *recycled* (retry) job must never jump
        ahead of a never-tried lead. We clamp its priority below the
        high-priority lane so a retry can't preempt fresh traffic via the
        priority queue; combined with the delayed re-enqueue (which
        RPUSHes to the back of the tenant FIFO when due), fresh leads
        always drain before recycled ones.
        """
        logger.info(
            f"Scheduling retry for job {job_id} (attempt {attempt_number + 1}) "
            f"in {delay_seconds}s"
        )

        fresh_priority = job_data.get("priority", 5)
        retry_priority = min(
            fresh_priority, DialerQueueService.HIGH_PRIORITY_THRESHOLD - 1,
        )

        retry_job = DialerJob(
            job_id=job_id,
            campaign_id=campaign_id,
            lead_id=lead_id,
            tenant_id=tenant_id,
            phone_number=job_data.get("phone_number", ""),
            priority=retry_priority,
            status=JobStatus.RETRY_SCHEDULED,
            attempt_number=attempt_number + 1,
            last_outcome=outcome
        )

        if self._queue_service:
            await self._queue_service.schedule_retry(retry_job, delay_seconds=delay_seconds)
        else:
            logger.error(f"Cannot schedule retry for job {job_id}: queue service unavailable")
    
    # =========================================================================
    # Goal Achievement & Spam Marking
    # =========================================================================
    
    async def mark_goal_achieved(self, tenant_id: str, call_id: str) -> Optional[dict]:
        """
        Mark a call as having achieved its goal — SCOPED to ``tenant_id``.

        SECURITY (object-level authz, P0): every UPDATE is constrained by
        BOTH the object id AND the verified ``tenant_id``, so a caller who
        holds tenant A's webhook secret can never mutate tenant B's rows by
        naming B's ``call_id``. A call that does not exist OR belongs to
        another tenant matches zero rows and returns ``None`` — the route
        maps that to a 404 identical to a genuinely-nonexistent id, so a
        foreign id is indistinguishable from a missing one (no existence
        leak).

        Args:
            tenant_id: The verified tenant (authenticated via the webhook
                HMAC secret) that owns the row being mutated.
            call_id: The call UUID.

        Returns:
            Confirmation dict on a real write, or ``None`` when no row
            matched (not-found / cross-tenant) so a 200 always means an
            actual write happened.
        """
        # Atomic, tenant-scoped write. RETURNING * (via the adapter) hands
        # back the affected rows AND dialer_job_id in one round trip.
        call_res = self._db_client.table("calls").update({
            "goal_achieved": True,
            "outcome": CallOutcome.GOAL_ACHIEVED.value,
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("id", call_id).eq("tenant_id", tenant_id).execute()

        call_rows = call_res.data or []
        if not call_rows:
            # Zero affected rows == nonexistent OR another tenant's call.
            logger.warning(
                "mark_goal_achieved: no call matched id=%s tenant=%s "
                "(not found or cross-tenant) — nothing written",
                call_id, tenant_id,
            )
            return None

        # Scope the derived dialer_jobs update by tenant_id too — never
        # trust the call's job pointer to escape the tenant boundary.
        job_id = call_rows[0].get("dialer_job_id")
        if job_id:
            job_res = self._db_client.table("dialer_jobs").update({
                "status": JobStatus.GOAL_ACHIEVED.value,
                "last_outcome": CallOutcome.GOAL_ACHIEVED.value,
                "completed_at": datetime.utcnow().isoformat(),
            }).eq("id", job_id).eq("tenant_id", tenant_id).execute()
            if not (job_res.data or []):
                logger.warning(
                    "mark_goal_achieved: call %s updated but dialer_job %s "
                    "not updated for tenant %s (row missing/foreign)",
                    call_id, job_id, tenant_id,
                )

        logger.info("Goal achieved for call %s (tenant %s)", call_id, tenant_id)
        return {"message": "Goal marked as achieved", "call_id": call_id}

    async def mark_as_spam(
        self,
        tenant_id: str,
        call_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        reason: str = "spam",
    ) -> Optional[dict]:
        """
        Mark a call/lead as spam — prevents future calls. SCOPED to ``tenant_id``.

        SECURITY (object-level authz, P0): both the ``calls`` and ``leads``
        writes carry an ``AND tenant_id = $`` predicate, so tenant A can
        never spam-mark / DNC tenant B's rows. Ownership of the call is
        validated BEFORE any mutation, so a cross-tenant or mismatched
        request writes NOTHING (no partial update). A supplied ``lead_id``
        that is not the scoped call's own lead is rejected
        (``WebhookTargetMismatch`` → 400) rather than silently DNC-ing an
        unrelated lead.

        Returns the confirmation dict on a real write, or ``None`` when no
        row matched (not-found / cross-tenant) so a 200 always means an
        actual write happened.
        """
        outcome_map = {
            "spam": CallOutcome.SPAM,
            "invalid": CallOutcome.INVALID,
            "unavailable": CallOutcome.UNAVAILABLE,
            "disconnected": CallOutcome.DISCONNECTED,
        }
        outcome = outcome_map.get(reason, CallOutcome.SPAM)

        resolved_lead_id: Optional[str] = None

        if call_id:
            # Validate ownership FIRST (tenant-scoped) so a foreign/mismatched
            # request never performs a partial write.
            sel = self._db_client.table("calls").select("id, lead_id").eq(
                "id", call_id).eq("tenant_id", tenant_id).single().execute()
            call_row = sel.data
            if not call_row:
                logger.warning(
                    "mark_as_spam: no call matched id=%s tenant=%s "
                    "(not found or cross-tenant) — nothing written",
                    call_id, tenant_id,
                )
                return None

            real_lead = call_row.get("lead_id")
            real_lead_str = str(real_lead) if real_lead is not None else None
            # A caller may not piggyback a spam-mark of an unrelated lead
            # onto a call they legitimately own.
            if lead_id is not None and str(lead_id) != real_lead_str:
                raise WebhookTargetMismatch(
                    "lead_id does not belong to the specified call"
                )

            upd = self._db_client.table("calls").update({
                "outcome": outcome.value,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", call_id).eq("tenant_id", tenant_id).execute()
            if not (upd.data or []):
                # Row vanished / changed tenant between select and update.
                logger.warning(
                    "mark_as_spam: call %s no longer matched tenant %s at "
                    "update time — nothing written", call_id, tenant_id,
                )
                return None

            resolved_lead_id = real_lead_str
        else:
            # Lead-only request: the supplied lead_id is only ever acted on
            # under the tenant predicate below.
            resolved_lead_id = str(lead_id) if lead_id is not None else None

        if resolved_lead_id:
            lead_res = self._db_client.table("leads").update({
                "status": "dnc",
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", resolved_lead_id).eq("tenant_id", tenant_id).execute()
            if not (lead_res.data or []):
                if not call_id:
                    # Lead-only request, foreign/nonexistent lead → nothing
                    # written → 404 (indistinguishable from not-found).
                    logger.warning(
                        "mark_as_spam: no lead matched id=%s tenant=%s "
                        "(not found or cross-tenant) — nothing written",
                        resolved_lead_id, tenant_id,
                    )
                    return None
                # Call was marked, but its lead row is missing/foreign — a
                # data anomaly, not a client-facing error.
                logger.warning(
                    "mark_as_spam: call %s marked but lead %s not updated "
                    "for tenant %s", call_id, resolved_lead_id, tenant_id,
                )

        if not call_id and not resolved_lead_id:
            # Neither identifier supplied — nothing to do; do not fake success.
            return None

        logger.info(
            "Marked as %s: call=%s lead=%s tenant=%s",
            reason, call_id, resolved_lead_id, tenant_id,
        )
        return {
            "message": f"Marked as {reason}",
            "call_id": call_id,
            "lead_id": resolved_lead_id,
        }


def get_call_service(db_client: Client, queue_service: Optional[DialerQueueService] = None) -> CallService:
    """Factory function for dependency injection."""
    return CallService(db_client=db_client, queue_service=queue_service)

"""
Call Service
Domain service for call lifecycle management.

Extracts business logic from webhooks.py endpoints into a testable,
reusable service following the Domain-Driven Design pattern established
by CampaignService.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

import asyncpg

from app.core.postgres_adapter import Client
from app.core.db import DatabasePoolTimeoutError, _ACQUIRE_TIMEOUT_S
from app.core.db_utils import acquire_with_tenant
from app.core.security.tenant_isolation import get_bypass_rls, get_current_tenant_id

from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.services.queue_service import DialerQueueService
from app.domain.repositories.call_repository import CallRepository
from app.domain.repositories.lead_repository import LeadRepository
from app.domain.services.call_status import (
    TERMINAL_CALL_STATUSES,
    CallOutcome as CallStatusOutcome,
)
from app.domain.services.dialer.job_states import IN_FLIGHT_STATUSES
from app.domain.services.dialer.job_lifecycle import NOT_ORIGINATED_JOB_STATUSES
from app.workers.disposition_policy import DNC_OUTCOMES
from app.domain.services.dnc_service import normalize_e164_for_storage

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

# Outcomes that should NOT retry (used for the lead's terminal 'failed'
# status, campaign-counter routing, and the terminal job status).
#
# A call OUTCOME never sets ``leads.status = 'dnc'`` (2026-10-16): a carrier
# refusing our line (SIP 403 -> Q.850 21 -> REJECTED) is indistinguishable from
# a callee declining, and neither is a do-not-call request. 'dnc' is written
# only by the explicit suppression paths -- the caller opt-out check in
# ``_active_caller_opt_out_pooled``, the opt-out purge and the operator
# ``mark_as_spam`` -- and settlement never downgrades an existing 'dnc'.
#
# DERIVED, NOT DUPLICATED (compliance fix 2026-07-28): this used to be a
# hand-maintained literal set, and it drifted — it listed UNAVAILABLE
# (counted in calls_failed) while
# ``disposition_policy`` simultaneously scheduled UNAVAILABLE for two
# more dials at +24h. We marked a lead do-not-call and then called it
# twice: a TCPA/Ofcom breach. The retry brain is now the single source
# of truth for both halves, and it raises at import if any outcome is
# ever both non-retryable and retryable again.
#
# GOAL_ACHIEVED is added here (and not in disposition_policy's DNC set)
# because it is non-retryable for a different reason — success, not
# suppression. The counter/routing code below strips it back out before
# deciding calls_failed vs calls_completed.
NON_RETRYABLE_OUTCOMES = frozenset(DNC_OUTCOMES) | {CallOutcome.GOAL_ACHIEVED}

# Job fields a retry must carry over so the second attempt greets the same lead
# the same way. ``dialer_jobs`` (Postgres) stores none of them, so only the
# values the settlement path can supply travel (see _handle_job_completion_pooled).
_RETRY_IDENTITY_KEYS = (
    "first_speaker", "agent_name",
    "lead_first_name", "lead_last_name", "lead_company",
)

# ``mark_as_spam`` reasons that mean "stop calling this number" (suppression).
_MARK_AS_SUPPRESSION_REASONS = frozenset({"spam", "unavailable"})


@dataclass(frozen=True)
class CallStatusResult:
    """Durable outcome of one terminal callback.

    ``handle_call_status`` used to return ``None`` for both success and every
    persistence failure.  Teardown therefore had no way to distinguish a
    committed call/lead/job/campaign transaction from a database outage before
    acknowledging its Redis recovery ledger.  This result is the commit proof
    used at that boundary.

    ``applied`` means this invocation won the durable settlement marker and ran
    the database side effects.  A duplicate may still be ``durable=True`` when
    an earlier invocation already committed them.  ``durable`` is never true
    for a missing row, a partial legacy write, or a swallowed database error.
    """

    call_id: str
    found: bool
    applied: bool
    durable: bool
    terminal_status: Optional[str] = None
    terminal_outcome: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class _CallStatusExecution:
    """Internal DB result plus an optional post-commit Redis retry action.

    Iteration intentionally preserves the old private helper's three-value
    unpacking contract for downstream tests and any out-of-tree integrations.
    New code should read ``result`` directly.
    """

    result: CallStatusResult
    job_id: Optional[str] = None
    campaign_id: Optional[str] = None
    retry_args: Optional[tuple] = field(default=None, repr=False)

    def __iter__(self) -> Iterator[object]:
        yield self.job_id
        yield self.campaign_id
        yield self.retry_args


def _terminal_row_is_durable(row: object) -> bool:
    """Return whether a row proves the canonical DB settlement committed."""

    if row is None:
        return False
    try:
        status = str(row["status"] or "").strip().lower()  # type: ignore[index]
        outcome = row["outcome"]  # type: ignore[index]
        ended_at = row["ended_at"]  # type: ignore[index]
        settled_at = row["terminal_settled_at"]  # type: ignore[index]
        retry_payload = row["terminal_retry_payload"]  # type: ignore[index]
        retry_enqueued_at = row["terminal_retry_enqueued_at"]  # type: ignore[index]
    except (KeyError, TypeError):
        return False
    return bool(
        status in TERMINAL_CALL_STATUSES
        and outcome is not None
        and ended_at is not None
        and settled_at is not None
        and (retry_payload is None or retry_enqueued_at is not None)
    )


# Operator/telephony endpoints settle a call with the richer
# ``call_status.CallOutcome`` vocabulary, which the dialer's own CallOutcome
# enum does not contain. These are REFERENCED, never re-spelled: a second
# literal copy of the answered-outcome vocabulary is exactly how the outcome
# sets drifted apart on 2026-08-03 and made billed minutes disagree with the
# quota gate. `test_outcome_sets_have_exactly_one_definition` enforces this.
_POST_ANSWER_HANGUP_OUTCOMES = frozenset({
    CallStatusOutcome.AGENT_HUNG_UP.value,
    CallStatusOutcome.CUSTOMER_HUNG_UP.value,
})

# ``canceled`` is the US spelling already present in production rows (see
# ``TERMINAL_CALL_STATUSES``); it has no CallOutcome member of its own, so it
# sits alongside the canonical enum value rather than replacing it.
_PRE_ANSWER_CANCEL_OUTCOMES = frozenset({
    CallStatusOutcome.CANCELLED.value,
    "canceled",
})


def _effective_terminal_outcome(
    persisted: object,
    fallback: CallOutcome,
) -> CallOutcome:
    """Map the first persisted terminal fact to dialer disposition truth."""

    value = str(persisted or "").strip().lower()
    try:
        return CallOutcome(value)
    except ValueError:
        # Operator endpoints use the richer call-status vocabulary. Preserve
        # that first-writer fact while mapping it to the closest existing
        # dialer disposition (never the later callback's contradictory value).
        if value in _POST_ANSWER_HANGUP_OUTCOMES:
            return CallOutcome.ANSWERED
        if value in _PRE_ANSWER_CANCEL_OUTCOMES:
            return CallOutcome.GOAL_NOT_ACHIEVED
        logger.warning(
            "call_status: persisted terminal outcome %r is unmapped; using "
            "fail-closed goal_not_achieved disposition instead of callback %s",
            value,
            fallback.value,
        )
        return CallOutcome.GOAL_NOT_ACHIEVED


def _retry_args_from_payload(payload: object) -> Optional[tuple]:
    """Rehydrate a durable retry outbox payload for idempotent Redis replay."""

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    try:
        return (
            str(payload["job_id"]),
            dict(payload["job_data"]),
            CallOutcome(str(payload["outcome"])),
            str(payload.get("campaign_id") or ""),
            str(payload.get("lead_id") or ""),
            str(payload["tenant_id"]),
            int(payload["attempt_number"]),
            int(payload["delay_seconds"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


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
        # for that on 4-6 sequential round-trips. ``handle_call_status`` now
        # commits all database effects through the pooled, non-blocking path.
        # It fails closed without a pool because the old RPC/sequential path
        # could claim success between separate commits.
        # ``PostgresClient`` already owns the canonical asyncpg pool. Prefer it
        # even when an older factory omitted the explicit ``db_pool`` keyword;
        # this keeps every real runtime caller on the locked, transactional
        # settlement path. The RPC/sequential methods remain only as explicit
        # rejection shims for obsolete out-of-tree callers.
        self._db_pool = db_pool or getattr(db_client, "pool", None)
    
    # =========================================================================
    # Call Status Handling
    # =========================================================================
    
    async def handle_call_status(
        self,
        call_uuid: str,
        outcome: CallOutcome,
        duration: Optional[int] = None
    ) -> CallStatusResult:
        """
        Handle a call status update from the telephony provider.

        The call projection, lead status, dialer job, campaign counters and
        retry outbox are serialized by a row lock and committed in one pooled
        PostgreSQL transaction. A retry enqueue is then delivered from the
        durable outbox with a stable Redis idempotency key. No-pool callers
        receive an explicit non-durable result and perform no writes.

        Args:
            call_uuid: Unique call identifier from telephony provider
            outcome: The call outcome (answered, busy, failed, etc.)
            duration: Call duration in seconds (if available)
        """
        campaign_id: Optional[str] = None
        try:
            outcome_value = outcome.value if hasattr(outcome, 'value') else str(outcome)

            if self._db_pool is None:
                # Exact-once settlement spans call, lead, job and campaign
                # rows. The historical RPC/sequential compatibility chain used
                # separate connections and could mark the call committed before
                # later effects failed. Fail closed rather than expose that
                # partial-write mode. Every real PostgresClient supplies its
                # pool automatically in __init__; this only rejects obsolete
                # third-party adapters and incomplete test doubles.
                logger.error(
                    "handle_call_status requires an atomic database pool "
                    "for call=%s",
                    call_uuid,
                )
                return CallStatusResult(
                    call_id=call_uuid,
                    found=False,
                    applied=False,
                    durable=False,
                    error="atomic_pool_required",
                )

            execution = await self._handle_call_status_pooled(
                call_uuid, outcome, outcome_value, duration,
            )
            campaign_id = execution.campaign_id
            if execution.retry_args is not None:
                # Redis I/O — deliberately done AFTER the DB transaction
                # above has committed, so we never hold a pooled connection
                # while talking to Redis. The DB outbox remains pending until
                # both the idempotent enqueue and its DB acknowledgement have
                # succeeded.
                retry_idempotency_key = (
                    f"call-terminal:{call_uuid}:"
                    f"{execution.retry_args[0]}:"
                    f"{int(execution.retry_args[6]) + 1}"
                )
                retry_scheduled = await self._schedule_retry(
                    *execution.retry_args,
                    idempotency_key=retry_idempotency_key,
                )
                if not retry_scheduled:
                    return CallStatusResult(
                        call_id=call_uuid,
                        found=execution.result.found,
                        applied=execution.result.applied,
                        durable=False,
                        terminal_status=execution.result.terminal_status,
                        terminal_outcome=execution.result.terminal_outcome,
                        error="retry_enqueue_failed",
                    )
                result = await self._mark_retry_enqueued(
                    call_uuid,
                    applied=execution.result.applied,
                )
                if result.durable:
                    # The marker deliberately had no expiry while PostgreSQL
                    # acknowledgement was pending. Once the DB outbox is
                    # durable, age it best-effort; failure only leaks a safe
                    # idempotency key and must not revoke settlement proof.
                    confirm_retry = getattr(
                        self._queue_service,
                        "confirm_retry_once",
                        None,
                    )
                    if callable(confirm_retry):
                        try:
                            await asyncio.wait_for(
                                confirm_retry(retry_idempotency_key),
                                timeout=1.0,
                            )
                        except Exception as confirm_exc:  # noqa: BLE001
                            logger.debug(
                                "retry idempotency marker retention failed "
                                "call=%s err=%s",
                                call_uuid,
                                confirm_exc,
                            )
            else:
                result = execution.result

            if not result.durable:
                return result

            logger.info(
                "Call %s status durably settled: %s",
                call_uuid,
                result.terminal_outcome,
            )
            
            # --- CRM sync (additive, fire-and-forget) -------------------
            # Every settled outbound call - answered or not - is logged into
            # the tenant's connected CRMs (HubSpot / Salesforce). Scheduled,
            # never awaited: a slow CRM must not hold the call teardown.
            self._schedule_crm_sync(call_uuid)

            # --- Day 1: Event logging (additive, non-blocking) ---
            try:
                from app.domain.repositories.call_event_repository import CallEventRepository
                if result.applied:
                    event_repo = CallEventRepository(self._db_client)
                    await event_repo.log_event(
                        call_id=call_uuid,
                        event_type="state_change",
                        source="call_service",
                        event_data={
                            "outcome": result.terminal_outcome,
                            "duration": duration,
                            "campaign_id": campaign_id,
                        },
                        new_state=result.terminal_outcome or result.terminal_status,
                    )
            except Exception as evt_err:
                logger.debug(f"Event logging failed (non-critical): {evt_err}")

            return result
        except Exception as e:
            logger.error(f"Error handling call status for {call_uuid}: {e}", exc_info=True)
            return CallStatusResult(
                call_id=call_uuid,
                found=False,
                applied=False,
                durable=False,
                error=str(e),
            )
    
    @staticmethod
    def _schedule_crm_sync(call_uuid: str) -> None:
        """Best-effort hand-off to the CRM sync; never raises."""
        try:
            from app.core.security.tenant_isolation import get_current_tenant_id
            from app.services.crm_sync_service import schedule_crm_sync

            schedule_crm_sync(
                str(call_uuid),
                tenant_id=get_current_tenant_id(),
                reason="settlement",
            )
        except Exception as exc:  # noqa: BLE001 - auxiliary side effect
            logger.debug("crm_sync scheduling skipped for %s: %s", call_uuid, exc)

    async def _try_atomic_update(
        self, call_uuid: str, outcome_value: str, duration: Optional[int]
    ) -> Optional[dict]:
        """Reject the obsolete partial-transaction compatibility path.

        Terminal settlement spans calls, leads, dialer jobs, campaign counters,
        and a retry outbox. A standalone RPC cannot truthfully certify those
        application-owned effects, so it must never be used as a fallback.
        """
        raise RuntimeError("atomic_pool_required")
    
    async def _sequential_update(
        self, call_uuid: str, outcome: CallOutcome, outcome_value: str,
        duration: Optional[int]
    ) -> _CallStatusExecution:
        """Reject sequential settlement because it cannot be crash-atomic."""
        raise RuntimeError("atomic_pool_required")

    # =========================================================================
    # Pooled (async, non-blocking) teardown path — 2026-07-08
    # =========================================================================
    #
    # Everything below owns the canonical terminal settlement transaction.
    # The locked call row elects one database-side-effect winner, and the
    # marker/outbox written last lets lifecycle distinguish a durable commit
    # from a dependency failure before acknowledging its cleanup ledger.

    async def _handle_call_status_pooled(
        self,
        call_uuid: str,
        outcome: CallOutcome,
        outcome_value: str,
        duration: Optional[int],
    ) -> _CallStatusExecution:
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

        A saturated pool fails closed: `acquire_with_tenant`'s
        `timeout` mirrors `get_db()`'s bounded acquire
        (`PG_POOL_ACQUIRE_TIMEOUT`, default 10s) — on expiry we log and
        returns an explicit non-durable result instead of stalling teardown
        indefinitely or manufacturing success.

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
                # ---- Step 1: elect one durable settlement winner -----------
                # The row lock is the concurrency boundary. A provider burst,
                # an operator endpoint followed by the provider callback, and
                # two different worker processes serialize here. The endpoint
                # is allowed to win the terminal *status*; terminal_settled_at
                # separately records that outbound lead/job/campaign effects
                # have committed exactly once.
                row = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, phone_number, direction, is_test,
                           lead_id, campaign_id, dialer_job_id,
                           dialer_attempt_number, status,
                           outcome, ended_at, duration_seconds,
                           terminal_settled_at, terminal_retry_payload,
                           terminal_retry_enqueued_at
                    FROM calls WHERE id = $1
                    FOR UPDATE
                    """,
                    call_uuid,
                )
                if row is None:
                    logger.warning(f"Call not found: {call_uuid}")
                    return _CallStatusExecution(
                        result=CallStatusResult(
                            call_id=call_uuid,
                            found=False,
                            applied=False,
                            durable=False,
                            error="call_not_found",
                        )
                    )

                # Serialize lead projection with a concurrent opt-out purge
                # before reading DNC. Otherwise a cached negative lookup can
                # overwrite an opt-out which committed while this lock waited.
                if row["lead_id"]:
                    owned_lead = await conn.fetchval(
                        "SELECT id FROM leads WHERE id=$1 AND tenant_id=$2 FOR UPDATE",
                        row["lead_id"], row["tenant_id"],
                    )
                    if owned_lead is None:
                        raise RuntimeError("call_lead_owner_unverified")

                # The persisted call owns the original destination. A changed
                # lead phone or a process-local marker cannot authorize suppression.
                caller_opted_out = await self._active_caller_opt_out_pooled(conn, row)

                if row["terminal_settled_at"] is not None:
                    if caller_opted_out:
                        if row["lead_id"]:
                            await conn.execute(
                                """UPDATE leads SET status='dnc', last_call_result='caller_opt_out',
                                       updated_at=NOW() WHERE id=$1 AND tenant_id=$2""",
                                row["lead_id"], row["tenant_id"],
                            )
                        if (row["terminal_retry_payload"] is not None
                                and row["terminal_retry_enqueued_at"] is None):
                            # Recovering the outbox must not re-create work which
                            # a durable opt-out has since cancelled. No counters
                            # or prior call outcome are replayed/rewritten.
                            if row["dialer_job_id"]:
                                await conn.execute(
                                    """UPDATE dialer_jobs SET status='non_retryable',
                                           failure_reason='caller_opt_out', completed_at=NOW(), updated_at=NOW()
                                       WHERE id=$1 AND tenant_id=$2
                                         AND status=ANY($3::text[])""",
                                    row["dialer_job_id"], row["tenant_id"],
                                    # The prior enqueue may already own a newer
                                    # live attempt. Only its own finalizer may
                                    # release processing/calling ownership.
                                    list(NOT_ORIGINATED_JOB_STATUSES),
                                )
                            await conn.execute(
                                """UPDATE calls SET terminal_retry_payload = NULL,
                                       terminal_retry_enqueued_at = NULL, updated_at=NOW() WHERE id=$1""",
                                call_uuid,
                            )
                            row = dict(row)
                            row["terminal_retry_payload"] = None
                            row["terminal_retry_enqueued_at"] = None
                    # Migration 0028 stamps pre-cutover terminal rows to
                    # prevent historical lead/campaign effects from replaying.
                    # A rare legacy row may still be missing outcome/ended_at;
                    # fill only those call facts under the same lock, without
                    # re-running any settlement side effect.
                    if (
                        str(row["status"] or "") in TERMINAL_CALL_STATUSES
                        and (row["outcome"] is None or row["ended_at"] is None)
                    ):
                        row = await self._update_call_row_pooled(
                            conn,
                            call_uuid,
                            outcome_value,
                            duration,
                            already_terminal=True,
                        )
                    durable = _terminal_row_is_durable(row)
                    pending_retry_args = None
                    if (
                        row["terminal_retry_payload"] is not None
                        and row["terminal_retry_enqueued_at"] is None
                    ):
                        pending_retry_args = _retry_args_from_payload(
                            row["terminal_retry_payload"]
                        )
                        if pending_retry_args is None:
                            durable = False
                    logger.info(
                        "handle_call_status: call %s settlement already committed "
                        "— skipping duplicate side effects", call_uuid,
                    )
                    return _CallStatusExecution(
                        result=CallStatusResult(
                            call_id=call_uuid,
                            found=True,
                            applied=False,
                            durable=durable,
                            terminal_status=str(row["status"] or ""),
                            terminal_outcome=row["outcome"],
                            error=None if durable else (
                                "retry_enqueue_pending"
                                if pending_retry_args is not None
                                else "terminal_settlement_unverified"
                            ),
                        ),
                        job_id=(
                            str(row["dialer_job_id"])
                            if pending_retry_args is not None
                            and row["dialer_job_id"] else None
                        ),
                        campaign_id=(
                            str(row["campaign_id"])
                            if pending_retry_args is not None
                            and row["campaign_id"] else None
                        ),
                        retry_args=pending_retry_args,
                    )

                terminal_row = await self._update_call_row_pooled(
                    conn,
                    call_uuid,
                    outcome_value,
                    duration,
                    already_terminal=(
                        str(row["status"] or "") in TERMINAL_CALL_STATUSES
                    ),
                )
                effective_outcome = _effective_terminal_outcome(
                    terminal_row["outcome"] if terminal_row else None,
                    outcome,
                )

                lead_id = str(row["lead_id"]) if row["lead_id"] else None
                campaign_id = str(row["campaign_id"]) if row["campaign_id"] else None
                job_id = str(row["dialer_job_id"]) if row["dialer_job_id"] else None

                # Every lead / job / campaign statement below is scoped by the
                # LOCKED call row's tenant: the production DB role bypasses
                # RLS, so an id alone must never be enough to reach a row.
                call_tenant_id = row["tenant_id"]

                if lead_id:
                    await self._update_lead_status_pooled(
                        conn, lead_id, effective_outcome,
                        scope_tenant_id=call_tenant_id,
                        caller_opted_out=caller_opted_out,
                    )

                # ---- Step 2: dialer job completion + retry decision --------
                # Runs BEFORE the campaign counters (it used to run after) so
                # the counters can be gated on the retry decision — see
                # `_update_campaign_counters_pooled`. A lead that is going to
                # be redialled has not been "completed" or "failed" yet.
                lead_is_terminal = True
                if job_id:
                    lead_is_terminal, retry_args = await self._handle_job_completion_pooled(
                        conn,
                        job_id=job_id,
                        outcome=effective_outcome,
                        campaign_id=campaign_id or "",
                        lead_id=lead_id or "",
                        scope_tenant_id=call_tenant_id,
                        call_attempt_number=row.get("dialer_attempt_number"),
                        caller_opted_out=caller_opted_out,
                        call_id=call_uuid,
                    )

                # ---- Step 3: campaign counters ------------------------------
                if campaign_id and lead_is_terminal:
                    await self._update_campaign_counters_pooled(
                        conn, campaign_id, effective_outcome,
                        scope_tenant_id=call_tenant_id,
                    )

                retry_payload = None
                if retry_args is not None:
                    (
                        retry_job_id,
                        retry_job_data,
                        retry_outcome,
                        retry_campaign_id,
                        retry_lead_id,
                        retry_tenant_id,
                        retry_attempt_number,
                        retry_delay_seconds,
                    ) = retry_args
                    retry_payload = {
                        "job_id": retry_job_id,
                        "job_data": {
                            "priority": retry_job_data.get("priority", 5),
                            "phone_number": retry_job_data.get("phone_number", ""),
                            **{
                                key: retry_job_data[key]
                                for key in _RETRY_IDENTITY_KEYS
                                if retry_job_data.get(key)
                            },
                        },
                        "outcome": retry_outcome.value,
                        "campaign_id": retry_campaign_id,
                        "lead_id": retry_lead_id,
                        "tenant_id": retry_tenant_id,
                        "attempt_number": retry_attempt_number,
                        "delay_seconds": retry_delay_seconds,
                    }

                # Commit marker is intentionally the last DB write in this
                # transaction. Any exception above rolls back the terminal row
                # and every side effect together, leaving the callback
                # retryable. COALESCE protects against accidental rewrites.
                terminal_row = await conn.fetchrow(
                    """
                    UPDATE calls
                    SET terminal_settled_at = COALESCE(terminal_settled_at, NOW()),
                        terminal_retry_payload = $2::jsonb,
                        terminal_retry_enqueued_at = CASE
                            WHEN $2::jsonb IS NULL THEN NULL
                            ELSE terminal_retry_enqueued_at
                        END,
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING status, outcome, ended_at, terminal_settled_at,
                              terminal_retry_payload,
                              terminal_retry_enqueued_at
                    """,
                    call_uuid,
                    retry_payload,
                )
        except (asyncio.TimeoutError, DatabasePoolTimeoutError) as exc:
            logger.error(
                "handle_call_status: DB pool acquire timed out for call=%s "
                "— teardown degrading gracefully (no writes landed): %s",
                call_uuid, exc,
            )
            return _CallStatusExecution(
                result=CallStatusResult(
                    call_id=call_uuid,
                    found=False,
                    applied=False,
                    durable=False,
                    error=f"db_pool_timeout:{exc}",
                )
            )

        durable = _terminal_row_is_durable(terminal_row)
        return _CallStatusExecution(
            result=CallStatusResult(
                call_id=call_uuid,
                found=True,
                applied=True,
                durable=durable,
                terminal_status=(
                    str(terminal_row["status"] or "") if terminal_row else None
                ),
                terminal_outcome=(terminal_row["outcome"] if terminal_row else None),
                error=None if durable else "terminal_settlement_unverified",
            ),
            job_id=job_id,
            campaign_id=campaign_id,
            retry_args=retry_args,
        )

    async def _mark_retry_enqueued(
        self,
        call_uuid: str,
        *,
        applied: bool,
    ) -> CallStatusResult:
        """Acknowledge a durable retry outbox only after Redis confirms it.

        ``schedule_retry_once`` is idempotent, so a crash after its atomic
        Redis write but before this PostgreSQL acknowledgement is safe: the
        next terminal/recovery callback replays the same key, Redis reports it
        already present, and this update completes the cross-store commit.
        """

        bypass = get_bypass_rls()
        tenant_id = get_current_tenant_id()
        if not bypass and not tenant_id:
            return CallStatusResult(
                call_id=call_uuid,
                found=False,
                applied=applied,
                durable=False,
                error="retry_ack_missing_tenant_context",
            )
        try:
            async with acquire_with_tenant(
                self._db_pool,
                None if bypass else tenant_id,
                timeout=_ACQUIRE_TIMEOUT_S,
            ) as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE calls
                    SET terminal_retry_enqueued_at = COALESCE(
                            terminal_retry_enqueued_at, NOW()
                        ),
                        updated_at = NOW()
                    WHERE id = $1
                      AND terminal_retry_payload IS NOT NULL
                    RETURNING status, outcome, ended_at,
                              terminal_settled_at,
                              terminal_retry_payload,
                              terminal_retry_enqueued_at
                    """,
                    call_uuid,
                )
        except Exception as exc:  # noqa: BLE001 - returned as commit failure
            logger.error(
                "call_status retry outbox ack failed call=%s err=%s",
                call_uuid,
                exc,
            )
            return CallStatusResult(
                call_id=call_uuid,
                found=False,
                applied=applied,
                durable=False,
                error=f"retry_ack_failed:{exc}",
            )

        durable = _terminal_row_is_durable(row)
        return CallStatusResult(
            call_id=call_uuid,
            found=row is not None,
            applied=applied,
            durable=durable,
            terminal_status=str(row["status"] or "") if row else None,
            terminal_outcome=row["outcome"] if row else None,
            error=None if durable else "retry_ack_unverified",
        )

    async def _update_call_row_pooled(
        self,
        conn: asyncpg.Connection,
        call_uuid: str,
        outcome_value: str,
        duration: Optional[int],
        *,
        already_terminal: bool,
    ):
        """Persist terminal facts without overwriting an earlier terminal.

        An operator endpoint may have written ``ended`` first. In that case
        its status/outcome/duration remain authoritative and we only fill
        missing facts before running the once-only settlement side effects.
        """
        if already_terminal:
            return await conn.fetchrow(
                """
                UPDATE calls
                SET outcome = COALESCE(outcome, $2),
                    duration_seconds = COALESCE(duration_seconds, $3),
                    ended_at = COALESCE(ended_at, NOW()),
                    updated_at = NOW()
                WHERE id = $1
                RETURNING status, outcome, ended_at, duration_seconds,
                          terminal_settled_at, terminal_retry_payload,
                          terminal_retry_enqueued_at
                """,
                call_uuid,
                outcome_value,
                int(duration) if duration is not None else None,
            )

        return await conn.fetchrow(
            """
            UPDATE calls
            SET status = 'completed', outcome = $2,
                duration_seconds = COALESCE($3, duration_seconds),
                ended_at = COALESCE(ended_at, NOW()), updated_at = NOW()
            WHERE id = $1
              AND status <> ALL($4::text[])
            RETURNING status, outcome, ended_at, duration_seconds,
                      terminal_settled_at, terminal_retry_payload,
                      terminal_retry_enqueued_at
            """,
            call_uuid,
            outcome_value,
            int(duration) if duration is not None else None,
            list(TERMINAL_CALL_STATUSES),
        )

    async def _active_caller_opt_out_pooled(self, conn, call_row) -> bool:
        """Read suppression using the locked call's tenant and original phone.

        Global entries match the existing CallGuard policy. Holding a matching
        row share lock serializes its deletion/expiry update with settlement;
        later additions are still checked at final origination by CallGuard.
        A storage-lookup failure aborts settlement. A destination that is not
        strict E.164 (inbound/test labels, and outbound PBX extensions such as
        ``1234`` / ``940005``) can never have a suppression row -- every
        ``dnc_entries`` writer is gated by the same normaliser -- so no DNC
        claim is applicable and settlement proceeds. (An INBOUND row that owns
        dialer retry work with such a number is corrupt and still fails closed.)
        """
        tenant_id = str(call_row["tenant_id"])
        try:
            number = normalize_e164_for_storage(call_row["phone_number"])
        except ValueError:
            if (
                (call_row.get("direction") == "inbound" or call_row.get("is_test") is True)
                and call_row["dialer_job_id"] is None
                and call_row["terminal_retry_payload"] is None
            ):
                # Anonymous inbound ANI and browser-test are legitimate stored
                # labels, not dialable destinations. No DNC claim/lookup is
                # applicable, and these rows own no outbound retry work.
                return False
            if call_row.get("direction") == "inbound":
                # An inbound row that owns outbound retry work yet carries a
                # non-dialable number is corrupt data, not an extension call:
                # stay fail-closed exactly as before.
                raise
            if not re.fullmatch(r"\d{3,8}", str(call_row["phone_number"] or "").strip()):
                # Not an extension either (e.g. "anonymous"): a malformed
                # destination must never gain retry permission -- stay
                # fail-closed exactly as before.
                raise
            # Outbound to a PBX extension (3-8 digits, not E.164).
            # This used to re-raise: the settlement transaction rolled back on
            # every retry, the dialer job stayed 'processing' and the lead
            # 'calling' forever. No dnc_entries row can exist for it, so the
            # only record of an in-call opt-out is the lead projection the
            # purge wrote (status 'dnc', last_call_result 'caller_opt_out').
            # Honour that; otherwise settle normally. (Last digits only: a
            # malformed real number must not be written to the log.)
            lead_id = call_row.get("lead_id")
            if lead_id:
                projected_opt_out = await conn.fetchval(
                    """SELECT status = 'dnc' AND last_call_result = 'caller_opt_out'
                       FROM leads WHERE id = $1 AND tenant_id = $2::uuid""",
                    lead_id, tenant_id,
                )
                if projected_opt_out is True:
                    return True
            logger.warning(
                "caller opt-out lookup skipped: call=%s destination ending %s "
                "is not strict E.164 (PBX extension or malformed number); no "
                "DNC claim applies, settling normally",
                call_row["id"],
                str(call_row["phone_number"] or "")[-4:],
            )
            return False
        active = await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM dnc_entries
                   WHERE (tenant_id=$1::uuid OR tenant_id IS NULL)
                     AND normalized_number=$2 AND source='caller_opt_out'
                     AND (expires_at IS NULL OR expires_at > NOW())
                   FOR SHARE)""",
            tenant_id, number,
        )
        if not isinstance(active, bool):
            raise RuntimeError("caller_opt_out_lookup_unverified")
        return active

    async def _update_lead_status_pooled(
        self, conn: asyncpg.Connection, lead_id: str, outcome: CallOutcome,
        *, scope_tenant_id, caller_opted_out: bool = False,
    ) -> None:
        """Pooled equivalent of `_update_lead_status` — same status rules,
        except that a call outcome never writes 'dnc' (see the note on
        ``NON_RETRYABLE_OUTCOMES``)."""
        lead_status = "called"
        last_call_result = outcome.value if hasattr(outcome, 'value') else str(outcome)

        if outcome == CallOutcome.ANSWERED:
            lead_status = "contacted"
        elif outcome == CallOutcome.GOAL_ACHIEVED:
            lead_status = "completed"
            last_call_result = "goal_achieved"
        elif outcome in NON_RETRYABLE_OUTCOMES:
            # Terminal for the campaign (never auto-retried) but NOT
            # do-not-call: a carrier refusing our line (SIP 403 on INVITE ->
            # Q.850 21) looks exactly like a callee declining, and a decline
            # is not an opt-out. 'failed' is what restart resets to pending
            # and what manual redial accepts; 'dnc' blocked both forever.
            lead_status = "failed"

        if caller_opted_out:
            lead_status, last_call_result = "dnc", "caller_opt_out"

        current_attempts = await conn.fetchval(
            "SELECT call_attempts FROM leads WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
            lead_id, scope_tenant_id,
        )
        current_attempts = current_attempts or 0

        # An explicit 'dnc' already on the lead is never downgraded to
        # 'failed' by a late settlement, and an operator spam-mark (which sets
        # leads.do_not_call) survives ANY outcome: a retryable/ANSWERED result
        # used to put the lead back to 'called'/'contacted' and the retry
        # re-dialled a suppressed number. A 'dnc' with no flag whose
        # suppression row was removed (dnc_entries is the source of truth, see
        # _active_caller_opt_out_pooled) may still be cleared by a retryable
        # outcome. A contact the operator removed ('deleted') stays removed
        # when the call that was ringing at removal time settles.
        await conn.execute(
            """
            UPDATE leads
            SET status = CASE WHEN status = 'deleted'
                                   OR (status = 'dnc' AND (do_not_call OR $6::boolean))
                              THEN status ELSE $2 END,
                last_call_result = $3, last_called_at = NOW(),
                call_attempts = $4, updated_at = NOW()
            WHERE id = $1 AND tenant_id = $5
            """,
            lead_id, lead_status, last_call_result, current_attempts + 1,
            scope_tenant_id, lead_status == "failed",
        )

    async def _update_campaign_counters_pooled(
        self, conn: asyncpg.Connection, campaign_id: str, outcome: CallOutcome,
        *, scope_tenant_id,
    ) -> None:
        """Pooled equivalent of `_update_campaign_counters` — same routing
        rules (see that method's docstring for the counter table).

        ONE BUMP PER LEAD, not per attempt: the caller only invokes this
        once the lead has reached a terminal state (no retry scheduled), so
        `calls_completed + calls_failed` counts leads finished and can no
        longer exceed `total_leads`. Previously this ran on every attempt,
        before the retry decision, so a single busy lead redialled four
        times bumped `calls_completed` four times.
        """
        # INVALID is retried once to confirm it (disposition_policy), but a
        # confirmed dead number is still a lead we could not reach.
        non_reachable = (
            NON_RETRYABLE_OUTCOMES - {CallOutcome.GOAL_ACHIEVED}
        ) | {CallOutcome.INVALID}
        counter = "calls_failed" if outcome in non_reachable else "calls_completed"
        # `counter` is one of two hard-coded literals above — never
        # interpolated from caller input — so this is not SQL-injectable.
        await conn.execute(
            f"""
            UPDATE campaigns
            SET {counter} = COALESCE({counter}, 0) + 1, updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2
            """,
            campaign_id, scope_tenant_id,
        )

    async def _handle_job_completion_pooled(
        self,
        conn: asyncpg.Connection,
        job_id: str,
        outcome: CallOutcome,
        campaign_id: str,
        lead_id: str,
        *, scope_tenant_id,
        call_attempt_number: Optional[int] = None,
        caller_opted_out: bool = False,
        call_id=None,
    ) -> tuple[bool, Optional[tuple]]:
        """Pooled equivalent of `_handle_job_completion`.

        Returns ``(lead_is_terminal, retry_args)``.

        * ``lead_is_terminal`` — True when this teardown really finalised the
          lead (no further attempt is coming). The caller uses it to decide
          whether to bump the campaign counters: a lead awaiting a retry, or
          a duplicate teardown whose idempotency guard tripped, must not
          count.
        * ``retry_args`` — the positional-argument tuple for
          `_schedule_retry` when a retry is due, else None. The caller must
          invoke `_schedule_retry` AFTER the transaction commits — that call
          talks to Redis and must not run while holding a pooled DB
          connection.

        ``scope_tenant_id`` is the locked call row's tenant: every statement
        carries it (the production DB role bypasses RLS). ``call_attempt_number``
        is that call's ``dialer_attempt_number``; it lets a call that settled
        BEFORE the dialer flipped its job to ``processing`` finalise the job
        (see the guard below).
        """
        job_data = await conn.fetchrow(
            "SELECT * FROM dialer_jobs WHERE id = $1 AND tenant_id = $2",
            job_id, scope_tenant_id,
        )
        if job_data is None:
            logger.warning(f"Dialer job not found: {job_id}")
            # Nothing to retry — the lead is as finished as we can tell, so
            # let the caller count it rather than stalling campaign progress.
            return True, None

        attempt_number = await self._effective_attempt_number(
            job_id, job_data["attempt_number"],
        )
        tenant_id = job_data["tenant_id"] or "default-tenant"

        from app.workers.disposition_policy import decide as decide_disposition
        decision = decide_disposition(outcome, attempt_number)

        should_retry = decision.should_retry and not caller_opted_out
        failure_reason = "caller_opt_out" if caller_opted_out else decision.reason
        if caller_opted_out:
            final_status = JobStatus.NON_RETRYABLE
        elif decision.is_success:
            final_status = (
                JobStatus.GOAL_ACHIEVED
                if outcome == CallOutcome.GOAL_ACHIEVED
                else JobStatus.COMPLETED
            )
        elif should_retry:
            final_status = JobStatus.RETRY_SCHEDULED
        elif outcome in NON_RETRYABLE_OUTCOMES:
            final_status = JobStatus.NON_RETRYABLE
        else:
            final_status = JobStatus.FAILED

        final_status_value = (
            final_status.value if hasattr(final_status, 'value') else str(final_status)
        )
        outcome_value = outcome.value if hasattr(outcome, 'value') else str(outcome)

        # Idempotency guard (defense in depth, alongside the call settlement
        # marker): only ever transition a job out of the canonical in-flight
        # states once. ``calling`` is retained for older workers/schemas while
        # current workers use ``processing``. If a duplicate teardown reaches
        # this far, updated_job_id is None and no second retry is scheduled.
        #
        # SETTLED-BEFORE-PROCESSING (2026-10-16): a call can reach terminal
        # settlement before the dialer has flipped its job to ``processing``
        # (the originate returned and the call ended first). The guard used to
        # match only the in-flight statuses, so that settlement no-op'd: no
        # retry, no counters, job left active. The job is also finalised from
        # the not-yet-originated statuses (pending / queued / retry_scheduled)
        # when it is provably THIS call's attempt: the call row points at this
        # job (job_id comes from calls.dialer_job_id) AND carries the job's
        # attempt number. A call without an attempt number (legacy rows,
        # released intents) never finalises a pending job, so a late
        # settlement of an earlier attempt cannot close the newer attempt.
        call_attempt = (
            int(call_attempt_number) if call_attempt_number is not None else None
        )
        if should_retry:
            # ATTEMPT ACCOUNTING (compliance-critical): advance the persisted
            # counter in the SAME guarded UPDATE that books the retry. Nothing
            # else in the codebase ever wrote this column, so it sat at 1
            # forever and `disposition_policy`'s `attempt_number >= cap` test
            # (every cap is >= 2) could never fire — a busy lead was redialled
            # every 5 minutes indefinitely. The increment rides the existing
            # in-flight-status guard, so it is atomic and cannot be
            # applied twice by a duplicate teardown. Expressed as SQL rather
            # than a Python value so it stays a pure, race-free increment.
            #
            # This column therefore counts CONNECTED (post-answer) attempts.
            # Attempts that died before connecting are counted only in the
            # Redis job, which `_effective_attempt_number` folds in with a
            # max() — so the live signal can only ever make the cap fire
            # SOONER, never later. Both paths are independently bounded.
            updated_job_id = await conn.fetchval(
                """
                UPDATE dialer_jobs
                SET status = $2, last_outcome = $3, failure_reason = $4,
                    attempt_number = GREATEST(COALESCE(attempt_number, 1), 1) + 1,
                    updated_at = NOW()
                WHERE id = $1 AND tenant_id = $6
                  AND (status = ANY($5::text[])
                       OR (status = ANY($7::text[])
                           AND $8::int IS NOT NULL
                           AND attempt_number = $8::int))
                RETURNING id
                """,
                job_id, final_status_value, outcome_value, failure_reason,
                list(IN_FLIGHT_STATUSES), scope_tenant_id,
                list(NOT_ORIGINATED_JOB_STATUSES), call_attempt,
            )
        else:
            updated_job_id = await conn.fetchval(
                """
                UPDATE dialer_jobs
                SET status = $2, last_outcome = $3, failure_reason = $4,
                    updated_at = NOW(), completed_at = NOW()
                WHERE id = $1 AND tenant_id = $6
                  AND (status = ANY($5::text[])
                       OR (status = ANY($7::text[])
                           AND $8::int IS NOT NULL
                           AND attempt_number = $8::int))
                RETURNING id
                """,
                job_id, final_status_value, outcome_value, failure_reason,
                list(IN_FLIGHT_STATUSES), scope_tenant_id,
                list(NOT_ORIGINATED_JOB_STATUSES), call_attempt,
            )

        if updated_job_id is None:
            logger.info(
                "job_completion job=%s already finalized (status was not "
                "in-flight, or a different attempt) — skipping duplicate "
                "finalize/retry-schedule",
                job_id,
            )
            # Someone else already finalised (and already counted) this job.
            return False, None

        logger.info(
            "job_completion job=%s final=%s %s",
            job_id, final_status_value, decision.log_message,
        )

        if not should_retry:
            return True, None

        logger.info(
            f"Scheduling retry for job {job_id} (attempt {attempt_number + 1}) "
            f"in {decision.delay_seconds}s"
        )
        # `_schedule_retry` reads job_data as a dict (job_data.get(...)); the
        # asyncpg Record supports mapping-style access, but pass a plain
        # dict for exact parity with the legacy path's `job_response.data[0]`.
        retry_job_data = dict(job_data)
        # dialer_jobs has no identity columns, so the retry job used to come
        # back nameless (a named lead greeted as a blind dial). The lead row is
        # the source of truth for who is being called; read it here, inside the
        # settlement transaction and under the tenant predicate.
        if lead_id:
            try:
                lead_ident = await conn.fetchrow(
                    "SELECT first_name, last_name, company FROM leads "
                    "WHERE id = $1 AND tenant_id = $2",
                    lead_id, scope_tenant_id,
                )
            except Exception as exc:  # identity is cosmetic; never block the retry
                logger.warning("retry identity lookup failed lead=%s: %s", lead_id, exc)
                lead_ident = None
            if lead_ident is not None:
                for src, dst in (
                    ("first_name", "lead_first_name"),
                    ("last_name", "lead_last_name"),
                    ("company", "lead_company"),
                ):
                    value = lead_ident[src] if src in lead_ident.keys() else None
                    if value:
                        retry_job_data[dst] = value
        # first_speaker / agent_name are per-Start choices dialer_jobs cannot
        # store; the dialer recorded them in THIS call's pstn_outbound leg
        # metadata. Tenant predicate via the calls row (role bypasses RLS).
        if call_id:
            try:
                leg = await conn.fetchrow(
                    "SELECT cl.metadata FROM call_legs cl "
                    "JOIN calls c ON c.id = cl.call_id "
                    "WHERE cl.call_id = $1 AND c.tenant_id = $2 "
                    "AND cl.leg_type = 'pstn_outbound' "
                    "ORDER BY cl.created_at ASC LIMIT 1",
                    call_id, scope_tenant_id,
                )
                meta = leg["metadata"] if leg is not None else None
                if isinstance(meta, (str, bytes)):
                    meta = json.loads(meta)
                if isinstance(meta, dict):
                    if meta.get("first_speaker") in ("agent", "user"):
                        retry_job_data["first_speaker"] = meta["first_speaker"]
                    name = meta.get("agent_name")
                    if isinstance(name, str) and name.strip():
                        retry_job_data["agent_name"] = name
            except Exception as exc:  # identity is cosmetic; never block the retry
                logger.warning("retry opening-mode lookup failed call=%s: %s", call_id, exc)
        return False, (
            job_id, retry_job_data, outcome, campaign_id, lead_id,
            str(tenant_id), attempt_number, decision.delay_seconds,
        )

    async def _effective_attempt_number(
        self, job_id: str, db_attempt_number: Optional[int],
    ) -> int:
        """How many dial attempts this lead has had, counting the one that
        just finished. This is the number the retry cap is enforced against.

        Two counters exist and neither is complete on its own:

        * ``dialer_jobs.attempt_number`` (Postgres) — durable, but only
          advanced by the post-answer finalizer, so it counts CONNECTED
          attempts. Before 2026-07-28 nothing advanced it at all, which is
          why the cap never fired.
        * the Redis ``DialerJob`` inflight payload — advanced by
          ``DialerQueueService.schedule_retry`` on BOTH the pre-answer
          (originate failed) and post-answer paths, so it is the true total
          while the call is live; but it is untracked when the job leaves
          flight and aged out by ``reap_stale_processing`` after ~15 min, so
          it is not durable.

        Taking the MAX is the compliance-safe combination: the live counter
        can only pull the cap forward (fewer calls), and the durable column
        guarantees a bound even when Redis has nothing to say. The lookup is
        advisory — any failure, timeout or missing service degrades silently
        to the DB value rather than risking the teardown.
        """
        db_value = 1
        try:
            if db_attempt_number is not None:
                db_value = max(1, int(db_attempt_number))
        except (TypeError, ValueError):
            db_value = 1

        getter = getattr(self._queue_service, "get_live_attempt_number", None)
        if getter is None:
            return db_value

        try:
            # Bounded: this is the one Redis read that happens while the
            # pooled DB connection is held (the retry ENQUEUE is still
            # deferred until after the commit). A stalled Redis must not
            # extend the transaction, so cap the wait and fall back.
            live = await asyncio.wait_for(getter(job_id), timeout=0.5)
        except Exception as exc:  # noqa: BLE001 — advisory, never fatal
            logger.debug(
                "live attempt lookup failed job=%s err=%s — using DB value %s",
                job_id, exc, db_value,
            )
            return db_value

        if isinstance(live, bool) or not isinstance(live, int) or live < 1:
            return db_value
        if live > db_value:
            logger.info(
                "attempt count job=%s: live=%s > persisted=%s — enforcing the "
                "cap against the live count", job_id, live, db_value,
            )
        return max(db_value, live)

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
            # Terminal but not do-not-call (see _update_lead_status_pooled).
            lead_status = "failed"

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
        # INVALID is retried once to confirm it (disposition_policy), but a
        # confirmed dead number is still a lead we could not reach.
        non_reachable = (
            NON_RETRYABLE_OUTCOMES - {CallOutcome.GOAL_ACHIEVED}
        ) | {CallOutcome.INVALID}
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
    ) -> bool:
        """
        Handle dialer job completion — decide retry or finalize.

        Returns ``lead_is_terminal``: True when no further attempt is coming,
        so the caller may bump the campaign counters. False when a retry was
        scheduled (the lead isn't finished) or when finalisation failed.

        Retry policy is owned by ``disposition_policy.decide`` (the single
        source of truth for post-answer outcomes). It replaced the old
        flat ``RETRY_DELAY_SECONDS`` (2h for everything) + ``MAX_RETRY_
        ATTEMPTS`` (3 for everything) logic, which treated busy,
        no-answer and voicemail identically. Each disposition now has its
        own cadence and attempt cap:

            Busy      5m → 15m → 45m   (cap 4 total attempts)
            No-answer 24h → 24h        (cap 3)
            Voicemail 24h once         (cap 2)
            Unavail.  no retry (operator-marked)
            Rejected  no retry — lead 'failed', NOT do-not-call
            Failed    30s → 2m         (cap 3)
            Timeout   30s → 2m         (cap 3)
        """
        try:
            # Get job details
            job_response = self._db_client.table("dialer_jobs").select("*").eq("id", job_id).execute()

            if not job_response.data:
                logger.warning(f"Dialer job not found: {job_id}")
                return True

            job_data = job_response.data[0]
            # Live-or-persisted attempt count — see
            # `_effective_attempt_number`. Reading the raw column alone is
            # what let the cap sit at a frozen 1 forever.
            attempt_number = await self._effective_attempt_number(
                job_id, job_data.get("attempt_number"),
            )
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
            else:
                # Persist the advanced attempt count so the NEXT teardown
                # sees a higher number and the cap can actually be reached.
                # (Legacy non-pooled path — no `RETURNING`, so this is a
                # plain write of the value we already resolved.)
                job_update["attempt_number"] = attempt_number + 1

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

            return not decision.should_retry

        except Exception as e:
            logger.error(f"Error handling job completion for {job_id}: {e}", exc_info=True)
            # Unknown state — do NOT count the lead as finished.
            return False
    
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
        *,
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Schedule a retry for a dialer job after ``delay_seconds``.

        Fresh-first sequencing: a *recycled* (retry) job must never jump
        ahead of a never-tried lead. We clamp its priority below the
        high-priority lane so a retry can't preempt fresh traffic via the
        priority queue; combined with the delayed re-enqueue (which
        RPUSHes to the back of the tenant FIFO when due), fresh leads
        always drain before recycled ones.

        ``attempt_number`` is the attempt that JUST COMPLETED and is passed
        through unchanged: ``DialerQueueService.schedule_retry`` owns the
        single increment. This used to build the job with
        ``attempt_number + 1`` and then let the queue bump it again, so a
        retry job was stamped two attempts ahead of reality — burning a
        retry the lead had never been given.
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
            # NOT +1 — `schedule_retry` increments this exactly once.
            attempt_number=attempt_number,
            last_outcome=outcome,
            # Who is being called / how the call opens, when the caller of this
            # method knows it (absent keys keep the model defaults).
            **{
                key: job_data[key]
                for key in _RETRY_IDENTITY_KEYS
                if job_data.get(key)
            },
        )

        if self._queue_service:
            if idempotency_key:
                schedule_once = getattr(
                    self._queue_service, "schedule_retry_once", None
                )
                if not callable(schedule_once):
                    logger.error(
                        "Cannot durably schedule retry for job %s: queue "
                        "service lacks idempotent retry primitive",
                        job_id,
                    )
                    return False
                return bool(
                    await schedule_once(
                        retry_job,
                        delay_seconds=delay_seconds,
                        idempotency_key=idempotency_key,
                    )
                )
            return bool(
                await self._queue_service.schedule_retry(
                    retry_job, delay_seconds=delay_seconds
                )
            )
        logger.error(f"Cannot schedule retry for job {job_id}: queue service unavailable")
        return False
    
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

    async def _add_spam_mark_to_dnc_list(
        self, tenant_id: str, phone_number: Optional[str], reason: str,
    ) -> None:
        """Write the operator's suppression to ``dnc_entries`` too.

        ``leads.status`` alone is rewritten by a re-import into another
        campaign (fresh 'pending' lead for the same number), and CallGuard only
        reads ``dnc_entries`` / ``leads.do_not_call``. Never raises: the lead
        flag above is already written; a failure here is logged loudly.
        """
        try:
            normalized = normalize_e164_for_storage(str(phone_number or ""))
            pool = getattr(self._db_client, "pool", None)
            if pool is None:
                raise RuntimeError("no database pool on the call service client")
            from app.domain.services.dnc_service import DNCService, SOURCE_MANUAL_ADMIN

            await DNCService(pool).add(
                tenant_id=str(tenant_id), e164=normalized,
                source=SOURCE_MANUAL_ADMIN, reason=f"mark_as_{reason}",
            )
        except Exception as exc:
            logger.error(
                "mark_as_spam: lead flagged do_not_call but the DNC list write "
                "failed tenant=%s number_tail=%s: %s",
                tenant_id, str(phone_number or "")[-4:], exc,
            )

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
            # Only a human "stop calling this number" (spam / unavailable) is a
            # do-not-call. invalid / disconnected mean the line is dead: the
            # lead is finished ('failed', which a restart can reset) but NOT
            # suppressed, matching the outcome rules above.
            suppress = reason in _MARK_AS_SUPPRESSION_REASONS
            lead_update = {
                "status": "dnc" if suppress else "failed",
                "updated_at": datetime.utcnow().isoformat(),
            }
            if suppress:
                # The flag survives a re-import / settlement that rewrites status.
                lead_update["do_not_call"] = True
            lead_res = self._db_client.table("leads").update(
                lead_update
            ).eq("id", resolved_lead_id).eq("tenant_id", tenant_id).execute()
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

        if resolved_lead_id and reason in _MARK_AS_SUPPRESSION_REASONS:
            await self._add_spam_mark_to_dnc_list(
                tenant_id, lead_res.data[0].get("phone_number") if (lead_res.data or []) else None,
                reason,
            )

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

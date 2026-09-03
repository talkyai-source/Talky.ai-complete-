"""
Campaign Service
Handles campaign business logic, extracted from API endpoints.

Responsibilities:
- Campaign lifecycle management (start, pause, stop)
- Job creation and queuing
- Priority calculation
- Status updates

Day 9+ refactoring: Business logic extracted from campaigns.py endpoints
"""
import uuid
import logging
from datetime import datetime
from typing import Literal, Optional, List, Dict, Any
from dataclasses import dataclass

from app.core.postgres_adapter import Client

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.queue_service import DialerQueueService

logger = logging.getLogger(__name__)


@dataclass
class StartCampaignResult:
    """Result of starting a campaign"""
    success: bool
    message: str
    jobs_enqueued: int
    campaign_id: str
    queue_stats: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class CampaignError(Exception):
    """Base exception for campaign operations"""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class CampaignNotFoundError(CampaignError):
    """Raised when campaign doesn't exist"""
    def __init__(self, campaign_id: str):
        super().__init__(f"Campaign {campaign_id} not found", status_code=404)


class CampaignStateError(CampaignError):
    """Raised when campaign is in invalid state for operation"""
    def __init__(self, message: str):
        super().__init__(message, status_code=400)


class CampaignDirectionError(CampaignStateError):
    """Raised when the inbound lifecycle owns the requested campaign."""

    def __init__(self, message: str = "Inbound campaign requires inbound lifecycle"):
        CampaignError.__init__(self, message, status_code=409)


class CampaignDispatchError(CampaignError):
    """A durable dialer job could not be confirmed in the queue.

    ``jobs_enqueued`` counts Redis acknowledgements from this invocation.
    ``jobs_pending`` counts durable ``pending`` rows that a later start can
    reconcile.  Keeping both values on the exception lets HTTP callers report
    a partial dispatch without pretending the whole list was queued.
    """

    def __init__(
        self,
        message: str,
        *,
        jobs_enqueued: int = 0,
        jobs_pending: int = 0,
    ) -> None:
        super().__init__(message, status_code=503)
        self.jobs_enqueued = max(0, int(jobs_enqueued))
        self.jobs_pending = max(0, int(jobs_pending))


class CampaignService:
    """
    Domain service for campaign operations.

    Encapsulates all campaign business logic:
    - Starting/stopping campaigns
    - Job creation and priority calculation
    - Queue management

    Usage:
        service = CampaignService(db_client, queue_service)
        result = await service.start_campaign(campaign_id, tenant_id)
    """

    def __init__(
        self,
        db_client: Client,
        queue_service: Optional[DialerQueueService] = None
    ):
        """
        Initialize campaign service.

        Args:
            db_client: PostgreSQL client for database operations
            queue_service: Optional pre-configured queue service
        """
        self.db_client = db_client
        self._queue_service = queue_service
        self._owns_queue_service = queue_service is None

    # =========================================================================
    # Tenant scoping helper (defense-in-depth alongside Postgres RLS)
    # =========================================================================

    @staticmethod
    def _resolve_tenant_id(tenant_id: Optional[str]) -> Optional[str]:
        """Resolve the tenant to scope a query by.

        RLS (``app.current_tenant_id``, set per-query in
        ``app.core.postgres_adapter`` from this same context var) is the
        primary enforcement layer and already blocks cross-tenant rows at
        the database. The explicit ``.eq("tenant_id", ...)`` filters this
        helper enables are defense-in-depth on top of that — they also make
        the scoping unit-testable without a real Postgres/RLS instance.

        Prefers an explicitly-passed ``tenant_id`` (a caller that already
        knows/validated it); otherwise falls back to the per-request RLS
        context var so callers that don't thread tenant_id through (most of
        this file, and every one of its callers today) still get the same
        scoping RLS applies.

        Returns None when no tenant context exists at all (e.g. a genuine
        system/worker path with no authenticated request behind it) — in
        that case callers must skip the app-level filter and rely on RLS
        alone rather than force a filter that would silently match zero
        rows and break the caller.
        """
        if tenant_id:
            return str(tenant_id)
        try:
            from app.core.security.tenant_isolation import get_current_tenant_id
            return get_current_tenant_id()
        except Exception:
            return None

    async def _get_queue_service(self):
        """Get or create the dialer queue service.

        T2.2 — when `DIALER_QUEUE_BACKEND=streams`, returns the
        Redis Streams backend for new enqueues. The worker keeps
        using its own list-service instance so in-flight retries
        drain cleanly during cutover. Default behaviour is
        unchanged.
        """
        if self._queue_service is not None:
            return self._queue_service

        from app.domain.services.queue_factory import get_enqueue_service

        # If Redis is reachable via the container, hand it to the
        # factory so the streams backend can attach to the live pool.
        redis_client = None
        try:
            from app.core.container import get_container
            c = get_container()
            if c.is_initialized:
                redis_client = getattr(c, "redis", None)
        except Exception:
            pass

        self._queue_service = await get_enqueue_service(
            redis_client=redis_client,
        )
        return self._queue_service

    async def _cleanup_queue_service(self) -> None:
        """Close queue service if we own it."""
        if self._owns_queue_service and self._queue_service:
            await self._queue_service.close()
            self._queue_service = None

    # =========================================================================
    # Campaign Retrieval
    # =========================================================================

    async def get_campaign(
        self,
        campaign_id: str,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get campaign by ID.

        ``tenant_id`` (explicit param, else the per-request RLS context var)
        is applied as an app-level filter alongside RLS — defense-in-depth
        so a cross-tenant campaign_id resolves to CampaignNotFoundError
        exactly like a missing row, never another tenant's data.

        Raises:
            CampaignNotFoundError: If campaign doesn't exist (or belongs to
                another tenant)
        """
        scoped_tenant = self._resolve_tenant_id(tenant_id)
        query = self.db_client.table("campaigns").select("*").eq("id", campaign_id)
        if scoped_tenant:
            query = query.eq("tenant_id", scoped_tenant)
        response = query.execute()
        if not response.data:
            raise CampaignNotFoundError(campaign_id)
        return response.data[0]

    # =========================================================================
    # Start Campaign
    # =========================================================================

    async def start_campaign(
        self,
        campaign_id: str,
        tenant_id: Optional[str] = None,
        priority_override: Optional[int] = None,
        first_speaker: Literal["agent", "user"] = "agent",
        list_id: Optional[str] = None,
        allow_running: bool = False,
    ) -> StartCampaignResult:
        """
        Start a campaign - enqueue all pending leads as dialer jobs.

        Process:
        1. Validates the campaign exists and is in a valid state
        2. Fetches all leads with status='pending' for this campaign
        3. Creates DialerJob for each lead with priority handling
        4. Enqueues all jobs to Redis queue
        5. Stores job metadata in database
        6. Updates campaign status to 'running'

        Priority Logic:
        - Base priority from lead.priority (default 5)
        - High-value leads (is_high_value=true): +2 priority
        - Tags 'urgent' or 'appointment': +1 priority
        - Priority >= 8 goes to priority queue (processed first)

        Args:
            campaign_id: Campaign UUID
            tenant_id: Tenant ID (defaults to 'default-tenant')
            priority_override: Override priority for all jobs (1-10)

        Returns:
            StartCampaignResult with job counts and queue stats

        Raises:
            CampaignNotFoundError: If campaign doesn't exist
            CampaignStateError: If campaign is already running
        """
        try:
            # 0. Preserve the tenant scope requested at entry (may be None,
            # in which case get_campaign falls back to the RLS context var).
            # Used below for defense-in-depth filters — kept distinct from
            # the `tenant_id` local reassigned two lines down, which gets a
            # non-empty "default-tenant" fallback for job creation and must
            # never leak into a row filter (it wouldn't match a real DB row
            # and would silently zero out an update).
            scoping_tenant_id = tenant_id

            # 1. Validate campaign
            campaign = await self.get_campaign(campaign_id, tenant_id=scoping_tenant_id)

            # Inbound configurations have their own readiness/activation
            # lifecycle.  They must never enqueue outbound dialer jobs even if
            # a stale client calls the legacy campaign start endpoint.
            if campaign.get("direction", "outbound") != "outbound":
                raise CampaignDirectionError(
                    "Inbound campaigns cannot be started by the outbound dialer"
                )

            # ``allow_running`` lets "call this list" enqueue a list's pending
            # leads even while the campaign is already running — the active-job
            # dedup below prevents double-dialing, so re-entry is safe.
            campaign_was_running = campaign.get("status") == "running"

            # 2. Resolve tenant_id
            tenant_id = tenant_id or campaign.get("tenant_id") or "default-tenant"

            # 3. Get pending leads (optionally scoped to a single contact list)
            leads = await self._get_pending_leads(
                campaign_id, list_id=list_id, tenant_id=scoping_tenant_id
            )

            if not leads and list_id is None:
                # No pending/calling leads — reset failed/skipped leads so
                # a campaign restart actually retries them. Skipped for a
                # single-list dial: we never want a "call this list" to revive
                # other lists' failed leads.
                reset_count = await self._reset_leads_for_restart(
                    campaign_id, tenant_id=scoping_tenant_id
                )
                if reset_count > 0:
                    logger.info(
                        f"Campaign {campaign_id}: reset {reset_count} "
                        f"failed/skipped leads to pending for restart"
                    )
                    leads = await self._get_pending_leads(
                        campaign_id, tenant_id=scoping_tenant_id
                    )

            if not leads:
                if campaign_was_running and not allow_running:
                    raise CampaignStateError("Campaign is already running")
                await self._update_campaign_status(
                    campaign_id, "running", tenant_id=scoping_tenant_id
                )
                return StartCampaignResult(
                    success=True,
                    message=f"Campaign {campaign_id} started (no pending leads)",
                    jobs_enqueued=0,
                    campaign_id=campaign_id
                )

            # 4. Build the exact durable job set. PostgreSQL is the dispatch
            # authority: a Redis payload must never exist before its job row.
            # ``pending`` is the existing recoverable outbox state and
            # ``queued`` is written only after Redis acknowledges the payload.
            jobs_to_dispatch: List[DialerJob] = []
            jobs_data = []

            # Agent-name pool lives on the campaign — picked per-call so
            # a single campaign can rotate through up to 3 names. The
            # rotator itself is provider-agnostic (see
            # app.services.scripts.prompts.pick_agent_name).
            agent_names_pool: List[str] = []
            agent_name_genders: Dict[str, str] = {}
            script_cfg = campaign.get("script_config") if isinstance(campaign, dict) else None
            if isinstance(script_cfg, dict):
                raw_pool = script_cfg.get("agent_names") or []
                if isinstance(raw_pool, list):
                    agent_names_pool = [str(n).strip() for n in raw_pool if str(n).strip()]
                raw_genders = script_cfg.get("agent_name_genders") or {}
                if isinstance(raw_genders, dict):
                    agent_name_genders = {str(k): str(v) for k, v in raw_genders.items()}

            # Resolve the campaign voice's gender ONCE so each picked agent
            # name matches the voice (male voice → male name, etc.).
            voice_gender = None
            try:
                from app.domain.services.global_ai_config import resolve_voice_gender
                _campaign_voice = (
                    campaign.get("voice_id") if isinstance(campaign, dict) else None
                )
                voice_gender = resolve_voice_gender(_campaign_voice)
                # A campaign with no voice_id of its own SPEAKS WITH THE TENANT'S
                # voice, so resolve that one instead — otherwise voice_gender
                # stays None and the name is picked gender-blind, which is how a
                # male voice ended up introducing itself as "Sarah".
                if not _campaign_voice:
                    from app.domain.services.tenant_ai_config_resolver import (
                        get_tenant_ai_config_resolver,
                    )
                    _tenant_cfg = await get_tenant_ai_config_resolver().for_tenant_async(
                        str(tenant_id) if tenant_id else None
                    )
                    voice_gender = resolve_voice_gender(
                        getattr(_tenant_cfg, "tts_voice_id", None)
                    )
            except Exception as exc:
                logger.debug("voice gender resolve failed campaign=%s err=%s", campaign_id, exc)

            # De-dupe: never enqueue a second concurrent dial for a lead that
            # already has an active job. The unique index
            # uq_dialer_jobs_one_active_per_lead is the DB backstop; this
            # app-level pre-filter keeps the Redis queue + batch insert clean.
            from app.domain.services.dialer.job_states import ACTIVE_STATUSES
            active_jobs_by_lead: Dict[str, Dict[str, Any]] = {}
            try:
                lead_ids_all = [str(l["id"]) for l in leads]
                if lead_ids_all:
                    _dj_query = (
                        self.db_client.table("dialer_jobs")
                        .select("*")
                        .in_("lead_id", lead_ids_all)
                        .in_("status", list(ACTIVE_STATUSES))
                    )
                    # Defense-in-depth: `leads` is already tenant-scoped above
                    # so lead_ids_all can't contain another tenant's leads,
                    # but filter dialer_jobs by tenant too rather than trust
                    # that invariant transitively.
                    _dj_tenant = self._resolve_tenant_id(scoping_tenant_id)
                    if _dj_tenant:
                        _dj_query = _dj_query.eq("tenant_id", _dj_tenant)
                    res = _dj_query.execute()
                    if getattr(res, "error", None):
                        raise CampaignDispatchError(
                            "Active dialer jobs could not be verified"
                        )
                    active_jobs_by_lead = {
                        str(row["lead_id"]): dict(row)
                        for row in (getattr(res, "data", None) or [])
                    }
            except CampaignDispatchError:
                raise
            except Exception as exc:
                raise CampaignDispatchError(
                    "Active dialer jobs could not be verified"
                ) from exc

            skipped_active = 0
            recovery_only = campaign_was_running and not allow_running
            for lead in leads:
                active_job = active_jobs_by_lead.get(str(lead["id"]))
                if active_job is not None:
                    skipped_active += 1
                    if str(active_job.get("status") or "") == "pending":
                        jobs_to_dispatch.append(
                            self._restore_pending_job(
                                active_job,
                                lead=lead,
                                first_speaker=first_speaker,
                                agent_names_pool=agent_names_pool,
                                agent_name_genders=agent_name_genders,
                                voice_gender=voice_gender,
                            )
                        )
                    continue
                if recovery_only:
                    # A normal second Start on a running campaign may repair
                    # its pending outbox, but it must not add new work. The
                    # contact-list path opts into adding work via allow_running.
                    continue
                job, job_record = self._create_job_for_lead(
                    campaign_id=campaign_id,
                    lead=lead,
                    tenant_id=tenant_id,
                    priority_override=priority_override,
                    first_speaker=first_speaker,
                    agent_names_pool=agent_names_pool,
                    agent_name_genders=agent_name_genders,
                    voice_gender=voice_gender,
                )
                jobs_to_dispatch.append(job)
                jobs_data.append(job_record)

            if skipped_active:
                logger.info(
                    "campaign %s: skipped %d lead(s) that already had an active job",
                    campaign_id, skipped_active,
                )

            if recovery_only and not jobs_to_dispatch:
                raise CampaignStateError("Campaign is already running")

            # 5. Commit every new row before making any Redis payload visible.
            # Exact returned IDs are load-bearing: adapter errors arrive in a
            # PostgREST-style response envelope rather than as exceptions.
            await self._store_jobs_batch(jobs_data)

            # 6. The worker re-checks campaign status as soon as it dequeues a
            # payload. Set running after the durable insert but before Redis so
            # it can neither observe a missing job row nor skip valid work as a
            # stopped campaign. A failed status write leaves only recoverable
            # ``pending`` rows and exposes no Redis payload.
            if list_id is None:
                await self._update_campaign_status(
                    campaign_id,
                    status="running",
                    total_leads=len(leads),
                    tenant_id=scoping_tenant_id,
                )
            else:
                await self._update_campaign_status(
                    campaign_id, status="running", tenant_id=scoping_tenant_id
                )

            # 7. Dispatch the durable outbox. A false enqueue result is a real
            # failure, not a log line; rows that did not reach ``queued`` stay
            # pending so this same endpoint can reconcile them on retry.
            queue_service = await self._get_queue_service()
            try:
                try:
                    _redis = getattr(queue_service, "_redis", None)
                    if _redis is not None:
                        await _redis.delete(f"dialer:last_dial:{campaign_id}")
                except Exception as _gap_exc:
                    logger.debug("start_campaign: gap-clock reset failed: %s", _gap_exc)

                jobs_created = await self._dispatch_durable_jobs(
                    queue_service,
                    jobs_to_dispatch,
                )
                stats = await queue_service.get_queue_stats()
            finally:
                await self._cleanup_queue_service()

            logger.info(f"Campaign {campaign_id} started with {jobs_created} jobs")

            return StartCampaignResult(
                success=True,
                message=f"Campaign {campaign_id} started",
                jobs_enqueued=jobs_created,
                campaign_id=campaign_id,
                queue_stats=stats
            )

        except CampaignError:
            raise
        except Exception as e:
            logger.error(f"Error starting campaign {campaign_id}: {e}")
            raise CampaignError(f"Failed to start campaign: {str(e)}")

    # =========================================================================
    # Pause Campaign
    # =========================================================================

    async def pause_campaign(
        self,
        campaign_id: str,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Pause a campaign.

        Sets status='paused' (so the dialer stops dequeuing new jobs — it
        re-checks status before originate) AND hangs up calls already in flight.
        Pending jobs are intentionally LEFT queued so a later resume continues
        where it left off; only the live calls are dropped. Without the hangup
        sweep, "Pause" looked like a no-op for 30-60s while ringing/connected
        calls kept running.

        ``tenant_id`` is applied to the UPDATE as defense-in-depth alongside
        RLS (see ``_resolve_tenant_id``); it's the same value ``get_campaign``
        just validated the row against, so a successful get_campaign here
        guarantees the filtered UPDATE below matches that same row.
        """
        # Validate exists (and belongs to this tenant). Inbound campaigns have
        # a separate, versioned lifecycle with readiness and assignment audits;
        # no internal caller may bypass it through this legacy outbound service.
        campaign = await self.get_campaign(campaign_id, tenant_id=tenant_id)
        if campaign.get("direction", "outbound") != "outbound":
            raise CampaignDirectionError(
                "Inbound campaigns cannot be paused by the outbound dialer"
            )

        scoped_tenant = self._resolve_tenant_id(tenant_id)
        query = self.db_client.table("campaigns").update({
            "status": "paused"
        }).eq("id", campaign_id)
        if scoped_tenant:
            query = query.eq("tenant_id", scoped_tenant)
        response = query.eq("direction", "outbound").execute()
        if getattr(response, "error", None):
            raise CampaignError("Failed to pause campaign")
        if not getattr(response, "data", None):
            raise CampaignDirectionError(
                "Campaign direction changed; outbound pause was refused"
            )

        # Hang up live calls now. The campaign state is already paused, but
        # provider cleanup has its own explicit outcome: a partial/failed
        # sweep is returned to the operator and retained as
        # ``termination_pending`` for watchdog retries.
        termination: Dict[str, Any] = {
            "status": "lookup_failed",
            "total_selected": 0,
            "requested": 0,
            "attempted": 0,
            "confirmed": 0,
            "deferred": 0,
            "unconfirmed": 0,
            "missing_identity": 0,
            "reasons": {},
            "lookup_error": "termination_sweep_unavailable",
        }
        try:
            from app.api.v1.endpoints.telephony_bridge import (
                hangup_calls_for_campaign,
            )
            termination = await hangup_calls_for_campaign(campaign_id)
        except Exception as exc:
            logger.warning("pause_campaign hangup sweep failed: %s", exc)

        logger.info(
            "Campaign %s paused (termination_status=%s selected=%s "
            "requested=%s confirmed=%s deferred=%s)",
            campaign_id,
            termination.get("status"),
            termination.get("total_selected", 0),
            termination.get("requested", termination.get("attempted", 0)),
            termination.get("confirmed", 0),
            termination.get("deferred", termination.get("unconfirmed", 0)),
        )
        result = dict(response.data[0])
        result["termination_summary"] = termination
        return result

    # =========================================================================
    # Stop Campaign
    # =========================================================================

    async def stop_campaign(
        self,
        campaign_id: str,
        clear_queue: bool = False,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Stop a campaign completely.

        Args:
            campaign_id: Campaign UUID
            clear_queue: If True, mark pending jobs as skipped
            tenant_id: Applied to the UPDATE as defense-in-depth alongside
                RLS (see ``_resolve_tenant_id``); same value get_campaign
                just validated the row against.
        """
        # Validate exists (and belongs to this tenant). See pause_campaign for
        # why direction is checked again at the domain boundary.
        campaign = await self.get_campaign(campaign_id, tenant_id=tenant_id)
        if campaign.get("direction", "outbound") != "outbound":
            raise CampaignDirectionError(
                "Inbound campaigns cannot be stopped by the outbound dialer"
            )

        scoped_tenant = self._resolve_tenant_id(tenant_id)
        # Update campaign status
        query = self.db_client.table("campaigns").update({
            "status": "stopped",
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", campaign_id)
        if scoped_tenant:
            query = query.eq("tenant_id", scoped_tenant)
        response = query.eq("direction", "outbound").execute()
        if getattr(response, "error", None):
            raise CampaignError("Failed to stop campaign")
        if not getattr(response, "data", None):
            raise CampaignDirectionError(
                "Campaign direction changed; outbound stop was refused"
            )

        # Cancel only queued/not-yet-originated work immediately. Jobs already
        # processing/calling remain non-terminal until the PBX sweep below has
        # proved every leg absent and normal CallService settlement completes;
        # cancelling them here would release logical ownership before proof.
        from app.domain.services.dialer.job_lifecycle import (
            cancel_active_jobs_for_campaign,
            REASON_CAMPAIGN_STOPPED,
        )

        cleared_jobs = cancel_active_jobs_for_campaign(
            self.db_client, campaign_id, reason=REASON_CAMPAIGN_STOPPED,
        )
        # Drain the Redis queue too so jobs already dequeued into Redis don't
        # get processed after the stop. Best-effort — never block the stop.
        try:
            queue_service = await self._get_queue_service()
            await queue_service.clear_campaign_jobs(campaign_id)
            await self._cleanup_queue_service()
        except Exception as exc:
            logger.warning("stop_campaign: Redis queue clear failed: %s", exc)

        # Always hang up live calls for the campaign, regardless of whether
        # the operator chose to clear the pending queue. Stop = stop now,
        # not "stop after the in-flight calls finish on their own."
        # A hangup failure does not roll back the stopped state, but it is not
        # hidden: callers receive an explicit partial/lookup-failed summary.
        termination: Dict[str, Any] = {
            "status": "lookup_failed",
            "total_selected": 0,
            "requested": 0,
            "attempted": 0,
            "confirmed": 0,
            "deferred": 0,
            "unconfirmed": 0,
            "missing_identity": 0,
            "reasons": {},
            "lookup_error": "termination_sweep_unavailable",
        }
        try:
            from app.api.v1.endpoints.telephony_bridge import (
                hangup_calls_for_campaign,
            )
            termination = await hangup_calls_for_campaign(campaign_id)
        except Exception as exc:
            logger.warning("stop_campaign hangup sweep failed: %s", exc)

        logger.info(
            "Campaign %s stopped (clear_queue=%s, cleared_jobs=%s, "
            "termination_status=%s, selected=%s, requested=%s, "
            "confirmed=%s, deferred=%s)",
            campaign_id,
            clear_queue,
            cleared_jobs,
            termination.get("status"),
            termination.get("total_selected", 0),
            termination.get("requested", termination.get("attempted", 0)),
            termination.get("confirmed", 0),
            termination.get("deferred", termination.get("unconfirmed", 0)),
        )
        result = dict(response.data[0])
        result["termination_summary"] = termination
        return result

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _inactive_list_ids(self, campaign_id: str, tenant_id: Optional[str] = None) -> set:
        """Return the set of contact_list ids that are toggled OFF for this
        campaign.

        Leads whose ``list_id`` is in this set must NOT be dialed. Leads with
        list_id NULL (Ungrouped) or pointing at an active list are always
        eligible. Fail-safe: on ANY error (table missing, query failure) we
        return an empty set so the dialer keeps calling rather than silently
        going dark — an over-inclusive dial is far less harmful than a
        campaign that stops dead.

        ``tenant_id`` filter mirrors the ``contact_lists_tenant_isolation``
        RLS policy exactly (strict equality, no NULL bypass) — defense in
        depth, not a behavior change versus RLS alone.
        """
        try:
            query = (
                self.db_client.table("contact_lists")
                .select("id, is_active")
                .eq("campaign_id", campaign_id)
                .eq("is_active", False)
            )
            scoped_tenant = self._resolve_tenant_id(tenant_id)
            if scoped_tenant:
                query = query.eq("tenant_id", scoped_tenant)
            resp = query.execute()
            return {str(r["id"]) for r in (getattr(resp, "data", None) or [])}
        except Exception as exc:  # noqa: BLE001 — never let this stop dialing
            logger.warning(
                "active-list filter lookup failed for campaign %s (including all "
                "leads as fail-safe): %s",
                campaign_id, exc,
            )
            return set()

    async def _get_pending_leads(
        self,
        campaign_id: str,
        list_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all pending leads for a campaign, ordered by priority.
        Also includes leads stuck at 'calling' from a previous crashed run.

        Leads belonging to an INACTIVE contact list are excluded (the core of
        the list on/off toggle). Leads with list_id NULL or an active list are
        kept. When ``list_id`` is provided the result is additionally scoped to
        that single list ("call this list").

        Leads the customer flagged ``do_not_call`` are excluded here, at the
        selection boundary, so they are never enqueued at all — and so
        ``start_campaign``'s ``total_leads`` denominator counts only leads the
        campaign will actually attempt (the same treatment an inactive list's
        leads already get, rather than enqueuing them and recording a
        pseudo-outcome). ``leads.do_not_call`` is the per-contact suppression
        flag from migration 0020; it is ADDITIVE to the tenant DNC *list*
        (``dnc_entries``, keyed by normalised phone number and enforced by
        CallGuard) and never a replacement for it. CallGuard re-checks the
        flag at dial time so an older queue entry can't slip past this.

        ``IS NOT TRUE`` — not ``COALESCE(do_not_call, false) = false`` — so a
        NULL reads as "not flagged" while the predicate stays on the bare
        column and the partial index ``idx_leads_do_not_call`` remains usable.

        ``tenant_id`` is applied as an app-level filter alongside RLS
        (defense-in-depth).
        """
        scoped_tenant = self._resolve_tenant_id(tenant_id)
        query = self.db_client.table("leads").select("*")\
            .eq("campaign_id", campaign_id)\
            .in_("status", ["pending", "calling"])\
            .is_("do_not_call", "NOT TRUE")
        if scoped_tenant:
            query = query.eq("tenant_id", scoped_tenant)
        if list_id is not None:
            query = query.eq("list_id", list_id)
        response = query.order("priority", desc=True)\
            .order("created_at")\
            .execute()
        leads = response.data or []

        # The do_not_call exclusion above produces no job, no call row and no
        # outcome — by design. Count what it suppressed so the decision is
        # auditable instead of invisible. Best-effort: a failed count must
        # never stop a campaign starting. ``IS TRUE`` matches the partial
        # index idx_leads_do_not_call exactly; ``limit(1)`` keeps the exact
        # COUNT(*) while transferring no rows.
        try:
            supp_query = self.db_client.table("leads")\
                .select("id", count="exact")\
                .eq("campaign_id", campaign_id)\
                .in_("status", ["pending", "calling"])\
                .is_("do_not_call", "TRUE")\
                .limit(1)
            if scoped_tenant:
                supp_query = supp_query.eq("tenant_id", scoped_tenant)
            if list_id is not None:
                supp_query = supp_query.eq("list_id", list_id)
            supp_resp = supp_query.execute()
            suppressed = getattr(supp_resp, "count", None)
            if suppressed is None:
                suppressed = len(getattr(supp_resp, "data", None) or [])
            if suppressed:
                logger.info(
                    "campaign %s: suppressed %d lead(s) flagged do_not_call "
                    "(not enqueued, not counted as an outcome)",
                    campaign_id, suppressed,
                )
        except Exception as exc:  # noqa: BLE001 — observability only
            logger.debug(
                "do_not_call suppression count failed for campaign %s: %s",
                campaign_id, exc,
            )

        # Exclude leads whose list is toggled off. Skipped entirely when a
        # single list was requested (that list is being explicitly dialed).
        if list_id is None:
            inactive = self._inactive_list_ids(campaign_id, tenant_id=tenant_id)
            if inactive:
                leads = [
                    l for l in leads
                    if str(l.get("list_id")) not in inactive or l.get("list_id") is None
                ]
        return leads

    async def _reset_leads_for_restart(
        self, campaign_id: str, tenant_id: Optional[str] = None
    ) -> int:
        """Reset failed/skipped/calling leads to pending so a campaign restart retries them.

        ``tenant_id`` is applied to the UPDATE as defense-in-depth alongside
        RLS (see ``_resolve_tenant_id``).
        """
        scoped_tenant = self._resolve_tenant_id(tenant_id)
        query = self.db_client.table("leads").update({
            "status": "pending",
            "last_called_at": None,
        }).eq("campaign_id", campaign_id)\
          .in_("status", ["failed", "skipped", "calling"])
        if scoped_tenant:
            query = query.eq("tenant_id", scoped_tenant)
        response = query.execute()
        return len(response.data) if response.data else 0

    def _calculate_priority(
        self,
        lead: Dict[str, Any],
        priority_override: Optional[int] = None
    ) -> int:
        """
        Calculate job priority based on lead attributes.

        Priority Logic:
        - Base priority from lead.priority (default 5)
        - High-value leads: +2 priority
        - Urgent tags: +1 priority
        - Capped at 10
        """
        if priority_override is not None:
            return min(max(priority_override, 1), 10)

        base_priority = lead.get("priority", 5)

        # High-value boost
        if lead.get("is_high_value"):
            base_priority += 2

        # Urgent tag boost
        lead_tags = lead.get("tags", []) or []
        if any(tag in lead_tags for tag in ["urgent", "appointment", "reminder"]):
            base_priority += 1

        return min(base_priority, 10)

    def _create_job_for_lead(
        self,
        campaign_id: str,
        lead: Dict[str, Any],
        tenant_id: str,
        priority_override: Optional[int] = None,
        first_speaker: Literal["agent", "user"] = "agent",
        agent_names_pool: Optional[List[str]] = None,
        agent_name_genders: Optional[Dict[str, str]] = None,
        voice_gender: Optional[str] = None,
    ) -> tuple:
        """
        Create a DialerJob and database record for a lead.

        Returns:
            Tuple of (DialerJob, dict for database insert)
        """
        job_id = str(uuid.uuid4())
        priority = self._calculate_priority(lead, priority_override)
        now = datetime.utcnow()

        lead_id = str(lead["id"])
        tenant_id_str = str(tenant_id)
        phone_number = str(lead["phone_number"])

        # Lead identity for the "who you're calling" prompt block. Company
        # lives in the lead's custom_fields JSONB (written by bulk_ingest).
        # All best-effort: a missing name/company just yields a blind dial.
        lead_first_name = (lead.get("first_name") or None)
        lead_last_name = (lead.get("last_name") or None)
        _custom = lead.get("custom_fields") or {}
        lead_company = None
        if isinstance(_custom, dict):
            lead_company = (_custom.get("company") or None)

        # Pick an agent name from the campaign pool — stays stable for
        # the whole call. Fall back to None (legacy campaigns) so the
        # session config can use its own default pool.
        agent_name: Optional[str] = None
        if agent_names_pool:
            try:
                from app.services.scripts.prompts import pick_agent_name_for_voice
                # Seed on lead_id so a retried/re-created job for the same lead
                # keeps the same agent name instead of re-rolling.
                agent_name = pick_agent_name_for_voice(
                    agent_names_pool, agent_name_genders, voice_gender,
                    seed=lead_id,
                )
            except Exception as exc:
                logger.warning(
                    "agent_name_pick_failed campaign=%s pool=%s err=%s",
                    campaign_id, agent_names_pool, exc,
                )

        job = DialerJob(
            job_id=job_id,
            campaign_id=str(campaign_id),
            lead_id=lead_id,
            tenant_id=tenant_id_str,
            phone_number=phone_number,
            priority=priority,
            status=JobStatus.PENDING,
            attempt_number=1,
            scheduled_at=now,
            created_at=now,
            first_speaker=first_speaker,
            agent_name=agent_name,
            lead_first_name=lead_first_name,
            lead_last_name=lead_last_name,
            lead_company=lead_company,
        )

        job_record = {
            "id": job_id,
            "campaign_id": str(campaign_id),
            "lead_id": lead_id,
            "tenant_id": tenant_id_str,
            "phone_number": phone_number,
            "priority": priority,
            "status": "pending",
            "attempt_number": 1,
            "scheduled_at": now.isoformat(),
            "created_at": now.isoformat()
        }

        return job, job_record

    def _restore_pending_job(
        self,
        row: Dict[str, Any],
        *,
        lead: Dict[str, Any],
        first_speaker: Literal["agent", "user"],
        agent_names_pool: Optional[List[str]],
        agent_name_genders: Optional[Dict[str, str]],
        voice_gender: Optional[str],
    ) -> DialerJob:
        """Rehydrate one durable pending row for at-least-once dispatch.

        The database row owns routing and attempt identity. Optional prompt
        metadata is recomputed from the same lead/campaign inputs used for a
        new job (agent-name selection is lead-id seeded and deterministic).
        """
        template, _ = self._create_job_for_lead(
            campaign_id=str(row["campaign_id"]),
            lead=lead,
            tenant_id=str(row["tenant_id"]),
            priority_override=int(row.get("priority") or 5),
            first_speaker=first_speaker,
            agent_names_pool=agent_names_pool,
            agent_name_genders=agent_name_genders,
            voice_gender=voice_gender,
        )
        return template.model_copy(
            update={
                "job_id": str(row["id"]),
                "campaign_id": str(row["campaign_id"]),
                "lead_id": str(row["lead_id"]),
                "tenant_id": str(row["tenant_id"]),
                "phone_number": str(row["phone_number"]),
                "priority": int(row.get("priority") or 5),
                "status": JobStatus.PENDING,
                "attempt_number": int(row.get("attempt_number") or 1),
                "scheduled_at": row.get("scheduled_at") or datetime.utcnow(),
                "created_at": row.get("created_at") or datetime.utcnow(),
            }
        )

    async def _store_jobs_batch(self, jobs_data: List[Dict[str, Any]]) -> List[str]:
        """Persist a complete durable batch and verify every returned ID."""
        if not jobs_data:
            return []

        try:
            response = self.db_client.table("dialer_jobs").insert(jobs_data).execute()
        except Exception as exc:
            raise CampaignDispatchError(
                "Dialer jobs could not be stored",
                jobs_pending=len(jobs_data),
            ) from exc

        if getattr(response, "error", None):
            raise CampaignDispatchError(
                "Dialer jobs could not be stored",
                jobs_pending=len(jobs_data),
            )

        expected_ids = [str(row["id"]) for row in jobs_data]
        returned = getattr(response, "data", None)
        returned_rows = returned if isinstance(returned, list) else []
        returned_ids = [
            str(row.get("id"))
            for row in returned_rows
            if isinstance(row, dict) and row.get("id") is not None
        ]
        if len(returned_ids) != len(expected_ids) or set(returned_ids) != set(expected_ids):
            raise CampaignDispatchError(
                "Dialer job persistence could not be confirmed",
                jobs_pending=len(jobs_data),
            )
        return returned_ids

    async def _confirm_job_dispatched(self, job: DialerJob) -> None:
        """Move one durable outbox row from pending to queued exactly once."""
        try:
            response = (
                self.db_client.table("dialer_jobs")
                .update(
                    {
                        "status": "queued",
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                )
                .eq("id", str(job.job_id))
                .eq("tenant_id", str(job.tenant_id))
                .eq("campaign_id", str(job.campaign_id))
                .eq("lead_id", str(job.lead_id))
                .eq("status", "pending")
                .execute()
            )
        except Exception as exc:
            raise RuntimeError("dialer dispatch acknowledgement failed") from exc
        if getattr(response, "error", None):
            raise RuntimeError("dialer dispatch acknowledgement failed")
        rows = getattr(response, "data", None) or []
        if len(rows) != 1 or str(rows[0].get("id")) != str(job.job_id):
            raise RuntimeError("dialer dispatch acknowledgement was not exact")

    async def _dispatch_durable_jobs(
        self,
        queue_service,
        jobs: List[DialerJob],
    ) -> int:
        """Dispatch pending rows at least once and expose partial truth."""
        redis_accepted = 0
        db_acknowledged = 0
        for job in jobs:
            try:
                accepted = await queue_service.enqueue_job(job)
            except Exception as exc:
                raise CampaignDispatchError(
                    "Dialer queue dispatch failed",
                    jobs_enqueued=redis_accepted,
                    jobs_pending=len(jobs) - db_acknowledged,
                ) from exc
            if not accepted:
                raise CampaignDispatchError(
                    "Dialer queue dispatch failed",
                    jobs_enqueued=redis_accepted,
                    jobs_pending=len(jobs) - db_acknowledged,
                )
            redis_accepted += 1
            try:
                await self._confirm_job_dispatched(job)
            except Exception as exc:
                # Redis accepted this payload, but without the DB transition a
                # later reconciliation must replay it. The downstream durable
                # (dialer_job_id, attempt_number) fence makes that at-least-once
                # delivery safe.
                raise CampaignDispatchError(
                    "Dialer queue acknowledgement could not be persisted",
                    jobs_enqueued=redis_accepted,
                    jobs_pending=len(jobs) - db_acknowledged,
                ) from exc
            db_acknowledged += 1
        return redis_accepted

    async def _update_campaign_status(
        self,
        campaign_id: str,
        status: str,
        total_leads: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        """Update campaign status and metadata.

        ``tenant_id`` is applied to the UPDATE as defense-in-depth alongside
        RLS (see ``_resolve_tenant_id``). Callers within start_campaign pass
        the ORIGINAL entry-point tenant scope (``scoping_tenant_id``), never
        the "default-tenant" literal fallback used for job creation — that
        fallback string would not match a real row's tenant_id and would
        silently zero out this update.
        """
        update_data = {
            "status": status,
            "started_at": datetime.utcnow().isoformat()
        }
        if total_leads is not None:
            update_data["total_leads"] = total_leads

        scoped_tenant = self._resolve_tenant_id(tenant_id)
        query = (
            self.db_client.table("campaigns")
            .update(update_data)
            .eq("id", campaign_id)
        )
        if scoped_tenant:
            query = query.eq("tenant_id", scoped_tenant)
        response = query.eq("direction", "outbound").execute()
        if getattr(response, "error", None):
            raise CampaignError("Failed to update campaign status")
        if not getattr(response, "data", None):
            raise CampaignDirectionError(
                "Campaign direction changed; outbound start was refused"
            )


# =========================================================================
# Factory function for dependency injection
# =========================================================================

def get_campaign_service(db_client: Client) -> CampaignService:
    """
    Factory function for FastAPI dependency injection.

    Usage:
        @router.post("/campaigns/{id}/start")
        async def start(
            id: str,
            service: CampaignService = Depends(get_campaign_service)
        ):
            return await service.start_campaign(id)
    """
    return CampaignService(db_client)

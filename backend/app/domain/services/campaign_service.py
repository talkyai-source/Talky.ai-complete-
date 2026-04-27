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
    
    async def get_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """
        Get campaign by ID.
        
        Raises:
            CampaignNotFoundError: If campaign doesn't exist
        """
        response = self.db_client.table("campaigns").select("*").eq("id", campaign_id).execute()
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
            # 1. Validate campaign
            campaign = await self.get_campaign(campaign_id)
            
            if campaign.get("status") == "running":
                raise CampaignStateError("Campaign is already running")
            
            # 2. Resolve tenant_id
            tenant_id = tenant_id or campaign.get("tenant_id") or "default-tenant"
            
            # 3. Get pending leads
            leads = await self._get_pending_leads(campaign_id)

            if not leads:
                # No pending/calling leads — reset failed/skipped leads so
                # a campaign restart actually retries them.
                reset_count = await self._reset_leads_for_restart(campaign_id)
                if reset_count > 0:
                    logger.info(
                        f"Campaign {campaign_id}: reset {reset_count} "
                        f"failed/skipped leads to pending for restart"
                    )
                    leads = await self._get_pending_leads(campaign_id)

            if not leads:
                await self._update_campaign_status(campaign_id, "running")
                return StartCampaignResult(
                    success=True,
                    message=f"Campaign {campaign_id} started (no pending leads)",
                    jobs_enqueued=0,
                    campaign_id=campaign_id
                )
            
            # 4. Update campaign status to 'running' BEFORE enqueuing to Redis.
            # The dialer worker dequeues jobs and immediately validates campaign
            # status against the DB.  If status is updated after the Redis push,
            # the worker sees the old status (e.g. "stopped") and skips every job.
            await self._update_campaign_status(
                campaign_id,
                status="running",
                total_leads=len(leads)
            )

            # 5. Get queue service
            queue_service = await self._get_queue_service()

            # 6. Create and enqueue jobs
            jobs_created = 0
            jobs_data = []

            # Agent-name pool lives on the campaign — picked per-call so
            # a single campaign can rotate through up to 3 names. The
            # rotator itself is provider-agnostic (see
            # app.services.scripts.prompts.pick_agent_name).
            agent_names_pool: List[str] = []
            script_cfg = campaign.get("script_config") if isinstance(campaign, dict) else None
            if isinstance(script_cfg, dict):
                raw_pool = script_cfg.get("agent_names") or []
                if isinstance(raw_pool, list):
                    agent_names_pool = [str(n).strip() for n in raw_pool if str(n).strip()]

            for lead in leads:
                job, job_record = self._create_job_for_lead(
                    campaign_id=campaign_id,
                    lead=lead,
                    tenant_id=tenant_id,
                    priority_override=priority_override,
                    first_speaker=first_speaker,
                    agent_names_pool=agent_names_pool,
                )

                await queue_service.enqueue_job(job)
                jobs_data.append(job_record)
                jobs_created += 1

            # 7. Store jobs in database (batch insert)
            await self._store_jobs_batch(jobs_data)
            
            # 8. Get queue stats
            stats = await queue_service.get_queue_stats()
            
            # Cleanup if we own the queue service
            await self._cleanup_queue_service()
            
            logger.info(f"Campaign {campaign_id} started with {jobs_created} jobs")
            
            return StartCampaignResult(
                success=True,
                message=f"Campaign {campaign_id} started",
                jobs_enqueued=jobs_created,
                campaign_id=campaign_id,
                queue_stats=stats
            )
            
        except (CampaignNotFoundError, CampaignStateError):
            raise
        except Exception as e:
            logger.error(f"Error starting campaign {campaign_id}: {e}")
            raise CampaignError(f"Failed to start campaign: {str(e)}")
    
    # =========================================================================
    # Pause Campaign
    # =========================================================================
    
    async def pause_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Pause a campaign."""
        # Validate exists
        await self.get_campaign(campaign_id)
        
        response = self.db_client.table("campaigns").update({
            "status": "paused"
        }).eq("id", campaign_id).execute()
        
        logger.info(f"Campaign {campaign_id} paused")
        return response.data[0]
    
    # =========================================================================
    # Stop Campaign
    # =========================================================================
    
    async def stop_campaign(
        self,
        campaign_id: str,
        clear_queue: bool = False
    ) -> Dict[str, Any]:
        """
        Stop a campaign completely.
        
        Args:
            campaign_id: Campaign UUID
            clear_queue: If True, mark pending jobs as skipped
        """
        # Validate exists
        await self.get_campaign(campaign_id)
        
        # Update campaign status
        response = self.db_client.table("campaigns").update({
            "status": "stopped",
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", campaign_id).execute()
        
        cleared_jobs = 0
        if clear_queue:
            # Mark queued/scheduled jobs as skipped in the database.
            self.db_client.table("dialer_jobs").update({
                "status": "skipped",
                "last_error": "Campaign stopped"
            }).eq("campaign_id", campaign_id).in_("status", ["pending", "retry_scheduled"]).execute()

            queue_service = await self._get_queue_service()
            cleared_jobs = await queue_service.clear_campaign_jobs(campaign_id)
            await self._cleanup_queue_service()

        logger.info(
            "Campaign %s stopped (clear_queue=%s, cleared_jobs=%s)",
            campaign_id,
            clear_queue,
            cleared_jobs,
        )
        return response.data[0]
    
    # =========================================================================
    # Private Helpers
    # =========================================================================
    
    async def _get_pending_leads(self, campaign_id: str) -> List[Dict[str, Any]]:
        """Get all pending leads for a campaign, ordered by priority.
        Also includes leads stuck at 'calling' from a previous crashed run."""
        response = self.db_client.table("leads").select("*")\
            .eq("campaign_id", campaign_id)\
            .in_("status", ["pending", "calling"])\
            .order("priority", desc=True)\
            .order("created_at")\
            .execute()
        return response.data or []

    async def _reset_leads_for_restart(self, campaign_id: str) -> int:
        """Reset failed/skipped/calling leads to pending so a campaign restart retries them."""
        response = self.db_client.table("leads").update({
            "status": "pending",
            "last_called_at": None,
        }).eq("campaign_id", campaign_id)\
          .in_("status", ["failed", "skipped", "calling"])\
          .execute()
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

        # Pick an agent name from the campaign pool — stays stable for
        # the whole call. Fall back to None (legacy campaigns) so the
        # session config can use its own default pool.
        agent_name: Optional[str] = None
        if agent_names_pool:
            try:
                from app.services.scripts.prompts import pick_agent_name
                agent_name = pick_agent_name(agent_names_pool)
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
    
    async def _store_jobs_batch(self, jobs_data: List[Dict[str, Any]]) -> None:
        """Store jobs in database as batch insert."""
        if not jobs_data:
            return
        
        try:
            self.db_client.table("dialer_jobs").insert(jobs_data).execute()
        except Exception as e:
            # Log but don't fail - jobs are already in Redis queue
            logger.warning(f"Failed to store jobs in database: {e}")
    
    async def _update_campaign_status(
        self,
        campaign_id: str,
        status: str,
        total_leads: Optional[int] = None
    ) -> None:
        """Update campaign status and metadata."""
        update_data = {
            "status": status,
            "started_at": datetime.utcnow().isoformat()
        }
        if total_leads is not None:
            update_data["total_leads"] = total_leads
        
        self.db_client.table("campaigns").update(update_data).eq("id", campaign_id).execute()


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

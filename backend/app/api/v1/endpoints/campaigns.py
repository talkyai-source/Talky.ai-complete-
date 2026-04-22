"""
Campaigns API with Dialer Engine Integration
Handles campaign CRUD, contact management, and job enqueueing for the dialer

Day 9 Additions:
- POST /campaigns/{id}/contacts - Add single contact to campaign
- GET /campaigns/{id}/contacts - List contacts for a campaign with pagination

Refactored: Business logic delegates to CampaignService.
"""
import logging
import os
import uuid
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel, Field, field_validator
from app.core.postgres_adapter import Client
from app.core.dotenv_compat import load_dotenv

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.queue_service import DialerQueueService
from app.domain.services.campaign_service import (
    CampaignService, CampaignError, CampaignNotFoundError, CampaignStateError
)
from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser

load_dotenv()

logger = logging.getLogger(__name__)


def _get_campaign_service(db_client: Client) -> CampaignService:
    """Build CampaignService using the DI container's queue service."""
    from app.core.container import get_container
    container = get_container()
    queue_service = container.queue_service if container.is_initialized else None
    return CampaignService(db_client, queue_service=queue_service)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignStartRequest(BaseModel):
    """Request body for starting a campaign"""
    priority_override: Optional[int] = None  # Override priority for all jobs (1-10)
    tenant_id: Optional[str] = None  # For multi-tenant support


class CampaignCreateRequest(BaseModel):
    """Request body for creating a campaign."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    system_prompt: str = Field(..., min_length=1)
    voice_id: str = Field(..., min_length=1, max_length=100)
    goal: Optional[str] = None


class ContactCreate(BaseModel):
    """
    Request body for adding a single contact to a campaign.
    Day 9: POST /campaigns/{id}/contacts
    """
    phone_number: str = Field(..., description="Phone number in any format (will be normalized)")
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=255)
    custom_fields: Optional[dict] = Field(default_factory=dict)
    
    @field_validator('phone_number')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Basic phone validation - more robust validation in endpoint"""
        # Remove common formatting characters
        cleaned = re.sub(r'[\s\-\(\)\.]', '', v)
        if not cleaned:
            raise ValueError('Phone number cannot be empty')
        # Allow SIP extensions (3-6 digits) and standard phone numbers (7+ digits)
        if len(cleaned) < 3:
            raise ValueError('Phone number too short (minimum 3 digits for SIP extensions)')
        return v


class ContactListResponse(BaseModel):
    """Response for listing contacts"""
    items: List[dict]
    page: int
    page_size: int
    total: int


@router.get("/")
async def list_campaigns(
    request: Request,
    db_client: Client = Depends(get_db_client)
):
    """List all campaigns"""
    try:
        response = db_client.table("campaigns").select("*").order("created_at", desc=True).execute()
        return {"campaigns": response.data}
    except Exception as e:
        logger.error(f"Error listing campaigns: {e}")
        raise HTTPException(status_code=500, detail="Failed to list campaigns")


@router.post("/")
async def create_campaign(
    campaign_data: CampaignCreateRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """Create a new campaign"""
    try:
        if not current_user.tenant_id:
            raise HTTPException(status_code=400, detail="Current user is not associated with a tenant")

        from app.api.v1.endpoints.ai_options import (
            _english_google_voices,
            _fetch_tenant_config,
            _get_deepgram_voices_for_current_key,
            get_elevenlabs_voices_for_current_key,
        )
        from app.domain.models.ai_config import AIProviderConfig

        selected_voice_id = campaign_data.voice_id.strip()

        async with db_client.pool.acquire() as conn:
            ai_config = await _fetch_tenant_config(conn, current_user.tenant_id)
        if ai_config is None:
            ai_config = AIProviderConfig()

        if ai_config.tts_provider == "google":
            valid_voice_ids = {voice.id for voice in _english_google_voices()}
        elif ai_config.tts_provider == "deepgram":
            valid_voice_ids = {
                voice.id for voice in await _get_deepgram_voices_for_current_key()
            }
        else:
            valid_voice_ids = {
                voice.id for voice in await get_elevenlabs_voices_for_current_key()
            }

        if selected_voice_id not in valid_voice_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Voice '{selected_voice_id}' is not available for the current "
                    f"global TTS provider '{ai_config.tts_provider}'. Update AI Options "
                    "or choose a matching campaign voice."
                ),
            )

        insert_payload = {
            "tenant_id": current_user.tenant_id,
            "name": campaign_data.name.strip(),
            "description": campaign_data.description.strip() if campaign_data.description else None,
            "system_prompt": campaign_data.system_prompt.strip(),
            "voice_id": selected_voice_id,
            "goal": campaign_data.goal.strip() if campaign_data.goal else None,
        }

        response = db_client.table("campaigns").insert(insert_payload).execute()
        if response.error:
            logger.error(f"Error creating campaign: {response.error}")
            raise HTTPException(status_code=500, detail=f"Failed to create campaign: {response.error}")
        if not response.data:
            logger.error("Campaign insert returned no rows for tenant=%s name=%s", current_user.tenant_id, insert_payload["name"])
            raise HTTPException(status_code=500, detail="Failed to create campaign")
        return {"campaign": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating campaign: {e}")
        raise HTTPException(status_code=500, detail="Failed to create campaign")


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    request: Request,
    db_client: Client = Depends(get_db_client)
):
    """Get campaign details — delegates to CampaignService."""
    try:
        service = _get_campaign_service(db_client)
        campaign = await service.get_campaign(campaign_id)
        return {"campaign": campaign}
    except CampaignNotFoundError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except Exception as e:
        logger.error(f"Error fetching campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get campaign")


@router.get("/{campaign_id}/calls")
async def list_campaign_calls_with_transcripts(
    campaign_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Paginated list of calls for a campaign with their transcripts.

    Powers the Script Card on the campaign detail page.
    """
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User is not associated with a tenant")

    campaign_resp = (
        db_client.table("campaigns")
        .select("id, tenant_id")
        .eq("id", campaign_id)
        .eq("tenant_id", current_user.tenant_id)
        .limit(1)
        .execute()
    )
    if not campaign_resp.data:
        raise HTTPException(status_code=404, detail="Campaign not found")

    from app.services.scripts import fetch_campaign_transcripts
    try:
        result = await fetch_campaign_transcripts(
            pool=db_client.pool,
            tenant_id=current_user.tenant_id,
            campaign_id=campaign_id,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"fetch_campaign_transcripts failed campaign={campaign_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to load campaign transcripts")
    return result


@router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: str,
    request: Request,
    start_request: Optional[CampaignStartRequest] = None,
    db_client: Client = Depends(get_db_client)
):
    """
    Start a campaign — delegates to CampaignService.
    
    Validates, enqueues jobs, and updates status atomically.
    """
    try:
        service = _get_campaign_service(db_client)
        
        tenant_id = (start_request.tenant_id if start_request else None)
        priority_override = (start_request.priority_override if start_request else None)
        
        result = await service.start_campaign(
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            priority_override=priority_override
        )
        
        return {
            "message": result.message,
            "jobs_enqueued": result.jobs_enqueued,
            "queue_stats": result.queue_stats,
            "campaign": {"id": result.campaign_id, "status": "running"}
        }
    except CampaignNotFoundError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except CampaignStateError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except CampaignError as e:
        logger.error(f"Campaign service error for {campaign_id}: {e}")
        raise HTTPException(status_code=e.status_code, detail="Failed to start campaign")
    except Exception as e:
        logger.error(f"Unexpected error starting campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    request: Request,
    db_client: Client = Depends(get_db_client)
):
    """Pause a campaign — delegates to CampaignService."""
    try:
        service = _get_campaign_service(db_client)
        campaign = await service.pause_campaign(campaign_id)
        return {"message": f"Campaign {campaign_id} paused", "campaign": campaign}
    except CampaignNotFoundError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except Exception as e:
        logger.error(f"Error pausing campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to pause campaign")


@router.post("/{campaign_id}/stop")
async def stop_campaign(
    campaign_id: str,
    clear_queue: bool = Query(False, description="Clear pending jobs from queue"),
    db_client: Client = Depends(get_db_client)
):
    """
    Stop a campaign — delegates to CampaignService.
    
    Args:
        campaign_id: Campaign UUID
        clear_queue: If True, mark pending jobs as skipped
    """
    try:
        service = _get_campaign_service(db_client)
        campaign = await service.stop_campaign(campaign_id, clear_queue=clear_queue)
        return {
            "message": f"Campaign {campaign_id} stopped",
            "campaign": campaign
        }
    except CampaignNotFoundError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except Exception as e:
        logger.error(f"Error stopping campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to stop campaign")


@router.get("/{campaign_id}/jobs")
async def get_campaign_jobs(
    campaign_id: str,
    status: Optional[str] = Query(None, description="Filter by job status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db_client: Client = Depends(get_db_client)
):
    """Get dialer jobs for a campaign"""
    try:
        query = db_client.table("dialer_jobs").select(
            "*", count="exact"
        ).eq("campaign_id", campaign_id)
        
        if status:
            query = query.eq("status", status)
        
        offset = (page - 1) * page_size
        response = query.order("created_at", desc=True).range(offset, offset + page_size - 1).execute()
        
        return {
            "jobs": response.data,
            "total": response.count,
            "page": page,
            "page_size": page_size
        }
    except Exception as e:
        logger.error(f"Error fetching jobs for campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch campaign jobs")


@router.get("/{campaign_id}/stats")
async def get_campaign_stats(
    campaign_id: str,
    db_client: Client = Depends(get_db_client)
):
    """Get statistics for a campaign"""
    try:
        # Get campaign
        campaign_response = db_client.table("campaigns").select("*").eq("id", campaign_id).execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        campaign = campaign_response.data[0]
        
        # Get job counts by status
        jobs_response = db_client.table("dialer_jobs").select("status").eq("campaign_id", campaign_id).execute()
        
        status_counts = {}
        for job in jobs_response.data or []:
            status = job.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # Get call outcomes
        calls_response = db_client.table("calls").select("outcome, goal_achieved").eq("campaign_id", campaign_id).execute()
        
        outcome_counts = {}
        goals_achieved = 0
        for call in calls_response.data or []:
            outcome = call.get("outcome", "unknown")
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            if call.get("goal_achieved"):
                goals_achieved += 1
        
        return {
            "campaign_id": campaign_id,
            "campaign_status": campaign.get("status"),
            "total_leads": campaign.get("total_leads", 0),
            "job_status_counts": status_counts,
            "call_outcome_counts": outcome_counts,
            "goals_achieved": goals_achieved
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching stats for campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch campaign stats")


# =============================================================================
# Day 9: Contact Management Endpoints
# =============================================================================

def normalize_phone_number(phone: str) -> str:
    """
    Normalize phone number to E.164 format.
    
    Handles common formats:
    - (555) 123-4567 -> +15551234567 (assumes US if no country code)
    - 555.123.4567 -> +15551234567
    - +44 20 7946 0958 -> +442079460958
    """
    # Remove all non-digit characters except leading +
    has_plus = phone.strip().startswith('+')
    cleaned = re.sub(r'[^\d]', '', phone)
    
    if not cleaned:
        raise ValueError("Invalid phone number")

    # Allow short SIP extensions (3–6 digits) to pass through as-is
    if len(cleaned) <= 6:
        if len(cleaned) < 3:
            raise ValueError("Phone number too short (minimum 3 digits for SIP extensions)")
        return cleaned  # Return raw SIP extension — no E.164 normalization

    # Validate maximum length (E.164 max is 15 digits)
    if len(cleaned) > 15:
        raise ValueError("Phone number too long (maximum 15 digits)")

    # If already has + and country code, use as-is
    if has_plus:
        return f"+{cleaned}"

    # If 10 digits (US/Canada without country code), add +1
    if len(cleaned) == 10:
        return f"+1{cleaned}"

    # If 11 digits starting with 1 (US/Canada with country code), add +
    if len(cleaned) == 11 and cleaned.startswith('1'):
        return f"+{cleaned}"

    # Otherwise, return with + prefix
    return f"+{cleaned}"


@router.post("/{campaign_id}/contacts")
async def add_contact_to_campaign(
    campaign_id: str,
    contact: ContactCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Add a single contact (lead) to a campaign.
    
    Day 9 Endpoint: POST /campaigns/{id}/contacts
    
    Features:
    - Validates campaign exists
    - Normalizes phone number to E.164 format
    - Checks for duplicate phone within campaign
    - Creates lead record with pending status
    
    Returns:
        Created lead object
    """
    try:
        # 1. Validate campaign exists and belongs to the current tenant
        campaign_query = db_client.table("campaigns").select("id, name, status, tenant_id").eq("id", campaign_id)
        if current_user.tenant_id:
            campaign_query = campaign_query.eq("tenant_id", current_user.tenant_id)
        campaign_response = campaign_query.execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        campaign = campaign_response.data[0]
        
        # 2. Normalize phone number
        try:
            normalized_phone = normalize_phone_number(contact.phone_number)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid phone number: {str(e)}")
        
        # 3. Check for duplicate within campaign
        existing = db_client.table("leads").select("id").eq(
            "campaign_id", campaign_id
        ).eq(
            "phone_number", normalized_phone
        ).neq(
            "status", "deleted"
        ).execute()
        
        if existing.data:
            raise HTTPException(
                status_code=409, 
                detail=f"Phone number {normalized_phone} already exists in this campaign"
            )
        
        # 4. Create lead record
        lead_id = str(uuid.uuid4())
        lead_data = {
            "id": lead_id,
            "tenant_id": campaign.get("tenant_id") or current_user.tenant_id,
            "campaign_id": campaign_id,
            "phone_number": normalized_phone,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "email": contact.email,
            "custom_fields": contact.custom_fields or {},
            "status": "pending",
            "last_call_result": "pending",
            "call_attempts": 0,
            "created_at": datetime.utcnow().isoformat()
        }
        
        response = db_client.table("leads").insert(lead_data).execute()
        
        if response.error:
            logger.error(f"Error creating lead for campaign {campaign_id}: {response.error}")
            raise HTTPException(status_code=500, detail=f"Failed to create contact: {response.error}")
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create contact")
        
        logger.info(f"Contact added to campaign {campaign_id}: {normalized_phone}")
        
        return {
            "message": "Contact added successfully",
            "contact": response.data[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding contact to campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add contact")


@router.get("/{campaign_id}/contacts")
async def list_campaign_contacts(
    campaign_id: str,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status (pending, called, completed, dnc)"),
    last_call_result: Optional[str] = Query(None, description="Filter by last call result"),
    db_client: Client = Depends(get_db_client)
):
    """
    List all contacts (leads) for a campaign with pagination.
    
    Day 9 Endpoint: GET /campaigns/{id}/contacts
    
    Features:
    - Paginated response
    - Filter by status (pending, called, completed, dnc)
    - Filter by last_call_result (pending, answered, no_answer, busy, failed, voicemail, goal_achieved)
    - Ordered by created_at descending
    
    Returns:
        Paginated list of contacts with their call status
    """
    try:
        # 1. Validate campaign exists
        campaign_response = db_client.table("campaigns").select("id").eq("id", campaign_id).execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # 2. Build query
        query = db_client.table("leads").select(
            "*", count="exact"
        ).eq("campaign_id", campaign_id).neq("status", "deleted")
        
        # Apply filters
        if status:
            query = query.eq("status", status)
        if last_call_result:
            query = query.eq("last_call_result", last_call_result)
        
        # 3. Apply pagination
        offset = (page - 1) * page_size
        response = query.order(
            "created_at", desc=True
        ).range(
            offset, offset + page_size - 1
        ).execute()
        
        return {
            "items": response.data or [],
            "page": page,
            "page_size": page_size,
            "total": response.count or 0
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing contacts for campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{campaign_id}/contacts/{contact_id}")
async def remove_contact_from_campaign(
    campaign_id: str,
    contact_id: str,
    db_client: Client = Depends(get_db_client)
):
    """
    Remove a contact from a campaign (soft delete).
    
    Sets status to 'deleted' rather than actually deleting,
    preserving history for analytics.
    """
    try:
        # Verify contact belongs to campaign
        existing = db_client.table("leads").select("id").eq(
            "id", contact_id
        ).eq(
            "campaign_id", campaign_id
        ).execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Contact not found in this campaign")
        
        # Soft delete
        response = db_client.table("leads").update({
            "status": "deleted"
        }).eq("id", contact_id).execute()
        
        logger.info(f"Contact {contact_id} removed from campaign {campaign_id}")
        
        return {"message": "Contact removed successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing contact {contact_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove contact")

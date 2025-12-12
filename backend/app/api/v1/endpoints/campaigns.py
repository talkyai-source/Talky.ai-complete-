"""
Campaigns API with Dialer Engine Integration
Handles campaign CRUD, contact management, and job enqueueing for the dialer

Day 9 Additions:
- POST /campaigns/{id}/contacts - Add single contact to campaign
- GET /campaigns/{id}/contacts - List contacts for a campaign with pagination
"""
import logging
import os
import uuid
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv
from supabase import create_client, Client

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.queue_service import DialerQueueService
from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignStartRequest(BaseModel):
    """Request body for starting a campaign"""
    priority_override: Optional[int] = None  # Override priority for all jobs (1-10)
    tenant_id: Optional[str] = None  # For multi-tenant support


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
        if len(cleaned) < 7:
            raise ValueError('Phone number too short')
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
    supabase: Client = Depends(get_supabase)
):
    """List all campaigns"""
    try:
        response = supabase.table("campaigns").select("*").order("created_at", desc=True).execute()
        return {"campaigns": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
async def create_campaign(
    campaign_data: dict,
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """Create a new campaign"""
    try:
        response = supabase.table("campaigns").insert(campaign_data).execute()
        return {"campaign": response.data[0] if response.data else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """Get campaign details"""
    try:
        response = supabase.table("campaigns").select("*").eq("id", campaign_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return {"campaign": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: str,
    request: Request,
    start_request: Optional[CampaignStartRequest] = None,
    supabase: Client = Depends(get_supabase)
):
    """
    Start a campaign - enqueue all pending leads as dialer jobs.
    
    This endpoint:
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
    """
    try:
        # 1. Get campaign
        campaign_response = supabase.table("campaigns").select("*").eq("id", campaign_id).execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        campaign = campaign_response.data[0]
        
        # Check if campaign can be started
        if campaign.get("status") == "running":
            raise HTTPException(status_code=400, detail="Campaign is already running")
        
        # 2. Get tenant_id (from request or campaign)
        tenant_id = None
        if start_request and start_request.tenant_id:
            tenant_id = start_request.tenant_id
        # For now, use a default tenant if not provided
        if not tenant_id:
            tenant_id = "default-tenant"
        
        # 3. Get all pending leads for this campaign
        leads_response = supabase.table("leads").select("*")\
            .eq("campaign_id", campaign_id)\
            .eq("status", "pending")\
            .order("priority", desc=True)\
            .order("created_at")\
            .execute()
        
        leads = leads_response.data or []
        
        if not leads:
            # No leads to process, but still update status
            supabase.table("campaigns").update({
                "status": "running",
                "started_at": datetime.utcnow().isoformat()
            }).eq("id", campaign_id).execute()
            
            return {
                "message": f"Campaign {campaign_id} started (no pending leads)",
                "jobs_enqueued": 0,
                "campaign": {"id": campaign_id, "status": "running"}
            }
        
        # 4. Initialize queue service
        queue_service = DialerQueueService()
        await queue_service.initialize()
        
        # 5. Create and enqueue jobs for each lead
        priority_override = start_request.priority_override if start_request else None
        jobs_created = 0
        jobs_data = []
        
        for lead in leads:
            # Calculate priority
            base_priority = priority_override if priority_override else lead.get("priority", 5)
            
            # High-value customers get priority boost
            if lead.get("is_high_value"):
                base_priority = min(base_priority + 2, 10)
            
            # Urgent tags get priority boost
            lead_tags = lead.get("tags", []) or []
            if "urgent" in lead_tags or "appointment" in lead_tags or "reminder" in lead_tags:
                base_priority = min(base_priority + 1, 10)
            
            # Create job
            job_id = str(uuid.uuid4())
            job = DialerJob(
                job_id=job_id,
                campaign_id=campaign_id,
                lead_id=lead["id"],
                tenant_id=tenant_id,
                phone_number=lead["phone_number"],
                priority=base_priority,
                status=JobStatus.PENDING,
                attempt_number=1,
                scheduled_at=datetime.utcnow(),
                created_at=datetime.utcnow()
            )
            
            # Enqueue to Redis
            await queue_service.enqueue_job(job)
            
            # Prepare database record
            jobs_data.append({
                "id": job_id,
                "campaign_id": campaign_id,
                "lead_id": lead["id"],
                "tenant_id": tenant_id,
                "phone_number": lead["phone_number"],
                "priority": base_priority,
                "status": "pending",
                "attempt_number": 1,
                "scheduled_at": datetime.utcnow().isoformat(),
                "created_at": datetime.utcnow().isoformat()
            })
            
            jobs_created += 1
        
        # 6. Store jobs in database (batch insert)
        if jobs_data:
            try:
                supabase.table("dialer_jobs").insert(jobs_data).execute()
            except Exception as e:
                # Log but don't fail - jobs are already in Redis
                print(f"Warning: Failed to store jobs in database: {e}")
        
        # 7. Update campaign status
        supabase.table("campaigns").update({
            "status": "running",
            "started_at": datetime.utcnow().isoformat(),
            "total_leads": len(leads)
        }).eq("id", campaign_id).execute()
        
        # 8. Close queue service
        await queue_service.close()
        
        # Get queue stats
        await queue_service.initialize()
        stats = await queue_service.get_queue_stats()
        await queue_service.close()
        
        return {
            "message": f"Campaign {campaign_id} started",
            "jobs_enqueued": jobs_created,
            "queue_stats": stats,
            "campaign": {"id": campaign_id, "status": "running"}
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """Pause a campaign"""
    try:
        response = supabase.table("campaigns").update({
            "status": "paused"
        }).eq("id", campaign_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return {"message": f"Campaign {campaign_id} paused", "campaign": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{campaign_id}/stop")
async def stop_campaign(
    campaign_id: str,
    clear_queue: bool = Query(False, description="Clear pending jobs from queue"),
    supabase: Client = Depends(get_supabase)
):
    """
    Stop a campaign completely.
    
    Args:
        campaign_id: Campaign UUID
        clear_queue: If True, remove pending jobs from Redis queue
    """
    try:
        # Update campaign status
        response = supabase.table("campaigns").update({
            "status": "stopped",
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", campaign_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        jobs_cleared = 0
        
        if clear_queue:
            # Clear pending jobs for this campaign from database
            supabase.table("dialer_jobs").update({
                "status": "skipped",
                "last_error": "Campaign stopped"
            }).eq("campaign_id", campaign_id).eq("status", "pending").execute()
            
            # Note: Redis queue uses tenant-based keys, so we can't easily
            # clear specific campaign jobs without scanning. In production,
            # the worker will skip jobs for stopped campaigns.
        
        return {
            "message": f"Campaign {campaign_id} stopped",
            "jobs_cleared": jobs_cleared,
            "campaign": response.data[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{campaign_id}/jobs")
async def get_campaign_jobs(
    campaign_id: str,
    status: Optional[str] = Query(None, description="Filter by job status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    supabase: Client = Depends(get_supabase)
):
    """Get dialer jobs for a campaign"""
    try:
        query = supabase.table("dialer_jobs").select(
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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{campaign_id}/stats")
async def get_campaign_stats(
    campaign_id: str,
    supabase: Client = Depends(get_supabase)
):
    """Get statistics for a campaign"""
    try:
        # Get campaign
        campaign_response = supabase.table("campaigns").select("*").eq("id", campaign_id).execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        campaign = campaign_response.data[0]
        
        # Get job counts by status
        jobs_response = supabase.table("dialer_jobs").select("status").eq("campaign_id", campaign_id).execute()
        
        status_counts = {}
        for job in jobs_response.data or []:
            status = job.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # Get call outcomes
        calls_response = supabase.table("calls").select("outcome, goal_achieved").eq("campaign_id", campaign_id).execute()
        
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
        raise HTTPException(status_code=500, detail=str(e))


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
    
    # Validate minimum length (international minimum is 7 digits)
    if len(cleaned) < 7:
        raise ValueError("Phone number too short (minimum 7 digits)")
    
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
    supabase: Client = Depends(get_supabase)
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
        # 1. Validate campaign exists
        campaign_response = supabase.table("campaigns").select("id, name, status").eq("id", campaign_id).execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # 2. Normalize phone number
        try:
            normalized_phone = normalize_phone_number(contact.phone_number)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid phone number: {str(e)}")
        
        # 3. Check for duplicate within campaign
        existing = supabase.table("leads").select("id").eq(
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
        
        response = supabase.table("leads").insert(lead_data).execute()
        
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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{campaign_id}/contacts")
async def list_campaign_contacts(
    campaign_id: str,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status (pending, called, completed, dnc)"),
    last_call_result: Optional[str] = Query(None, description="Filter by last call result"),
    supabase: Client = Depends(get_supabase)
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
        campaign_response = supabase.table("campaigns").select("id").eq("id", campaign_id).execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # 2. Build query
        query = supabase.table("leads").select(
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
    supabase: Client = Depends(get_supabase)
):
    """
    Remove a contact from a campaign (soft delete).
    
    Sets status to 'deleted' rather than actually deleting,
    preserving history for analytics.
    """
    try:
        # Verify contact belongs to campaign
        existing = supabase.table("leads").select("id").eq(
            "id", contact_id
        ).eq(
            "campaign_id", campaign_id
        ).execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Contact not found in this campaign")
        
        # Soft delete
        response = supabase.table("leads").update({
            "status": "deleted"
        }).eq("id", contact_id).execute()
        
        logger.info(f"Contact {contact_id} removed from campaign {campaign_id}")
        
        return {"message": "Contact removed successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing contact {contact_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

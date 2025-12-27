"""
Recordings Endpoints
Provides recording list, audio streaming, and signed URL access

Day 18: Added plan-based recording access control
- Recording availability determined by tenant's purchased plan
- Basic: 30-day retention, Professional: 90-day, Enterprise: 365-day
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter, verify_tenant_access
from app.domain.models.retention_config import (
    get_retention_config_for_plan,
    is_recording_accessible
)

router = APIRouter(prefix="/recordings", tags=["recordings"])



class RecordingListItem(BaseModel):
    """Recording list item"""
    id: str
    call_id: str
    created_at: str
    duration_seconds: Optional[int] = None


class RecordingListResponse(BaseModel):
    """Paginated recording list response"""
    items: List[RecordingListItem]
    page: int
    page_size: int
    total: int


class RecordingUrlResponse(BaseModel):
    """Signed URL response for direct audio access"""
    url: str
    expires_in: int  # seconds
    recording_id: str
    mime_type: str
    retention_days_remaining: Optional[int] = None


@router.get("/", response_model=RecordingListResponse)
async def list_recordings(
    call_id: Optional[str] = Query(None, description="Filter by call ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get paginated list of recordings.
    
    Used by: /dashboard/recordings page.
    
    Query params:
        - call_id: Optional filter by specific call
        - page: Page number (1-indexed)
        - page_size: Items per page (max 100)
    """
    try:
        # Recordings are linked to calls. We need to filter by tenant via the calls table.
        # First, get the list of call_ids that belong to this tenant
        if current_user.tenant_id:
            # Get calls belonging to tenant
            calls_query = supabase.table("calls").select("id")
            calls_query = apply_tenant_filter(calls_query, current_user.tenant_id)
            calls_response = calls_query.execute()
            tenant_call_ids = [call["id"] for call in (calls_response.data or [])]
            
            if not tenant_call_ids:
                # No calls for this tenant, return empty
                return RecordingListResponse(
                    items=[],
                    page=page,
                    page_size=page_size,
                    total=0
                )
            
            # Build query filtering by tenant's call_ids
            query = supabase.table("recordings").select(
                "id, call_id, created_at, duration_seconds",
                count="exact"
            ).in_("call_id", tenant_call_ids)
        else:
            # No tenant filter (admin or no tenant assigned)
            query = supabase.table("recordings").select(
                "id, call_id, created_at, duration_seconds",
                count="exact"
            )
        
        # Apply call_id filter if specified
        if call_id:
            query = query.eq("call_id", call_id)
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Execute with pagination
        response = query.order("created_at", desc=True).range(offset, offset + page_size - 1).execute()
        
        # Get total count
        total = response.count if response.count else 0
        
        # Map results
        items = []
        for recording in response.data or []:
            items.append(RecordingListItem(
                id=recording["id"],
                call_id=recording["call_id"],
                created_at=recording.get("created_at", ""),
                duration_seconds=recording.get("duration_seconds")
            ))
        
        return RecordingListResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch recordings: {str(e)}"
        )


@router.get("/{recording_id}/stream")
async def stream_recording(
    recording_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Stream recording audio file.
    
    Used by: Audio player in call details view.
    
    Returns: audio/* streamed bytes for HTML5 <audio> player.
    """
    try:
        # First verify the recording's associated call belongs to the tenant
        recording_check = supabase.table("recordings").select("call_id").eq("id", recording_id).single().execute()
        
        if not recording_check.data:
            raise HTTPException(
                status_code=404,
                detail="Recording not found"
            )
        
        associated_call_id = recording_check.data.get("call_id")
        if not verify_tenant_access(supabase, "calls", associated_call_id, current_user.tenant_id):
            raise HTTPException(
                status_code=404,
                detail="Recording not found"
            )
        
        # Get recording details
        response = supabase.table("recordings").select(
            "storage_path, mime_type"
        ).eq("id", recording_id).single().execute()
        
        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Recording not found"
            )
        
        recording = response.data
        storage_path = recording.get("storage_path")
        mime_type = recording.get("mime_type", "audio/wav")
        
        if not storage_path:
            raise HTTPException(
                status_code=404,
                detail="Recording file not found"
            )
        
        # Download from Supabase Storage
        try:
            # Assuming recordings are stored in 'recordings' bucket
            bucket_name = "recordings"
            file_data = supabase.storage.from_(bucket_name).download(storage_path)
            
            # Stream the audio
            async def audio_generator():
                # Yield in chunks for streaming
                chunk_size = 8192
                for i in range(0, len(file_data), chunk_size):
                    yield file_data[i:i + chunk_size]
            
            return StreamingResponse(
                audio_generator(),
                media_type=mime_type,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": f"inline; filename=recording_{recording_id}.wav"
                }
            )
        
        except Exception as storage_error:
            raise HTTPException(
                status_code=404,
                detail=f"Recording file not accessible: {str(storage_error)}"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stream recording: {str(e)}"
        )


@router.get("/{recording_id}/url", response_model=RecordingUrlResponse)
async def get_recording_url(
    recording_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get a time-limited signed URL for direct audio access.
    
    RECOMMENDED: Use this endpoint instead of /stream for better performance.
    The signed URL allows direct download from Supabase Storage without
    routing through the backend.
    
    PLAN-BASED ACCESS CONTROL:
    - Basic plan: 30-day recording retention
    - Professional plan: 90-day recording retention
    - Enterprise plan: 365-day recording retention
    
    Recordings beyond the tenant's retention period return 403 Forbidden.
    
    Returns:
        url: Signed URL (valid for 1 hour)
        expires_in: Seconds until URL expires
        recording_id: The recording ID
        mime_type: Audio MIME type
        retention_days_remaining: Days until recording expires (based on plan)
    """
    try:
        # 1. Get recording details including created_at for retention check
        response = supabase.table("recordings").select(
            "id, call_id, storage_path, mime_type, created_at"
        ).eq("id", recording_id).single().execute()
        
        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Recording not found"
            )
        
        recording = response.data
        associated_call_id = recording.get("call_id")
        
        # 2. Verify tenant access via calls table
        if not verify_tenant_access(supabase, "calls", associated_call_id, current_user.tenant_id):
            raise HTTPException(
                status_code=404,
                detail="Recording not found"
            )
        
        # 3. Get tenant's plan for retention policy enforcement
        # CRITICAL: Recording access is determined by purchased plan
        plan_id = "basic"  # Default to most restrictive
        if current_user.tenant_id:
            tenant_response = supabase.table("tenants").select(
                "plan_id"
            ).eq("id", current_user.tenant_id).single().execute()
            
            if tenant_response.data and tenant_response.data.get("plan_id"):
                plan_id = tenant_response.data.get("plan_id")
        
        # 4. Calculate recording age and check retention policy
        created_at_str = recording.get("created_at", "")
        recording_age_days = 0
        retention_days_remaining = None
        
        if created_at_str:
            try:
                # Parse ISO timestamp (handle both with and without timezone)
                if created_at_str.endswith("Z"):
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                elif "+" in created_at_str or created_at_str.endswith("00"):
                    created_at = datetime.fromisoformat(created_at_str)
                else:
                    created_at = datetime.fromisoformat(created_at_str).replace(tzinfo=timezone.utc)
                
                now = datetime.now(timezone.utc)
                recording_age_days = (now - created_at).days
                
                # Get retention config for plan
                retention_config = get_retention_config_for_plan(plan_id)
                retention_days_remaining = max(0, retention_config.recording_retention_days - recording_age_days)
                
                # CRITICAL: Check if recording is still within retention period
                if not is_recording_accessible(plan_id, recording_age_days):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Recording has exceeded your plan's {retention_config.recording_retention_days}-day retention period. "
                               f"Upgrade your plan to access older recordings."
                    )
            except ValueError:
                # If date parsing fails, allow access (don't block on parse errors)
                pass
        
        # 5. Get signed URL from storage
        storage_path = recording.get("storage_path")
        mime_type = recording.get("mime_type", "audio/wav")
        
        if not storage_path:
            raise HTTPException(
                status_code=404,
                detail="Recording file not found"
            )
        
        try:
            bucket_name = "recordings"
            expires_in = 3600  # 1 hour
            
            # Generate signed URL
            signed_url_response = supabase.storage.from_(bucket_name).create_signed_url(
                path=storage_path,
                expires_in=expires_in
            )
            
            signed_url = signed_url_response.get("signedURL") or signed_url_response.get("signedUrl")
            
            if not signed_url:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to generate signed URL"
                )
            
            return RecordingUrlResponse(
                url=signed_url,
                expires_in=expires_in,
                recording_id=recording_id,
                mime_type=mime_type,
                retention_days_remaining=retention_days_remaining
            )
            
        except HTTPException:
            raise
        except Exception as storage_error:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to generate signed URL: {str(storage_error)}"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get recording URL: {str(e)}"
        )

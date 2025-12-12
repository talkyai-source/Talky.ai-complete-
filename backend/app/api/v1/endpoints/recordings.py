"""
Recordings Endpoints
Provides recording list and audio streaming
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser

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
        # Build query
        query = supabase.table("recordings").select(
            "id, call_id, created_at, duration_seconds",
            count="exact"
        )
        
        # Apply filter
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

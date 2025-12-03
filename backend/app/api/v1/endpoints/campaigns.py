"""
Update campaigns API to use Supabase client
Works without direct PostgreSQL connection
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from typing import List
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # Use service key for backend

def get_supabase() -> Client:
    """Get Supabase client"""
    return create_client(SUPABASE_URL, SUPABASE_KEY)


@router.get("/")
async def list_campaigns(request: Request):
    """List all campaigns"""
    try:
        supabase = get_supabase()
        response = supabase.table("campaigns").select("*").execute()
        return {"campaigns": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
async def create_campaign(request: Request, campaign_data: dict):
    """Create a new campaign"""
    try:
        supabase = get_supabase()
        response = supabase.table("campaigns").insert(campaign_data).execute()
        return {"campaign": response.data[0] if response.data else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, request: Request):
    """Get campaign details"""
    try:
        supabase = get_supabase()
        response = supabase.table("campaigns").select("*").eq("id", campaign_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return {"campaign": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{campaign_id}/start")
async def start_campaign(campaign_id: str, request: Request):
    """Start a campaign"""
    try:
        supabase = get_supabase()
        response = supabase.table("campaigns").update({"status": "running"}).eq("id", campaign_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return {"message": f"Campaign {campaign_id} started", "campaign": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, request: Request):
    """Pause a campaign"""
    try:
        supabase = get_supabase()
        response = supabase.table("campaigns").update({"status": "paused"}).eq("id", campaign_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return {"message": f"Campaign {campaign_id} paused", "campaign": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

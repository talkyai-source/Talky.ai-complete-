"""
Admin Usage Endpoints
Usage analytics: summary and breakdown by provider/tenant/type
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, require_admin, CurrentUser

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class UsageBreakdownItem(BaseModel):
    """Usage breakdown by provider"""
    provider: str  # deepgram, groq, openai, twilio
    usage_type: str  # stt, tts, llm, sms, calls
    total_units: int  # seconds, tokens, count
    estimated_cost: float
    tenant_count: int


class UsageSummaryResponse(BaseModel):
    """Aggregated usage summary"""
    total_cost: float
    total_call_minutes: int
    total_api_calls: int
    providers: List[UsageBreakdownItem]
    period_start: str
    period_end: str


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/usage/summary", response_model=UsageSummaryResponse)
async def get_admin_usage_summary(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)")
):
    """
    Get aggregated usage summary across providers.
    
    Returns total costs, call minutes, and breakdown by provider.
    """
    try:
        # Default date range: current month
        now = datetime.utcnow()
        if not from_date:
            from_date = now.replace(day=1).strftime("%Y-%m-%d")
        if not to_date:
            to_date = now.strftime("%Y-%m-%d")
        
        # Get call statistics
        calls_query = db_client.table("calls").select("id, duration_seconds, cost, tenant_id")
        if tenant_id:
            calls_query = calls_query.eq("tenant_id", tenant_id)
        calls_query = calls_query.gte("created_at", from_date).lte("created_at", to_date)
        calls_response = calls_query.execute()
        
        calls = calls_response.data or []
        total_call_minutes = sum((c.get("duration_seconds") or 0) for c in calls) // 60
        call_costs = sum((c.get("cost") or 0) for c in calls)
        call_tenants = len(set(c.get("tenant_id") for c in calls))
        
        # Get actions for API usage
        actions_query = db_client.table("assistant_actions").select("id, type, tenant_id")
        if tenant_id:
            actions_query = actions_query.eq("tenant_id", tenant_id)
        actions_query = actions_query.gte("created_at", from_date).lte("created_at", to_date)
        actions_response = actions_query.execute()
        
        actions = actions_response.data or []
        total_api_calls = len(actions)
        
        # Build provider breakdown (estimated based on typical usage)
        providers = []
        
        # Deepgram (STT/TTS) - estimate based on call minutes
        if total_call_minutes > 0:
            # Estimate: ~$0.0125/min for STT + ~$0.02/min for TTS
            deepgram_cost = total_call_minutes * 0.0325
            providers.append(UsageBreakdownItem(
                provider="deepgram",
                usage_type="stt_tts",
                total_units=total_call_minutes * 60,  # seconds
                estimated_cost=round(deepgram_cost, 2),
                tenant_count=call_tenants
            ))
        
        # Groq/OpenAI (LLM) - estimate based on calls
        llm_calls = len(calls)
        if llm_calls > 0:
            # Estimate: ~$0.01/call for LLM
            llm_cost = llm_calls * 0.01
            providers.append(UsageBreakdownItem(
                provider="groq",
                usage_type="llm",
                total_units=llm_calls,
                estimated_cost=round(llm_cost, 2),
                tenant_count=call_tenants
            ))
        
        # Twilio/Vonage (Calls) - from actual costs
        if call_costs > 0:
            providers.append(UsageBreakdownItem(
                provider="twilio",
                usage_type="voice",
                total_units=total_call_minutes,
                estimated_cost=round(call_costs, 2),
                tenant_count=call_tenants
            ))
        
        # SMS actions
        sms_actions = [a for a in actions if a.get("type") == "send_sms"]
        if sms_actions:
            sms_cost = len(sms_actions) * 0.01  # Estimate $0.01/SMS
            providers.append(UsageBreakdownItem(
                provider="twilio",
                usage_type="sms",
                total_units=len(sms_actions),
                estimated_cost=round(sms_cost, 2),
                tenant_count=len(set(a.get("tenant_id") for a in sms_actions))
            ))
        
        total_cost = sum(p.estimated_cost for p in providers)
        
        return UsageSummaryResponse(
            total_cost=round(total_cost, 2),
            total_call_minutes=total_call_minutes,
            total_api_calls=total_api_calls,
            providers=providers,
            period_start=from_date,
            period_end=to_date
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get usage summary: {str(e)}"
        )


@router.get("/usage/breakdown")
async def get_admin_usage_breakdown(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
    group_by: str = Query("provider", description="Group by: provider, tenant, type"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    from_date: Optional[str] = Query(None, description="Start date"),
    to_date: Optional[str] = Query(None, description="End date")
):
    """
    Get detailed usage breakdown.
    
    Can group by provider, tenant, or usage type.
    """
    try:
        now = datetime.utcnow()
        if not from_date:
            from_date = now.replace(day=1).strftime("%Y-%m-%d")
        if not to_date:
            to_date = now.strftime("%Y-%m-%d")
        
        # Get call data with tenant info
        calls_query = db_client.table("calls").select(
            "id, tenant_id, duration_seconds, cost, created_at, "
            "tenants(business_name)"
        )
        if tenant_id:
            calls_query = calls_query.eq("tenant_id", tenant_id)
        calls_query = calls_query.gte("created_at", from_date).lte("created_at", to_date)
        calls_response = calls_query.execute()
        
        calls = calls_response.data or []
        
        breakdown = []
        
        if group_by == "tenant":
            # Group by tenant
            tenant_stats = {}
            for call in calls:
                tid = call.get("tenant_id")
                if tid not in tenant_stats:
                    tenant = call.get("tenants") or {}
                    tenant_stats[tid] = {
                        "tenant_id": tid,
                        "tenant_name": tenant.get("business_name", "Unknown"),
                        "call_count": 0,
                        "total_minutes": 0,
                        "total_cost": 0
                    }
                tenant_stats[tid]["call_count"] += 1
                tenant_stats[tid]["total_minutes"] += (call.get("duration_seconds") or 0) // 60
                tenant_stats[tid]["total_cost"] += call.get("cost") or 0
            
            breakdown = list(tenant_stats.values())
        
        elif group_by == "type":
            # Group by usage type
            breakdown = [
                {
                    "type": "voice_calls",
                    "total_units": sum((c.get("duration_seconds") or 0) // 60 for c in calls),
                    "total_cost": sum(c.get("cost") or 0 for c in calls),
                    "count": len(calls)
                }
            ]
            
            # Add actions breakdown
            actions_query = db_client.table("assistant_actions").select("type")
            if tenant_id:
                actions_query = actions_query.eq("tenant_id", tenant_id)
            actions_query = actions_query.gte("created_at", from_date).lte("created_at", to_date)
            actions = (actions_query.execute()).data or []
            
            action_types = {}
            for action in actions:
                atype = action.get("type", "unknown")
                action_types[atype] = action_types.get(atype, 0) + 1
            
            for atype, count in action_types.items():
                breakdown.append({
                    "type": atype,
                    "total_units": count,
                    "total_cost": 0,  # Actions don't have direct cost
                    "count": count
                })
        
        else:  # Default: group by provider
            call_minutes = sum((c.get("duration_seconds") or 0) // 60 for c in calls)
            call_cost = sum(c.get("cost") or 0 for c in calls)
            
            if call_minutes > 0:
                breakdown.append({
                    "provider": "deepgram",
                    "usage_type": "stt_tts",
                    "total_units": call_minutes * 60,
                    "estimated_cost": round(call_minutes * 0.0325, 2)
                })
                breakdown.append({
                    "provider": "groq",
                    "usage_type": "llm",
                    "total_units": len(calls),
                    "estimated_cost": round(len(calls) * 0.01, 2)
                })
                breakdown.append({
                    "provider": "twilio",
                    "usage_type": "voice",
                    "total_units": call_minutes,
                    "estimated_cost": round(call_cost, 2)
                })
        
        return {
            "breakdown": breakdown,
            "group_by": group_by,
            "period_start": from_date,
            "period_end": to_date
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get usage breakdown: {str(e)}"
        )

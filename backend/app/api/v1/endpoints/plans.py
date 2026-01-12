"""
Plans & Pricing Endpoints
Provides pricing plan information for the frontend
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from pydantic import BaseModel
from supabase import Client

from app.api.v1.dependencies import get_supabase

router = APIRouter(prefix="/plans", tags=["plans"])


class PlanResponse(BaseModel):
    """Plan response model"""
    id: str
    name: str
    price: float
    description: str
    minutes: int
    agents: int
    concurrent_calls: int
    features: List[str]
    not_included: List[str]
    popular: bool
    # Stripe billing fields
    stripe_price_id: Optional[str] = None
    stripe_product_id: Optional[str] = None
    billing_period: str = "monthly"


@router.get("/", response_model=List[PlanResponse])
async def list_plans(
    supabase: Client = Depends(get_supabase)
):
    """
    Get all available pricing plans.
    
    Used by: PackagesPage to render pricing cards dynamically.
    
    This endpoint is public (no auth required).
    """
    try:
        response = supabase.table("plans").select("*").order("price").execute()
        
        if not response.data:
            return []
        
        plans = []
        for plan in response.data:
            plans.append(PlanResponse(
                id=plan["id"],
                name=plan["name"],
                price=float(plan["price"]),
                description=plan.get("description", ""),
                minutes=plan["minutes"],
                agents=plan["agents"],
                concurrent_calls=plan["concurrent_calls"],
                features=plan.get("features", []),
                not_included=plan.get("not_included", []),
                popular=plan.get("popular", False),
                stripe_price_id=plan.get("stripe_price_id"),
                stripe_product_id=plan.get("stripe_product_id"),
                billing_period=plan.get("billing_period", "monthly")
            ))
        
        return plans
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch plans: {str(e)}"
        )

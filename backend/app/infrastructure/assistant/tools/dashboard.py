"""
Dashboard and usage tools for the assistant agent.
"""
import logging
from typing import Optional, Dict, Any
from datetime import date
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class GetDashboardStatsInput(BaseModel):
    """Input for get_dashboard_stats tool"""
    date: Optional[str] = Field(None, description="Date in YYYY-MM-DD format, defaults to today")


async def get_dashboard_stats(
    tenant_id: str,
    db_client: Client,
    date_str: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get dashboard statistics for the tenant.

    Returns:
        - Total calls today
        - Calls completed
        - Calls failed
        - Success rate
        - Active campaigns
    """
    try:
        # Default to today
        target_date = date_str or date.today().isoformat()

        # Get calls for the day
        calls_response = db_client.table("calls").select(
            "id, status, outcome, goal_achieved",
            count="exact"
        ).eq("tenant_id", tenant_id).gte(
            "created_at", f"{target_date}T00:00:00"
        ).lte(
            "created_at", f"{target_date}T23:59:59"
        ).execute()

        total_calls = calls_response.count or 0
        completed = len([c for c in calls_response.data if c.get("status") == "completed"])
        failed = len([c for c in calls_response.data if c.get("status") == "failed"])
        goal_achieved = len([c for c in calls_response.data if c.get("goal_achieved")])

        # Get active campaigns
        campaigns_response = db_client.table("campaigns").select(
            "id",
            count="exact"
        ).eq("tenant_id", tenant_id).eq("status", "running").execute()

        active_campaigns = campaigns_response.count or 0

        success_rate = (completed / total_calls * 100) if total_calls > 0 else 0

        return {
            "date": target_date,
            "total_calls": total_calls,
            "completed": completed,
            "failed": failed,
            "goal_achieved": goal_achieved,
            "success_rate": round(success_rate, 1),
            "active_campaigns": active_campaigns
        }
    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        return {"error": str(e)}


async def get_usage_info(
    tenant_id: str,
    db_client: Client
) -> Dict[str, Any]:
    """
    Get plan usage information.

    Returns:
        - Plan name
        - Minutes allocated
        - Minutes used
        - Minutes remaining
        - Plan expiry (if applicable)
    """
    try:
        # Get tenant with plan info
        tenant_response = db_client.table("tenants").select(
            "id, plan_id, minutes_allocated, minutes_used, subscription_status"
        ).eq("id", tenant_id).single().execute()

        tenant = tenant_response.data
        if not tenant:
            return {"error": "Tenant not found"}

        # Get plan details
        plan_response = db_client.table("plans").select(
            "name, price, minutes"
        ).eq("id", tenant.get("plan_id")).single().execute()

        plan = plan_response.data or {}

        minutes_allocated = tenant.get("minutes_allocated", 0)
        minutes_used = tenant.get("minutes_used", 0)

        return {
            "plan_name": plan.get("name", "Free"),
            "plan_price": plan.get("price", 0),
            "minutes_allocated": minutes_allocated,
            "minutes_used": minutes_used,
            "minutes_remaining": max(0, minutes_allocated - minutes_used),
            "usage_percentage": round((minutes_used / minutes_allocated * 100), 1) if minutes_allocated > 0 else 0,
            "subscription_status": tenant.get("subscription_status", "inactive")
        }
    except Exception as e:
        logger.error(f"Error getting usage info: {e}")
        return {"error": str(e)}


async def get_actions_today(
    tenant_id: str,
    db_client: Client
) -> Dict[str, Any]:
    """
    Get assistant actions performed today.
    """
    try:
        today = date.today().isoformat()

        response = db_client.table("assistant_actions").select(
            "id, type, status, triggered_by, created_at",
            count="exact"
        ).eq("tenant_id", tenant_id).gte(
            "created_at", f"{today}T00:00:00"
        ).execute()

        # Group by type
        by_type = {}
        for action in response.data:
            action_type = action.get("type")
            by_type[action_type] = by_type.get(action_type, 0) + 1

        return {
            "total_actions": response.count,
            "by_type": by_type,
            "recent_actions": response.data[:5]
        }
    except Exception as e:
        logger.error(f"Error getting actions: {e}")
        return {"error": str(e)}

"""
Dashboard and usage tools for the assistant agent.
"""
import logging
from typing import Optional, Dict, Any
from datetime import date
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client
from app.infrastructure.assistant.tools.campaigns import campaign_names
from app.infrastructure.assistant.tools.date_ranges import (
    SINCE_DESCRIPTION, UNTIL_DESCRIPTION, apply_range, normalize_range, utc_day_range,
)

logger = logging.getLogger(__name__)


class GetDashboardStatsInput(BaseModel):
    """Input for get_dashboard_stats tool"""
    date_str: Optional[str] = Field(None, description="Date in YYYY-MM-DD format, defaults to today")
    campaign_id: Optional[str] = Field(None, description="Only this campaign's calls")
    since: Optional[str] = Field(None, description=SINCE_DESCRIPTION)
    until: Optional[str] = Field(None, description=UNTIL_DESCRIPTION)


async def get_dashboard_stats(
    tenant_id: str,
    db_client: Client,
    date_str: Optional[str] = None,
    campaign_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get dashboard statistics for the tenant, or for one of its campaigns.

    Returns:
        - Total calls in the applied range
        - Calls completed
        - Calls failed
        - Success rate
        - Active campaigns
        - scope: "campaign" (with campaign_id/campaign_name) or "all_campaigns"
    """
    try:
        if date_str is not None and (since is not None or until is not None):
            return {"error": "Use either date_str or since/until, not both."}
        start, end = normalize_range(since, until)
        target_date = None
        if since is None and until is None:
            start, end = utc_day_range(date_str)
            target_date = start[:10]

        campaign_name = None
        if campaign_id:
            campaign_name = campaign_names(tenant_id, db_client, [campaign_id]).get(str(campaign_id))
            if campaign_name is None:
                return {"error": "campaign not found"}

        # The result describes exactly these inclusive/exclusive UTC bounds.
        calls_query = db_client.table("calls").select(
            "id, status, outcome, goal_achieved",
            count="exact"
        ).eq("tenant_id", tenant_id)
        if campaign_id:
            calls_query = calls_query.eq("campaign_id", campaign_id)
        calls_response = apply_range(calls_query, "created_at", start, end).execute()

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

        stats = {
            "date": target_date,
            "scope": "campaign" if campaign_id else "all_campaigns",
            "total_calls": total_calls,
            "completed": completed,
            "failed": failed,
            "goal_achieved": goal_achieved,
            "success_rate": round(success_rate, 1),
            "active_campaigns": active_campaigns,
            "active_campaigns_scope": "current_account",
            "applied_range": {"since": start, "until": end, "timestamp_field": "created_at"},
        }
        if campaign_id:
            stats["campaign_id"] = str(campaign_id)
            stats["campaign_name"] = campaign_name
        return stats
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

        # The minutes come from the meter the Billing page and every call
        # gate use (plan minutes plus bought minutes, this allowance month).
        # tenants.minutes_used is stale and minutes_allocated omits bought
        # minutes, so a pay-as-you-go customer was told they had none (QA
        # F3-07). An unknown meter is reported as unknown, never as zero.
        from app.domain.services import minutes_quota

        status = await minutes_quota.tenant_minutes_status(tenant_id)
        known = status.state == "known"
        minutes_allocated = status.allocated if known else None
        minutes_used = status.used_minutes if status.state != "unavailable" else None

        return {
            "plan_name": plan.get("name", "Free"),
            "plan_price": plan.get("price", 0),
            "minutes_state": status.state,
            "minutes_allocated": minutes_allocated,
            "minutes_used": minutes_used,
            "minutes_remaining": status.remaining_minutes if known else None,
            "usage_percentage": (
                round(minutes_used / minutes_allocated * 100, 1)
                if known and minutes_allocated else None
            ),
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

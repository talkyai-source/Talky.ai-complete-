"""
QuotaService - Tenant Action Quota Management

Enforces per-tenant limits on actions (emails, SMS, calls, meetings).
Uses daily reset at midnight UTC.
"""
import logging
from typing import Optional, Dict, Any
from datetime import date
from dataclasses import dataclass
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Raised when a tenant exceeds their action quota."""
    def __init__(self, action_type: str, limit: int, used: int):
        self.action_type = action_type
        self.limit = limit
        self.used = used
        self.message = f"Daily {action_type} quota exceeded: {used}/{limit}"
        super().__init__(self.message)


@dataclass
class QuotaStatus:
    """Current quota status for a tenant."""
    emails_limit: int
    emails_used: int
    sms_limit: int
    sms_used: int
    calls_limit: int
    calls_used: int
    meetings_limit: int
    meetings_used: int
    
    @property
    def emails_remaining(self) -> int:
        return max(0, self.emails_limit - self.emails_used)
    
    @property
    def sms_remaining(self) -> int:
        return max(0, self.sms_limit - self.sms_used)
    
    @property
    def calls_remaining(self) -> int:
        return max(0, self.calls_limit - self.calls_used)
    
    @property
    def meetings_remaining(self) -> int:
        return max(0, self.meetings_limit - self.meetings_used)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "emails": {"limit": self.emails_limit, "used": self.emails_used, "remaining": self.emails_remaining},
            "sms": {"limit": self.sms_limit, "used": self.sms_used, "remaining": self.sms_remaining},
            "calls": {"limit": self.calls_limit, "used": self.calls_used, "remaining": self.calls_remaining},
            "meetings": {"limit": self.meetings_limit, "used": self.meetings_used, "remaining": self.meetings_remaining}
        }


# Default quotas for new tenants
DEFAULT_QUOTAS = {
    "emails_per_day": 50,
    "sms_per_day": 25,
    "calls_per_day": 50,
    "meetings_per_day": 10
}

# Mapping from action type to quota/usage field names
ACTION_TYPE_MAPPING = {
    "send_email": ("emails_per_day", "emails_sent"),
    "send_sms": ("sms_per_day", "sms_sent"),
    "initiate_call": ("calls_per_day", "calls_initiated"),
    "book_meeting": ("meetings_per_day", "meetings_booked")
}


class QuotaService:
    """
    Manages per-tenant action quotas.
    
    Usage:
        service = get_quota_service(db_client)
        
        # Check if action is allowed
        if await service.check_quota(tenant_id, "send_email"):
            # Execute action
            await service.increment_usage(tenant_id, "send_email")
        else:
            raise QuotaExceededError(...)
    """
    
    def __init__(self, db_client: Client):
        self.db_client = db_client
    
    async def check_quota(self, tenant_id: str, action_type: str) -> bool:
        """
        Check if tenant is within quota for the given action type.
        
        Args:
            tenant_id: Tenant ID
            action_type: Action type (send_email, send_sms, initiate_call, book_meeting)
            
        Returns:
            True if within quota, False if exceeded
        """
        if action_type not in ACTION_TYPE_MAPPING:
            # Unknown action type, allow by default
            logger.warning(f"Unknown action type for quota check: {action_type}")
            return True
        
        quota_field, usage_field = ACTION_TYPE_MAPPING[action_type]
        
        # Get quota limit
        quota = await self._get_tenant_quota(tenant_id)
        limit = quota.get(quota_field, DEFAULT_QUOTAS.get(quota_field, float('inf')))
        
        # Get current usage
        usage = await self._get_today_usage(tenant_id)
        used = usage.get(usage_field, 0)
        
        return used < limit
    
    async def increment_usage(self, tenant_id: str, action_type: str) -> int:
        """
        Increment usage counter for the given action type.
        
        Args:
            tenant_id: Tenant ID
            action_type: Action type
            
        Returns:
            New usage count
        """
        if action_type not in ACTION_TYPE_MAPPING:
            logger.warning(f"Unknown action type for usage increment: {action_type}")
            return 0
        
        _, usage_field = ACTION_TYPE_MAPPING[action_type]
        today = date.today().isoformat()
        
        try:
            # Try to upsert usage record
            response = self.db_client.rpc(
                "increment_quota_usage",
                {
                    "p_tenant_id": tenant_id,
                    "p_usage_date": today,
                    "p_field": usage_field
                }
            ).execute()
            
            if response.data is not None:
                return response.data
            
            # Fallback: manual upsert if RPC doesn't exist
            return await self._increment_usage_manual(tenant_id, usage_field, today)
            
        except Exception as e:
            logger.warning(f"RPC not available, using manual increment: {e}")
            return await self._increment_usage_manual(tenant_id, usage_field, today)
    
    async def _increment_usage_manual(self, tenant_id: str, usage_field: str, today: str) -> int:
        """Manually increment usage when RPC is not available."""
        try:
            # Check if record exists
            existing = self.db_client.table("tenant_quota_usage").select(
                "id", usage_field
            ).eq("tenant_id", tenant_id).eq("usage_date", today).execute()
            
            if existing.data:
                # Update existing record
                current = existing.data[0].get(usage_field, 0)
                new_value = current + 1
                
                self.db_client.table("tenant_quota_usage").update({
                    usage_field: new_value
                }).eq("id", existing.data[0]["id"]).execute()
                
                return new_value
            else:
                # Insert new record
                insert_data = {
                    "tenant_id": tenant_id,
                    "usage_date": today,
                    usage_field: 1
                }
                self.db_client.table("tenant_quota_usage").insert(insert_data).execute()
                return 1
                
        except Exception as e:
            logger.error(f"Error incrementing usage: {e}")
            return 0
    
    async def get_quota_status(self, tenant_id: str) -> QuotaStatus:
        """
        Get full quota status for a tenant.
        
        Args:
            tenant_id: Tenant ID
            
        Returns:
            QuotaStatus with limits and usage for all action types
        """
        quota = await self._get_tenant_quota(tenant_id)
        usage = await self._get_today_usage(tenant_id)
        
        return QuotaStatus(
            emails_limit=quota.get("emails_per_day", DEFAULT_QUOTAS["emails_per_day"]),
            emails_used=usage.get("emails_sent", 0),
            sms_limit=quota.get("sms_per_day", DEFAULT_QUOTAS["sms_per_day"]),
            sms_used=usage.get("sms_sent", 0),
            calls_limit=quota.get("calls_per_day", DEFAULT_QUOTAS["calls_per_day"]),
            calls_used=usage.get("calls_initiated", 0),
            meetings_limit=quota.get("meetings_per_day", DEFAULT_QUOTAS["meetings_per_day"]),
            meetings_used=usage.get("meetings_booked", 0)
        )
    
    async def _get_tenant_quota(self, tenant_id: str) -> Dict[str, Any]:
        """Get quota limits for a tenant."""
        try:
            response = self.db_client.table("tenant_quotas").select(
                "emails_per_day, sms_per_day, calls_per_day, meetings_per_day"
            ).eq("tenant_id", tenant_id).execute()
            
            if response.data:
                return response.data[0]
            
            # Create default quotas for new tenant
            await self._create_default_quota(tenant_id)
            return DEFAULT_QUOTAS
            
        except Exception as e:
            logger.error(f"Error fetching tenant quota: {e}")
            return DEFAULT_QUOTAS
    
    async def _get_today_usage(self, tenant_id: str) -> Dict[str, Any]:
        """Get today's usage for a tenant."""
        try:
            today = date.today().isoformat()
            
            response = self.db_client.table("tenant_quota_usage").select(
                "emails_sent, sms_sent, calls_initiated, meetings_booked"
            ).eq("tenant_id", tenant_id).eq("usage_date", today).execute()
            
            if response.data:
                return response.data[0]
            
            return {
                "emails_sent": 0,
                "sms_sent": 0,
                "calls_initiated": 0,
                "meetings_booked": 0
            }
            
        except Exception as e:
            logger.error(f"Error fetching today's usage: {e}")
            return {
                "emails_sent": 0,
                "sms_sent": 0,
                "calls_initiated": 0,
                "meetings_booked": 0
            }
    
    async def _create_default_quota(self, tenant_id: str) -> None:
        """Create default quota record for a new tenant."""
        try:
            self.db_client.table("tenant_quotas").insert({
                "tenant_id": tenant_id,
                **DEFAULT_QUOTAS
            }).execute()
            logger.info(f"Created default quotas for tenant {tenant_id}")
        except Exception as e:
            # Ignore duplicate key errors
            if "duplicate" not in str(e).lower():
                logger.error(f"Error creating default quota: {e}")


# Singleton instance
_quota_service: Optional[QuotaService] = None


def get_quota_service(db_client: Client) -> QuotaService:
    """Get or create QuotaService instance."""
    global _quota_service
    if _quota_service is None:
        _quota_service = QuotaService(db_client)
    return _quota_service

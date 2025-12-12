"""
Scheduling Rules Engine
Determines if a call can be made based on tenant rules
"""
import logging
from typing import Tuple, Optional
from datetime import datetime

from app.domain.models.calling_rules import CallingRules

logger = logging.getLogger(__name__)


class SchedulingRuleEngine:
    """
    Evaluates scheduling rules to determine if a call can be made.
    
    Rules checked:
    1. Time window (tenant-configurable hours)
    2. Day of week (tenant-configurable days)
    3. Max concurrent calls per tenant/campaign
    4. Lead cooldown period (min hours between calls)
    """
    
    def __init__(self):
        self._active_calls: dict[str, int] = {}  # tenant_id -> count
        self._campaign_calls: dict[str, int] = {}  # campaign_id -> count
    
    async def can_make_call(
        self,
        tenant_id: str,
        campaign_id: str,
        rules: CallingRules,
        lead_last_called: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """
        Check if a call can be made now.
        
        Args:
            tenant_id: Tenant making the call
            campaign_id: Campaign the call belongs to
            rules: Tenant's calling rules
            lead_last_called: Last time this lead was called (optional)
            
        Returns:
            (can_call, reason)
        """
        # Rule 1: Time window check
        in_window, window_reason = rules.is_within_time_window()
        if not in_window:
            logger.debug(f"Time window check failed: {window_reason}")
            return False, window_reason
        
        # Rule 2: Concurrent call limit check
        current_tenant_calls = self._active_calls.get(tenant_id, 0)
        if current_tenant_calls >= rules.max_concurrent_calls:
            reason = f"max_concurrent_calls_reached_{current_tenant_calls}/{rules.max_concurrent_calls}"
            logger.debug(f"Concurrent limit reached for tenant {tenant_id}: {reason}")
            return False, reason
        
        # Rule 3: Lead cooldown check
        if lead_last_called:
            hours_since_last_call = (datetime.utcnow() - lead_last_called).total_seconds() / 3600
            if hours_since_last_call < rules.min_hours_between_calls:
                reason = f"lead_cooldown_{hours_since_last_call:.1f}h_of_{rules.min_hours_between_calls}h"
                logger.debug(f"Lead cooldown active: {reason}")
                return False, reason
        
        # All rules passed
        return True, "all_rules_passed"
    
    def register_call_start(self, tenant_id: str, campaign_id: str) -> None:
        """Register that a call has started (increment counters)."""
        self._active_calls[tenant_id] = self._active_calls.get(tenant_id, 0) + 1
        self._campaign_calls[campaign_id] = self._campaign_calls.get(campaign_id, 0) + 1
        logger.debug(
            f"Call started: tenant {tenant_id} now has {self._active_calls[tenant_id]} active calls"
        )
    
    def register_call_end(self, tenant_id: str, campaign_id: str) -> None:
        """Register that a call has ended (decrement counters)."""
        if tenant_id in self._active_calls and self._active_calls[tenant_id] > 0:
            self._active_calls[tenant_id] -= 1
        if campaign_id in self._campaign_calls and self._campaign_calls[campaign_id] > 0:
            self._campaign_calls[campaign_id] -= 1
        logger.debug(
            f"Call ended: tenant {tenant_id} now has {self._active_calls.get(tenant_id, 0)} active calls"
        )
    
    def get_active_call_count(self, tenant_id: str) -> int:
        """Get current active call count for a tenant."""
        return self._active_calls.get(tenant_id, 0)
    
    def get_campaign_call_count(self, campaign_id: str) -> int:
        """Get current active call count for a campaign."""
        return self._campaign_calls.get(campaign_id, 0)
    
    def get_delay_until_next_window(self, rules: CallingRules) -> int:
        """
        Calculate seconds until next calling window opens.
        
        Args:
            rules: Tenant's calling rules
            
        Returns:
            Seconds until next window (0 if currently in window)
        """
        in_window, _ = rules.is_within_time_window()
        if in_window:
            return 0
        
        next_window = rules.get_next_window_start()
        delay = (next_window - datetime.now()).total_seconds()
        return max(0, int(delay))
    
    def reset(self) -> None:
        """Reset all counters (for testing)."""
        self._active_calls.clear()
        self._campaign_calls.clear()

"""
Scheduling Rules Engine
Determines if a call can be made based on tenant rules
"""
import logging
from typing import Tuple, Optional
from datetime import datetime, timezone

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
        lead_last_called: Optional[datetime] = None,
        active_calls_override: Optional[int] = None,
        lead_attempts_today: Optional[int] = None,
        lead_timezone: Optional[str] = None,
        enforce_window: bool = True,
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
        # Rule 1: Time window check — evaluated in the campaign's timezone
        # (Phase 3c-v2, via the effective rules' tz) or an explicit
        # lead_timezone override. Skipped entirely when enforce_window is
        # False — that's the client's "call anytime" override; the UI still
        # warns, but we never block the dial here.
        if enforce_window:
            in_window, window_reason = rules.is_within_time_window(tz_override=lead_timezone)
            if not in_window:
                logger.debug(f"Time window check failed: {window_reason}")
                return False, window_reason
        
        # Rule 2: Concurrent call limit check.
        # Prefer the authoritative live-call count the caller passes in (the
        # telephony bridge's global_concurrency Redis ledger, which acquires a
        # slot on answer, releases on hangup, and self-heals via the watchdog
        # channel reconcile). The in-memory self._active_calls counter is a
        # last-resort fallback ONLY: it has no reliable decrement signal here
        # (a call's lifecycle lives in the API process, not the dialer), so
        # relying on it alone leaks monotonically until restart and wedges
        # every outbound call at the cap (the 10/10 outage).
        current_tenant_calls = (
            active_calls_override
            if active_calls_override is not None
            else self._active_calls.get(tenant_id, 0)
        )
        if current_tenant_calls >= rules.max_concurrent_calls:
            reason = f"max_concurrent_calls_reached_{current_tenant_calls}/{rules.max_concurrent_calls}"
            logger.debug(f"Concurrent limit reached for tenant {tenant_id}: {reason}")
            return False, reason
        
        # Rule 3: Lead cooldown check
        if lead_last_called:
            now = datetime.now(timezone.utc)
            # Normalize to aware datetime if the DB value is naive
            if lead_last_called.tzinfo is None:
                lead_last_called = lead_last_called.replace(tzinfo=timezone.utc)
            hours_since_last_call = (now - lead_last_called).total_seconds() / 3600
            if hours_since_last_call < rules.min_hours_between_calls:
                reason = f"lead_cooldown_{hours_since_last_call:.1f}h_of_{rules.min_hours_between_calls}h"
                logger.debug(f"Lead cooldown active: {reason}")
                return False, reason

        # Rule 4: Daily per-lead attempt ceiling. Defense-in-depth above
        # the per-disposition caps — even if busy/no-answer retries stack
        # up, a single lead is never dialled more than the configured
        # number of times within one calendar day. Disabled when the cap
        # is 0 or the caller didn't supply today's count.
        daily_cap = getattr(rules, "max_calls_per_lead_per_day", 0)
        if daily_cap and lead_attempts_today is not None and lead_attempts_today >= daily_cap:
            reason = f"daily_lead_cap_reached_{lead_attempts_today}/{daily_cap}"
            logger.debug(f"Daily per-lead cap reached: {reason}")
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
    
    def get_delay_until_next_window(
        self, rules: CallingRules, lead_timezone: Optional[str] = None,
    ) -> int:
        """
        Calculate seconds until next calling window opens.

        Args:
            rules: Tenant's calling rules
            lead_timezone: per-lead IANA tz (Phase 3c); the delay is
                computed against the prospect's local window when set.

        Returns:
            Seconds until next window (0 if currently in window)
        """
        in_window, _ = rules.is_within_time_window(tz_override=lead_timezone)
        if in_window:
            return 0

        next_window = rules.get_next_window_start(tz_override=lead_timezone)
        delay = (next_window - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(delay))
    
    def reset(self) -> None:
        """Reset all counters (for testing)."""
        self._active_calls.clear()
        self._campaign_calls.clear()

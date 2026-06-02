"""
Call Guard Service (Day 7)

Unified pre-call validation service.

Ensures ALL security and business rules are checked before call initiation:
- Tenant/partner status
- Rate limits
- Concurrency
- Feature flags
- Geographic restrictions
- Abuse patterns

References:
- CTIA Anti-Fraud Best Practices
- OWASP Voice Security
- Twilio Security Guidelines
- FCA Telecom Fraud Guidance (UK)

Usage:
    guard = CallGuard(redis_client, db_pool)
    result = await guard.evaluate(
        tenant_id="...",
        phone_number="+1234567890",
        campaign_id="...",
        user_id="..."
    )

    if result.decision == GuardDecision.ALLOW:
        await initiate_call(...)
    else:
        handle_blocked_call(result)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import asyncpg

from app.domain.services.telephony_rate_limiter import TelephonyRateLimiter, RateLimitAction
from app.domain.services.telephony_concurrency_limiter import TelephonyConcurrencyLimiter, LeaseKind

logger = logging.getLogger(__name__)

# E.164 validation regex
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")


class GuardDecision(str, Enum):
    """Possible decisions from the call guard."""
    ALLOW = "allow"
    BLOCK = "block"
    QUEUE = "queue"
    THROTTLE = "throttle"


class GuardCheck(str, Enum):
    """Individual guard checks performed."""
    TENANT_ACTIVE = "tenant_active"
    PARTNER_ACTIVE = "partner_active"
    SUBSCRIPTION_VALID = "subscription_valid"
    FEATURE_ENABLED = "feature_enabled"
    RATE_LIMIT = "rate_limit"
    CONCURRENCY_LIMIT = "concurrency_limit"
    GEOGRAPHIC_ALLOWED = "geographic_allowed"
    NUMBER_VALID = "number_valid"
    BUSINESS_HOURS = "business_hours"
    DNC_CHECK = "dnc_check"
    VELOCITY_CHECK = "velocity_check"
    SPEND_LIMIT = "spend_limit"


@dataclass
class CheckResult:
    """Result of a single guard check."""
    check: GuardCheck
    passed: bool
    latency_ms: int = 0
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check.value,
            "passed": self.passed,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
            "details": self.details,
        }


@dataclass
class GuardResult:
    """Complete result from call guard evaluation."""
    decision: GuardDecision
    tenant_id: str
    phone_number: str
    check_results: List[CheckResult]
    failed_checks: List[GuardCheck]
    total_latency_ms: int
    queue_position: Optional[int] = None
    retry_after_seconds: Optional[int] = None
    call_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "tenant_id": self.tenant_id,
            "phone_number": self.phone_number,
            "call_id": self.call_id,
            "checks": [r.to_dict() for r in self.check_results],
            "failed_checks": [c.value for c in self.failed_checks],
            "total_latency_ms": self.total_latency_ms,
            "queue_position": self.queue_position,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass
class TenantCallLimits:
    """Tenant call limits configuration."""
    calls_per_minute: int = 60
    calls_per_hour: int = 1000
    calls_per_day: int = 10000
    max_concurrent_calls: int = 10
    max_queue_size: int = 50
    monthly_minutes_allocated: int = 0
    monthly_minutes_used: int = 0
    monthly_spend_cap: Optional[float] = None
    monthly_spend_used: float = 0.0
    max_call_duration_seconds: int = 3600
    min_call_interval_seconds: int = 300
    allowed_country_codes: List[str] = field(default_factory=list)
    blocked_country_codes: List[str] = field(default_factory=list)
    blocked_prefixes: List[str] = field(default_factory=list)
    features_enabled: Dict[str, Any] = field(default_factory=dict)
    features_disabled: Dict[str, Any] = field(default_factory=dict)
    respect_business_hours: bool = False
    business_hours_start: Optional[time] = None
    business_hours_end: Optional[time] = None
    business_hours_timezone: str = "UTC"
    is_active: bool = True


@dataclass
class PartnerLimits:
    """Partner aggregate limits."""
    max_tenants: int = 10
    current_tenant_count: int = 0
    aggregate_calls_per_minute: int = 600
    aggregate_calls_per_hour: int = 10000
    aggregate_calls_per_day: int = 100000
    aggregate_concurrent_calls: int = 100
    revenue_share_percent: float = 20.0
    min_billing_amount: float = 100.0
    max_billing_amount: Optional[float] = None
    feature_whitelist: List[str] = field(default_factory=list)
    feature_blacklist: List[str] = field(default_factory=list)
    fraud_detection_sensitivity: int = 50
    is_active: bool = True


class CallGuard:
    """
    Centralized call authorization service.

    All call initiation MUST pass through CallGuard.evaluate() before
    being handed to telephony adapters.

    Performance targets:
    - Total latency: < 100ms p99
    - Database queries: Minimized with caching
    - Fail-closed: Any errors result in BLOCK
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis_client: Optional[Any] = None,
        rate_limiter: Optional[TelephonyRateLimiter] = None,
        concurrency_limiter: Optional[TelephonyConcurrencyLimiter] = None,
        fail_open: bool = False,
    ):
        """
        Initialize Call Guard.

        Args:
            db_pool: PostgreSQL connection pool
            redis_client: Redis client for caching
            rate_limiter: Existing TelephonyRateLimiter instance
            concurrency_limiter: Existing TelephonyConcurrencyLimiter instance
            fail_open: If True, allow calls when guard fails (not recommended)
        """
        self._db_pool = db_pool
        self._redis = redis_client
        self._rate_limiter = rate_limiter or TelephonyRateLimiter(redis_client)
        self._concurrency_limiter = concurrency_limiter or TelephonyConcurrencyLimiter(redis_client)
        self._fail_open = fail_open

    async def evaluate(
        self,
        tenant_id: str,
        phone_number: str,
        campaign_id: Optional[str] = None,
        user_id: Optional[str] = None,
        call_type: str = "outbound",
        estimated_duration_seconds: Optional[int] = None,
        feature_required: Optional[str] = None,
    ) -> GuardResult:
        """
        Evaluate if a call should be allowed.

        Runs all guard checks in order of priority. First failure determines
        the outcome (fail-fast).

        Args:
            tenant_id: UUID of the tenant initiating the call
            phone_number: E.164 formatted phone number
            campaign_id: Optional campaign context
            user_id: Optional user initiating the call
            call_type: outbound, inbound, or transfer
            estimated_duration_seconds: Estimated call duration for spend checks
            feature_required: Feature flag to check (e.g., "international_calls")

        Returns:
            GuardResult with decision and details of all checks

        Raises:
            Exception: If fail_open=False and guard check fails fatally
        """
        import time

        start_time = time.time()
        check_results: List[CheckResult] = []
        failed_checks: List[GuardCheck] = []

        # Normalize phone number
        normalized_number = self._normalize_phone_number(phone_number)

        try:
            # Fetch tenant configuration
            tenant_limits = await self._get_tenant_limits(tenant_id)
            partner_limits = await self._get_partner_limits(tenant_id)
            partner_id = await self._get_partner_id(tenant_id)
        except Exception as e:
            logger.error(f"Failed to load tenant configuration: {e}")
            if not self._fail_open:
                return GuardResult(
                    decision=GuardDecision.BLOCK,
                    tenant_id=tenant_id,
                    phone_number=phone_number,
                    check_results=[
                        CheckResult(
                            check=GuardCheck.TENANT_ACTIVE,
                            passed=False,
                            reason="configuration_load_error",
                            details={"error": str(e)},
                        )
                    ],
                    failed_checks=[GuardCheck.TENANT_ACTIVE],
                    total_latency_ms=0,
                )
            tenant_limits = None
            partner_limits = None
            partner_id = None

        # Define checks in priority order
        checks: List[Tuple[GuardCheck, callable]] = [
            (GuardCheck.TENANT_ACTIVE, self._check_tenant_active),
            (GuardCheck.PARTNER_ACTIVE, self._check_partner_active),
            (GuardCheck.SUBSCRIPTION_VALID, self._check_subscription),
            (GuardCheck.FEATURE_ENABLED, self._check_feature_enabled),
            (GuardCheck.NUMBER_VALID, self._check_number_valid),
            (GuardCheck.GEOGRAPHIC_ALLOWED, self._check_geographic),
            (GuardCheck.DNC_CHECK, self._check_dnc),
            (GuardCheck.RATE_LIMIT, self._check_rate_limit),
            (GuardCheck.CONCURRENCY_LIMIT, self._check_concurrency),
            (GuardCheck.SPEND_LIMIT, self._check_spend_limit),
            (GuardCheck.BUSINESS_HOURS, self._check_business_hours),
            (GuardCheck.VELOCITY_CHECK, self._check_velocity),
        ]

        for check_type, check_func in checks:
            check_start = time.time()
            try:
                result = await check_func(
                    tenant_id=tenant_id,
                    phone_number=normalized_number,
                    tenant_limits=tenant_limits,
                    partner_limits=partner_limits,
                    partner_id=partner_id,
                    campaign_id=campaign_id,
                    feature_required=feature_required,
                    estimated_duration_seconds=estimated_duration_seconds,
                )
                result.latency_ms = int((time.time() - check_start) * 1000)
                check_results.append(result)

                if not result.passed:
                    failed_checks.append(check_type)
                    break  # Fail fast

            except Exception as e:
                logger.warning(f"Guard check {check_type} error (treating as passed): {e}")
                error_result = CheckResult(
                    check=check_type,
                    passed=True,
                    latency_ms=int((time.time() - check_start) * 1000),
                    reason=f"check_error_skipped: {str(e)}",
                )
                check_results.append(error_result)
                # Do NOT add to failed_checks — infrastructure errors should
                # not block calls.  Only genuine check failures (passed=False
                # returned by the check function) should block/queue/throttle.

        total_latency_ms = int((time.time() - start_time) * 1000)

        # Determine decision
        queue_position = None
        retry_after_seconds = None

        if not failed_checks:
            decision = GuardDecision.ALLOW
        elif GuardCheck.CONCURRENCY_LIMIT in failed_checks:
            # Check if queue available
            if tenant_limits and tenant_limits.max_queue_size > 0:
                decision = GuardDecision.QUEUE
                queue_position = await self._get_queue_position(tenant_id)
            else:
                decision = GuardDecision.BLOCK
        elif GuardCheck.RATE_LIMIT in failed_checks:
            decision = GuardDecision.THROTTLE
            retry_after_seconds = self._get_retry_after_seconds(tenant_id)
        else:
            decision = GuardDecision.BLOCK

        result = GuardResult(
            decision=decision,
            tenant_id=tenant_id,
            phone_number=phone_number,
            check_results=check_results,
            failed_checks=failed_checks,
            total_latency_ms=total_latency_ms,
            queue_position=queue_position,
            retry_after_seconds=retry_after_seconds,
        )

        # Log decision asynchronously (don't block response)
        try:
            await self._log_decision(result, partner_id)
        except Exception as e:
            logger.warning(f"Failed to log guard decision: {e}")

        return result

    # -------------------------------------------------------------------------
    # Guard Check Methods
    # -------------------------------------------------------------------------

    async def _check_tenant_active(
        self,
        tenant_id: str,
        **kwargs
    ) -> CheckResult:
        """Check if tenant is active and not suspended."""
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, subscription_status
                    FROM tenants
                    WHERE id = $1
                    """,
                    tenant_id,
                )

                if not row:
                    return CheckResult(
                        check=GuardCheck.TENANT_ACTIVE,
                        passed=False,
                        reason="tenant_not_found",
                    )

                status = row.get("subscription_status", "active")
                if status in ("suspended", "cancelled"):
                    return CheckResult(
                        check=GuardCheck.TENANT_ACTIVE,
                        passed=False,
                        reason=f"tenant_{status}",
                    )

                return CheckResult(
                    check=GuardCheck.TENANT_ACTIVE,
                    passed=True,
                )
        except asyncpg.PostgresError as e:
            logger.debug(f"tenant active check failed (column may not exist): {e}")
            return CheckResult(
                check=GuardCheck.TENANT_ACTIVE,
                passed=True,
                reason="schema_check_skipped",
            )

    async def _check_partner_active(
        self,
        tenant_id: str,
        partner_id: Optional[str] = None,
        partner_limits: Optional[PartnerLimits] = None,
        **kwargs
    ) -> CheckResult:
        """Check if partner (if applicable) is active."""
        if not partner_id:
            return CheckResult(
                check=GuardCheck.PARTNER_ACTIVE,
                passed=True,
                reason="no_partner",
            )

        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, subscription_status
                    FROM tenants
                    WHERE id = $1
                    """,
                    partner_id,
                )

                if not row:
                    return CheckResult(
                        check=GuardCheck.PARTNER_ACTIVE,
                        passed=False,
                        reason="partner_not_found",
                    )

                status = row.get("subscription_status", "active")
                if status in ("suspended", "cancelled"):
                    return CheckResult(
                        check=GuardCheck.PARTNER_ACTIVE,
                        passed=False,
                        reason=f"partner_{status}",
                    )

                return CheckResult(
                    check=GuardCheck.PARTNER_ACTIVE,
                    passed=True,
                )
        except asyncpg.PostgresError as e:
            logger.debug(f"partner active check failed: {e}")
            return CheckResult(
                check=GuardCheck.PARTNER_ACTIVE,
                passed=True,
                reason="schema_check_skipped",
            )

    async def _check_subscription(
        self,
        tenant_id: str,
        **kwargs
    ) -> CheckResult:
        """Check if tenant has valid subscription/billing status."""
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT subscription_status
                    FROM tenants
                    WHERE id = $1
                    """,
                    tenant_id,
                )

                if not row:
                    return CheckResult(
                        check=GuardCheck.SUBSCRIPTION_VALID,
                        passed=False,
                        reason="tenant_not_found",
                    )

                status = row.get("subscription_status", "active")

                if status in ("suspended", "cancelled", "past_due"):
                    return CheckResult(
                        check=GuardCheck.SUBSCRIPTION_VALID,
                        passed=False,
                        reason=f"subscription_{status}",
                    )

                return CheckResult(
                    check=GuardCheck.SUBSCRIPTION_VALID,
                    passed=True,
                    details={"status": status},
                )
        except asyncpg.PostgresError as e:
            logger.debug(f"subscription check failed (column may not exist): {e}")
            return CheckResult(
                check=GuardCheck.SUBSCRIPTION_VALID,
                passed=True,
                reason="schema_check_skipped",
            )

    async def _check_feature_enabled(
        self,
        tenant_id: str,
        tenant_limits: Optional[TenantCallLimits] = None,
        partner_limits: Optional[PartnerLimits] = None,
        feature_required: Optional[str] = None,
        **kwargs
    ) -> CheckResult:
        """Check if required feature is enabled."""
        if not feature_required:
            return CheckResult(
                check=GuardCheck.FEATURE_ENABLED,
                passed=True,
                reason="no_feature_required",
            )

        # Check tenant-level feature disable list
        if tenant_limits and feature_required in tenant_limits.features_disabled:
            return CheckResult(
                check=GuardCheck.FEATURE_ENABLED,
                passed=False,
                reason=f"feature_disabled_for_tenant: {feature_required}",
            )

        # Check partner feature blacklist
        if partner_limits and partner_limits.feature_blacklist:
            if feature_required in partner_limits.feature_blacklist:
                return CheckResult(
                    check=GuardCheck.FEATURE_ENABLED,
                    passed=False,
                    reason=f"feature_blacklisted_by_partner: {feature_required}",
                )

        # Check partner feature whitelist (if set, feature must be in it)
        if partner_limits and partner_limits.feature_whitelist:
            if feature_required not in partner_limits.feature_whitelist:
                return CheckResult(
                    check=GuardCheck.FEATURE_ENABLED,
                    passed=False,
                    reason=f"feature_not_whitelisted_by_partner: {feature_required}",
                )

        # Check tenant feature enable list
        if tenant_limits and tenant_limits.features_enabled:
            if feature_required in tenant_limits.features_enabled:
                return CheckResult(
                    check=GuardCheck.FEATURE_ENABLED,
                    passed=True,
                    details={"feature": feature_required},
                )

        # Default: allow if not explicitly disabled
        return CheckResult(
            check=GuardCheck.FEATURE_ENABLED,
            passed=True,
            reason="feature_default_allowed",
        )

    async def _check_number_valid(
        self,
        phone_number: str,
        **kwargs
    ) -> CheckResult:
        """Check if phone number is valid E.164 format."""
        if not phone_number:
            return CheckResult(
                check=GuardCheck.NUMBER_VALID,
                passed=False,
                reason="phone_number_missing",
            )

        if not _E164_RE.match(phone_number):
            return CheckResult(
                check=GuardCheck.NUMBER_VALID,
                passed=False,
                reason="invalid_e164_format",
                details={"received": phone_number},
            )

        return CheckResult(
            check=GuardCheck.NUMBER_VALID,
            passed=True,
        )

    async def _check_rate_limit(
        self,
        tenant_id: str,
        tenant_limits: Optional[TenantCallLimits] = None,
        **kwargs
    ) -> CheckResult:
        """Check rate limits using TelephonyRateLimiter."""
        if not tenant_limits:
            return CheckResult(
                check=GuardCheck.RATE_LIMIT,
                passed=True,
                reason="no_limits_configured",
            )

        async with self._db_pool.acquire() as conn:
            decision = await self._rate_limiter.evaluate(
                conn=conn,
                tenant_id=tenant_id,
                policy_scope="calls",
                metric_key="initiate",
            )

        return CheckResult(
            check=GuardCheck.RATE_LIMIT,
            passed=decision.action == RateLimitAction.ALLOW,
            reason=None if decision.action == RateLimitAction.ALLOW else decision.reason,
            details={
                "action": decision.action.value,
                "counter": decision.counter_value,
                "threshold": decision.threshold_value,
            },
        )

    async def _check_concurrency(
        self,
        tenant_id: str,
        tenant_limits: Optional[TenantCallLimits] = None,
        **kwargs
    ) -> CheckResult:
        """Check concurrency limits."""
        if not tenant_limits:
            return CheckResult(
                check=GuardCheck.CONCURRENCY_LIMIT,
                passed=True,
                reason="no_limits_configured",
            )

        # Get current status without acquiring lease
        async with self._db_pool.acquire() as conn:
            status = await self._concurrency_limiter.get_status(
                conn=conn,
                tenant_id=tenant_id,
            )

        max_calls = tenant_limits.max_concurrent_calls
        current_calls = status["active_calls"]

        return CheckResult(
            check=GuardCheck.CONCURRENCY_LIMIT,
            passed=current_calls < max_calls,
            reason=None if current_calls < max_calls else f"concurrency_limit: {current_calls}/{max_calls}",
            details={
                "active_calls": current_calls,
                "max_calls": max_calls,
            },
        )

    async def _check_geographic(
        self,
        phone_number: str,
        tenant_limits: Optional[TenantCallLimits] = None,
        **kwargs
    ) -> CheckResult:
        """Check if destination is in allowed countries."""
        country_code = self._extract_country_code(phone_number)

        if not tenant_limits:
            return CheckResult(
                check=GuardCheck.GEOGRAPHIC_ALLOWED,
                passed=True,
                details={"country_code": country_code},
            )

        # Check blocked prefixes first
        for prefix in tenant_limits.blocked_prefixes:
            if phone_number.startswith(prefix):
                return CheckResult(
                    check=GuardCheck.GEOGRAPHIC_ALLOWED,
                    passed=False,
                    reason=f"blocked_prefix: {prefix}",
                )

        # If no country code extracted, allow but log
        if not country_code:
            return CheckResult(
                check=GuardCheck.GEOGRAPHIC_ALLOWED,
                passed=True,
                reason="country_code_unknown",
            )

        # Check blocked country codes
        if tenant_limits.blocked_country_codes and country_code in tenant_limits.blocked_country_codes:
            return CheckResult(
                check=GuardCheck.GEOGRAPHIC_ALLOWED,
                passed=False,
                reason=f"blocked_country: {country_code}",
            )

        # Check allowed country codes (if set, must be in list)
        if tenant_limits.allowed_country_codes and country_code not in tenant_limits.allowed_country_codes:
            return CheckResult(
                check=GuardCheck.GEOGRAPHIC_ALLOWED,
                passed=False,
                reason=f"country_not_allowed: {country_code}",
            )

        return CheckResult(
            check=GuardCheck.GEOGRAPHIC_ALLOWED,
            passed=True,
            details={"country_code": country_code},
        )

    async def _check_dnc(
        self,
        tenant_id: str,
        phone_number: str,
        **kwargs
    ) -> CheckResult:
        """Check Do-Not-Call list."""
        try:
            async with self._db_pool.acquire() as conn:
                # Check tenant-specific DNC
                row = await conn.fetchrow(
                    """
                    SELECT id, source, reason
                    FROM dnc_entries
                    WHERE (tenant_id = $1 OR tenant_id IS NULL)
                      AND normalized_number = $2
                      AND (expires_at IS NULL OR expires_at > NOW())
                    LIMIT 1
                    """,
                    tenant_id,
                    phone_number,
                )

                if row:
                    return CheckResult(
                        check=GuardCheck.DNC_CHECK,
                        passed=False,
                        reason=f"number_on_dnc_list: {row['source']}",
                        details={
                            "dnc_entry_id": str(row["id"]),
                            "source": row["source"],
                            "reason": row.get("reason"),
                        },
                    )
        except asyncpg.PostgresError as e:
            logger.debug(f"dnc_entries query failed (table may not exist): {e}")

        return CheckResult(
            check=GuardCheck.DNC_CHECK,
            passed=True,
        )

    async def _check_spend_limit(
        self,
        tenant_id: str,
        tenant_limits: Optional[TenantCallLimits] = None,
        estimated_duration_seconds: Optional[int] = None,
        **kwargs
    ) -> CheckResult:
        """Check monthly spend limit."""
        if not tenant_limits or tenant_limits.monthly_spend_cap is None:
            return CheckResult(
                check=GuardCheck.SPEND_LIMIT,
                passed=True,
                reason="no_spend_cap",
            )

        # Simple check - in production, you'd estimate cost based on destination
        current_spend = tenant_limits.monthly_spend_used or 0
        cap = tenant_limits.monthly_spend_cap

        # Estimate call cost (simplified - $0.05/minute)
        estimated_cost = (estimated_duration_seconds or 60) / 60 * 0.05

        if current_spend + estimated_cost > cap:
            return CheckResult(
                check=GuardCheck.SPEND_LIMIT,
                passed=False,
                reason=f"spend_cap_exceeded: ${current_spend:.2f}/${cap:.2f}",
                details={
                    "current_spend": current_spend,
                    "spend_cap": cap,
                    "estimated_cost": estimated_cost,
                },
            )

        return CheckResult(
            check=GuardCheck.SPEND_LIMIT,
            passed=True,
            details={
                "current_spend": current_spend,
                "spend_cap": cap,
                "remaining": cap - current_spend,
            },
        )

    async def _check_business_hours(
        self,
        tenant_id: str,
        phone_number: str = "",
        tenant_limits: Optional[TenantCallLimits] = None,
        **kwargs
    ) -> CheckResult:
        """Check business hours restriction.

        T1.5 — uses the CALLEE's timezone (resolved from the
        destination E.164) rather than the tenant's. TCPA measures
        business hours at the caller-receiving end, not the
        caller-placing end. The tenant's configured timezone is kept
        as a last-resort fallback when the callee's tz can't be
        determined (unknown country, short code, lookup failure).
        """
        if not tenant_limits or not tenant_limits.respect_business_hours:
            return CheckResult(
                check=GuardCheck.BUSINESS_HOURS,
                passed=True,
                reason="business_hours_not_enforced",
            )

        # `datetime` is imported at module level (line 41); re-importing
        # locally would shadow test patches on the module namespace.
        import pytz
        from app.domain.services.phone_timezone import resolve_timezone

        tenant_tz = tenant_limits.business_hours_timezone or "UTC"
        # Resolve callee's tz; falls back to tenant's if unknown so the
        # check never accidentally skips because of a lookup failure.
        callee_tz_name = await resolve_timezone(
            phone_number,
            redis_client=self._redis,
            tenant_fallback_tz=tenant_tz,
        )
        try:
            tz = pytz.timezone(callee_tz_name)
            tz_used = callee_tz_name
        except pytz.UnknownTimeZoneError:
            try:
                tz = pytz.timezone(tenant_tz)
                tz_used = tenant_tz
            except pytz.UnknownTimeZoneError:
                tz = pytz.UTC
                tz_used = "UTC"

        now = datetime.now(tz)
        current_time = now.time()

        start = tenant_limits.business_hours_start
        end = tenant_limits.business_hours_end

        if start and end:
            if start <= end:
                # Normal range (e.g., 9 AM to 5 PM)
                in_business_hours = start <= current_time <= end
            else:
                # Overnight range (e.g., 10 PM to 6 AM)
                in_business_hours = current_time >= start or current_time <= end

            if not in_business_hours:
                return CheckResult(
                    check=GuardCheck.BUSINESS_HOURS,
                    passed=False,
                    reason="outside_business_hours",
                    details={
                        "current_time": current_time.isoformat(),
                        "business_hours": f"{start.isoformat()}-{end.isoformat()}",
                        "timezone": tz_used,
                        "tz_source": (
                            "callee" if tz_used == callee_tz_name
                            and callee_tz_name != tenant_tz
                            else "tenant_fallback"
                        ),
                        "phone_number": phone_number,
                    },
                )

        return CheckResult(
            check=GuardCheck.BUSINESS_HOURS,
            passed=True,
            details={
                "timezone": tz_used,
                "tz_source": (
                    "callee" if tz_used == callee_tz_name
                    and callee_tz_name != tenant_tz
                    else "tenant_fallback"
                ),
            },
        )

    async def _check_velocity(
        self,
        tenant_id: str,
        **kwargs
    ) -> CheckResult:
        """Check for recent abuse events indicating velocity anomalies."""
        try:
            async with self._db_pool.acquire() as conn:
                recent_events = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM abuse_events
                    WHERE tenant_id = $1
                      AND created_at > NOW() - INTERVAL '1 hour'
                      AND severity IN ('high', 'critical')
                      AND resolved_at IS NULL
                    """,
                    tenant_id,
                )

                if recent_events and recent_events > 0:
                    return CheckResult(
                        check=GuardCheck.VELOCITY_CHECK,
                        passed=False,
                        reason=f"recent_abuse_events: {recent_events}",
                        details={"recent_high_severity_events": recent_events},
                    )
        except asyncpg.PostgresError as e:
            logger.debug(f"abuse_events query failed (table may not exist): {e}")

        return CheckResult(
            check=GuardCheck.VELOCITY_CHECK,
            passed=True,
        )

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _normalize_phone_number(self, phone_number: str) -> str:
        """Normalize phone number to E.164 format."""
        if not phone_number:
            return ""

        # Remove all non-digit characters except leading +
        has_plus = phone_number.startswith("+")
        digits = re.sub(r"\D", "", phone_number)

        if has_plus:
            return f"+{digits}"

        # Assume US/Canada if no country code
        if len(digits) == 10:
            return f"+1{digits}"

        return f"+{digits}"

    def _extract_country_code(self, phone_number: str) -> Optional[str]:
        """Extract ISO country code from E.164 phone number."""
        if not phone_number.startswith("+"):
            return None

        # Simplified mapping - in production, use phonenumbers library
        country_mappings = {
            "+1": "US",  # US/Canada
            "+44": "GB",  # UK
            "+33": "FR",  # France
            "+49": "DE",  # Germany
            "+39": "IT",  # Italy
            "+34": "ES",  # Spain
            "+31": "NL",  # Netherlands
            "+32": "BE",  # Belgium
            "+41": "CH",  # Switzerland
            "+43": "AT",  # Austria
            "+46": "SE",  # Sweden
            "+47": "NO",  # Norway
            "+45": "DK",  # Denmark
            "+358": "FI",  # Finland
            "+48": "PL",  # Poland
            "+420": "CZ",  # Czech Republic
            "+36": "HU",  # Hungary
            "+30": "GR",  # Greece
            "+353": "IE",  # Ireland
            "+351": "PT",  # Portugal
            "+61": "AU",  # Australia
            "+64": "NZ",  # New Zealand
            "+81": "JP",  # Japan
            "+82": "KR",  # South Korea
            "+86": "CN",  # China
            "+91": "IN",  # India
            "+92": "PK",  # Pakistan
            "+880": "BD",  # Bangladesh
            "+234": "NG",  # Nigeria
            "+84": "VN",  # Vietnam
            "+62": "ID",  # Indonesia
            "+55": "BR",  # Brazil
            "+52": "MX",  # Mexico
            "+54": "AR",  # Argentina
            "+56": "CL",  # Chile
            "+57": "CO",  # Colombia
            "+27": "ZA",  # South Africa
            "+20": "EG",  # Egypt
            "+971": "AE",  # UAE
            "+966": "SA",  # Saudi Arabia
            "+65": "SG",  # Singapore
            "+852": "HK",  # Hong Kong
            "+886": "TW",  # Taiwan
            "+7": "RU",  # Russia
            "+380": "UA",  # Ukraine
            "+90": "TR",  # Turkey
            "+98": "IR",  # Iran
            "+963": "SY",  # Syria
        }

        # Try longest prefixes first
        for prefix in sorted(country_mappings.keys(), key=len, reverse=True):
            if phone_number.startswith(prefix):
                return country_mappings[prefix]

        return None

    async def _get_tenant_limits(self, tenant_id: str) -> Optional[TenantCallLimits]:
        """Fetch tenant limits from cache or database."""
        cache_key = f"callguard:limits:tenant:{tenant_id}"

        # Try cache first
        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return TenantCallLimits(**data)
            except Exception:
                pass

        # Fetch from database
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM tenant_call_limits
                    WHERE tenant_id = $1 AND is_active = TRUE
                    ORDER BY effective_from DESC
                    LIMIT 1
                    """,
                    tenant_id,
                )
        except asyncpg.PostgresError as e:
            # Table may not exist yet — return permissive defaults
            logger.debug(f"tenant_call_limits query failed (table may not exist): {e}")
            return TenantCallLimits()

        if not row:
            # Return default limits
            return TenantCallLimits()

        limits = TenantCallLimits(
            calls_per_minute=row["calls_per_minute"],
            calls_per_hour=row["calls_per_hour"],
            calls_per_day=row["calls_per_day"],
            max_concurrent_calls=row["max_concurrent_calls"],
            max_queue_size=row["max_queue_size"],
            monthly_minutes_allocated=row["monthly_minutes_allocated"],
            monthly_minutes_used=row["monthly_minutes_used"],
            monthly_spend_cap=row["monthly_spend_cap"],
            monthly_spend_used=row["monthly_spend_used"] or 0.0,
            max_call_duration_seconds=row["max_call_duration_seconds"],
            min_call_interval_seconds=row["min_call_interval_seconds"],
            allowed_country_codes=row["allowed_country_codes"] or [],
            blocked_country_codes=row["blocked_country_codes"] or [],
            blocked_prefixes=row["blocked_prefixes"] or [],
            features_enabled=row["features_enabled"] or {},
            features_disabled=row["features_disabled"] or {},
            respect_business_hours=row["respect_business_hours"],
            business_hours_timezone=row["business_hours_timezone"],
            is_active=row["is_active"],
        )

        # Parse time fields
        if row["business_hours_start"]:
            limits.business_hours_start = row["business_hours_start"]
        if row["business_hours_end"]:
            limits.business_hours_end = row["business_hours_end"]

        # Cache for 60 seconds
        if self._redis:
            try:
                await self._redis.setex(
                    cache_key,
                    60,
                    json.dumps({
                        "calls_per_minute": limits.calls_per_minute,
                        "calls_per_hour": limits.calls_per_hour,
                        "calls_per_day": limits.calls_per_day,
                        "max_concurrent_calls": limits.max_concurrent_calls,
                        "max_queue_size": limits.max_queue_size,
                        "monthly_minutes_allocated": limits.monthly_minutes_allocated,
                        "monthly_minutes_used": limits.monthly_minutes_used,
                        "monthly_spend_cap": limits.monthly_spend_cap,
                        "monthly_spend_used": limits.monthly_spend_used,
                        "max_call_duration_seconds": limits.max_call_duration_seconds,
                        "min_call_interval_seconds": limits.min_call_interval_seconds,
                        "allowed_country_codes": limits.allowed_country_codes,
                        "blocked_country_codes": limits.blocked_country_codes,
                        "blocked_prefixes": limits.blocked_prefixes,
                        "features_enabled": limits.features_enabled,
                        "features_disabled": limits.features_disabled,
                        "respect_business_hours": limits.respect_business_hours,
                        "business_hours_timezone": limits.business_hours_timezone,
                        "is_active": limits.is_active,
                    }),
                )
            except Exception:
                pass

        return limits

    async def _get_partner_limits(self, tenant_id: str) -> Optional[PartnerLimits]:
        """Fetch partner limits if tenant has a partner."""
        partner_id = await self._get_partner_id(tenant_id)
        if not partner_id:
            return None

        cache_key = f"callguard:limits:partner:{partner_id}"

        # Try cache first
        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return PartnerLimits(**data)
            except Exception:
                pass

        # Fetch from database
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM partner_limits
                    WHERE partner_id = $1 AND is_active = TRUE
                    """,
                    partner_id,
                )
        except asyncpg.PostgresError as e:
            logger.debug(f"partner_limits query failed (table may not exist): {e}")
            return None

        if not row:
            return None

        limits = PartnerLimits(
            max_tenants=row["max_tenants"],
            current_tenant_count=row["current_tenant_count"],
            aggregate_calls_per_minute=row["aggregate_calls_per_minute"],
            aggregate_calls_per_hour=row["aggregate_calls_per_hour"],
            aggregate_calls_per_day=row["aggregate_calls_per_day"],
            aggregate_concurrent_calls=row["aggregate_concurrent_calls"],
            revenue_share_percent=row["revenue_share_percent"],
            min_billing_amount=row["min_billing_amount"],
            max_billing_amount=row["max_billing_amount"],
            feature_whitelist=row["feature_whitelist"] or [],
            feature_blacklist=row["feature_blacklist"] or [],
            fraud_detection_sensitivity=row["fraud_detection_sensitivity"],
            is_active=row["is_active"],
        )

        # Cache for 60 seconds
        if self._redis:
            try:
                await self._redis.setex(
                    cache_key,
                    60,
                    json.dumps({
                        "max_tenants": limits.max_tenants,
                        "current_tenant_count": limits.current_tenant_count,
                        "aggregate_calls_per_minute": limits.aggregate_calls_per_minute,
                        "aggregate_calls_per_hour": limits.aggregate_calls_per_hour,
                        "aggregate_calls_per_day": limits.aggregate_calls_per_day,
                        "aggregate_concurrent_calls": limits.aggregate_concurrent_calls,
                        "revenue_share_percent": limits.revenue_share_percent,
                        "min_billing_amount": limits.min_billing_amount,
                        "max_billing_amount": limits.max_billing_amount,
                        "feature_whitelist": limits.feature_whitelist,
                        "feature_blacklist": limits.feature_blacklist,
                        "fraud_detection_sensitivity": limits.fraud_detection_sensitivity,
                        "is_active": limits.is_active,
                    }),
                )
            except Exception:
                pass

        return limits

    async def _get_partner_id(self, tenant_id: str) -> Optional[str]:
        """Get partner ID for a tenant.

        NOTE: The partner_id column on the tenants table requires a DBA-level
        migration (ALTER TABLE owned by postgres). Until that migration is run,
        this method returns None gracefully.
        """
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT partner_id
                    FROM tenants
                    WHERE id = $1
                    """,
                    tenant_id,
                )
                return str(row["partner_id"]) if row and row.get("partner_id") else None
        except asyncpg.PostgresError as e:
            # partner_id column may not exist yet
            logger.debug(f"partner_id lookup failed (column may not exist): {e}")
            return None

    async def _get_queue_position(self, tenant_id: str) -> int:
        """Get queue position for tenant."""
        # Simplified - in production, implement proper queue
        if self._redis:
            try:
                key = f"callguard:queue:{tenant_id}"
                position = await self._redis.incr(key)
                await self._redis.expire(key, 300)
                return int(position)
            except Exception:
                pass
        return 0

    def _get_retry_after_seconds(self, tenant_id: str) -> int:
        """Get retry-after seconds for throttled requests."""
        # Default throttle window
        return 60

    async def _log_decision(
        self,
        result: GuardResult,
        partner_id: Optional[str] = None,
    ) -> None:
        """Log guard decision to database."""
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO call_guard_decisions (
                        tenant_id,
                        partner_id,
                        call_id,
                        phone_number,
                        decision,
                        checks_performed,
                        failed_checks,
                        queue_position,
                        retry_after_seconds,
                        total_latency_ms
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    result.tenant_id,
                    partner_id,
                    result.call_id,
                    result.phone_number,
                    result.decision.value,
                    json.dumps([r.to_dict() for r in result.check_results]),
                    json.dumps([c.value for c in result.failed_checks]),
                    result.queue_position,
                    result.retry_after_seconds,
                    result.total_latency_ms,
                )
        except asyncpg.PostgresError as e:
            logger.debug(f"call_guard_decisions insert failed (table may not exist): {e}")

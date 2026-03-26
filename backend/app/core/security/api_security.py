"""
Unified API Rate Limiter (Day 6)

OWASP API Security Top 10 (2023) - API4: Unrestricted Resource Consumption
https://owasp.org/www-project-api-security/

Implements tiered rate limiting:
- L1 IP: Per-client IP (DDoS protection)
- L2 User: Per-user ID (abuse prevention)
- L3 Tenant: Per-tenant (resource control)
- L4 Global: System-wide (overload protection)
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any

import redis.asyncio as redis
from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)


class RateLimitTier(str, Enum):
    IP = "ip"
    USER = "user"
    TENANT = "tenant"
    GLOBAL = "global"


class RateLimitAction(str, Enum):
    ALLOW = "allow"
    THROTTLE = "throttle"
    BLOCK = "block"


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration for a rate limit tier."""
    tier: RateLimitTier
    requests: int           # Max requests
    window: int             # Window in seconds
    block_duration: int     # Block duration in seconds


# Default configurations
DEFAULT_LIMITS: Dict[RateLimitTier, RateLimitConfig] = {
    RateLimitTier.IP: RateLimitConfig(
        tier=RateLimitTier.IP,
        requests=100,      # 100 requests
        window=60,         # per minute
        block_duration=300  # 5 min block
    ),
    RateLimitTier.USER: RateLimitConfig(
        tier=RateLimitTier.USER,
        requests=1000,     # 1000 requests
        window=60,         # per minute
        block_duration=600  # 10 min block
    ),
    RateLimitTier.TENANT: RateLimitConfig(
        tier=RateLimitTier.TENANT,
        requests=10000,    # 10k requests
        window=60,         # per minute
        block_duration=900  # 15 min block
    ),
    RateLimitTier.GLOBAL: RateLimitConfig(
        tier=RateLimitTier.GLOBAL,
        requests=100000,   # 100k requests
        window=60,         # per minute
        block_duration=60   # 1 min block
    ),
}


class APIRateLimiter:
    """
    Unified Redis-backed rate limiter.

    Uses sliding window algorithm with Redis sorted sets.
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self._redis = redis_client
        self._configs = DEFAULT_LIMITS.copy()

    def _make_key(self, tier: RateLimitTier, scope: str, endpoint: Optional[str] = None) -> str:
        """Generate Redis key for rate limit counter."""
        parts = ["ratelimit", tier.value, scope]
        if endpoint:
            # Hash endpoint to keep key short
            endpoint_hash = hashlib.sha256(endpoint.encode()).hexdigest()[:16]
            parts.append(endpoint_hash)
        return ":".join(parts)

    def _block_key(self, tier: RateLimitTier, scope: str) -> str:
        """Generate Redis key for block status."""
        return f"ratelimit:block:{tier.value}:{scope}"

    async def check_rate_limit(
        self,
        tier: RateLimitTier,
        scope: str,
        endpoint: Optional[str] = None,
        increment: bool = True
    ) -> tuple[RateLimitAction, Dict[str, Any]]:
        """
        Check if request is within rate limit.

        Returns:
            Tuple of (action, metadata)
            action: ALLOW, THROTTLE, or BLOCK
            metadata: includes remaining, reset_time, etc.
        """
        if not self._redis:
            # Fail open if Redis unavailable
            return RateLimitAction.ALLOW, {"reason": "redis_unavailable"}

        config = self._configs.get(tier)
        if not config:
            return RateLimitAction.ALLOW, {"reason": "no_config"}

        # Check block status first
        block_key = self._block_key(tier, scope)
        is_blocked = await self._redis.exists(block_key)
        if is_blocked:
            ttl = await self._redis.ttl(block_key)
            return RateLimitAction.BLOCK, {
                "reason": "blocked",
                "retry_after": max(ttl, 1),
                "limit": config.requests,
                "window": config.window
            }

        # Sliding window implementation
        now = time.time()
        window_start = now - config.window
        counter_key = self._make_key(tier, scope, endpoint)

        # Remove old entries outside window
        await self._redis.zremrangebyscore(counter_key, 0, window_start)

        # Count current entries
        current_count = await self._redis.zcard(counter_key)

        # Check if over limit
        if current_count >= config.requests:
            # Set block
            await self._redis.setex(block_key, config.block_duration, "1")
            return RateLimitAction.BLOCK, {
                "reason": "limit_exceeded",
                "retry_after": config.block_duration,
                "limit": config.requests,
                "window": config.window,
                "current": current_count
            }

        if increment:
            # Add current request with unique member to prevent dedup
            member = f"{now}:{hashlib.sha256(str(now).encode()).hexdigest()[:8]}"
            await self._redis.zadd(counter_key, {member: now})
            await self._redis.expire(counter_key, config.window + 1)

        remaining = config.requests - current_count - (1 if increment else 0)

        # Determine if throttling (within 10% of limit)
        if current_count >= config.requests * 0.9:
            action = RateLimitAction.THROTTLE
        else:
            action = RateLimitAction.ALLOW

        return action, {
            "limit": config.requests,
            "remaining": max(remaining, 0),
            "window": config.window,
            "current": current_count + (1 if increment else 0)
        }

    async def check_all_tiers(
        self,
        ip: str,
        user_id: Optional[str],
        tenant_id: Optional[str],
        endpoint: Optional[str] = None
    ) -> tuple[bool, Optional[Dict], Optional[str]]:
        """
        Check all applicable rate limit tiers.

        Returns:
            (allowed, headers, error_message)
            If not allowed, error_message contains the reason
        """
        tiers_to_check = [(RateLimitTier.IP, ip)]

        if user_id:
            tiers_to_check.append((RateLimitTier.USER, user_id))

        if tenant_id:
            tiers_to_check.append((RateLimitTier.TENANT, tenant_id))

        # Check each tier
        most_restrictive = None
        headers = {
            "X-RateLimit-IP-Limit": str(self._configs[RateLimitTier.IP].requests),
        }

        for tier, scope in tiers_to_check:
            action, meta = await self.check_rate_limit(tier, scope, endpoint)

            # Build headers
            if tier == RateLimitTier.USER and user_id:
                headers["X-RateLimit-User-Limit"] = str(meta.get("limit", 0))
                headers["X-RateLimit-User-Remaining"] = str(meta.get("remaining", 0))
            elif tier == RateLimitTier.TENANT and tenant_id:
                headers["X-RateLimit-Tenant-Limit"] = str(meta.get("limit", 0))
                headers["X-RateLimit-Tenant-Remaining"] = str(meta.get("remaining", 0))

            if action == RateLimitAction.BLOCK:
                most_restrictive = (tier, meta)
                break
            elif action == RateLimitAction.THROTTLE and not most_restrictive:
                most_restrictive = (tier, meta)

        if most_restrictive and most_restrictive[0]:
            tier, meta = most_restrictive
            retry_after = meta.get("retry_after", 60)
            headers["Retry-After"] = str(retry_after)

            return False, headers, f"Rate limit exceeded ({tier.value} tier)"

        return True, headers, None

    def update_config(self, tier: RateLimitTier, requests: int, window: int, block_duration: int) -> None:
        """Update rate limit configuration for a tier."""
        self._configs[tier] = RateLimitConfig(
            tier=tier,
            requests=requests,
            window=window,
            block_duration=block_duration
        )


# Singleton instance
_rate_limiter: Optional[APIRateLimiter] = None


def get_api_rate_limiter(redis_client: Optional[redis.Redis] = None) -> APIRateLimiter:
    """Get or create API rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = APIRateLimiter(redis_client)
    return _rate_limiter


def reset_api_rate_limiter() -> None:
    """Reset rate limiter singleton (for testing)."""
    global _rate_limiter
    _rate_limiter = None


# FastAPI Dependency
async def rate_limit_dependency(request: Request) -> None:
    """
    FastAPI dependency for rate limiting.

    Usage:
        @router.get("/endpoint")
        async def endpoint(_=Depends(rate_limit_dependency)):
            ...
    """
    from app.core.container import get_container

    # Extract identifiers
    ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()

    user_id = None
    tenant_id = None

    # Try to get from request state (set by auth middleware)
    if hasattr(request.state, "user_id"):
        user_id = request.state.user_id
    if hasattr(request.state, "tenant_id"):
        tenant_id = request.state.tenant_id

    # Get limiter
    container = get_container()
    limiter = get_api_rate_limiter(
        redis_client=container.redis if container.is_initialized else None
    )

    # Check rate limits
    allowed, headers, error = await limiter.check_all_tiers(
        ip=ip,
        user_id=user_id,
        tenant_id=tenant_id,
        endpoint=request.url.path
    )

    # Store headers for middleware to add
    request.state.rate_limit_headers = headers

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error,
            headers=headers
        )

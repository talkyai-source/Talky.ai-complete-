"""
API Security Middleware (Day 6)

OWASP API Security Top 10 2023 Protection:
- API6:2023 - Unrestricted Access to Sensitive Business Flows
- API8:2023 - Security Misconfiguration
- API10:2023 - Unsafe Consumption of APIs

Provides:
- Request validation (size, content-type)
- Payload sanitization
- Security headers
- Suspicious pattern detection
"""

import logging
import re
from typing import Optional, Any

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Configuration
MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_CONTENT_TYPES = {
    "application/json",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
}

# Patterns for basic XSS detection
XSS_PATTERNS = [
    re.compile(r"<script[^>]*>[\s\S]*?</script>", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"on\w+\s*=", re.IGNORECASE),  # onclick=, onerror=, etc.
]

# Suspicious user agents
SUSPICIOUS_UAS = [
    "sqlmap",
    "nikto",
    "nmap",
    "masscan",
    "zgrab",
    "gobuster",
    "dirbuster",
]

# ---------------------------------------------------------------------------
# Monitoring-probe exemption
# ---------------------------------------------------------------------------
# WHY THIS EXISTS (incident, 2026-07-28)
# The IP tier was 100 req/min with a 300s block. /api/v1/health was 429'd 51
# times in two hours while the service was perfectly healthy. A throttled
# health endpoint is worse than no health endpoint: the 429 makes the uptime
# monitor and the load balancer mark the pod DOWN, so the limiter *manufactures*
# the outage it is reporting. The limits were retuned (600/min, 60s block) and
# probes were exempted outright.
#
# WHY THE FIRST FIX WAS NOT ENOUGH
# The original rule was `path.endswith("/health") or path.endswith("/ready")`.
# Against the routes that actually exist that exempted only /api/v1/health and
# /api/v1/healthz/ready, and silently missed /api/v1/healthz/live,
# /api/v1/healthz/deep, /api/v1/healthz/workers and /api/v1/health/detailed —
# all of them unauthenticated probes meant to be polled by monitoring. A suffix
# test is exactly the wrong shape here: it can only ever cover the literal leaf
# names someone remembered to list, so every new health sub-route re-opens the
# same hole.
#
# THE RULE
# Match on the FIRST path segment after an optional /api/v{N} version prefix.
# A path is a monitoring probe iff it is rooted at a probe namespace
# (/healthz/..., /health/...). Everything under such a root is covered
# automatically, so adding GET /healthz/whatever tomorrow needs no change here.
#
# WHY THIS DOES NOT OVER-EXEMPT
# Anchoring at the root segment (rather than a substring or suffix test) is what
# keeps application routes limited. Concretely, these all keep their rate
# limiting:
#   /api/v1/calls/live                  — tenant call listing (a suffix rule on
#                                         "/live" would have exempted it, and it
#                                         is the single most-polled DB read in
#                                         the product)
#   /api/v1/admin/calls/live
#   /api/v1/admin/health/{detailed,workers,queues,database,incidents,alerts}
#                                       — admin-authenticated dashboard reads
#                                         that run real DB queries and fan out
#                                         to provider health checks. They are
#                                         rooted at "admin", not at a probe
#                                         namespace, so they stay limited: they
#                                         are not what a load balancer polls,
#                                         and they are expensive.
#   /api/v1/admin/system-health         — a substring match on "health" would
#                                         have exempted this. The root-segment
#                                         rule does not.
#   /api/v1/campaigns/minutes/status, /api/v1/connectors/status,
#   /api/v1/auth/mfa/status, /api/v1/telephony/sip/quotas/status, …
#                                       — "status" is a normal application verb
#                                         in this codebase (~10 routes), which
#                                         is why it is deliberately NOT a probe
#                                         namespace.
#
# WHY "live"/"status"/"metrics" ARE NOT ROOTS
#   live    — /api/v1/calls/live proves the word is application vocabulary here.
#             "livez" is unambiguous, "live" is not, so only the former is a
#             root. A bare top-level /live probe does not exist today.
#   status  — see above.
#   metrics — the Prometheus scrape endpoint (GET /metrics, app/api/operational.py)
#             is token-gated in constant time and fails CLOSED when
#             TELEPHONY_METRICS_TOKEN is unset, so it must never be widened by
#             namespace. It is already skipped by _is_exempt_path() as an exact
#             path, and an unauthenticated flood is rejected at the header check
#             before any DB work. /api/v1/telephony/sip/runtime/metrics/activation
#             is an authenticated admin report, not a scrape target, and stays
#             limited. A real scrape at 15s intervals is 4 req/min — three orders
#             of magnitude under the 600/min IP tier — so metrics endpoints have
#             no operational need for an exemption in the first place.
#
# COST OF THE EXEMPTION
# Exempting a path removes abuse protection from it, so the exempt set is kept
# to handlers that are cheap and bounded. /healthz/live, /health and
# /health/detailed are pure in-memory. /healthz/ready reads a process-local
# snapshot. /healthz/deep and /healthz/workers touch Postgres/Redis, but only
# with SELECT 1 and a GET, each capped at 500ms (see health.py
# _DEEP_PING_TIMEOUT_S) so neither can be turned into a slow-query amplifier.
# The exemption is additionally restricted to safe methods below: every probe
# is a GET, so a POST flood aimed at a health path stays rate limited even
# though it would only ever earn a 405.
_API_VERSION_PREFIX_RE = re.compile(r"^/api/v\d+(?=/|$)")

# First path segment (after the optional version prefix) that marks a genuine
# liveness/readiness/monitoring namespace. Add a name here ONLY if the whole
# subtree under it is a cheap, unauthenticated probe.
MONITORING_ROOT_SEGMENTS = frozenset(
    {
        "health",      # /api/v1/health, /api/v1/health/detailed
        "healthz",     # /api/v1/healthz/{ready,live,deep,workers}
        "healthcheck",
        "ready",
        "readyz",
        "livez",
        "ping",
    }
)

# Methods a monitoring probe may use. Anything else stays rate limited.
MONITORING_METHODS = frozenset({"GET", "HEAD"})


def is_monitoring_path(path: str) -> bool:
    """True if `path` is rooted at a monitoring/probe namespace.

    Matches the first path segment after an optional ``/api/v{N}`` prefix, so
    the whole subtree of a probe namespace is covered (present and future) while
    application routes that merely *contain* a probe-ish word are not.

        /api/v1/healthz/workers   -> True
        /api/v1/health/detailed   -> True
        /health                   -> True
        /api/v1/calls/live        -> False
        /api/v1/admin/health/workers -> False
        /api/v1/admin/system-health  -> False
    """
    stripped = path.rstrip("/") or "/"
    stripped = _API_VERSION_PREFIX_RE.sub("", stripped, count=1)
    if not stripped.startswith("/"):
        # Path was exactly "/api/v1" (or similar) — not a probe.
        return False
    segments = stripped.split("/")
    root = segments[1].lower() if len(segments) > 1 else ""
    return root in MONITORING_ROOT_SEGMENTS


class APISecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware for API security hardening.

    Runs early in the middleware stack to validate requests.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip for public/static paths
        if self._is_exempt_path(request.url.path):
            return await call_next(request)

        # 1. Request size check (Content-Length)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > MAX_REQUEST_BODY_SIZE:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": f"Request body too large (max {MAX_REQUEST_BODY_SIZE} bytes)"}
                    )
            except ValueError:
                pass

        # 2. Content-Type validation and Payload Sanitization
        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if request.method in ("POST", "PUT", "PATCH"):
            # Content-Type validation
            if not self._is_webhook_path(request.url.path):
                if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                    return JSONResponse(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        content={"detail": f"Content-Type '{content_type}' not supported"}
                    )

            # Payload Sanitization (Day 6) — DISABLED.
            #
            # This block originally read request.body() then re-injected a
            # sanitized version via a custom receive() callable. That pattern
            # is fundamentally incompatible with Starlette's BaseHTTPMiddleware
            # streaming response handling: once the response starts streaming,
            # Starlette's listen_for_disconnect() polls receive() expecting
            # http.disconnect frames, but the wrapped receive() we left behind
            # caused "RuntimeError: Unexpected message received: http.request"
            # on every streamed JSON response and broke the SIP audio loop,
            # the campaign-create POST, and any other endpoint that returns a
            # streamed body.
            #
            # XSS protection is the frontend's responsibility (React escapes
            # by default, the API never renders raw HTML), and inputs that
            # need server-side sanitization should be validated per-endpoint
            # with Pydantic. Removing this generic interceptor unblocks the
            # whole call pipeline.

        # 3. User-Agent validation (basic bot detection)
        user_agent = request.headers.get("user-agent", "").lower()
        if self._is_suspicious_ua(user_agent):
            logger.warning(f"Suspicious User-Agent blocked: {user_agent[:100]}")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access denied"}
            )

        # 4. Global Rate Limiting (Day 6)
        # Attempt to apply rate limiting here if not already handled by dependencies
        # This provides a catch-all safety net.
        # Skip CORS preflight: OPTIONS isn't real traffic and rate-limiting it
        # makes the browser fail every actual request that follows. Skip
        # loopback traffic in ALL environments — 127.0.0.1 can only originate
        # from same-host processes (C++ voice gateway POSTing audio frames,
        # workers, healthchecks), and those are by definition trusted
        # internal services. In production this matters even more: without
        # the exemption the gateway's per-frame audio POSTs (~50/sec/call)
        # trip the limiter within seconds and Asterisk hangs up the call.
        _ip_for_skip = request.client.host if request.client else ""
        _is_loopback = _ip_for_skip in {"127.0.0.1", "::1", "localhost"}
        # Trusted first-party services (the dialer worker, the C++ voice gateway)
        # authenticate with a valid internal service token. They are exempt from
        # the shared IP-tier bucket for the same reason loopback is: a legitimate
        # campaign's origination burst — and the gateway's per-frame audio POSTs
        # — would otherwise exhaust the single IP tier and 429 every call (the
        # dialer's "No call_id returned" terminal failures). Call volume for this
        # path is already governed by the call-guard limiter and the concurrency
        # cap. The check is a constant-time shared-secret compare; an unset
        # INTERNAL_SERVICE_TOKEN disables the exemption (fail-safe).
        _is_internal = False
        try:
            from app.core.security.internal_auth import _valid_internal_token
            _is_internal = _valid_internal_token(request)
        except Exception:
            _is_internal = False
        # Liveness/readiness probes are never rate-limited. Throttling a health
        # endpoint is self-defeating: the 429 makes monitoring (and any load
        # balancer) mark a perfectly healthy service as DOWN, turning a traffic
        # spike into a reported outage. Observed 2026-07-28 — /api/v1/health was
        # 429'd 51 times in two hours while the service was fine.
        #
        # The rule is root-segment based, not suffix based — see the long note
        # on is_monitoring_path() above for the enumerated exempt set and why
        # application routes (/calls/live, /admin/health/*, */status) are NOT
        # in it. Suffix matching is what left /healthz/{live,deep,workers} and
        # /health/detailed still throttleable after the first fix.
        _path = request.url.path
        _is_health = (
            request.method in MONITORING_METHODS and is_monitoring_path(_path)
        )

        if request.method == "OPTIONS" or _is_loopback or _is_internal or _is_health:
            return await call_next(request)

        try:
            from app.core.security.api_security import get_api_rate_limiter
            from app.core.container import get_container

            container = get_container()
            if container.is_initialized and container.redis_enabled:
                limiter = get_api_rate_limiter(container.redis)

                # We can't easily get user_id/tenant_id yet as auth middleware might run after
                # But we can at least check IP tier early
                ip = request.client.host if request.client else "unknown"
                forwarded = request.headers.get("X-Forwarded-For")
                if forwarded:
                    ip = forwarded.split(",")[0].strip()
                
                allowed, headers, error = await limiter.check_all_tiers(
                    ip=ip,
                    user_id=None, # User/Tenant checked later in dependencies
                    tenant_id=None,
                    endpoint=request.url.path
                )
                
                if not allowed:
                    return JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={"detail": error},
                        headers=headers
                    )
                
                # Store headers to be added to response later
                request.state.rate_limit_headers = headers
        except Exception as e:
            logger.error(f"Global rate limiting error: {e}")

        # Process request
        response = await call_next(request)

        # 4. Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Add rate limit headers if present
        if hasattr(request.state, "rate_limit_headers"):
            for header, value in request.state.rate_limit_headers.items():
                response.headers[header] = str(value)

        # Add idempotency replay header if applicable
        if hasattr(request.state, "idempotency_replay"):
            response.headers["Idempotent-Replay"] = "true"

        return response

    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from security checks."""
        exempt = {
            "/",
            "/health",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
        }
        if path in exempt:
            return True
        # Auth endpoints handle their own security; skip body-consuming middleware
        if path.startswith("/api/v1/auth/"):
            return True
        return False

    def _is_webhook_path(self, path: str) -> bool:
        """Check if path is a webhook endpoint."""
        return "/webhook" in path.lower()

    def _is_suspicious_ua(self, ua: str) -> bool:
        """Check if user agent is suspicious."""
        for pattern in SUSPICIOUS_UAS:
            if pattern in ua:
                return True
        return False


def sanitize_json_value(value: Any) -> Any:
    """
    Recursively sanitize JSON values to prevent XSS.

    Basic sanitization - removes script tags and event handlers.
    """
    if isinstance(value, str):
        for pattern in XSS_PATTERNS:
            value = pattern.sub("[removed]", value)
        return value
    elif isinstance(value, dict):
        return {k: sanitize_json_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [sanitize_json_value(item) for item in value]
    return value

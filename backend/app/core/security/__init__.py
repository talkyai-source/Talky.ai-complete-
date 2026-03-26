"""
Security package for Talky.ai backend.

Provides:
- password  : Argon2id hashing + bcrypt backward compatibility
- sessions  : DB-backed server-side session management
- lockout   : Per-account progressive login lockout (OWASP)
- api_security   : Day 6 - Tiered rate limiting
- webhook_verification : Day 6 - HMAC-SHA256 webhook verification
- idempotency    : Day 6 - API-wide idempotency support

All sub-modules follow OWASP Cheat Sheet Series guidance:
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
  https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
  https://owasp.org/www-project-api-security/
"""

from app.core.security.lockout import (
    LOCKOUT_THRESHOLDS,
    OBSERVATION_WINDOW_MINUTES,
    check_account_locked,
    get_consecutive_failures,
    record_login_attempt,
)
from app.core.security.password import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_IDLE_TIMEOUT_MINUTES,
    SESSION_LIFETIME_HOURS,
    create_session,
    generate_session_token,
    hash_session_token,
    revoke_all_user_sessions,
    revoke_session_by_token,
    validate_session,
)
from app.core.security.api_security import (
    APIRateLimiter,
    RateLimitTier,
    RateLimitAction,
    get_api_rate_limiter,
    rate_limit_dependency,
)
from app.core.security.webhook_verification import (
    WebhookVerificationError,
    generate_webhook_secret,
    verify_signature,
    verify_webhook_request,
    create_webhook_signature_headers,
    require_webhook_signature,
    WebhookSecretManager,
)
from app.core.security.idempotency import (
    IdempotencyManager,
    get_idempotency_manager,
    idempotency_dependency,
    store_idempotent_response,
    release_idempotency_lock,
    IDEMPOTENCY_KEY_HEADER,
)

__all__ = [
    # password
    "hash_password",
    "verify_password",
    "needs_rehash",
    "validate_password_strength",
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    # sessions
    "create_session",
    "validate_session",
    "revoke_session_by_token",
    "revoke_all_user_sessions",
    "generate_session_token",
    "hash_session_token",
    "SESSION_COOKIE_NAME",
    "SESSION_LIFETIME_HOURS",
    "SESSION_IDLE_TIMEOUT_MINUTES",
    # lockout
    "check_account_locked",
    "record_login_attempt",
    "get_consecutive_failures",
    "LOCKOUT_THRESHOLDS",
    "OBSERVATION_WINDOW_MINUTES",
    # Day 6: API Security - Rate Limiting
    "APIRateLimiter",
    "RateLimitTier",
    "RateLimitAction",
    "get_api_rate_limiter",
    "rate_limit_dependency",
    # Day 6: Webhook Verification
    "WebhookVerificationError",
    "generate_webhook_secret",
    "verify_signature",
    "verify_webhook_request",
    "create_webhook_signature_headers",
    "require_webhook_signature",
    "WebhookSecretManager",
    # Day 6: Idempotency
    "IdempotencyManager",
    "get_idempotency_manager",
    "idempotency_dependency",
    "store_idempotent_response",
    "release_idempotency_lock",
    "IDEMPOTENCY_KEY_HEADER",
]

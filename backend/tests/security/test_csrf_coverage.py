"""CSRF wiring and explicit route allow-list guard (QA-SEC-061)."""
from __future__ import annotations

import pathlib

from app.core.security.csrf import _EXEMPT_POST_PATHS, _EXEMPT_POST_PATTERNS


def test_csrf_middleware_is_mounted_in_app_bootstrap():
    src = (pathlib.Path(__file__).resolve().parents[2] / "app/core/app_bootstrap.py").read_text(encoding="utf-8")
    assert "from app.core.security.csrf import CSRFMiddleware" in src
    assert "add_middleware(CSRFMiddleware" in src


def test_csrf_exempt_list_matches_only_authenticated_callbacks_and_login_steps():
    # Auth steps establish sessions or consume single-use proofs. Passkey
    # registration/deletion and all other cookie mutations remain protected.
    assert _EXEMPT_POST_PATHS == {
        "/api/v1/auth/login",
        "/api/v1/auth/signup/start",
        "/api/v1/auth/signup/verify-code",
        "/api/v1/auth/signup/complete",
        "/api/v1/auth/register",
        "/api/v1/auth/refresh",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/auth/passkey-check",
        "/api/v1/auth/passkeys/login/begin",
        "/api/v1/auth/passkeys/login/complete",
        "/api/v1/billing/webhooks",  # Stripe raw-body signature
        "/api/v1/webhooks/call/goal-achieved",  # tenant HMAC + timestamp
        "/api/v1/webhooks/call/mark-spam",
        "/api/v1/webhooks/secure/call/goal-achieved",
        "/api/v1/webhooks/secure/call/mark-spam",
        "/api/v1/webhooks/secure/idempotent-example",
    }
    # Dynamic callbacks match the exact route shape, not arbitrary children.
    assert {pattern.pattern for pattern in _EXEMPT_POST_PATTERNS} == {
        r"/api/v1/sip/telephony/audio/[^/]+",  # existing internal audio path
        r"/api/v1/connectors/salesforce/callback-requests/[^/]+/[^/]+",  # URL secret
        r"/api/v1/connectors/salesforce/outbound-message/[^/]+/[^/]+",  # URL secret
    }

"""Shared Google API error classification for connectors.

Generalises the Gmail connector's response mapping so Drive/Calendar raise the
same structured ``ConnectorProviderError`` instead of stringly ``ValueError``s.
Tools then distinguish a revoked credential from a disabled API, missing
permission, rate limiting, or a transient outage — without parsing Google's
error prose (whose exact wording Google does not guarantee).
"""
from __future__ import annotations

from typing import Any

import httpx

from app.infrastructure.connectors.base import ConnectorProviderError

_GOOGLE_RATE_LIMIT_REASONS = {
    "ratelimitexceeded",
    "userratelimitexceeded",
    "quotaexceeded",
    "dailylimitexceeded",
}


def google_api_error_from_response(
    provider: str,
    response: httpx.Response,
    operation: str,
    *,
    token_endpoint: bool = False,
) -> ConnectorProviderError:
    """Convert a Google API response into a stable, categorised error."""
    payload: Any = None
    try:
        payload = response.json()
    except Exception:
        payload = None

    provider_code = ""
    provider_message = ""
    if isinstance(payload, dict):
        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            provider_code = str(raw_error.get("status") or raw_error.get("code") or "")
            provider_message = str(raw_error.get("message") or "")
            details = raw_error.get("errors") or []
            if details and isinstance(details[0], dict):
                provider_code = str(details[0].get("reason") or provider_code)
        elif raw_error:
            provider_code = str(raw_error)
            provider_message = str(payload.get("error_description") or "")

    status = response.status_code
    normalized_code = provider_code.lower()
    # Token-endpoint client failures describe this deployment's OAuth client,
    # not the user's grant — never expire a healthy user connector for them.
    if token_endpoint and normalized_code in {
        "invalid_client",
        "unauthorized_client",
        "redirect_uri_mismatch",
    }:
        category = "configuration"
    elif status == 401 or (token_endpoint and normalized_code == "invalid_grant"):
        category = "authentication"
    elif status == 403 and normalized_code in _GOOGLE_RATE_LIMIT_REASONS:
        category = "rate_limit"
    elif status == 403:
        # Includes accessNotConfigured — the API is disabled in the Google
        # Cloud project. That is a permission problem, NOT "not connected".
        category = "permission"
    elif status == 404:
        category = "not_found"
    elif status == 429:
        category = "rate_limit"
    elif status >= 500:
        category = "temporary"
    else:
        category = "unknown"

    safe_message = provider_message or provider_code or f"Google returned HTTP {status}"
    return ConnectorProviderError(
        provider=provider,
        operation=operation,
        category=category,
        message=safe_message,
        status_code=status,
        retry_after=response.headers.get("Retry-After"),
    )

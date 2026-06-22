"""Webhook signature validation for telephony providers (Twilio, Vonage).

Policy — env-gated, fail-soft:
  * When the provider's verification secret is NOT configured, validation is
    SKIPPED (logged once) so existing deployments keep working unchanged.
  * When the secret IS configured, a missing / invalid signature is REJECTED
    (fail-closed). Setting the secret is what turns enforcement on.

This means wiring these validators in is safe by default (no behaviour change)
and an operator opts into enforcement by setting the env secret.

Twilio: HMAC-SHA1 over the full request URL + sorted POST params, base64,
        in the ``X-Twilio-Signature`` header. Uses the official RequestValidator
        when the SDK is present; falls back to a pure-stdlib implementation.
        Secret: the account Auth Token (``TWILIO_AUTH_TOKEN``).
Vonage: signed webhooks carry a JWT (HS256) signed with the signature secret in
        the ``Authorization: Bearer <jwt>`` header. Secret: ``VONAGE_SIGNATURE_SECRET``.
        Uses PyJWT when present; falls back to a stdlib HS256 check.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

# One warning per process per provider when running unverified — avoids log spam.
_warned: set[str] = set()


def _warn_once(provider: str, env_var: str) -> None:
    if provider not in _warned:
        _warned.add(provider)
        logger.warning(
            "%s_webhook_unverified — %s not set; skipping signature validation. "
            "Set %s to enforce.", provider, env_var, env_var,
        )


# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------

def verify_twilio_signature(
    *, url: str, params: Mapping[str, str], signature: Optional[str]
) -> bool:
    """Validate a Twilio webhook request.

    ``url`` must be the EXACT public URL Twilio used (scheme + host + path),
    ``params`` the POST form fields, ``signature`` the ``X-Twilio-Signature``
    header. Returns True (allow) when ``TWILIO_AUTH_TOKEN`` is unset.
    """
    token = os.getenv("TWILIO_AUTH_TOKEN") or None
    if not token:
        _warn_once("twilio", "TWILIO_AUTH_TOKEN")
        return True
    if not signature:
        return False
    try:
        from twilio.request_validator import RequestValidator  # type: ignore
        return bool(RequestValidator(token).validate(url, dict(params), signature))
    except ImportError:
        return _twilio_signature_stdlib(token, url, params, signature)


def _twilio_signature_stdlib(
    token: str, url: str, params: Mapping[str, str], signature: str
) -> bool:
    """Twilio's documented scheme without the SDK: append each POST param
    (sorted by key) as key+value to the URL, HMAC-SHA1 with the auth token,
    base64-encode, constant-time compare."""
    data = url
    for key in sorted(params.keys()):
        data += key + str(params[key])
    digest = hmac.new(token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Vonage
# ---------------------------------------------------------------------------

def verify_vonage_signature(*, authorization: Optional[str]) -> bool:
    """Validate a Vonage signed webhook (JWT in the Authorization header).

    Returns True (allow) when ``VONAGE_SIGNATURE_SECRET`` is unset.
    """
    secret = os.getenv("VONAGE_SIGNATURE_SECRET") or None
    if not secret:
        _warn_once("vonage", "VONAGE_SIGNATURE_SECRET")
        return True
    token = _bearer(authorization)
    if not token:
        return False
    try:
        import jwt  # type: ignore
        jwt.decode(token, secret, algorithms=["HS256"])
        return True
    except ImportError:
        return _jwt_hs256_stdlib(token, secret)
    except Exception as exc:  # invalid signature / expired / malformed
        logger.warning("vonage_webhook_signature_invalid: %s", exc)
        return False


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return authorization.strip() or None


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _jwt_hs256_stdlib(token: str, secret: str) -> bool:
    """Minimal HS256 JWT verification (signature + exp) without PyJWT."""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        return False
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        provided = _b64url_decode(sig_b64)
    except Exception:
        return False
    if not hmac.compare_digest(expected, provided):
        return False
    # Honour exp when present.
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        exp = payload.get("exp")
        if exp is not None and time.time() > float(exp):
            return False
    except Exception:
        pass
    return True

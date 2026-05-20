"""
Centralized JWT encoding/validation helpers.

Security baseline follows RFC 8725 guidance:
- fixed algorithm allow-list
- explicit header algorithm match
- required registered claims
- optional issuer/audience validation
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt

from app.core.config import get_settings


ALLOWED_HMAC_ALGORITHMS = {"HS256", "HS384", "HS512"}
REQUIRED_CLAIMS = ("sub", "iat", "exp")

ACCESS_TOKEN_TTL_MINUTES = 15


class JWTValidationError(Exception):
    def __init__(self, detail: str, status_code: int = 401):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _require_secret() -> str:
    secret = get_settings().effective_jwt_secret
    if secret:
        return secret
    raise JWTValidationError("Server authentication is not configured", status_code=503)


def _resolve_algorithm() -> str:
    algorithm = (get_settings().jwt_algorithm or "").strip().upper()
    if algorithm not in ALLOWED_HMAC_ALGORITHMS:
        raise JWTValidationError(
            f"Server authentication algorithm is not supported: {algorithm}",
            status_code=503,
        )
    return algorithm


def encode_access_token(
    *,
    user_id: str,
    email: str,
    role: str,
    tenant_id: Optional[str],
    session_id: Optional[str] = None,
    ttl: Optional[timedelta] = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    effective_ttl = ttl if ttl is not None else timedelta(hours=settings.jwt_expiry_hours)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "tenant_id": tenant_id,
        "iat": now,
        "nbf": now,
        "exp": now + effective_ttl,
    }
    if settings.jwt_issuer:
        payload["iss"] = settings.jwt_issuer
    if settings.jwt_audience:
        payload["aud"] = settings.jwt_audience
    if session_id:
        payload["sid"] = session_id

    return jwt.encode(payload, _require_secret(), algorithm=_resolve_algorithm())


def decode_and_validate_token(token: str) -> Dict[str, Any]:
    """
    Verify a JWT and return its payload.

    AH-Phase-C: supports graceful signing-key rotation. When the
    operator rotates `JWT_SECRET`, the previous value is moved into
    `JWT_SECRET_PREVIOUS`. This verifier tries the current secret
    first; on a signature-only failure (other validation errors
    propagate immediately so an expired or malformed token doesn't get
    a second chance), it retries with the previous secret. Tokens
    signed under the old key keep working until they expire naturally
    — 15min for access, 7d for refresh — at which point operators can
    clear `JWT_SECRET_PREVIOUS`.
    """
    settings = get_settings()
    secret = _require_secret()
    algorithm = _resolve_algorithm()

    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise JWTValidationError("Invalid authentication token") from exc

    header_alg = str(unverified_header.get("alg", "")).upper()
    if header_alg != algorithm:
        raise JWTValidationError("Invalid authentication token")

    header_typ = unverified_header.get("typ")
    if header_typ is not None and str(header_typ).upper() != "JWT":
        raise JWTValidationError("Invalid authentication token")

    options = {
        "require": list(REQUIRED_CLAIMS),
        "verify_signature": True,
        "verify_exp": True,
        "verify_iat": True,
        "verify_nbf": True,
        "verify_aud": bool(settings.jwt_audience),
        "verify_iss": bool(settings.jwt_issuer),
    }
    base_kwargs: Dict[str, Any] = {
        "algorithms": [algorithm],
        "options": options,
        "leeway": max(0, int(settings.jwt_leeway_seconds)),
    }
    if settings.jwt_issuer:
        base_kwargs["issuer"] = settings.jwt_issuer
    if settings.jwt_audience:
        base_kwargs["audience"] = settings.jwt_audience

    # Primary verification path — current signing secret.
    try:
        payload = jwt.decode(token, key=secret, **base_kwargs)
    except jwt.ExpiredSignatureError as exc:
        # Expired tokens never get a second chance. The previous secret
        # would not save them.
        raise JWTValidationError("Token has expired") from exc
    except jwt.InvalidSignatureError:
        # Signature didn't verify under the current key. If a previous
        # key is configured, retry with it — this is the rotation
        # graceful-handoff window. Any other InvalidTokenError sub-class
        # (missing claim, wrong audience, etc.) skips the fallback.
        previous = settings.effective_jwt_secret_previous
        if not previous:
            raise JWTValidationError("Invalid or expired token")
        try:
            payload = jwt.decode(token, key=previous, **base_kwargs)
        except jwt.ExpiredSignatureError as exc:
            raise JWTValidationError("Token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise JWTValidationError("Invalid or expired token") from exc
    except jwt.InvalidTokenError as exc:
        raise JWTValidationError("Invalid or expired token") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise JWTValidationError("Invalid token: missing subject")
    return payload

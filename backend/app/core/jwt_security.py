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
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "tenant_id": tenant_id,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(hours=settings.jwt_expiry_hours),
    }
    if settings.jwt_issuer:
        payload["iss"] = settings.jwt_issuer
    if settings.jwt_audience:
        payload["aud"] = settings.jwt_audience
    if session_id:
        payload["sid"] = session_id

    return jwt.encode(payload, _require_secret(), algorithm=_resolve_algorithm())


def decode_and_validate_token(token: str) -> Dict[str, Any]:
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
    decode_kwargs: Dict[str, Any] = {
        "key": secret,
        "algorithms": [algorithm],
        "options": options,
        "leeway": max(0, int(settings.jwt_leeway_seconds)),
    }
    if settings.jwt_issuer:
        decode_kwargs["issuer"] = settings.jwt_issuer
    if settings.jwt_audience:
        decode_kwargs["audience"] = settings.jwt_audience

    try:
        payload = jwt.decode(token, **decode_kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise JWTValidationError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise JWTValidationError("Invalid or expired token") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise JWTValidationError("Invalid token: missing subject")
    return payload

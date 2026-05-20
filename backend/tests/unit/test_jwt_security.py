from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import get_settings
from app.core.jwt_security import (
    JWTValidationError,
    decode_and_validate_token,
    encode_access_token,
)


@pytest.fixture(autouse=True)
def _jwt_env(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET",
        "unit-test-secret-with-minimum-length-48-bytes-0001",
    )
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ISSUER", "talky-backend")
    monkeypatch.setenv("JWT_AUDIENCE", "talky-client")
    monkeypatch.setenv("JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("JWT_LEEWAY_SECONDS", "5")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_encode_and_decode_roundtrip():
    token = encode_access_token(
        user_id="user-1",
        email="user@example.com",
        role="admin",
        tenant_id="tenant-1",
    )
    payload = decode_and_validate_token(token)
    assert payload["sub"] == "user-1"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["iss"] == "talky-backend"
    assert payload["aud"] == "talky-client"


def test_rejects_algorithm_header_mismatch():
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user-1",
        "email": "user@example.com",
        "role": "admin",
        "tenant_id": "tenant-1",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=10),
        "iss": "talky-backend",
        "aud": "talky-client",
    }
    token = jwt.encode(
        payload,
        "unit-test-secret-with-minimum-length-48-bytes-0001",
        algorithm="HS384",
    )

    with pytest.raises(JWTValidationError, match="Invalid authentication token"):
        decode_and_validate_token(token)


def test_rejects_missing_required_subject_claim():
    now = datetime.now(timezone.utc)
    payload = {
        "email": "user@example.com",
        "role": "admin",
        "tenant_id": "tenant-1",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=10),
        "iss": "talky-backend",
        "aud": "talky-client",
    }
    token = jwt.encode(
        payload,
        "unit-test-secret-with-minimum-length-48-bytes-0001",
        algorithm="HS256",
    )

    with pytest.raises(JWTValidationError):
        decode_and_validate_token(token)


# ---- AH-Phase-C: graceful signing-key rotation ----------------------------

PREV_SECRET = "unit-test-previous-secret-with-minimum-length-48-bytes"
CURR_SECRET = "unit-test-current-secret-with-minimum-length-48-bytes-"


def _sign(secret: str, *, sub: str = "user-1", expired: bool = False) -> str:
    now = datetime.now(timezone.utc)
    exp = now - timedelta(minutes=10) if expired else now + timedelta(minutes=10)
    return jwt.encode(
        {
            "sub": sub,
            "email": "user@example.com",
            "role": "admin",
            "tenant_id": "tenant-1",
            "iat": now - timedelta(minutes=5),
            "nbf": now - timedelta(minutes=5),
            "exp": exp,
            "iss": "talky-backend",
            "aud": "talky-client",
        },
        secret,
        algorithm="HS256",
    )


def test_rotation_token_signed_with_previous_key_verifies(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", CURR_SECRET)
    monkeypatch.setenv("JWT_SECRET_PREVIOUS", PREV_SECRET)
    get_settings.cache_clear()

    token = _sign(PREV_SECRET)
    payload = decode_and_validate_token(token)
    assert payload["sub"] == "user-1"


def test_rotation_token_signed_with_current_key_still_works(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", CURR_SECRET)
    monkeypatch.setenv("JWT_SECRET_PREVIOUS", PREV_SECRET)
    get_settings.cache_clear()

    token = _sign(CURR_SECRET)
    payload = decode_and_validate_token(token)
    assert payload["sub"] == "user-1"


def test_rotation_expired_token_signed_with_previous_key_rejected(monkeypatch):
    # An expired token must NOT get a second chance via the previous
    # key — expiry trumps signing-key rotation.
    monkeypatch.setenv("JWT_SECRET", CURR_SECRET)
    monkeypatch.setenv("JWT_SECRET_PREVIOUS", PREV_SECRET)
    monkeypatch.setenv("JWT_LEEWAY_SECONDS", "0")
    get_settings.cache_clear()

    expired_token = _sign(PREV_SECRET, expired=True)
    with pytest.raises(JWTValidationError, match="expired"):
        decode_and_validate_token(expired_token)


def test_rotation_token_signed_with_unknown_key_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", CURR_SECRET)
    monkeypatch.setenv("JWT_SECRET_PREVIOUS", PREV_SECRET)
    get_settings.cache_clear()

    unknown_token = _sign("third-key-that-was-never-configured-anywhere-0001")
    with pytest.raises(JWTValidationError, match="Invalid or expired token"):
        decode_and_validate_token(unknown_token)


def test_no_previous_key_means_previous_signed_tokens_rejected(monkeypatch):
    # When JWT_SECRET_PREVIOUS is unset (steady state after a completed
    # rotation), tokens signed with the OLD key must fail.
    monkeypatch.setenv("JWT_SECRET", CURR_SECRET)
    monkeypatch.delenv("JWT_SECRET_PREVIOUS", raising=False)
    get_settings.cache_clear()

    stale_token = _sign(PREV_SECRET)
    with pytest.raises(JWTValidationError, match="Invalid or expired token"):
        decode_and_validate_token(stale_token)

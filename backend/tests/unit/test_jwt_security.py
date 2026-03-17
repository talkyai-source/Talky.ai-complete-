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

"""Tests for telephony webhook signature validation (Twilio + Vonage)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from app.core.security.telephony_webhook_auth import (
    verify_twilio_signature,
    verify_vonage_signature,
)


def _twilio_sig(token: str, url: str, params: dict) -> str:
    data = url
    for key in sorted(params.keys()):
        data += key + str(params[key])
    digest = hmac.new(token.encode(), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _hs256_jwt(secret: str, payload: dict | None = None) -> str:
    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(payload or {"iss": "Vonage"}).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    return f"{header}.{body}.{sig}"


# ── Twilio ────────────────────────────────────────────────────────────────

def test_twilio_accepts_valid_signature(monkeypatch):
    token = "test-auth-token"
    url = "https://host.example/api/v1/twilio/answer"
    params = {"CallSid": "CA123", "From": "+15551234567", "To": "+15557654321"}
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)

    good = _twilio_sig(token, url, params)
    assert verify_twilio_signature(url=url, params=params, signature=good) is True


def test_twilio_rejects_bad_and_missing_signature(monkeypatch):
    token = "test-auth-token"
    url = "https://host.example/api/v1/twilio/answer"
    params = {"CallSid": "CA123"}
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)

    assert verify_twilio_signature(url=url, params=params, signature="deadbeef") is False
    assert verify_twilio_signature(url=url, params=params, signature=None) is False
    # Tampered params must not match a signature computed over the originals.
    good = _twilio_sig(token, url, params)
    assert verify_twilio_signature(
        url=url, params={"CallSid": "CA999"}, signature=good
    ) is False


def test_twilio_fail_open_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    assert verify_twilio_signature(url="https://h/x", params={}, signature=None) is True


# ── Vonage ────────────────────────────────────────────────────────────────

def test_vonage_accepts_valid_jwt(monkeypatch):
    secret = "vonage-signature-secret"
    monkeypatch.setenv("VONAGE_SIGNATURE_SECRET", secret)
    token = _hs256_jwt(secret)
    assert verify_vonage_signature(authorization=f"Bearer {token}") is True
    # Raw token without the Bearer prefix is also accepted.
    assert verify_vonage_signature(authorization=token) is True


def test_vonage_rejects_wrong_secret_and_missing(monkeypatch):
    monkeypatch.setenv("VONAGE_SIGNATURE_SECRET", "the-right-secret")
    forged = _hs256_jwt("the-wrong-secret")
    assert verify_vonage_signature(authorization=f"Bearer {forged}") is False
    assert verify_vonage_signature(authorization=None) is False
    assert verify_vonage_signature(authorization="Bearer not.a.jwt") is False


def test_vonage_fail_open_when_unconfigured(monkeypatch):
    monkeypatch.delenv("VONAGE_SIGNATURE_SECRET", raising=False)
    assert verify_vonage_signature(authorization=None) is True

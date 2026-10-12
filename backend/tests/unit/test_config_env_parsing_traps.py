"""Startup configuration accepts the documented formats.

Bug hunt 2026-10-10:
  * db-schema-core-10: CORS_ORIGINS in the documented comma-separated form
    (.env.docker, the field comment) made Settings() raise SettingsError, so
    the backend could not boot.
  * db-schema-core-9: a REDIS_PASSWORD containing '/', '+', '=' or '#'
    (e.g. base64) was spliced raw into the URL; redis.from_url raised "Port
    could not be cast to integer value" and the app silently fell back to
    per-process memory. An explicit REDIS_URL with a password was logged whole.
"""
from __future__ import annotations

from redis.connection import parse_url

from app.core import container
from app.core.config import Settings


def test_comma_separated_cors_origins_boot(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com, https://admin.example.com")
    assert Settings().cors_origins == ["https://app.example.com", "https://admin.example.com"]


def test_json_cors_origins_still_work(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["https://app.example.com"]')
    assert Settings().cors_origins == ["https://app.example.com"]


def test_a_redis_password_with_url_characters_survives(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_HOST", "10.0.0.5")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_PASSWORD", "ab/cd+ef=#gh@ij")
    parsed = parse_url(container._build_redis_url())
    assert (parsed["password"], parsed["host"], parsed["port"]) == ("ab/cd+ef=#gh@ij", "10.0.0.5", 6380)


def test_logged_redis_urls_never_carry_the_password():
    assert container._mask_redis_url("redis://:s3cret@10.0.0.5:6379/0") == "redis://:***@10.0.0.5:6379/0"
    assert container._mask_redis_url("redis://app:s3cret@h:6379/0") == "redis://app:***@h:6379/0"
    assert container._mask_redis_url("redis://h:6379/0") == "redis://h:6379/0"

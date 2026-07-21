"""Redis-URL password redaction for startup logs.

DialerQueueService.initialize() and SessionManager.initialize() used to log
the FULL Redis connection URL — including the password — at INFO level. That
leaked credentials into journald / any log aggregator. redact_redis_url()
masks the password segment only; host/port/db stay visible for diagnostics.
"""
from __future__ import annotations

from app.core.log_redact import redact_redis_url


def test_redacts_password_only_auth():
    url = "redis://:s3cr3tpass@10.0.0.5:6379/0"
    out = redact_redis_url(url)

    assert "****" in out
    assert "s3cr3tpass" not in out
    assert "10.0.0.5:6379/0" in out


def test_redacts_username_and_password():
    url = "redis://myuser:s3cr3tpass@10.0.0.5:6379/0"
    out = redact_redis_url(url)

    assert "****" in out
    assert "s3cr3tpass" not in out
    assert "myuser" in out  # username is not a secret, kept for diagnostics
    assert "10.0.0.5:6379/0" in out


def test_leaves_unauthenticated_url_unchanged():
    url = "redis://localhost:6379/0"
    assert redact_redis_url(url) == url


def test_leaves_empty_string_unchanged():
    assert redact_redis_url("") == ""


def test_handles_rediss_scheme():
    url = "rediss://:s3cr3tpass@redis.example.com:6380/1"
    out = redact_redis_url(url)

    assert "****" in out
    assert "s3cr3tpass" not in out
    assert out.startswith("rediss://")


def test_queue_service_logs_redacted_url(monkeypatch, caplog):
    """End-to-end: DialerQueueService.initialize() must not put the raw
    password in the log record it emits on connect."""
    import logging
    from types import SimpleNamespace

    from app.domain.services import queue_service as qs_module

    class _FakeRedisClient:
        async def ping(self):
            return True

        def type(self, *a, **k):  # pragma: no cover - not exercised here
            raise NotImplementedError

    class _FakeRedisModule:
        @staticmethod
        async def from_url(url, **kwargs):
            return _FakeRedisClient()

    monkeypatch.setattr(qs_module, "redis", _FakeRedisModule)
    monkeypatch.setattr(qs_module, "REDIS_AVAILABLE", True)
    monkeypatch.setenv("REDIS_URL", "redis://:s3cr3tpass@10.0.0.5:6379/0")

    service = qs_module.DialerQueueService()
    # Avoid the real migration path touching a real client.
    service._migrate_processing_key = lambda: _noop()

    async def _noop():
        return None

    import asyncio

    with caplog.at_level(logging.INFO, logger="app.domain.services.queue_service"):
        asyncio.run(service.initialize())

    logged = "\n".join(caplog.messages)
    assert "s3cr3tpass" not in logged
    assert "****" in logged

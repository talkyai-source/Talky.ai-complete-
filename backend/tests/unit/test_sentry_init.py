"""T2.3 — Sentry init.

No-op path is the common case in CI (no DSN). The happy path is
exercised against a fake `sentry_sdk.init` to avoid real network
calls.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_init_no_dsn_is_noop(monkeypatch: pytest.MonkeyPatch):
    from app.core.sentry_init import init_sentry
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry() is False


def test_init_empty_dsn_is_noop(monkeypatch: pytest.MonkeyPatch):
    from app.core.sentry_init import init_sentry
    monkeypatch.setenv("SENTRY_DSN", "   ")
    assert init_sentry() is False


def test_init_with_dsn_calls_sentry_sdk(monkeypatch: pytest.MonkeyPatch):
    import app.core.sentry_init as mod

    monkeypatch.setenv("SENTRY_DSN", "https://test@o0.ingest.sentry.io/0")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")

    fake_init = MagicMock()
    fake_sdk = MagicMock(init=fake_init)

    fake_modules = {
        "sentry_sdk": fake_sdk,
        "sentry_sdk.integrations.fastapi": MagicMock(FastApiIntegration=MagicMock),
        "sentry_sdk.integrations.starlette": MagicMock(StarletteIntegration=MagicMock),
        "sentry_sdk.integrations.asyncio": MagicMock(AsyncioIntegration=MagicMock),
        "sentry_sdk.integrations.logging": MagicMock(LoggingIntegration=MagicMock),
    }

    with patch.dict("sys.modules", fake_modules):
        assert mod.init_sentry() is True

    fake_init.assert_called_once()
    kwargs = fake_init.call_args.kwargs
    assert kwargs["dsn"] == "https://test@o0.ingest.sentry.io/0"
    assert kwargs["environment"] == "staging"
    assert kwargs["traces_sample_rate"] == 0.25
    # Security: PII default OFF for a voice system.
    assert kwargs["send_default_pii"] is False


def test_init_invalid_sample_rates_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch):
    import app.core.sentry_init as mod
    monkeypatch.setenv("SENTRY_DSN", "https://test@o0.ingest.sentry.io/0")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "not-a-number")
    monkeypatch.setenv("SENTRY_PROFILES_SAMPLE_RATE", "also-garbage")

    fake_init = MagicMock()
    fake_modules = {
        "sentry_sdk": MagicMock(init=fake_init),
        "sentry_sdk.integrations.fastapi": MagicMock(FastApiIntegration=MagicMock),
        "sentry_sdk.integrations.starlette": MagicMock(StarletteIntegration=MagicMock),
        "sentry_sdk.integrations.asyncio": MagicMock(AsyncioIntegration=MagicMock),
        "sentry_sdk.integrations.logging": MagicMock(LoggingIntegration=MagicMock),
    }
    with patch.dict("sys.modules", fake_modules):
        mod.init_sentry()
    kwargs = fake_init.call_args.kwargs
    assert kwargs["traces_sample_rate"] == 0.01  # module default
    assert kwargs["profiles_sample_rate"] == 0.0


def test_capture_exception_no_sdk_is_noop():
    """When sentry_sdk isn't installed the helper must not raise."""
    from app.core.sentry_init import capture_exception
    # Should silently succeed even if Sentry was never initialised.
    capture_exception(RuntimeError("boom"))

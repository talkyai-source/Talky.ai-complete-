"""Sentry never receives request bodies, so an uploaded document cannot leak there."""
from unittest.mock import MagicMock, patch

from app.core.sentry_scrub import scrub_event


def test_sentry_init_never_sends_request_bodies(monkeypatch):
    import app.core.sentry_init as mod

    monkeypatch.setenv("SENTRY_DSN", "https://test@o0.ingest.sentry.io/0")
    fake_init = MagicMock()
    fake_modules = {
        "sentry_sdk": MagicMock(init=fake_init),
        "sentry_sdk.integrations.fastapi": MagicMock(FastApiIntegration=MagicMock),
        "sentry_sdk.integrations.starlette": MagicMock(StarletteIntegration=MagicMock),
        "sentry_sdk.integrations.asyncio": MagicMock(AsyncioIntegration=MagicMock),
        "sentry_sdk.integrations.logging": MagicMock(LoggingIntegration=MagicMock),
    }
    with patch.dict("sys.modules", fake_modules):
        assert mod.init_sentry() is True
    kwargs = fake_init.call_args.kwargs
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["send_default_pii"] is False


def test_scrub_event_drops_multipart_request_data():
    event = {"request": {
        "url": "https://api.example/api/v1/telephony/number-requests",
        "method": "POST",
        "headers": {"content-type": "multipart/form-data; boundary=x", "authorization": "Bearer abc"},
        "data": {"business_registration": "SECRETCONTENTMARKER", "use_type": "business"},
    }}
    out = scrub_event(event)
    assert "data" not in out["request"]
    assert "SECRETCONTENTMARKER" not in repr(out)
    assert "authorization" not in out["request"]["headers"]

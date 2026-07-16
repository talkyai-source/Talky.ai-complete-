"""read_calendar_events + honest Drive/Calendar provider errors."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.infrastructure.assistant.tools import calendar_read, drive
from app.infrastructure.assistant.tools.calendar_read import (
    _calendar_provider_error,
    read_calendar_events,
)
from app.infrastructure.assistant.tools.drive import _drive_provider_error
from app.infrastructure.connectors.base import ConnectorProviderError
from app.infrastructure.connectors.google_errors import google_api_error_from_response


def _provider_error(category: str, provider: str = "google_drive") -> ConnectorProviderError:
    return ConnectorProviderError(
        provider=provider,
        operation="list",
        category=category,
        message="detail",
        status_code=403,
    )


class TestGoogleErrorClassifier:
    def test_access_not_configured_is_permission_not_disconnected(self):
        response = httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "message": "Google Drive API has not been used in project 205089823214 before or it is disabled.",
                    "errors": [{"reason": "accessNotConfigured"}],
                }
            },
        )
        err = google_api_error_from_response("google_drive", response, "list_files")
        assert err.category == "permission"

    def test_401_is_authentication(self):
        err = google_api_error_from_response(
            "google_calendar", httpx.Response(401, json={}), "list_events"
        )
        assert err.category == "authentication"


class TestDriveErrorMapping:
    def test_permission_error_mentions_api_not_reconnect(self):
        result = _drive_provider_error(_provider_error("permission"))
        assert result["error_code"] == "drive_permission_denied"
        assert "Drive API" in result["error"]
        assert "reconnect" not in result["error"].lower()

    def test_authentication_error_asks_reconnect(self):
        result = _drive_provider_error(_provider_error("authentication"))
        assert result["error_code"] == "drive_authentication_failed"
        assert result["drive_required"] is True

    def test_timeout_is_classified(self):
        result = _drive_provider_error(httpx.ConnectTimeout("slow"))
        assert result["error_code"] == "drive_timeout"


class TestCalendarErrorMapping:
    def test_permission_error_mentions_calendar_api(self):
        result = _calendar_provider_error(_provider_error("permission", "google_calendar"))
        assert result["error_code"] == "calendar_permission_denied"
        assert "Calendar API" in result["error"]


class TestReadCalendarEvents:
    @pytest.mark.asyncio
    async def test_lists_upcoming_events(self, monkeypatch):
        now = datetime.utcnow()
        event = SimpleNamespace(
            id="ev-1",
            title="Demo call",
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(hours=3),
            timezone="UTC",
            location="Meet",
            attendees=["a@example.com"],
        )
        connector = SimpleNamespace(list_events=AsyncMock(return_value=[event]))

        async def fake_resolver(db, tenant, ctype, **kwargs):
            assert ctype == "calendar"
            return connector, "connector-1", "google_calendar"

        import app.services.connector_resolver as resolver_mod

        monkeypatch.setattr(resolver_mod, "resolve_active_connector", fake_resolver)

        result = await read_calendar_events("tenant-1", SimpleNamespace(), days_ahead="1")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["events"][0]["title"] == "Demo call"
        # string days_ahead was coerced and clamped
        assert result["window_days"] == 1
        call = connector.list_events.await_args
        assert call.kwargs["max_results"] == 10

    @pytest.mark.asyncio
    async def test_not_connected_is_flagged(self, monkeypatch):
        import app.services.connector_resolver as resolver_mod

        async def fake_resolver(db, tenant, ctype, **kwargs):
            raise resolver_mod.ConnectorNotConnectedError("calendar")

        monkeypatch.setattr(resolver_mod, "resolve_active_connector", fake_resolver)

        result = await read_calendar_events("tenant-1", SimpleNamespace())

        assert result["success"] is False
        assert result["calendar_required"] is True
        assert result["error_code"] == "calendar_not_connected"

    @pytest.mark.asyncio
    async def test_provider_permission_failure_is_honest(self, monkeypatch):
        connector = SimpleNamespace(
            list_events=AsyncMock(side_effect=_provider_error("permission", "google_calendar"))
        )
        import app.services.connector_resolver as resolver_mod

        async def fake_resolver(db, tenant, ctype, **kwargs):
            return connector, "connector-1", "google_calendar"

        monkeypatch.setattr(resolver_mod, "resolve_active_connector", fake_resolver)

        result = await read_calendar_events("tenant-1", SimpleNamespace())

        assert result["success"] is False
        assert result["error_code"] == "calendar_permission_denied"
        assert "not connected" not in result["error"].lower()

"""Actual callback/resolver/reviewed-effect probe; synthetic I/O only.

Run from backend with the declared dependency overlay. This file changes no
application behavior. Explicit-external-ID rows are positive controls, not
claims about the canonical callback's persisted identity.
"""
import asyncio
import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.requests import Request

from tests.unit.test_inbox_original_account import _Rows
from app.api.v1.endpoints import connectors as endpoints
from app.infrastructure.assistant.tools import comms, meetings
from app.infrastructure.connectors.base import OAuthTokens
from app.infrastructure.connectors.calendar.base import CalendarEvent
from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector
from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
from app.infrastructure.connectors.email.base import EmailMessage
from app.infrastructure.connectors.email.gmail import GmailConnector
from app.services import connector_resolver, email_service, meeting_service


async def run_case(provider, control):
    cls = {"gmail": GmailConnector, "google_calendar": GoogleCalendarConnector,
           "outlook_calendar": OutlookCalendarConnector}[provider]
    kind = "email" if provider == "gmail" else "calendar"
    db = _Rows()
    db.rows["connectors"][0].update(provider=provider, type=kind)
    db.rows.update(assistant_actions=[], meetings=[])
    enc = SimpleNamespace(encrypt=lambda v: "synthetic:" + v,
                          decrypt=lambda v: v.removeprefix("synthetic:"))
    start = datetime.now(timezone.utc) + timedelta(days=2)
    tokens = OAuthTokens(access_token="synthetic-access", refresh_token="synthetic-refresh",
                         expires_at=start, scope=None)
    provider_effect = AsyncMock(return_value=(EmailMessage(id="synthetic-message") if kind == "email"
        else CalendarEvent(id="synthetic-event", title="Synthetic meeting", start_time=start,
                           end_time=start + timedelta(minutes=30))))
    with pytest.MonkeyPatch.context() as patch:
        def no_network(*args, **kwargs):
            raise AssertionError("Probe attempted network I/O")
        patch.setattr(socket.socket, "connect", no_network)
        patch.setattr(socket.socket, "connect_ex", no_network)
        patch.setattr(socket, "getaddrinfo", no_network)
        for module in (endpoints, connector_resolver, email_service, meeting_service):
            patch.setattr(module, "get_encryption_service", lambda: enc)
        patch.setattr("app.core.security.tenant_isolation.set_current_tenant_id", lambda _: None)
        patch.setattr(endpoints, "get_oauth_state_manager", lambda: SimpleNamespace(
            validate_state=AsyncMock(return_value={"tenant_id": "tenant-a", "user_id": "user-a",
                "provider": provider, "connector_id": "connector-a", "code_verifier": "synthetic",
                "redirect_uri": "https://example.invalid/callback"})))
        patch.setattr(cls, "exchange_code", AsyncMock(return_value=tokens))
        patch.setattr(cls, "refresh_tokens", AsyncMock(return_value=tokens))
        if kind == "email":
            patch.setattr(cls, "get_profile", AsyncMock(return_value={"emailAddress": "synthetic@example.invalid"}))
        # Calendar fetch_account_identity is deliberately the real inherited method.
        patch.setattr(cls, "send_email" if kind == "email" else "create_event", provider_effect)
        response = await endpoints.oauth_callback(
            Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
            state="synthetic", code="synthetic", error=None, db_client=db)
        assert "status=success" in response.headers["location"], response.headers["location"]
        account = db.rows["connector_accounts"][0]
        assert account["external_account_id"] is None
        assert account["status"] == "active" and db.rows["connectors"][0]["status"] == "active"
        if control:
            account["external_account_id"] = "synthetic-provider-subject"
        selected, cid, selected_provider = await connector_resolver.resolve_active_connector(db, "tenant-a", kind)
        assert selected.account_row_id == account["id"] and cid == "connector-a" and selected_provider == provider
        if kind == "email":
            service = email_service.EmailService(db, template_manager=SimpleNamespace(validate_content=Mock()))
            patch.setattr(email_service, "get_email_service", lambda _db: service)
            preview = await comms.send_email("tenant-a", db, to=["recipient@example.invalid"],
                subject="Synthetic subject", body="Synthetic body", confirm=False)
            direct = await service.send_email("tenant-a", ["recipient@example.invalid"],
                                               "Synthetic subject", "Synthetic body")
        else:
            service = meeting_service.MeetingService(db)
            patch.setattr(meeting_service, "get_meeting_service", lambda _db: service)
            preview = await meetings.book_meeting("tenant-a", db, title="Synthetic meeting",
                start_time=start.isoformat(), attendees=["recipient@example.invalid"], confirm=False)
            try:
                direct = await service.create_meeting("tenant-a", "Synthetic meeting", start, 30,
                    ["recipient@example.invalid"])
            except Exception as exc:
                direct = {"success": False, "error_type": type(exc).__name__, "error": str(exc)}
        observed = {"provider": provider, "explicit_external_identity_control": control,
            "canonical_callback": "active_success", "canonical_external_account_id": None,
            "canonical_verified_email_present": bool(account.get("account_email")),
            "resolver_original_row_bound": selected.account_row_id == account["id"],
            "reviewed_preview_success": preview.get("preview") is True,
            "reviewed_preview_error": preview.get("error"),
            "direct_effect_success": direct.get("success") is True,
            "direct_effect_error": direct.get("error"), "provider_effect_attempts": provider_effect.await_count,
            "saved_action_statuses": [a["status"] for a in db.rows["assistant_actions"]]}
        if control:
            assert observed["reviewed_preview_success"] and observed["direct_effect_success"], observed
            assert provider_effect.await_count == 1
        else:
            assert provider_effect.await_count == 0
        # A pin is continuity proof, not proof that this remains the newest
        # selected authorization when reconnect cleanup left an old active row.
        replacement = {**account, "id": "synthetic-replacement-row",
                       "last_refreshed_at": "2099-01-01T00:00:00Z"}
        db.rows["connector_accounts"].append(replacement)
        current, _, _ = await connector_resolver.resolve_active_connector(db, "tenant-a", kind)
        pinned, _, _ = await connector_resolver.resolve_active_connector(
            db, "tenant-a", kind, connector_id="connector-a", account_id=account["id"])
        observed["newer_active_row_becomes_current_selection"] = current.account_row_id == replacement["id"]
        observed["exact_pin_can_still_resolve_older_active_row"] = pinned.account_row_id == account["id"]
        return observed


async def main():
    results = [await run_case(provider, control) for provider in ("gmail", "google_calendar", "outlook_calendar")
               for control in (False, True)]
    report = {"base": "39407989", "scope": "Actual OAuth callback, resolver, preview tools and services; synthetic database/provider ports, blocked network.",
        "results": results, "canonical_failures": sum(not r["reviewed_preview_success"] for r in results if not r["explicit_external_identity_control"]),
        "limitations": ["No real OAuth, provider operation, PostgreSQL/RLS or browser acceptance.",
                        "Positive controls inject an explicit synthetic external identity after the canonical callback."]}
    output = Path(__file__).resolve().parents[2] / "docs/sessions/artifacts/reviewed-account-identity/initial.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if report["canonical_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

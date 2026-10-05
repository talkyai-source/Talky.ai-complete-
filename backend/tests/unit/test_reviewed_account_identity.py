"""Canonical OAuth authorizations must survive review without invented provider IDs."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import socket

import pytest
from starlette.requests import Request

from tests.unit.test_inbox_original_account import _Rows, _RowQuery
from app.api.v1.endpoints import connectors as endpoints
from app.infrastructure.assistant.tools import comms, meetings
from app.infrastructure.connectors.base import OAuthTokens
from app.infrastructure.connectors.base import ConnectorProviderError
from app.infrastructure.connectors.calendar.base import CalendarEvent
from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector
from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
from app.infrastructure.connectors.email.base import EmailMessage
from app.infrastructure.connectors.email.gmail import GmailConnector
from app.services import connector_resolver as resolver, email_service, meeting_service
from app.services.action_execution import public_action_receipt


class Rows(_Rows):
    def __init__(self, provider):
        super().__init__()
        self.rows["connectors"][0].update(provider=provider, type="email" if provider == "gmail" else "calendar")
        self.rows.update(assistant_actions=[], meetings=[], reminders=[])
        self.failure = None
        self.after_write = None

    def table(self, name):
        return Query(self, name)


class Query(_RowQuery):
    def execute(self):
        if self.db.failure and self.db.failure(self):
            return SimpleNamespace(data=[], error="synthetic write failure")
        if self.action == "insert" and self.table_name == "connector_accounts":
            self.payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        result = super().execute()
        if self.db.after_write and self.action in {"insert", "update"}:
            self.db.after_write(self)
        return result


@pytest.fixture(params=["gmail", "google_calendar", "outlook_calendar"])
async def canonical(request, monkeypatch):
    provider = request.param
    cls = {"gmail": GmailConnector, "google_calendar": GoogleCalendarConnector,
           "outlook_calendar": OutlookCalendarConnector}[provider]
    db = Rows(provider)
    enc = SimpleNamespace(encrypt=lambda v: "synthetic:" + v, decrypt=lambda v: v.removeprefix("synthetic:"))
    for module in (endpoints, resolver, email_service, meeting_service):
        monkeypatch.setattr(module, "get_encryption_service", lambda: enc)
    monkeypatch.setattr("app.core.security.tenant_isolation.set_current_tenant_id", lambda _: None)
    def no_network(*_args, **_kwargs):
        raise AssertionError("Synthetic authorization control attempted network I/O")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(endpoints, "get_oauth_state_manager", lambda: SimpleNamespace(validate_state=AsyncMock(
        return_value={"tenant_id": "tenant-a", "user_id": "user-a", "provider": provider,
            "connector_id": "connector-a", "code_verifier": "synthetic", "redirect_uri": "https://example.invalid/callback"})))
    start = datetime.now(timezone.utc) + timedelta(days=2)
    tokens = OAuthTokens(access_token="synthetic-access", refresh_token="synthetic-refresh", expires_at=start)
    monkeypatch.setattr(cls, "exchange_code", AsyncMock(return_value=tokens))
    refresh = AsyncMock(return_value=tokens)
    monkeypatch.setattr(cls, "refresh_tokens", refresh)
    if provider == "gmail":
        monkeypatch.setattr(cls, "get_profile", AsyncMock(return_value={"emailAddress": "synthetic@example.invalid"}))
    effect = AsyncMock(return_value=EmailMessage(id="synthetic-message") if provider == "gmail" else
        CalendarEvent(id="synthetic-event", start_time=start, end_time=start + timedelta(minutes=30)))
    monkeypatch.setattr(cls, "send_email" if provider == "gmail" else "create_event", effect)
    response = await endpoints.oauth_callback(Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
        state="synthetic", code="synthetic", error=None, db_client=db)
    assert "status=success" in response.headers["location"]
    account = db.rows["connector_accounts"][0]
    assert account["external_account_id"] is None and account["status"] == "active"
    if provider == "gmail":
        service = email_service.EmailService(db, template_manager=SimpleNamespace(validate_content=Mock()))
        monkeypatch.setattr(email_service, "get_email_service", lambda _db: service)
        async def preview():
            return await comms.send_email("tenant-a", db, to=["recipient@example.invalid"], subject="Synthetic", body="Synthetic")
        async def apply(proposal):
            return await comms.send_email("tenant-a", db, confirm=True, **proposal["_apply_args"])
    else:
        service = meeting_service.MeetingService(db)
        monkeypatch.setattr(meeting_service, "get_meeting_service", lambda _db: service)
        async def preview():
            return await meetings.book_meeting("tenant-a", db, title="Synthetic", start_time=start.isoformat(), attendees=["recipient@example.invalid"])
        async def apply(proposal):
            return await meetings.book_meeting("tenant-a", db, confirm=True, **proposal["_apply_args"])
    yield SimpleNamespace(provider=provider, cls=cls, db=db, account=account, tokens=tokens,
        refresh=refresh, effect=effect, service=service, start=start, preview=preview, apply=apply)
    monkeypatch.undo()


def newer_account(case, **changes):
    row = {**case.account, "id": "replacement-row", "created_at": (
        datetime.fromisoformat(case.account["created_at"]) + timedelta(seconds=1)).isoformat(), **changes}
    case.db.rows["connector_accounts"].append(row)
    return row


async def test_canonical_preview_apply_persists_versioned_row_without_fabricated_subject(canonical):
    c = canonical
    preview = await c.preview()
    assert preview.get("preview") is True, preview
    proof = preview["_apply_args"]["_reviewed_connector"]
    assert proof == {"identity_version": "authorization_row_v1", "tenant_id": "tenant-a", "connector_id": "connector-a",
                     "provider": c.provider, "account_row_id": c.account["id"]}
    assert preview["changes"][0]["after"] == "synthetic@example.invalid" if c.provider == "gmail" else "authorization" in preview["changes"][0]["after"]
    result = await c.apply(preview)
    assert result["success"] is True and result["confirmation_allowed"] is True, result
    c.effect.assert_awaited_once()
    saved = c.db.rows["assistant_actions"][0]
    assert saved["input_data"]["reviewed_connector"] == proof
    assert all(saved["output_data"][key] == value for key, value in proof.items())
    public = public_action_receipt(saved)
    assert all(public["receipt"][key] == value for key, value in proof.items())
    assert "external_account_id" not in public["receipt"]
    if c.provider != "gmail":
        assert c.db.rows["meetings"][0]["metadata"]["reviewed_connector"] == proof


@pytest.mark.parametrize("field", ["tenant_id", "connector_id", "provider", "account_row_id", "identity_version"])
async def test_wrong_review_owner_cannot_dispatch(canonical, field):
    preview = await canonical.preview()
    preview["_apply_args"]["_reviewed_connector"][field] = "wrong-identity"
    result = await canonical.apply(preview)
    assert result.get("success") is False
    canonical.effect.assert_not_awaited()


async def test_retained_older_active_authorization_cannot_override_newer_reconnect(canonical):
    c = canonical
    preview = await c.preview()
    newer_account(c, last_refreshed_at="2000-01-01T00:00:00+00:00")
    c.account["last_refreshed_at"] = "2099-01-01T00:00:00+00:00"
    result = await c.apply(preview)
    assert result.get("success") is False
    c.effect.assert_not_awaited()
    c.refresh.assert_not_awaited()
    # Global/inbox refresh-time ordering is intentionally unchanged.
    ordinary, _, _ = await resolver.resolve_active_connector(c.db, "tenant-a", "email" if c.provider == "gmail" else "calendar")
    assert ordinary.account_row_id == c.account["id"]


@pytest.mark.parametrize("ordering", [None, "not-a-date", "tie"])
async def test_unprovable_creation_order_cannot_preview(canonical, ordering):
    c = canonical
    if ordering == "tie":
        newer_account(c, created_at=c.account["created_at"])
    else:
        c.account["created_at"] = ordering
    result = await c.preview()
    assert result.get("preview") is not True
    c.effect.assert_not_awaited()
    c.refresh.assert_not_awaited()


async def test_same_authorization_refresh_keeps_review_valid(canonical):
    c = canonical
    preview = await c.preview()
    c.account["token_expires_at"] = "2000-01-01T00:00:00Z"
    result = await c.apply(preview)
    assert result["success"] is True, result
    c.refresh.assert_awaited_once()
    c.effect.assert_awaited_once()


@pytest.mark.parametrize("change", ["reconnect", "revoked", "deleted"])
async def test_authorization_lost_during_refresh_cannot_dispatch(canonical, change):
    c = canonical
    preview = await c.preview()
    c.account["token_expires_at"] = "2000-01-01T00:00:00Z"
    async def changed(_token):
        if change == "reconnect":
            newer_account(c)
        elif change == "revoked":
            c.account["status"] = "revoked"
        else:
            c.db.rows["connector_accounts"] = []
        return c.tokens
    c.refresh.side_effect = changed
    result = await c.apply(preview)
    assert result.get("success") is False
    c.effect.assert_not_awaited()


async def test_optional_genuine_external_identity_remains_a_binding(canonical):
    c = canonical
    c.account["external_account_id"] = "original-subject"
    preview = await c.preview()
    c.account["external_account_id"] = "replacement-subject"
    assert (await c.apply(preview)).get("success") is False
    c.effect.assert_not_awaited()


async def test_pre_effect_intent_write_failure_cannot_send(canonical):
    c = canonical
    preview = await c.preview()
    c.db.failure = lambda q: q.table_name == "assistant_actions" and (
        q.action == "insert" if c.provider != "gmail" else q.action == "update" and "input_data" in (q.payload or {}))
    result = await c.apply(preview)
    assert result.get("success") is False
    c.effect.assert_not_awaited()


@pytest.mark.parametrize("all_receipt_writes_fail", [False, True])
async def test_effect_receipt_failure_preserves_durable_original_intent(canonical, all_receipt_writes_fail):
    c = canonical
    preview = await c.preview()
    c.db.failure = lambda q: q.table_name == "assistant_actions" and q.action == "update" and (
        q.payload.get("status") == "completed" or all_receipt_writes_fail and q.payload.get("status") in {"failed", "unknown"})
    result = await c.apply(preview)
    assert result["success"] is False and result["status"] == "unknown"
    c.effect.assert_awaited_once()
    assert c.db.rows["assistant_actions"][0]["input_data"]["reviewed_connector"] == preview["_apply_args"]["_reviewed_connector"]
    assert c.db.rows["assistant_actions"][0]["status"] != "completed"


@pytest.mark.parametrize("operation", ["update", "cancel"])
@pytest.mark.parametrize("canonical", ["google_calendar", "outlook_calendar"], indirect=True)
async def test_meeting_saved_proof_roundtrips_and_final_check_follows_awaited_validation(canonical, monkeypatch, operation):
    c = canonical
    result = await c.apply(await c.preview())
    meeting = c.db.rows["meetings"][0]
    proof = await c.service.review_meeting("tenant-a", meeting)
    update = AsyncMock(return_value=CalendarEvent(id="synthetic-event"))
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(c.cls, "update_event", update)
    monkeypatch.setattr(c.cls, "delete_event", delete)
    if operation == "update":
        changed = await c.service.update_meeting("tenant-a", result["meeting_id"], new_title="Changed", reviewed_connector=proof)
    else:
        changed = await c.service.cancel_meeting("tenant-a", result["meeting_id"], reviewed_connector=proof)
    assert changed["success"] is True
    assert c.db.rows["meetings"][0]["metadata"]["reviewed_connector"] == proof
    update.reset_mock()
    delete.reset_mock()
    original = c.service.get_meeting
    calls = 0
    async def late_change(*args):
        nonlocal calls
        current = await original(*args)
        calls += 1
        if calls == 2:
            newer_account(c)
        return current
    monkeypatch.setattr(c.service, "get_meeting", late_change)
    with pytest.raises(resolver.ReviewedConnectorChanged):
        if operation == "update":
            await c.service.update_meeting("tenant-a", result["meeting_id"], new_title="Later", reviewed_connector=proof)
        else:
            await c.service.cancel_meeting("tenant-a", result["meeting_id"], reviewed_connector=proof)
    update.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.parametrize("original_identity", [None, "genuine-legacy-subject"])
@pytest.mark.parametrize("canonical", ["google_calendar", "outlook_calendar"], indirect=True)
async def test_legacy_meeting_is_not_retroactively_row_bound(canonical, monkeypatch, original_identity):
    c = canonical
    await c.apply(await c.preview())
    meeting = c.db.rows["meetings"][0]
    meeting["metadata"].pop("reviewed_connector")
    meeting["metadata"]["external_account_id"] = original_identity
    c.account["external_account_id"] = original_identity
    update = AsyncMock(return_value=CalendarEvent(id="synthetic-event"))
    monkeypatch.setattr(c.cls, "update_event", update)
    if original_identity:
        reviewed = await c.service.review_meeting("tenant-a", meeting)
        result = await c.service.update_meeting("tenant-a", meeting["id"], new_title="Legacy update", reviewed_connector=reviewed)
        assert result["success"] is True
        update.assert_awaited_once()
    else:
        with pytest.raises(resolver.ReviewedConnectorChanged):
            await c.service.review_meeting("tenant-a", meeting)
        update.assert_not_awaited()
    assert "reviewed_connector" not in meeting["metadata"]


@pytest.mark.parametrize("canonical", ["google_calendar", "outlook_calendar"], indirect=True)
@pytest.mark.parametrize("operation", ["create", "update", "cancel"])
@pytest.mark.parametrize("change", ["newer_row", "external_identity_added"])
async def test_final_calendar_admission_after_intent_denial_is_not_unknown(canonical, monkeypatch, operation, change):
    c = canonical
    preview = await c.preview()
    if operation != "create":
        await c.apply(preview)
    c.effect.reset_mock()
    update, delete = AsyncMock(), AsyncMock()
    monkeypatch.setattr(c.cls, "update_event", update)
    monkeypatch.setattr(c.cls, "delete_event", delete)
    def after_intent(query):
        if query.table_name == "assistant_actions" and query.action == "insert":
            if change == "newer_row":
                newer_account(c)
            else:
                c.account["external_account_id"] = "new-subject"
    c.db.after_write = after_intent
    if operation == "create":
        result = await c.apply(preview)
    elif operation == "update":
        result = await c.service.update_meeting("tenant-a", c.db.rows["meetings"][0]["id"], new_title="Later")
    else:
        result = await c.service.cancel_meeting("tenant-a", c.db.rows["meetings"][0]["id"])
    assert result["success"] is False and result["status"] == "failed", result
    assert c.db.rows["assistant_actions"][-1]["status"] == "failed"
    c.effect.assert_not_awaited()
    update.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.parametrize("canonical", ["gmail"], indirect=True)
@pytest.mark.parametrize("change", ["newer_row", "external_identity_added"])
async def test_final_email_admission_after_binding_refuses_changed_authorization(canonical, monkeypatch, change):
    c = canonical
    preview = await c.preview()
    original = c.service._bind_action_authorization
    async def changed(*args):
        await original(*args)
        if change == "newer_row":
            newer_account(c)
        else:
            c.account["external_account_id"] = "new-subject"
    monkeypatch.setattr(c.service, "_bind_action_authorization", changed)
    result = await c.apply(preview)
    assert result["success"] is False and result["status"] == "failed"
    c.effect.assert_not_awaited()


@pytest.mark.parametrize("canonical", ["gmail"], indirect=True)
@pytest.mark.parametrize("replace_during_refresh", [False, True])
async def test_definite_401_retry_remains_within_reviewed_authorization(canonical, replace_during_refresh):
    c = canonical
    preview = await c.preview()
    c.effect.side_effect = [ConnectorProviderError(provider="gmail", operation="send_email", category="authentication",
        status_code=401, message="Synthetic rejected authentication"), EmailMessage(id="synthetic-message")]
    if replace_during_refresh:
        async def replaced(_token):
            newer_account(c)
            return c.tokens
        c.refresh.side_effect = replaced
    result = await c.apply(preview)
    assert result["success"] is (not replace_during_refresh)
    assert c.effect.await_count == (1 if replace_during_refresh else 2)
    assert c.db.rows["assistant_actions"][0]["input_data"]["reviewed_connector"] == preview["_apply_args"]["_reviewed_connector"]


@pytest.mark.parametrize("canonical", ["gmail"], indirect=True)
@pytest.mark.parametrize("competing_status", ["cancelled", "running"])
async def test_failed_binding_cleanup_cannot_overwrite_competing_action_phase(canonical, competing_status):
    c = canonical
    preview = await c.preview()
    def competing(query):
        if query.table_name == "assistant_actions" and query.action == "insert":
            c.db.rows["assistant_actions"][-1]["status"] = competing_status
    c.db.after_write = competing
    result = await c.apply(preview)
    assert result["success"] is False
    c.effect.assert_not_awaited()
    assert c.db.rows["assistant_actions"][-1]["status"] == competing_status


@pytest.mark.parametrize("canonical", ["gmail"], indirect=True)
@pytest.mark.parametrize("competing_status", ["cancelled", "completed"])
async def test_post_effect_lost_phase_preserves_competing_state_and_returns_unconfirmed_receipt(canonical, competing_status):
    c = canonical
    preview = await c.preview()
    async def acknowledged(**_kwargs):
        saved = c.db.rows["assistant_actions"][-1]
        assert saved["status"] == "running"
        saved["status"] = competing_status
        return EmailMessage(id="synthetic-late-message")
    c.effect.side_effect = acknowledged
    result = await c.apply(preview)
    assert result["success"] is False and result["status"] == "unknown"
    assert result["message_id"] == "synthetic-late-message"
    assert result["confirmation_allowed"] is False
    saved = c.db.rows["assistant_actions"][-1]
    assert saved["status"] == competing_status
    assert saved["input_data"]["reviewed_connector"] == preview["_apply_args"]["_reviewed_connector"]
    c.effect.assert_awaited_once()


@pytest.mark.parametrize("canonical", ["gmail"], indirect=True)
async def test_lost_binding_ack_does_not_dispatch_or_claim_running_cleanup_authority(canonical):
    c = canonical
    preview = await c.preview()
    def lost_ack(query):
        if query.table_name == "assistant_actions" and query.action == "update" and "input_data" in query.payload:
            raise RuntimeError("Synthetic lost binding acknowledgement")
    c.db.after_write = lost_ack
    result = await c.apply(preview)
    assert result["success"] is False and result["confirmation_allowed"] is False
    c.effect.assert_not_awaited()
    saved = c.db.rows["assistant_actions"][-1]
    assert saved["status"] == "running"
    assert saved["input_data"]["reviewed_connector"] == preview["_apply_args"]["_reviewed_connector"]


@pytest.mark.parametrize("canonical", ["google_calendar", "outlook_calendar"], indirect=True)
async def test_standalone_availability_read_does_not_require_new_review_proof(canonical, monkeypatch):
    c = canonical
    c.account["created_at"] = None
    available = AsyncMock(return_value=[])
    monkeypatch.setattr(c.cls, "get_availability", available)
    assert await c.service.get_availability("tenant-a", c.start, c.start + timedelta(hours=1)) == []
    available.assert_awaited_once()


@pytest.mark.parametrize("canonical", ["google_calendar"], indirect=True)
@pytest.mark.parametrize("operation", ["create", "update", "cancel"])
@pytest.mark.parametrize("competing_status", ["cancelled", "completed"])
@pytest.mark.parametrize("after_effect", [False, True])
async def test_calendar_finalization_preserves_competing_phase(canonical, monkeypatch, operation, competing_status, after_effect):
    c = canonical
    preview = await c.preview()
    if operation != "create":
        await c.apply(preview)
    c.effect.reset_mock()
    def competing(query):
        if query.table_name == "assistant_actions" and query.action == "insert":
            c.db.rows["assistant_actions"][-1]["status"] = competing_status
            newer_account(c)
    if not after_effect:
        c.db.after_write = competing
    async def acknowledged(*_args, **_kwargs):
        c.db.rows["assistant_actions"][-1]["status"] = competing_status
        return True if operation == "cancel" else CalendarEvent(id="synthetic-event")
    effect = c.effect if operation == "create" else AsyncMock()
    effect.side_effect = acknowledged
    if operation != "create":
        monkeypatch.setattr(c.cls, "update_event" if operation == "update" else "delete_event", effect)
    if operation == "create":
        result = await c.apply(preview)
    elif operation == "update":
        result = await c.service.update_meeting("tenant-a", c.db.rows["meetings"][0]["id"], new_title="Changed")
    else:
        result = await c.service.cancel_meeting("tenant-a", c.db.rows["meetings"][0]["id"])
    assert result["success"] is False and result["confirmation_allowed"] is False
    assert result["status"] == ("unknown" if after_effect else "failed")
    assert c.db.rows["assistant_actions"][-1]["status"] == competing_status
    assert effect.await_count == int(after_effect)
    if after_effect:
        assert result["external_event_id"] == "synthetic-event"
